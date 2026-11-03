from __future__ import annotations

from fun_time.chrome_tab_node import UiaNode


class _UIA:
    UIA_TabItemControlTypeId = 50019
    UIA_DocumentControlTypeId = 50030
    UIA_PaneControlTypeId = 50033
    UIA_SelectionItemIsSelectedPropertyId = 30079
    UIA_LegacyIAccessiblePatternId = 10018
    IUIAutomationLegacyIAccessiblePattern = "the legacy pattern"


_READ_WITH_EACH_NODE = "what a walk reads with each node"


class _FakeElement:
    def __init__(self, control_type, *, class_name=None, selected=False, children=()):
        self.CachedControlType = control_type
        self.CachedClassName = class_name
        self.selected = selected
        self.kids = list(children)
        self.default_actions = 0

    def GetCurrentPropertyValue(self, property_id):
        return self.selected if property_id == _UIA.UIA_SelectionItemIsSelectedPropertyId else None

    def GetCurrentPattern(self, pattern_id):
        assert pattern_id == _UIA.UIA_LegacyIAccessiblePatternId
        return self

    def QueryInterface(self, interface):
        assert interface == _UIA.IUIAutomationLegacyIAccessiblePattern
        return self

    def DoDefaultAction(self):
        self.default_actions += 1


class _FakeWalker:
    def __init__(self, *roots):
        self._next = {}
        for parent in roots:
            for one, after in zip(parent.kids, parent.kids[1:], strict=False):
                self._next[id(one)] = after

    def GetFirstChildElementBuildCache(self, element, reads):
        assert reads == _READ_WITH_EACH_NODE
        return element.kids[0] if element.kids else None

    def GetNextSiblingElementBuildCache(self, element, reads):
        assert reads == _READ_WITH_EACH_NODE
        return self._next.get(id(element))


class _FakeAutomation:
    uia = _UIA
    read_with_each_node = _READ_WITH_EACH_NODE

    def __init__(self, *roots):
        self.walker = _FakeWalker(*roots)


def _node(element, *roots):
    return UiaNode.of(_FakeAutomation(*roots), element)


def test_a_selected_tab_is_the_active_tab():
    node = _node(_FakeElement(_UIA.UIA_TabItemControlTypeId, selected=True))

    assert (node.is_tab, node.is_active, node.is_page) == (True, True, False)


def test_a_tab_not_selected_is_a_tab_but_not_the_active_one():
    node = _node(_FakeElement(_UIA.UIA_TabItemControlTypeId))

    assert (node.is_tab, node.is_active) == (True, False)


def test_only_a_tab_can_be_the_active_one():
    node = _node(_FakeElement(_UIA.UIA_PaneControlTypeId, selected=True))

    assert (node.is_tab, node.is_active) == (False, False)


def test_a_document_is_a_page():
    assert _node(_FakeElement(_UIA.UIA_DocumentControlTypeId)).is_page is True


def test_the_window_chrome_draws_a_page_into_is_a_page_whatever_its_control_type():
    element = _FakeElement(_UIA.UIA_PaneControlTypeId, class_name="Chrome_RenderWidgetHostHWND")

    assert _node(element).is_page is True


def test_a_pane_with_no_class_name_is_no_page():
    assert _node(_FakeElement(_UIA.UIA_PaneControlTypeId)).is_page is False


def test_a_nodes_children_come_in_the_order_the_tab_strip_holds_them():
    first = _FakeElement(_UIA.UIA_TabItemControlTypeId, selected=True)
    second = _FakeElement(_UIA.UIA_TabItemControlTypeId)
    strip = _FakeElement(_UIA.UIA_PaneControlTypeId, children=[first, second])

    assert [child.is_active for child in _node(strip, strip).children()] == [True, False]


def test_a_node_with_no_children_has_none():
    assert _node(_FakeElement(_UIA.UIA_PaneControlTypeId)).children() == []


def test_switching_to_a_tab_presses_it_once():
    tab = _FakeElement(_UIA.UIA_TabItemControlTypeId)

    _node(tab).switch_to()

    assert tab.default_actions == 1
