"""Sidebar section labels (运行 / 诊断) for the main navigation (mockup V2).

Uses qfluentwidgets' built-in ``NavigationItemHeader`` so the labels collapse
automatically when the navigation panel is in compact mode.  Installed once
from ``Globals.on_show_main_window`` when all tabs already exist.
"""

from __future__ import annotations

from ok import Logger
from qfluentwidgets import NavigationItemPosition

logger = Logger.get_logger(__name__)

_RUN_LABEL = "运 行"
_DIAGNOSIS_LABEL = "诊 断"

# Custom/group tabs that belong to the 诊断 section, in priority order: the
# first one present in the navigation becomes the section anchor.
_DIAGNOSIS_TAB_CLASSES = ("BD2StatusTab", "AutoLoginStatusTab")
_DIAGNOSIS_GROUP_NAMES = ("测试",)


def _scroll_index(panel, route_key: str) -> int | None:
    item = panel.items.get(route_key)
    if item is None:
        return None
    index = panel.scrollLayout.indexOf(item.widget)
    return index if index >= 0 else None


def _tab_route_keys(main_window, class_names: tuple[str, ...]) -> list[str]:
    """Route keys of stacked-widget tabs whose class name matches."""
    keys = []
    stacked = main_window.stackedWidget
    for index in range(stacked.count()):
        widget = stacked.widget(index)
        if type(widget).__name__ in class_names:
            keys.append(widget.objectName())
    return keys


def _diagnosis_anchor(main_window, panel) -> int | None:
    """Layout index of the first tab belonging to the 诊断 section."""
    candidates: list[int] = []
    for route_key in _tab_route_keys(main_window, _DIAGNOSIS_TAB_CLASSES):
        index = _scroll_index(panel, route_key)
        if index is not None:
            candidates.append(index)
    stacked = main_window.stackedWidget
    for index in range(stacked.count()):
        widget = stacked.widget(index)
        if getattr(widget, "group_name", None) in _DIAGNOSIS_GROUP_NAMES:
            layout_index = _scroll_index(panel, widget.objectName())
            if layout_index is not None:
                candidates.append(layout_index)
    return min(candidates) if candidates else None


def install_nav_sections(main_window) -> bool:
    """Insert 运行/诊断 group headers into the navigation panel."""
    panel = main_window.navigationInterface.panel
    if getattr(panel, "_bd2_nav_sections_installed", False):
        return False

    # 运行: above the first SCROLL item (the Capture/截图方式 tab).
    run_anchor = _scroll_index(panel, main_window.start_tab.objectName())
    if run_anchor is None:
        logger.warning("nav sections: start tab route not found, skip")
        return False
    panel.insertItemHeader(run_anchor, _RUN_LABEL, position=NavigationItemPosition.SCROLL)

    # 诊断: above the earliest of (测试 group tab, BD2 tab, 任务状态确认 tab).
    diagnosis_anchor = _diagnosis_anchor(main_window, panel)
    if diagnosis_anchor is not None:
        panel.insertItemHeader(
            diagnosis_anchor, _DIAGNOSIS_LABEL, position=NavigationItemPosition.SCROLL
        )
    else:
        logger.info("nav sections: no diagnosis tab present, skip 诊断 label")

    panel._bd2_nav_sections_installed = True
    return True
