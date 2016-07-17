# coding=utf-8

from functools import partial
import re
import time

from lxml import etree
import simplejson
from tornado.concurrent import Future
from tornado.ioloop import IOLoop
from tornado.options import options

from frontik.async import AsyncGroup
from frontik.auth import DEBUG_AUTH_HEADER_NAME
from frontik.compat import iteritems
from frontik.handler_debug import PageHandlerDebug, response_from_debug
from frontik.loggers.request import logger as request_logger
import frontik.util


class HttpClient(object):
    def __init__(self, handler, http_client_impl, modify_http_request_hook):
        self.handler = handler
        self.modify_http_request_hook = modify_http_request_hook
        self.http_client_impl = http_client_impl

    def group(self, futures, callback=None, name=None):
        if callable(callback):
            results_holder = {}
            group_callback = self.handler.finish_group.add(partial(callback, results_holder))

            def delay_cb():
                IOLoop.instance().add_callback(self.handler.check_finished(group_callback))

            async_group = AsyncGroup(delay_cb, logger=self.handler.log, name=name)

            def future_callback(name, future):
                results_holder[name] = future.result()

            for name, future in iteritems(futures):
                if future.done():
                    future_callback(name, future)
                else:
                    self.handler.add_future(future, async_group.add(partial(future_callback, name)))

            async_group.try_finish()

        return futures

    def get_url(self, url, data=None, headers=None, connect_timeout=None, request_timeout=None,
                callback=None, error_callback=None, follow_redirects=True, labels=None,
                add_to_finish_group=True, parse_response=True, parse_on_error=False):

        request = frontik.util.make_get_request(url, data, headers, connect_timeout, request_timeout, follow_redirects)
        request._frontik_labels = labels

        return self.fetch(
            request,
            callback=callback, error_callback=error_callback,
            parse_response=parse_response, parse_on_error=parse_on_error,
            add_to_finish_group=add_to_finish_group
        )

    def head_url(self, url, data=None, headers=None, connect_timeout=None, request_timeout=None,
                 callback=None, error_callback=None, follow_redirects=True, labels=None,
                 add_to_finish_group=True):

        request = frontik.util.make_head_request(url, data, headers, connect_timeout, request_timeout, follow_redirects)
        request._frontik_labels = labels

        return self.fetch(
            request,
            callback=callback, error_callback=error_callback,
            parse_response=False, parse_on_error=False,
            add_to_finish_group=add_to_finish_group
        )

    def post_url(self, url, data='', headers=None, files=None, connect_timeout=None, request_timeout=None,
                 callback=None, error_callback=None, follow_redirects=True, content_type=None, labels=None,
                 add_to_finish_group=True, parse_response=True, parse_on_error=False):

        request = frontik.util.make_post_request(
            url, data, headers, files, content_type, connect_timeout, request_timeout, follow_redirects
        )
        request._frontik_labels = labels

        return self.fetch(
            request,
            callback=callback, error_callback=error_callback,
            parse_response=parse_response, parse_on_error=parse_on_error,
            add_to_finish_group=add_to_finish_group
        )

    def put_url(self, url, data='', headers=None, connect_timeout=None, request_timeout=None,
                callback=None, error_callback=None, content_type=None, labels=None,
                add_to_finish_group=True, parse_response=True, parse_on_error=False):

        request = frontik.util.make_put_request(url, data, headers, content_type, connect_timeout, request_timeout)
        request._frontik_labels = labels

        return self.fetch(
            request,
            callback=callback, error_callback=error_callback,
            parse_response=parse_response, parse_on_error=parse_on_error,
            add_to_finish_group=add_to_finish_group
        )

    def delete_url(self, url, data=None, headers=None, connect_timeout=None, request_timeout=None,
                   callback=None, error_callback=None, content_type=None, labels=None,
                   add_to_finish_group=True, parse_response=True, parse_on_error=False):

        request = frontik.util.make_delete_request(url, data, headers, content_type, connect_timeout, request_timeout)
        request._frontik_labels = labels

        return self.fetch(
            request,
            callback=callback, error_callback=error_callback,
            parse_response=parse_response, parse_on_error=parse_on_error,
            add_to_finish_group=add_to_finish_group
        )

    def fetch(self, request, callback=None, error_callback=None,
              parse_response=True, parse_on_error=False,
              add_to_finish_group=True):
        """ Tornado HTTP client compatible method """
        future = Future()

        if self.handler._finished:
            self.handler.log.warning(
                'attempted to make http request to %s when page is finished, ignoring', request.url
            )
            return future

        if self.handler._prepared and self.handler.debug.debug_mode.pass_debug:
            request.headers[PageHandlerDebug.DEBUG_HEADER_NAME] = True
            request.url = frontik.util.make_url(request.url, hh_debug_param=int(time.time()))

            for header_name in ('Authorization', DEBUG_AUTH_HEADER_NAME):
                authorization = self.handler.request.headers.get(header_name)
                if authorization is not None:
                    request.headers[header_name] = authorization

        request.headers['X-Request-Id'] = self.handler.request_id

        if request.connect_timeout is None:
            request.connect_timeout = options.http_client_default_connect_timeout
        if request.request_timeout is None:
            request.request_timeout = options.http_client_default_request_timeout

        request.connect_timeout *= options.timeout_multiplier
        request.request_timeout *= options.timeout_multiplier

        if options.http_proxy_host is not None:
            request.proxy_host = options.http_proxy_host
            request.proxy_port = options.http_proxy_port

        def request_callback(response):
            response = self._unwrap_debug_and_log_response(request, response)
            request_result = self._parse_response(response, parse_response, parse_on_error)

            if callable(error_callback) and (response.error or request_result.error is not None):
                error_callback(request_result.data, response)
            elif callable(callback):
                callback(request_result.data, response)

            future.set_result(request_result)

        if add_to_finish_group:
            request_callback = self.handler.finish_group.add(self.handler.check_finished(request_callback))

        self.http_client_impl.fetch(self.modify_http_request_hook(request), callback=request_callback)
        return future

    def _unwrap_debug_and_log_response(self, request, response):
        try:
            debug_extra = {}
            if response.headers.get(PageHandlerDebug.DEBUG_HEADER_NAME):
                debug_response = response_from_debug(request, response)
                if debug_response is not None:
                    debug_xml, response = debug_response
                    debug_extra['_debug_response'] = debug_xml

            debug_extra.update({'_response': response, '_request': request})
            if getattr(request, '_frontik_labels', None) is not None:
                debug_extra['_labels'] = request._frontik_labels

            self.handler.log.info(
                'got {code}{size} {url} in {time:.2f}ms'.format(
                    code=response.code,
                    url=response.effective_url,
                    size=' {0} bytes'.format(len(response.body)) if response.body is not None else '',
                    time=response.request_time * 1000
                ),
                extra=debug_extra
            )
        except Exception:
            self.handler.log.exception('Cannot log response info')

        return response

    def _parse_response(self, response, parse_response, parse_on_error):
        if response.error and not parse_on_error:
            log_func = self.handler.log.error if response.code >= 500 else self.handler.log.warning
            log_func('{code} failed {url} ({reason!s})'.format(
                code=response.code, url=response.effective_url, reason=response.error)
            )

            return RequestResult.from_error(reason=str(response.error), code=response.code)

        elif not parse_response:
            return RequestResult.from_parsed_response(response.body, response)

        elif response.code != 204:
            content_type = response.headers.get('Content-Type', '')
            for k, v in iteritems(DEFAULT_REQUEST_TYPES):
                if k.search(content_type):
                    return v(response, logger=self.handler.log)

        return RequestResult.from_parsed_response(None, response)


