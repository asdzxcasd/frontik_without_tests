# coding=utf-8

from functools import partial


class Arg(object):
    __slots__ = ('arg_type', 'default', 'name', 'choice', 'default_on_exception')

    _ARG_DEFAULT = []

    def __init__(self, arg_type, default=_ARG_DEFAULT, name=None, choice=None, default_on_error=False):
        """Argument descriptor
        :arg type arg_type: Type of an argument. Can be one of the types:
            `bool` — will be parsed as `True` only if the argument contains a string `true` (case-insensitive)
            `int`, `float`, `str` — just converts an argument to one of these types
            `[bool]`, `[str]`, `[int]`, `[float]` — a list of values of specified type
        :arg Any default: Default value, used when an argument is missing.
            If default value is not specified, and an argument is missing, `HTTPError` is raised. For list types
            `HTTPError` is never raised and argument value in this case is considered to be an empty list.
        :arg str name: query argument name (
        :arg list choice: A list of valid values for an argument.
        :arg bool default_on_error: Use default value if argument parsing fails
             (if `True`, `default` must also be defined).
        """
        self.arg_type = arg_type
        self.default = default
        self.name = name
        self.choice = choice
        self.default_on_exception = default_on_error

        if default_on_error and not self.has_default:
            raise AssertionError('default_on_error = True, but no default value')

        if isinstance(arg_type, list) and not arg_type:
            raise AssertionError('list argument must specify the type of its items')

    @property
    def has_default(self):
        return self.default is not self._ARG_DEFAULT

    def parse(self, name, values):
        if isinstance(self.arg_type, list):
            return self._parse_list(name, values)

        return self._parse_single(name, values)

    def _parse_list(self, name, values):
        if not values and self.has_default:
            return self.default

        try:
            parsed_values = _list_parser(_ARG_PARSERS[self.arg_type[0]], values)
        except ValueError:
            if self.default_on_exception:
                return self.default

            raise ValueError(u'parameter "{}" must be of type [{}], got [{}]'.format(
                name, self.arg_type[0].__name__, u', '.join(values)
            ))

        return self._validate_choice(name, parsed_values)

    def _parse_single(self, name, values):
        if not values:
            if self.has_default:
                return self.default

            raise ValueError(u'parameter "{}" is missing'.format(name))

        try:
            parsed_values = _ARG_PARSERS[self.arg_type](values[-1])
        except ValueError:
            if self.default_on_exception:
                return self.default

            raise ValueError(u'parameter "{}" must be of type {}, got "{}"'.format(
                name, self.arg_type.__name__, values[-1]
            ))

        return self._validate_choice(name, parsed_values)

    def _validate_choice(self, name, values):
        values_set = set(values) if isinstance(values, list) else {values}

        if self.choice is not None and not values_set.issubset(self.choice):
            if self.default_on_exception:
                return self.default

            raise ValueError(u'parameter "{}" must be one of the following: [{}], got [{}]'.format(
                name, u', '.join(self.choice), u', '.join(values_set)
            ))

        return values


def _list_parser(arg_parser, values):
    return [arg_parser(x) for x in values]


def _str_parser(value):
    return value


def _int_parser(value):
    return int(value)


def _float_parser(value):
    return float(value)


def _bool_parser(value):
    return value.lower() == 'true'


_ARG_PARSERS = {
    str: _str_parser,
    int: _int_parser,
    float: _float_parser,
    bool: _bool_parser
}
