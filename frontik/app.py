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


class FrontikDispatcher(object):
    def __call__(self, request, logger):
        raise NotImplementedError()


class FileMappingDispatcher(FrontikDispatcher):
    def __init__(self, module):
        self.name = module.__name__
        app_logger.info('initialized %r', self)

    def __call__(self, request, logger):
        url_parts = request.path.strip('/').split('/')

        if any('.' in part for part in url_parts):
            logger.info('url contains "." character, using 404 page')
            return None

        page_name = '.'.join(filter(None, url_parts))
        page_module_name = '.'.join(filter(None, (self.name, page_name)))
        logger.debug('page module: %s', page_module_name)

        try:
            page_module = importlib.import_module(page_module_name)
            logger.debug('using %s from %s', page_module_name, page_module.__file__)
        except ImportError:
            logger.warning('%s module not found', (self.name, page_module_name))
            return None
        except:
            logger.exception('error while importing %s module', page_module_name)
            return ErrorHandler, {'status_code': 500}

        if not hasattr(page_module, 'Page'):
            logger.error('%s.Page class not found', page_module_name)
            return None

        return page_module.Page, {}

    def __repr__(self):
        return '{}.{}(<{}>)'.format(__package__, self.__class__.__name__, self.name)


class RegexpDispatcher(FrontikDispatcher):
    def __init__(self, application):
        self.handlers = [(re.compile(pattern), handler) for pattern, handler in application.application_urls()]
        self.fallback_dispatcher = application.application_request_dispatcher()
        self.handler_404 = application.application_404_handler()
        app_logger.info('initialized %r', self)

    def __call__(self, request, logger):
        logger.info('requested url: %s', request.uri)

        for pattern, handler in self.handlers:
            match = pattern.match(request.uri)
            if match:
                logger.debug('using %r', handler)
                extend_request_arguments(request, match)
                return handler, {}

        if self.fallback_dispatcher is not None:
            route = self.fallback_dispatcher(request, logger)
            if route is not None:
                return route

        logger.error('match for request url "%s" not found', request.uri)
        return self.handler_404


class _FrontikRequestDispatcher(tornado.web._RequestDispatcher):
    def _find_handler(self):
        super(_FrontikRequestDispatcher, self)._find_handler()

        if not isinstance(self.handler_class, FrontikDispatcher):
            return

        request_id = self.request.headers.get('X-Request-Id', FrontikApplication.next_request_id())
        logger = RequestLogger(self.request, request_id)

        self.handler_class, self.handler_kwargs = self.handler_class(self.request, logger)
        self.handler_kwargs['logger'] = logger


class FrontikApplication(tornado.web.Application):
    request_id = 0

    class DefaultConfig(object):
        pass

    def __init__(self, **settings):
        tornado_settings = settings.get('tornado_settings')

        if tornado_settings is None:
            tornado_settings = {}

        self.start_time = time.time()
        self.app_settings = settings
        self.app = settings.get('app')
        self.config = self.application_config()

        super(FrontikApplication, self).__init__([
            (r'/version/?', VersionHandler),
            (r'/status/?', StatusHandler),
            (r'.*', RegexpDispatcher(self)),
        ], **tornado_settings)

        self.xml = frontik.producers.xml_producer.ApplicationXMLGlobals(self.config)
        self.json = frontik.producers.json_producer.ApplicationJsonGlobals(self.config)
        self.curl_http_client = tornado.curl_httpclient.CurlAsyncHTTPClient(max_clients=options.max_http_clients)
        self.loggers_initializers = frontik.loggers.bootstrap_app_loggers(self)

    def application_urls(self):
        return []

    def application_request_dispatcher(self):
        return FileMappingDispatcher(importlib.import_module('{}.pages'.format(self.app)))

    def application_404_handler(self):
        return ErrorHandler, {'status_code': 404}

    def application_config(self):
        return FrontikApplication.DefaultConfig()

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

    def start_request(self, server_conn, request_conn):
        return _FrontikRequestDispatcher(self, request_conn)


# Temporary for backward compatibility
App = FrontikApplication
