"""每周跑图的卡带范围：配置名称、自选卡带与卡带屏蔽。

配置名称：剧情卡 ``Story_01_血骑士``，角色卡 ``Character_1_杰登之门``，活动卡按登记
顺序编号为 ``Event_1_夏日骑士``。屏蔽项可写完整名称或“类别_编号”短前缀。
"""

from __future__ import annotations

import re

from src.tasks.map_trade.models import COLLECTABLE_CARDS, EVENT_CARDS, CardSpec

# “Story_3”式短前缀（类别_编号，可带前导零）。
_SHORT_CARD_PREFIX = re.compile(r"^(Story|Character|Event)_(\d+)$")


def _catalog() -> tuple[tuple[str, CardSpec], ...]:
    entries = [
        (
            f"Story_{card.number:02d}_{card.name}"
            if card.category == "story"
            else f"Character_{card.number}_{card.name}",
            card,
        )
        for card in COLLECTABLE_CARDS
    ]
    entries.extend(
        (f"Event_{index}_{card.name}", card)
        for index, card in enumerate(EVENT_CARDS, start=1)
    )
    return tuple(entries)


# 采集顺序：剧情 → 角色 → 活动。
COLLECTION_CARD_CATALOG = _catalog()
COLLECTION_CARD_NAMES = tuple(name for name, _card in COLLECTION_CARD_CATALOG)


def parse_card_blacklist(raw: object) -> frozenset[str]:
    """卡带屏蔽输入：逗号/分号分隔的卡带名或“Story_3”式短前缀。

    短前缀“Story_3”会归一为对应卡带名前缀“Story_03”参与匹配；不做任意前缀
    匹配，避免“Story_1”误吞 Story_10–Story_19。
    """

    pieces = {
        piece.strip()
        for piece in str(raw or "").replace("，", ",").replace(";", ",").split(",")
        if piece.strip()
    }
    names: set[str] = set()
    for piece in pieces:
        names.add(piece)
        match = _SHORT_CARD_PREFIX.match(piece)
        if match and match.group(1) == "Story":
            names.add(f"Story_{int(match.group(2)):02d}")
    return frozenset(names)


def is_card_blacklisted(card_name: str, blacklist: frozenset[str] | set[str]) -> bool:
    """完整名称或“类别_编号”前缀命中即屏蔽；前缀后必须紧跟下划线。"""

    if card_name in blacklist:
        return True
    return any(entry and card_name.startswith(f"{entry}_") for entry in blacklist)


def resolve_collection_cards(config) -> tuple[tuple[CardSpec, ...], str]:
    """按自选与屏蔽配置给出本次采集的卡带；配置无效时返回空范围和原因。"""

    blacklist = parse_card_blacklist(config.get("采集卡带屏蔽", ""))
    custom = bool(config.get("自定义采集关卡", False))
    selected = COLLECTION_CARD_NAMES
    if custom:
        chosen = config.get("采集关卡", list(COLLECTION_CARD_NAMES))
        if (
            not isinstance(chosen, list)
            or not chosen
            or any(
                not isinstance(name, str) or name not in COLLECTION_CARD_NAMES
                for name in chosen
            )
        ):
            return (), "请选择至少一张有效的采集卡带"
        selected = tuple(name for name in COLLECTION_CARD_NAMES if name in chosen)
    cards = tuple(
        card
        for name, card in COLLECTION_CARD_CATALOG
        if name in selected and not is_card_blacklisted(name, blacklist)
    )
    if not cards:
        return (), (
            "所选采集关卡均已被屏蔽，请检查卡带屏蔽配置"
            if custom
            else "全部卡带均已被屏蔽，请检查卡带屏蔽配置"
        )
    return cards, ""
