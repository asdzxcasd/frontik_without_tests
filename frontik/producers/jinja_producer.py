import time
import weakref

import jinja2
from tornado.concurrent import Future
import tornado.options

import frontik.jobs
from frontik.producers import json_producer
import frontik.util

PRODUCER_NAME = 'jinja'


def bootstrap_producer(app):
    cache_size = getattr(app.config, 'template_cache_limit', 50)
    template_root = getattr(app.config, 'template_root', None)

    if template_root:
        jinja_environment = jinja2.Environment(
            cache_size=cache_size,
            auto_reload=tornado.options.options.autoreload,
            loader=jinja2.FileSystemLoader(template_root)
        )
    else:
        jinja_environment = None

    def producer_initializer(handler):
        return JinjaProducer(handler, jinja_environment)

    return producer_initializer


class JinjaMixin(json_producer.JsonMixin):
    def prepare(self):
        self._jinja_producer = self.application.initialize_producer(PRODUCER_NAME, self)
        super(JinjaMixin, self).prepare()

    def set_template(self, filename):
        return self._jinja_producer.set_template(filename)

    def produce_response(self, callback):
        if frontik.util.get_cookie_or_url_param_value(self, 'notpl') is not None:
            self.require_debug_access()
            self.log.debug('ignoring templating because notpl parameter is passed')
            return super(JinjaMixin, self).produce_response(callback)

        if self._jinja_producer.template_filename is None:
            return super(JinjaMixin, self).produce_response(callback)

        return self._jinja_producer.finish_with_template(self.json, callback)

    produce_jinja_json_response = produce_response


class JinjaProducer(object):
    def __init__(self, handler, environment):
        self.handler = weakref.proxy(handler)
        self.log = weakref.proxy(self.handler.log)

        self.template_filename = None
        self.executor = frontik.jobs.get_executor(tornado.options.options.json_executor)
        self.environment = environment

    def set_template(self, filename):
        self.template_filename = filename

    def finish_with_template(self, json, callback):
        if not self.environment:
            raise Exception('Cannot apply template, option "template_root" is not set in application config')

        if self.handler._headers.get('Content-Type') is None:
            self.handler.set_header('Content-Type', 'text/html; charset=utf-8')

        def job():
            start_time = time.time()
            template = self.environment.get_template(self.template_filename)
            result = template.render(json.to_dict())
            return start_time, result

        future = Future()

        def success_cb(result):
            start_time, result = result

            self.log.stage_tag('tpl')
            self.log.info('applied template %s in %.2fms', self.template_filename, (time.time() - start_time) * 1000)

            callback(result)

        def exception_cb(exception):
            self.log.error('failed applying template %s', self.template_filename)
            raise exception

        self.executor.add_job(job, self.handler.check_finished(success_cb), self.handler.check_finished(exception_cb))

        return future
