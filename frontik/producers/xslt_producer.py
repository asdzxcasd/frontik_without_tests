import copy
import time
import weakref

from lxml import etree
from tornado.concurrent import Future
import tornado.options

import frontik.file_cache
import frontik.jobs
from frontik.producers import xml_producer
import frontik.util
from frontik.xml_util import xsl_from_file

PRODUCER_NAME = 'xslt'


def bootstrap_producer(app):
    xsl_cache = frontik.file_cache.make_file_cache(
        'XSL', 'XSL_root',
        getattr(app.config, 'XSL_root', None),
        xsl_from_file,
        getattr(app.config, 'XSL_cache_limit', None),
        getattr(app.config, 'XSL_cache_step', None)
    )

    def producer_initializer(handler):
        return XsltProducer(handler, xsl_cache)

    return producer_initializer


class XsltMixin(xml_producer.XmlMixin):
    def prepare(self):
        super(XsltMixin, self).prepare()

        self._xslt_producer = self.application.initialize_producer(PRODUCER_NAME, self)

    def set_xsl(self, filename):
        self._xslt_producer.transform_filename = filename

    def produce_response(self, callback):
        if any(frontik.util.get_cookie_or_url_param_value(self, p) is not None for p in ('noxsl', 'notpl')):
            self.require_debug_access()
            self.log.debug('ignoring XSLT because noxsl/notpl parameter is passed')
            return super(XsltMixin, self).produce_response(callback)

        if not self._xslt_producer.transform_filename:
            return super(XsltMixin, self).produce_response(callback)

        return self._xslt_producer.finish_with_xslt(self.doc.to_etree_element(), callback)

    produce_xslt_xml_response = produce_response


class XsltProducer(object):
    def __init__(self, handler, xsl_cache):
        self.handler = weakref.proxy(handler)
        self.log = weakref.proxy(self.handler.log)

        self.executor = frontik.jobs.get_executor(tornado.options.options.xsl_executor)
        self.xsl_cache = xsl_cache
        self.transform_filename = None

    def finish_with_xslt(self, xml, callback):
        self.log.debug('finishing with XSLT')

        try:
            transform = self.xsl_cache.load(self.transform_filename, self.log)
        except etree.XMLSyntaxError:
            self.log.error('failed parsing XSL file %s (XML syntax)', self.transform_filename)
            raise
        except etree.XSLTParseError:
            self.log.error('failed parsing XSL file %s (XSL parse error)', self.transform_filename)
            raise
        except:
            self.log.error('failed loading XSL file %s', self.transform_filename)
            raise

        if self.handler._headers.get('Content-Type') is None:
            self.handler.set_header('Content-Type', 'text/html; charset=utf-8')

        future = Future()

        def job():
            start_time = time.time()

            result = transform(copy.deepcopy(xml), profile_run=self.handler.debug.debug_mode.profile_xslt)

            return start_time, str(result), result.xslt_profile

        def success_cb(result):
            start_time, xml_result, xslt_profile = result

            self.log.info('applied XSL %s in %.2fms', self.transform_filename, (time.time() - start_time) * 1000)

            if xslt_profile is not None:
                self.log.debug('XSLT profiling results', extra={'_xslt_profile': xslt_profile.getroot()})

            if len(transform.error_log):
                self.log.warning(get_xsl_log())

            self.log.stage_tag('xsl')
            callback(xml_result)

        def exception_cb(exception):
            self.log.error('failed transformation with XSL %s', self.transform_filename)
            self.log.error(get_xsl_log())
            raise exception

        def get_xsl_log():
            xsl_line = 'XSLT {0.level_name} in file "{0.filename}", line {0.line}, column {0.column}\n\t{0.message}'
            return '\n'.join(map(xsl_line.format, transform.error_log))

        self.executor.add_job(job, self.handler.check_finished(success_cb), self.handler.check_finished(exception_cb))

        return future
