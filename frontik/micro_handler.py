# coding=utf-8

from functools import partial

from tornado.concurrent import Future

from frontik.compat import iteritems
from frontik.handler import BaseHandler, HTTPError


class MicroHandler(BaseHandler):
    @staticmethod
    def make_url(host, uri):
        return u'{}/{}'.format(host.rstrip(u'/'), uri.lstrip(u'/'))

    def GET(self, host, uri, data=None, headers=None, connect_timeout=None, request_timeout=None,
            follow_redirects=True, labels=None, fail_on_error=False):

        future = self._http_client.get_url(
            self.make_url(host, uri),
            data=data, headers=headers,
            connect_timeout=connect_timeout, request_timeout=request_timeout,
            follow_redirects=follow_redirects, labels=labels,
            parse_on_error=True
        )

        future.fail_on_error = fail_on_error
        return future

    def POST(self, host, uri, data='', headers=None, files=None, connect_timeout=None, request_timeout=None,
             follow_redirects=True, content_type=None, labels=None, fail_on_error=False):

        future = self._http_client.post_url(
            self.make_url(host, uri),
            data=data, headers=headers, files=files,
            connect_timeout=connect_timeout, request_timeout=request_timeout,
            follow_redirects=follow_redirects, content_type=content_type, labels=labels,
            parse_on_error=True
        )

        future.fail_on_error = fail_on_error
        return future

    def PUT(self, host, uri, data='', headers=None, connect_timeout=None, request_timeout=None,
            content_type=None, labels=None, fail_on_error=False):

        future = self._http_client.put_url(
            self.make_url(host, uri),
            data=data, headers=headers,
            connect_timeout=connect_timeout, request_timeout=request_timeout,
            content_type=content_type, labels=labels,
            parse_on_error=True
        )

        future.fail_on_error = fail_on_error
        return future

    def DELETE(self, host, uri, data=None, headers=None, connect_timeout=None, request_timeout=None,
               content_type=None, labels=None, fail_on_error=False):

        future = self._http_client.delete_url(
            self.make_url(host, uri),
            data=data, headers=headers,
            connect_timeout=connect_timeout, request_timeout=request_timeout,
            content_type=content_type, labels=labels,
            parse_on_error=True
        )

        future.fail_on_error = fail_on_error
        return future

    def handle_return_value(self, handler_method_name, return_value):
        def _fail_on_error_wrapper(name, future):
            result = future._result
            if not result.response.error and not result.exception:
                return

            error_method_name = handler_method_name + '_requests_failed'
            if hasattr(self, error_method_name):
                getattr(self, error_method_name)(name, result.data, result.response)

            status_code = result.response.code if 300 <= result.response.code < 500 else 502
            raise HTTPError(status_code, 'HTTP request failed with code {}'.format(result.response.code))

        if isinstance(return_value, dict):
            futures = {}
            for name, req in iteritems(return_value):
                if isinstance(req, Future):
                    futures[name] = req
                    if getattr(req, 'fail_on_error', False):
                        self.add_future(futures[name], partial(_fail_on_error_wrapper, name))
                else:
                    raise Exception('Invalid request object: {!r}'.format(req))

            done_method_name = handler_method_name + '_requests_done'
            self._http_client.group(futures, getattr(self, done_method_name, None), name='MicroHandler')

        elif return_value is not None:
            raise Exception('Invalid return type: {}'.format(type(return_value)))
