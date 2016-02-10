# coding=utf-8

import frontik.doc
import frontik.file_cache
import frontik.jobs
import frontik.util
from frontik.xml_util import xml_from_file

PRODUCER_NAME = 'xml'


def bootstrap_producer(app):
    xml_cache = frontik.file_cache.make_file_cache(
        'XML', 'XML_root',
        getattr(app.config, 'XML_root', None),
        xml_from_file,
        getattr(app.config, 'XML_cache_limit', None),
        getattr(app.config, 'XML_cache_step', None),
        deepcopy=True
    )

    def producer_initializer(handler):
        return xml_cache

    return producer_initializer


class XmlMixin(object):
    def prepare(self):
        super(XmlMixin, self).prepare()

        self._xml_cache = self.application.initialize_producer(PRODUCER_NAME, self)

        self.doc = frontik.doc.Doc(logger=self.log)

    def produce_response(self, callback):
        self.log.debug('finishing without XSLT')

        if self._headers.get('Content-Type') is None:
            self.set_header('Content-Type', 'application/xml; charset=utf-8')

        callback(self.doc.to_string())

    def xml_from_file(self, filename):
        return self._xml_cache.load(filename, self.log)
