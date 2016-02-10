# coding=utf-8

from frontik.producers import jinja_producer, json_producer, xml_producer, xslt_producer

PRODUCERS = (jinja_producer, json_producer, xml_producer, xslt_producer)


def bootstrap_app_producers(app):
    return {producer.PRODUCER_NAME: producer.bootstrap_producer(app) for producer in PRODUCERS}
