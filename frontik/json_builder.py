# coding=utf-8

import json
import logging

from tornado.concurrent import Future

from frontik.compat import basestring_type, iteritems
from frontik.http_client import RequestResult

future_logger = logging.getLogger('frontik.future')


class JsonBuilder(object):
    __slots__ = ('_data', '_encoder', 'root_node', 'logger')

    def __init__(self, root_node=None, json_encoder=None, logger=None):
        if root_node is not None and not isinstance(root_node, basestring_type):
            raise TypeError('Cannot set {} as root node'.format(root_node))

        self._data = []
        self._encoder = json_encoder
        self.logger = logger if logger is not None else future_logger
        self.root_node = root_node

    def put(self, *args, **kwargs):
        self._data.extend(args)
        if kwargs:
            self._data.append(kwargs)

    def is_empty(self):
        return len(self._data) == 0

    def clear(self):
        self._data = []

    @staticmethod
    def get_error_node(exception):
        return {
            'error': {k: v for k, v in iteritems(exception.attrs)}
        }

    def _check_value(self, v):
        def _check_iterable(l):
            return [self._check_value(v) for v in l]

        def _check_dict(d):
            return {k: self._check_value(v) for k, v in iteritems(d)}

        if isinstance(v, dict):
            return _check_dict(v)

        elif isinstance(v, (set, frozenset, list, tuple)):
            return _check_iterable(v)

        elif isinstance(v, RequestResult):
            if v.exception is not None:
                return self.get_error_node(v.exception)
            return self._check_value(v.data)

        elif isinstance(v, Future):
            if v.done():
                return self._check_value(v.result())

            self.logger.info('unresolved Future in JsonBuilder')
            return None

        elif hasattr(v, 'to_dict'):
            return _check_dict(v.to_dict())

        return v

    def to_dict(self):
        result = {}
        for chunk in self._check_value(self._data):
            if chunk is not None:
                result.update(chunk)

        if self.root_node is not None:
            result = {self.root_node: result}

        return result

    def to_string(self):
        if self._encoder is not None:
            return json.dumps(self.to_dict(), cls=self._encoder, ensure_ascii=False)
        return json.dumps(self.to_dict(), ensure_ascii=False)
