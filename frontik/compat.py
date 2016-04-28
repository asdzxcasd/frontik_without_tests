# coding=utf-8

import sys

PY3 = sys.version_info >= (3,)

if PY3:
    basestring_type = str
    long_type = int
    unicode_type = str

    def iteritems(d, **kw):
        return d.items(**kw)

else:
    basestring_type = basestring
    long_type = long
    unicode_type = unicode

    def iteritems(d, **kw):
        return d.iteritems(**kw)
