# coding=utf-8

import frontik.jobs
import frontik.json_builder
import frontik.util

PRODUCER_NAME = 'json'


def bootstrap_producer(app):
    return None


class JsonMixin(object):
    def prepare(self):
        self.log.debug('222')
        self.json = frontik.json_builder.JsonBuilder(json_encoder=getattr(self, 'json_encoder', None), logger=self.log)
        super(JsonMixin, self).prepare()

    def produce_response(self, callback):
        self.log.debug('finishing without templating')

        if self._headers.get('Content-Type') is None:
            self.set_header('Content-Type', 'application/json; charset=utf-8')

        callback(self.json.to_string())
