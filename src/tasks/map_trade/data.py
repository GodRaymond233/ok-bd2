from __future__ import annotations

from dataclasses import dataclass

# User-calibrated favorite-button centers. The source listed each point label below
# its coordinates, so the entries are reordered here by point number. Keep these as
# ratios derived from the supplied 1920x1080 reference points so runtime clicks scale
# with the game client. The shop currently has 15 favorite-button positions.
SHOP_FAVORITE_POINTS: dict[int, tuple[float, float]] = {
    1: (580 / 1920, 140 / 1080),
    2: (913 / 1920, 141 / 1080),
    3: (1244 / 1920, 140 / 1080),
    4: (1576 / 1920, 140 / 1080),
    5: (581 / 1920, 250 / 1080),
    6: (912 / 1920, 251 / 1080),
    7: (1244 / 1920, 250 / 1080),
    8: (1575 / 1920, 250 / 1080),
    9: (580 / 1920, 359 / 1080),
    10: (912 / 1920, 362 / 1080),
    11: (1243 / 1920, 360 / 1080),
    12: (1576 / 1920, 360 / 1080),
    13: (580 / 1920, 469 / 1080),
    14: (913 / 1920, 470 / 1080),
    15: (1244 / 1920, 471 / 1080),
}

# Gray-star positions that must remain unfavorited for each cartridge. Keys use
# the established S=story, R=character, and E=event numbering; an empty set means
# every detected product star on that cartridge should be favorited. These values
# follow the latest supplied position calibration. Event cartridges retain their
# actual numbers (1, 2, 3, 5, 7).
SHOP_UNFAVORITED_POINTS: dict[str, frozenset[int]] = {
    "S1": frozenset({6}),
    "S2": frozenset({1}),
    "S3": frozenset({8, 9, 12, 13}),
    "S4": frozenset({3, 4, 11, 12, 13}),
    "S5": frozenset({2, 4, 8}),
    "S6": frozenset({8, 9}),
    "S7": frozenset({5, 9}),
    "S8": frozenset({3, 4, 9, 10, 11, 12}),
    "S9": frozenset({1, 2, 3, 4, 5, 6, 7, 8}),
    "S10": frozenset({2, 3, 4, 5, 9, 12}),
    "S11": frozenset({9}),
    "S12": frozenset({3, 4, 6, 11, 12, 13}),
    "S13": frozenset({7, 8, 9, 11, 12, 13}),
    "S14": frozenset({2, 3, 4, 5, 9, 11, 12}),
    "S15": frozenset({1, 8, 9}),
    "S16": frozenset({7, 9, 10}),
    "S17": frozenset({2, 8, 9, 10}),
    "S18": frozenset({2, 9}),
    "S19": frozenset({3, 8, 9}),
    "R1": frozenset(),
    "R2": frozenset({4}),
    "R3": frozenset({3, 10}),
    "R4": frozenset(),
    "R5": frozenset({3, 7, 8, 9, 11}),
    "R6": frozenset({3, 7, 8, 9, 11}),
    "R7": frozenset({4}),
    "E1": frozenset(),
    "E2": frozenset({4}),
    "E3": frozenset({3, 8, 10}),
    "E5": frozenset({4}),
    "E7": frozenset({5}),
}


@dataclass(frozen=True)
class ShopPurchaseReference:
    """Local cartridge and favorite-button calibration used by the buy flow."""

    shop_id: str
    label: str
    cartridge_templates: tuple[str, ...]
    unfavorited_points: tuple[tuple[int, tuple[float, float]], ...]

    @property
    def unfavorited_slots(self) -> frozenset[int]:
        return frozenset(slot for slot, _point in self.unfavorited_points)


@dataclass(frozen=True)
class ShopCartridgePage:
    """One shop cartridge viewport and the wheel steps needed to reach it."""

    page_number: int
    scroll_down_from_previous: int
    shop_ids: tuple[str, ...]
    confirmation_shop_ids: tuple[str, ...]


@dataclass(frozen=True)
class ShopCartridgeBrightnessCalibration:
    """Selected/unselected brightness calibration from the supplied S1 crops."""

    normal_template: str
    unselected_template: str
    foreground_min_gray: int
    normal_reference_ratio: float
    unselected_reference_ratio: float
    selected_brightness_threshold: float

    def is_selected(self, brightness_ratio: float) -> bool:
        return float(brightness_ratio) >= self.selected_brightness_threshold


# Shop labels follow the cartridge numbers shown in the shop UI. Event cartridges
# retain the non-contiguous numbers 1, 2, 3, 5, and 7 from the supplied screenshots.
SHOP_CARTRIDGE_LABELS: dict[str, str] = {
    "S1": "剧情游戏卡1 血骑士",
    "S2": "剧情游戏卡2 苍蓝魔女",
    "S3": "剧情游戏卡3 迷雾神射手",
    "S4": "剧情游戏卡4 眼镜与猫",
    "S5": "剧情游戏卡5 沙漠之花",
    "S6": "剧情游戏卡6 异教塔",
    "S7": "剧情游戏卡7 愤怒天使",
    "S8": "剧情游戏卡8 血之狂想曲",
    "S9": "剧情游戏卡9 铁假面",
    "S10": "剧情游戏卡10 霍尔蒙克斯",
    "S11": "剧情游戏卡11 虚假游戏",
    "S12": "剧情游戏卡12 黑羽毛",
    "S13": "剧情游戏卡13 雪之歌",
    "S14": "剧情游戏卡14 神圣审判",
    "S15": "剧情游戏卡15 复仇的誓言",
    "S16": "剧情游戏卡16 三国同盟",
    "S17": "剧情游戏卡17 试炼之路",
    "S18": "剧情游戏卡18 救赎",
    "S19": "剧情游戏卡19 被遗忘的战争",
    "R1": "角色游戏卡1 杰登之门",
    "R2": "角色游戏卡2 火晶片",
    "R3": "角色游戏卡3 美丽无望",
    "R4": "角色游戏卡4 大逃脱",
    "R5": "角色游戏卡5 鲁的迷宫",
    "R6": "角色游戏卡6 御剑传",
    "R7": "角色游戏卡7 合约之战",
    "E1": "活动游戏卡1 夏日骑士",
    "E2": "活动游戏卡2 恶梦之冬",
    "E3": "活动游戏卡3 海滨天使",
    "E5": "活动游戏卡5 记忆边缘",
    "E7": "活动游戏卡7 戏水女王",
}