class RequestResult(object):
    __slots__ = ('data', 'response', 'error')

    def __init__(self):
        self.data = None
        self.response = None
        self.error = None

    @staticmethod
    def from_parsed_response(data, response):
        request_result = RequestResult()
        request_result.data = data
        request_result.response = response
        return request_result

    @staticmethod
    def from_error(**kwargs):
        request_result = RequestResult()
        request_result.error = kwargs
        return request_result


def _parse_response(response, logger=request_logger, parser=None, response_type=None):
    try:
        return RequestResult.from_parsed_response(parser(response.body), response)
    except:
        _preview_len = 100

        if len(response.body) > _preview_len:
            body_preview = '{0}...'.format(response.body[:_preview_len])
        else:
            body_preview = response.body

        logger.exception('failed to parse {0} response from {1}, bad data: "{2}"'.format(
            response_type, response.effective_url, body_preview))

        return RequestResult.from_error(url=response.effective_url, reason='invalid {}'.format(response_type))


_xml_parser = etree.XMLParser(strip_cdata=False)
_parse_response_xml = partial(_parse_response,
                              parser=lambda x: etree.fromstring(x, parser=_xml_parser),
                              response_type='XML')

_parse_response_json = partial(_parse_response,
                               parser=simplejson.loads,
                               response_type='JSON')

DEFAULT_REQUEST_TYPES = {
    re.compile('.*xml.?'): _parse_response_xml,
    re.compile('.*json.?'): _parse_response_json,
    re.compile('.*text/plain.?'): (
        lambda response, logger: RequestResult.from_parsed_response(response.body, response)
    ),
}
