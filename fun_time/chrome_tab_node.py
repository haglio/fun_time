from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PAGE_WINDOW_CLASS_PART = "RenderWidgetHost"


def _kind_of(uia: Any, element: Any) -> tuple[bool, bool, bool]:
    control_type = element.CachedControlType
    is_tab = control_type == uia.UIA_TabItemControlTypeId
    is_page = (control_type == uia.UIA_DocumentControlTypeId
               or PAGE_WINDOW_CLASS_PART in (element.CachedClassName or ""))
    is_active = is_tab and element.GetCurrentPropertyValue(
        uia.UIA_SelectionItemIsSelectedPropertyId) is True
    return is_tab, is_page, is_active


def _children_of(automation: Any, element: Any) -> list[UiaNode]:
    walker, reads = automation.walker, automation.read_with_each_node
    found = []
    child = walker.GetFirstChildElementBuildCache(element, reads)
    while child:
        found.append(UiaNode.of(automation, child))
        child = walker.GetNextSiblingElementBuildCache(child, reads)
    return found


def _press_without_the_activation_select_brings(automation: Any, element: Any) -> None:
    uia = automation.uia
    element.GetCurrentPattern(uia.UIA_LegacyIAccessiblePatternId).QueryInterface(
        uia.IUIAutomationLegacyIAccessiblePattern).DoDefaultAction()


@dataclass(frozen=True)
class UiaNode:
    is_tab: bool
    is_page: bool
    is_active: bool
    automation: Any
    element: Any

    @classmethod
    def of(cls, automation: Any, element: Any) -> UiaNode:
        return cls(*_kind_of(automation.uia, element), automation, element)

    def children(self) -> list[UiaNode]:
        return _children_of(self.automation, self.element)

    def switch_to(self) -> None:
        _press_without_the_activation_select_brings(self.automation, self.element)

    def close(self) -> None:
        buttons = [child.element for child in self.children()
                   if child.element.CachedControlType == self.automation.uia.UIA_ButtonControlTypeId]
        if buttons:
            the_close_button_comes_after_the_mute_button = buttons[-1]
            _press_without_the_activation_select_brings(
                self.automation, the_close_button_comes_after_the_mute_button)
