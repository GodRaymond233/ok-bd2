from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

MF_REFERENCE_WIDTH = 1280
MF_REFERENCE_HEIGHT = 720
DAILY_SUBMAP_LIMIT = 21
SUBMAPS_PER_CARD = 3
MERCHANT_CARD_ID = "Q_sp6"
PINNED_CARD_IDS = frozenset({"Q_sp6", "Q_sp20"})


class ScreenState(str, Enum):
    HOME = "home"
    CARD_MENU = "card_menu"
    SANDBOX = "sandbox"
    AREA_MAP = "area_map"
    MERCHANT_DIALOG = "merchant_dialog"
    SHOP = "shop"
    COOKING = "cooking"
    LOADING = "loading"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CardSpec:
    card_id: str
    number: int
    name: str
    template: str
    shop_label: str
    collectable: bool = True


@dataclass(frozen=True)
class TemplateSpec:
    name: str
    file_name: str
    threshold: float = 0.76
    roi: tuple[int, int, int, int] | None = None
    green_mask: bool = False
    relative_roi: tuple[float, float, float, float] | None = None
    reference_scale: float | None = None
    scale_ratios: tuple[float, ...] = (1.0,)
    min_pixel_score: float | None = None
    candidate_center_roi: tuple[float, float, float, float] | None = None
    minimum_safe_threshold: float | None = None


@dataclass(frozen=True)
class MatchResult:
    score: float
    position: tuple[int, int]
    size: tuple[int, int]
    pixel_score: float = -1.0

    @property
    def center(self) -> tuple[int, int]:
        return (
            self.position[0] + self.size[0] // 2,
            self.position[1] + self.size[1] // 2,
        )


@dataclass(frozen=True)
class CalendarEntry:
    item: str
    shop: str
    aliases: tuple[str, ...] = ()
    sell: bool = True
    reserve: int = 0


@dataclass(frozen=True)
class NavigationResult:
    success: bool
    state: ScreenState
    message: str = ""


@dataclass(frozen=True)
class CollectionResult:
    success: bool
    depleted: bool = False
    completed_submaps: int = 0
    message: str = ""


STORY_CARD_NAMES = (
    "血骑士",
    "苍蓝魔女",
    "迷雾神射手",
    "眼镜与猫",
    "沙漠之花",
    "异教塔",
    "愤怒天使",
    "血之狂想曲",
    "铁假面",
    "霍尔蒙克斯",
    "虚假游戏",
    "黑羽毛",
    "雪之歌",
    "神圣审判",
    "复仇的誓言",
    "三国同盟",
    "试炼之路",
    "救赎",
    "被遗忘的战争",
    "剧情游戏卡20",
)

STORY_CARDS = tuple(
    CardSpec(
        card_id=f"Q_sp{number}",
        number=number,
        name=name,
        template=f"image/Cartridges/Q_sp{number}.png",
        shop_label=f"S{number}:{name}",
        collectable=number not in {6, 20},
    )
    for number, name in enumerate(STORY_CARD_NAMES, start=1)
)

COLLECTABLE_CARDS = tuple(card for card in STORY_CARDS if card.collectable)
CARD_BY_ID = {card.card_id: card for card in STORY_CARDS}


CHARACTER_SHOPS = {
    "R1": "R1:杰登之门",
    "R2": "R2:火晶片",
    "R3": "R3:美丽无望",
    "R4": "R4:大逃脱",
    "R5": "R5:鲁的迷宫",
    "R6": "R6:御剑传",
    "R7": "R7:合约之战",
}
EVENT_SHOPS = {
    "E1": "E1:夏日骑士",
    "E2": "E2:恶梦之冬",
    "E3": "E3:海滨天使",
    "E4": "E4:记忆边缘",
    "E5": "E5:戏水女王",
}

KNOWN_SHOPS = {
    **{f"S{card.number}": card.shop_label for card in STORY_CARDS},
    **CHARACTER_SHOPS,
    **EVENT_SHOPS,
}


DEFAULT_RECIPES = (
    "卢戈山参烤串",
    "煤炭饼干",
    "闪闪铁板虾",
    "透明沙拉",
    "地狱火紫菜包饭",
)

RECIPE_TEMPLATES = {
    "卢戈山参烤串": "image/Shop/cook_Lugas Ginseng Skewer.png",
    "煤炭饼干": "image/Shop/cook_Coal Cookies.png",
    "闪闪铁板虾": "image/Shop/cook_Sparkling Griddle Shrimp.png",
    "透明沙拉": "image/Shop/cook_Transparent Salad.png",
    "地狱火紫菜包饭": "image/Shop/cook_Hellfire Gimbap.png",
}

DEFAULT_SALE_WHITELIST = (
    "烤蜂蜜苹果",
    "蜂蜜黄油杏仁",
    "三角美乃滋饭团",
    "鱼子酱蛋包饭",
    "炸三文鱼便当",
    "桑格利亚酒",
    "巧克力鸡尾酒",
    "火烤鱼板棒",
    "卢戈山参烤串",
    "煤炭饼干",
    "闪闪铁板虾",
    "透明沙拉",
    "地狱火紫菜包饭",
    "米",
    "土豆",
    "泰瑞丝派",
    "黄油",
    "甜辣酱",
    "藏红花",
    "萝卜缨",
    "哈密瓜",
)
