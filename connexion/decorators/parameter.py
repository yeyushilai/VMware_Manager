import functools
import inspect
import logging
import re

import inflection
import six

from ..http_facts import FORM_CONTENT_TYPES
from ..lifecycle import ConnexionRequest  # NOQA
from ..utils import all_json

try:
    import __builtin__
except ImportError:  # pragma: no cover
    import __builtin__ as builtins


logger = logging.getLogger(__name__)

# Python 2/3 compatibility:
try:
    py_string = unicode
except NameError:  # pragma: no cover
    py_string = str  # pragma: no cover


def inspect_getargspec(func):
    """Get the names and default values of a function's arguments.

    A tuple of four things is returned: (args, varargs, varkw, defaults).
    'args' is a list of the argument names (it may contain nested lists).
    'varargs' and 'varkw' are the names of the * and ** arguments or None.
    'defaults' is an n-tuple of the default values of the last n arguments.
    """

    if inspect.ismethod(func):
        func = func.im_func

    # Py2 Stdlib uses isfunction(func) which is too strict for Cython-compiled
    # functions though such have perfectly usable func_code, func_defaults.
    if not (hasattr(func, "func_code") and hasattr(func, "func_defaults")):
        raise TypeError('{!r} missing func_code or func_defaults'.format(func))

    args, varargs, varkw = inspect.getargs(func.func_code)
    return inspect.ArgSpec(args, varargs, varkw, func.func_defaults)


def inspect_function_arguments(function):  # pragma: no cover
    """
    Returns the list of variables names of a function and if it
    accepts keyword arguments.

    :type function: Callable
    :rtype: tuple[list[str], bool]
    """
    if six.PY3:
        parameters = inspect.signature(function).parameters
        bound_arguments = [name for name, p in parameters.items()
                           if p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)]
        has_kwargs = any(p.kind == p.VAR_KEYWORD for p in parameters.values())
        return list(bound_arguments), has_kwargs
    else:
        argspec = inspect_getargspec(function)
        return argspec.args, bool(argspec.keywords)


def snake_and_shadow(name):
    """
    Converts the given name into Pythonic form. Firstly it converts CamelCase names to snake_case. Secondly it looks to
    see if the name matches a known built-in and if it does it appends an underscore to the name.
    :param name: The parameter name
    :type name: str
    :return:
    """
    snake = inflection.underscore(name)
    if snake in builtins.__dict__.keys():
        return "{}_".format(snake)
    return snake


def parameter_to_arg(operation, function, pythonic_params=False,
                     pass_context_arg_name=None):
    """
    Pass query and body parameters as keyword arguments to handler function.

    See (https://github.com/zalando/connexion/issues/59)
    :param operation: The operation being called
    :type operation: connexion.operations.AbstractOperation
    :param pythonic_params: When True CamelCase parameters are converted to snake_case and an underscore is appended to
    any shadowed built-ins
    :type pythonic_params: bool
    :param pass_context_arg_name: If not None URL and function has an argument matching this name, the framework's
    request context will be passed as that argument.
    :type pass_context_arg_name: str|None
    """
    consumes = operation.consumes

    def sanitized(name):
        return name and re.sub('^[^a-zA-Z_]+', '', re.sub('[^0-9a-zA-Z_]', '', name))

    def pythonic(name):
        name = name and snake_and_shadow(name)
        return sanitized(name)

    sanitize = pythonic if pythonic_params else sanitized
    logger.debug('Function is : %s', function)
    import types
    logger.debug('Function isinstance(object, types.FunctionType) : %s'
                 % isinstance(object, types.FunctionType))
    logger.debug('Function type(function) : %s'
                 % type(function))
    arguments, has_kwargs = inspect_function_arguments(function)

    @functools.wraps(function)
    def wrapper(request):
        # type: (ConnexionRequest) -> Any
        logger.debug('Function Arguments: %s', arguments)
        kwargs = {}

        if all_json(consumes):
            request_body = request.json
        elif consumes[0] in FORM_CONTENT_TYPES:
            request_body = {sanitize(k): v for k, v in request.form.items()}
        else:
            request_body = request.body

        try:
            query = request.query.to_dict(flat=False)
        except AttributeError:
            query = dict(request.query.items())

        kwargs.update(
            operation.get_arguments(request.path_params, query, request_body,
                                    request.files, arguments, has_kwargs, sanitize)
        )

        # optionally convert parameter variable names to un-shadowed, snake_case form
        if pythonic_params:
            kwargs = {snake_and_shadow(k): v for k, v in kwargs.items()}

        # add context info (e.g. from security decorator)
        for key, value in request.context.items():
            if has_kwargs or key in arguments:
                kwargs[key] = value
            else:
                logger.debug("Context parameter '%s' not in function arguments", key)

        # attempt to provide the request context to the function
        if pass_context_arg_name and (has_kwargs or pass_context_arg_name in arguments):
            kwargs[pass_context_arg_name] = request.context

        return function(**kwargs)

    return wrapper
