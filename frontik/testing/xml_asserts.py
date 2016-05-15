# coding=utf-8

from collections import defaultdict, deque
import sys

from lxml import etree
from lxml.doctestcompare import LXMLOutputChecker
from tornado.util import raise_exc_info

from frontik.compat import unicode_type

XML_checker = LXMLOutputChecker()


def _describe_element(elem):
    root = elem.getroottree()
    if not root:
        return '? [tag name: {}]'.format(elem.tag)
    else:
        return root.getpath(elem)


def _xml_text_compare(t1, t2):
    return (t1 or '').strip() == (t2 or '').strip()


def _assert_tag_and_attributes_are_equal(xml1, xml2, can_extend=False):
    if xml1.tag != xml2.tag:
        raise AssertionError(u'Tags do not match: {tag1} != {tag2}'.format(
            tag1=_describe_element(xml1), tag2=_describe_element(xml2)
        ))

    added_attributes = set(xml2.attrib).difference(xml1.attrib)
    missing_attributes = set(xml1.attrib).difference(xml2.attrib)

    if missing_attributes:
        raise AssertionError(u'Second xml misses attributes: {path}/({attributes})'.format(
            path=_describe_element(xml2), attributes=','.join(missing_attributes)
        ))

    if not can_extend and added_attributes:
        raise AssertionError(u'Second xml has additional attributes: {path}/({attributes})'.format(
            path=_describe_element(xml2), attributes=','.join(added_attributes)
        ))

    for attrib in xml1.attrib:
        if not XML_checker.text_compare(xml1.attrib[attrib], xml2.attrib[attrib], False):
            raise AssertionError(u"Attribute values are not equal: {path}/{attribute}['{v1}' != '{v2}']".format(
                path=_describe_element(xml1), attribute=attrib, v1=xml1.attrib[attrib], v2=xml2.attrib[attrib]
            ))

    if not XML_checker.text_compare(xml1.text, xml2.text, True):
        raise AssertionError(u"Tags text differs: {path}['{t1}' != '{t2}']".format(
            path=_describe_element(xml1), t1=xml1.text, t2=xml2.text
        ))

    if not XML_checker.text_compare(xml1.tail, xml2.tail, True):
        raise AssertionError(u"Tags tail differs: {path}['{t1}' != '{t2}']".format(
            path=_describe_element(xml1), t1=xml1.tail, t2=xml2.tail
        ))


def _assert_xml_docs_are_equal(xml1, xml2, check_tags_order=False):
    _assert_tag_and_attributes_are_equal(xml1, xml2)

    children1 = list(xml1)
    children2 = list(xml2)

    if len(children1) != len(children2):
        raise AssertionError(u'Children are not equal: {path}[{len1} children != {len2} children]'.format(
            path=_describe_element(xml1), len1=len(children1), len2=len(children2)
        ))

    if check_tags_order:
        for c1, c2 in zip(children1, children2):
            _assert_xml_docs_are_equal(c1, c2)

    else:
        children1 = set(children1)
        children2 = set(children2)

        for c1 in children1:
            c1_match = None

            for c2 in children2:
                try:
                    _assert_xml_docs_are_equal(c1, c2, check_tags_order)
                except AssertionError:
                    pass
                else:
                    c1_match = c2
                    break

            if c1_match is None:
                raise AssertionError(u'No equal child found in second xml: {path}'.format(path=_describe_element(c1)))

            children2.remove(c1_match)


def _find_max_matching(graph):
    left = list(graph)
    pair = defaultdict(lambda: None)
    dist = defaultdict(lambda: None)
    q = deque()

    full_graph = graph.copy()
    for v in left:
        for n in graph[v]:
            full_graph[n].add(v)

    def bfs():
        for v in left:
            if pair[v] is None:
                dist[v] = 0
                q.append(v)
            else:
                dist[v] = None

        dist[None] = None

        while q:
            v = q.popleft()
            if v is not None:
                for u in full_graph[v]:
                    if dist[pair[u]] is None:
                        dist[pair[u]] = dist[v] + 1
                        q.append(pair[u])

        return dist[None] is not None

    def dfs(v):
        if v is not None:
            for u in full_graph[v]:
                if dist[pair[u]] == dist[v] + 1 and dfs(pair[u]):
                    pair[u] = v
                    pair[v] = u
                    return True

            dist[v] = None
            return False

        return True

    matching = 0

    while bfs():
        for v in left:
            if pair[v] is None and dfs(v):
                matching += 1
                if matching == len(graph):
                    break

    return {v: pair[v] for v in pair if pair[v] is not None}


def _assert_xml_docs_are_compatible(xml1, xml2):
    _assert_tag_and_attributes_are_equal(xml1, xml2, can_extend=True)

    children1 = list(xml1)
    children2 = list(xml2)

    if not children1:
        return

    elif len(children2) < len(children1):
        raise AssertionError(u'Second xml {path} contains less children ({len2} < {len1})'.format(
            path=_describe_element(xml1), len1=len(children1), len2=len(children2)
        ))

    else:
        compatibility_bipartite_graph = defaultdict(set)

        for c1 in children1:
            for c2 in children2:
                try:
                    _assert_xml_docs_are_compatible(c1, c2)
                except AssertionError:
                    pass
                else:
                    compatibility_bipartite_graph[c1].add(c2)

            if not compatibility_bipartite_graph[c1]:
                raise AssertionError(
                    u'Second xml has no compatible child for {path}'.format(path=_describe_element(c1))
                )

        max_matching = _find_max_matching(compatibility_bipartite_graph)
        any_missing = next((c for c in children1 if c not in max_matching), None)

        if any_missing is not None:
            raise AssertionError(
                u'Second xml has no compatible child for {path}'.format(path=_describe_element(any_missing))
            )


class XmlTestCaseMixin(object):
    """Mixin for L{unittest.TestCase}."""

    def _assert_xml_compare(self, cmp_func, xml1, xml2, msg, **kwargs):
        if not isinstance(xml1, etree._Element):
            xml1 = etree.fromstring(xml1)

        if not isinstance(xml2, etree._Element):
            xml2 = etree.fromstring(xml2)

        try:
            cmp_func(xml1, xml2, **kwargs)
        except AssertionError as e:
            raise_exc_info((AssertionError, AssertionError(u'{} — {}'.format(msg, unicode_type(e))), sys.exc_info()[2]))

    def assertXmlEqual(self, first, second, check_tags_order=False, msg=None):
        """Assert that two xml documents are equal (the order of attributes is always ignored)."""
        if msg is None:
            msg = u'XML documents are not equal'

        self._assert_xml_compare(_assert_xml_docs_are_equal, first, second, msg, check_tags_order=check_tags_order)

    def assertXmlCompatible(self, first, second, msg=None):
        """Assert that second xml document is an extension of the first
        (must contain all tags and attributes from the first xml and any number of extra tags and attributes).
        """
        if msg is None:
            msg = u'XML documents are not compatible'

        self._assert_xml_compare(_assert_xml_docs_are_compatible, first, second, msg)