def _shop_cartridge_templates(shop_id: str) -> tuple[str, ...]:
    category = {"S": "story", "R": "character", "E": "event"}[shop_id[0]]
    number = int(shop_id[1:])
    normal = f"shop/cartridges/{category}_cartridge_{number:02d}.png"
    if shop_id == "S1":
        return normal, "shop/cartridges/story_cartridge_01_dimmed.png"
    return (normal,)


# This is the self-contained local purchase reference. Each cartridge points to
# its local tab template and to the exact gray-star slots/relative coordinates that
# must remain unfavorited. It deliberately does not depend on OCR product names or
# the online highest-price calendar.
SHOP_PURCHASE_REFERENCES: dict[str, ShopPurchaseReference] = {
    shop_id: ShopPurchaseReference(
        shop_id=shop_id,
        label=SHOP_CARTRIDGE_LABELS[shop_id],
        cartridge_templates=_shop_cartridge_templates(shop_id),
        unfavorited_points=tuple(
            (slot, SHOP_FAVORITE_POINTS[slot])
            for slot in sorted(SHOP_UNFAVORITED_POINTS[shop_id])
        ),
    )
    for shop_id in SHOP_CARTRIDGE_LABELS
}


# The normal and dimmed S1 crops align at the same scale with ~0.994 structural
# correlation. On foreground pixels whose normal gray value is at least 50, the
# unselected crop measures about 0.50 of the normal brightness. A midpoint threshold
# of 0.75 keeps the two supplied states well separated.
SHOP_CARTRIDGE_BRIGHTNESS = ShopCartridgeBrightnessCalibration(
    normal_template="shop/cartridges/story_cartridge_01.png",
    unselected_template="shop/cartridges/story_cartridge_01_dimmed.png",
    foreground_min_gray=50,
    normal_reference_ratio=1.0,
    unselected_reference_ratio=0.50,
    selected_brightness_threshold=0.75,
)


# The shop cartridge list starts at page 1. Repeated one-notch downward wheel
# events reach the following viewports in order: 9 steps, 10 steps, then 1 step.
# These four viewports cover every cartridge in the local purchase reference.
SHOP_CARTRIDGE_PAGES: tuple[ShopCartridgePage, ...] = (
    ShopCartridgePage(
        page_number=1,
        scroll_down_from_previous=0,
        shop_ids=tuple(f"S{number}" for number in range(1, 11)),
        confirmation_shop_ids=("S1",),
    ),
    ShopCartridgePage(
        page_number=2,
        scroll_down_from_previous=9,
        shop_ids=(*tuple(f"S{number}" for number in range(11, 20)), "R1"),
        confirmation_shop_ids=("R1", "S11"),
    ),
    ShopCartridgePage(
        page_number=3,
        scroll_down_from_previous=10,
        shop_ids=(
            *tuple(f"R{number}" for number in range(2, 8)),
            "E1",
            "E2",
            "E3",
            "E5",
        ),
        confirmation_shop_ids=("E5", "R2"),
    ),
    ShopCartridgePage(
        page_number=4,
        scroll_down_from_previous=1,
        shop_ids=("E7",),
        confirmation_shop_ids=("E7",),
    ),
)


def shop_purchase_reference(shop: str) -> ShopPurchaseReference:
    """Return a local reference for either ``S1`` or a label such as ``S1:血骑士``."""

    shop_id = str(shop).strip().split(":", 1)[0].upper()
    try:
        return SHOP_PURCHASE_REFERENCES[shop_id]
    except KeyError as exc:
        raise KeyError(f"未知商品卡带：{shop}") from exc

ITEM_ALIASES: dict[str, tuple[str, ...]] = {
    "蜂蜜黄油杏仁": ("蜂蜜奶油杏仁", "Honey Butter Almond"),
    "炸三文鱼便当": ("炸鲑鱼便当", "Salmon Cutlet Lunchbox"),
    "卢戈山参烤串": ("卢戈烤山参串", "Lugo Ginseng Grilled Skewer"),
    "闪闪铁板虾": ("闪耀西班牙蒜味虾", "Shiny Gambas", "Sparkling Griddle Shrimp"),
    "透明沙拉": ("透明化沙拉", "Transparent Salad"),
    "地狱火紫菜包饭": ("地狱火海苔饭卷", "Fire of Hell Kimbab"),
    "巧克力鸡尾酒": ("Chocolat Cocktail",),
    "鱼子酱蛋包饭": ("Caviar Omelette",),
}
