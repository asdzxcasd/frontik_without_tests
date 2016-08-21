# coding=utf-8

import importlib
import logging
import re
import time
from lxml import etree

import tornado.autoreload
from tornado.concurrent import Future
import tornado.curl_httpclient
import tornado.ioloop
from tornado.options import options
from tornado.routing import HandlerMatch
import tornado.web

from frontik.compat import iteritems
from frontik.handler import ErrorHandler
import frontik.loggers
from frontik.loggers.request import RequestLogger
import frontik.producers.json_producer
import frontik.producers.xml_producer

app_logger = logging.getLogger('frontik.app')


def get_frontik_and_apps_versions(application):
    from frontik.version import version
    import simplejson
    import sys
    import tornado

    versions = etree.Element('versions')
    etree.SubElement(versions, 'frontik').text = version
    etree.SubElement(versions, 'tornado').text = tornado.version
    etree.SubElement(versions, 'lxml.etree.LXML').text = '.'.join(str(x) for x in etree.LXML_VERSION)
    etree.SubElement(versions, 'lxml.etree.LIBXML').text = '.'.join(str(x) for x in etree.LIBXML_VERSION)
    etree.SubElement(versions, 'lxml.etree.LIBXSLT').text = '.'.join(str(x) for x in etree.LIBXSLT_VERSION)
    etree.SubElement(versions, 'simplejson').text = simplejson.__version__
    etree.SubElement(versions, 'python').text = sys.version.replace('\n', '')
    etree.SubElement(versions, 'application', name=options.app).extend(application.application_version_xml())

    return versions


class VersionHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header('Content-Type', 'text/xml')
        self.write(
            etree.tostring(get_frontik_and_apps_versions(self.application), encoding='utf-8', xml_declaration=True))


class StatusHandler(tornado.web.RequestHandler):

    @tornado.web.asynchronous
    def get(self):
        self.set_header('Content-Type', 'application/json; charset=UTF-8')

        cur_uptime = time.time() - self.application.start_time
        if cur_uptime < 60:
            uptime_value = '{:.2f} seconds'.format(cur_uptime)
        elif cur_uptime < 3600:
            uptime_value = '{:.2f} minutes'.format(cur_uptime / 60)
        else:
            uptime_value = '{:.2f} hours and {:.2f} minutes'.format(cur_uptime / 3600, (cur_uptime % 3600) / 60)

        result = {
            'uptime': uptime_value,
            'workers': {
                'total': tornado.options.options.max_http_clients,
                'free':  len(self.application.curl_http_client._free_list)
            }
        }

        self.finish(result)


def extend_request_arguments(request, match):
    arguments = match.groupdict()
    for name, value in iteritems(arguments):
        if value:
            request.arguments.setdefault(name, []).append(value)


class NotFoundRouter(object):
    def __init__(self, app):
        self.app = app
        self.handler_class = self.app.application_404_handler()

    def find_handler(self, request):
        request_id = request.headers.get('X-Request-Id', FrontikApplication.next_request_id())
        logger = RequestLogger(request, request_id)
        return HandlerMatch(self.handler_class(self.app, request, logger=logger), [], {})


class FileMappingDispatcher(object):
    def __init__(self, app, *args, **kwargs):
        self.app = app
        self.pages_module = '{}.pages'.format(self.app.app)

        app_logger.info('initialized %r', self)

    def find_handler(self, request):
        request_id = request.headers.get('X-Request-Id', FrontikApplication.next_request_id())
        logger = RequestLogger(request, request_id)

        url_parts = request.path.strip('/').split('/')

        if any('.' in part for part in url_parts):
            logger.info('url contains "." character, using 404 page')
            return None

        page_name = '.'.join(filter(None, url_parts))
        page_module_name = '.'.join(filter(None, (self.pages_module, page_name)))
        logger.debug('page module: %s', page_module_name)

        try:
            page_module = importlib.import_module(page_module_name)
            logger.debug('using %s from %s', page_module_name, page_module.__file__)
        except ImportError:
            logger.warning('%s module not found', (self.pages_module, page_module_name))
            return None
        except:
            logger.exception('error while importing %s module', page_module_name)
            return HandlerMatch(
                handler=ErrorHandler(self.app, request, logger=logger, status_code=500),
                args=[], kwargs={}
            )

        if not hasattr(page_module, 'Page'):
            logger.error('%s.Page class not found', page_module_name)
            return None

        return HandlerMatch(
            handler=page_module.Page(self.app, request, logger=logger), args=[], kwargs={}
        )

    def __repr__(self):
        return '{}.{}(<{}>)'.format(__package__, self.__class__.__name__, self.app.app)


class RegexpDispatcher(object):
    def __init__(self, app):
        self.app = app
        self.handlers = [(re.compile(pattern), handler) for pattern, handler in self.app.application_urls()]

        app_logger.info('initialized %r', self)

    def find_handler(self, request):
        request_id = request.headers.get('X-Request-Id', FrontikApplication.next_request_id())
        logger = RequestLogger(request, request_id)

        logger.info('requested url: %s', request.uri)

        for pattern, handler in self.handlers:
            match = pattern.match(request.uri)
            if match:
                logger.debug('using %r', handler)
                extend_request_arguments(request, match)

                return HandlerMatch(
                    handler=handler(self.app, request, logger=logger), args=[], kwargs={}
                )

    def __repr__(self):
        return '{}.{}(<{} routes>)'.format(__package__, self.__class__.__name__, len(self.handlers))


class FrontikApplication(tornado.web.Application):
    request_id = 0

    class DefaultConfig(object):
        pass

    def __init__(self, **settings):
        self.start_time = time.time()

        tornado_settings = settings.get('tornado_settings')
        if tornado_settings is None:
            tornado_settings = {}

        self.app_settings = settings
        self.config = self.application_config()
        self.app = settings.get('app')

        super(FrontikApplication, self).__init__([
            (r'/version/?', VersionHandler),
            (r'/status/?', StatusHandler),
        ], **tornado_settings)

        if self.application_urls():
            self.add_router(RegexpDispatcher(self))

        self.add_router(FileMappingDispatcher(self))

        if self.application_404_handler() is not None:
            self.add_router(NotFoundRouter(self))

        self.xml = frontik.producers.xml_producer.ApplicationXMLGlobals(self.config)
        self.json = frontik.producers.json_producer.ApplicationJsonGlobals(self.config)
        self.curl_http_client = tornado.curl_httpclient.CurlAsyncHTTPClient(max_clients=options.max_http_clients)
        self.loggers_initializers = frontik.loggers.bootstrap_app_loggers(self)

    def application_urls(self):
        return []

    def application_config(self):
        return FrontikApplication.DefaultConfig()

    def application_404_handler(self):
        return None

    def application_version_xml(self):
        version = etree.Element('version')
        version.text = 'unknown'
        return [version]

    def init_async(self):
        init_future = Future()
        init_future.set_result(None)
        return init_future

    @staticmethod
    def next_request_id():
        FrontikApplication.request_id += 1
        return str(FrontikApplication.request_id)

# Temporary for backward compatibility
App = FrontikApplication
