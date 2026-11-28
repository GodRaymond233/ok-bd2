from __future__ import annotations

from dataclasses import dataclass

from src.utils.calibration import FHD_1080

# User-calibrated favorite-button centers. The source listed each point label below
# its coordinates, so the entries are reordered here by point number. Keep these as
# ratios derived from the supplied 1920x1080 reference points so runtime clicks scale
# with the game client. The shop currently has 15 favorite-button positions.
SHOP_FAVORITE_POINTS: dict[int, tuple[float, float]] = {
    1: (580 / FHD_1080.width, 140 / FHD_1080.height),
    2: (913 / FHD_1080.width, 141 / FHD_1080.height),
    3: (1244 / FHD_1080.width, 140 / FHD_1080.height),
    4: (1576 / FHD_1080.width, 140 / FHD_1080.height),
    5: (581 / FHD_1080.width, 250 / FHD_1080.height),
    6: (912 / FHD_1080.width, 251 / FHD_1080.height),
    7: (1244 / FHD_1080.width, 250 / FHD_1080.height),
    8: (1575 / FHD_1080.width, 250 / FHD_1080.height),
    9: (580 / FHD_1080.width, 359 / FHD_1080.height),
    10: (912 / FHD_1080.width, 362 / FHD_1080.height),
    11: (1243 / FHD_1080.width, 360 / FHD_1080.height),
    12: (1576 / FHD_1080.width, 360 / FHD_1080.height),
    13: (580 / FHD_1080.width, 469 / FHD_1080.height),
    14: (913 / FHD_1080.width, 470 / FHD_1080.height),
    15: (1244 / FHD_1080.width, 471 / FHD_1080.height),
}

# Four-column shop grid, numbered left to right and top to bottom.
SHOP_FAVORITE_SLOTS: dict[str, tuple[int, ...]] = {
    "S1": (1, 2, 3, 4, 5, 7),
    "S2": (2, 3, 4, 5, 6, 7, 8),
    "S3": (1, 2, 4, 5, 6, 7, 10, 11),
    "S4": (1, 2, 5, 6, 7, 8, 9, 10),
    "S5": (1, 3, 5, 6, 7),
    "S6": (1, 2, 3, 4, 5, 6, 7),
    "S7": (1, 2, 3, 4, 6, 7, 8),
    "S8": (1, 2, 5, 6, 7, 8, 13),
    "S9": (),
    "S10": (1, 6, 7, 8, 10, 11),
    "S11": (1, 2, 3, 4, 5, 6, 7, 8, 10),
    "S12": (1, 2, 5, 7, 8, 9, 10, 14, 15),
    "S13": (1, 2, 4, 5, 6, 10),
    "S14": (1, 6, 7, 10),
    "S15": (2, 3, 4, 5, 6, 7),
    "S16": (2, 3, 4, 5, 6, 8),
    "S17": (1, 3, 4, 5, 6, 7),
    "S18": (1, 3, 4, 5, 6, 7, 8),
    "S19": (1, 2, 4, 5, 6, 7),
    "R1": (1, 3, 5, 6, 7),
    "R2": (1, 2, 3, 6, 7, 8),
    "R3": (2, 4, 5, 6, 7, 8, 9),
    "R4": (1, 2, 3, 4, 5, 6, 7, 8),
    "R5": (1, 2, 4, 5, 6, 10),
    "R6": (1, 2, 4, 5, 6, 10),
    "R7": (1, 3, 5, 6, 7),
    "E1": (1, 3, 5, 6, 7),
    "E2": (1, 2, 3, 6, 7),
    "E3": (2, 4, 5, 6, 7, 8, 9),
    "E5": (1, 2, 3, 6, 7),
    "E7": (2, 3, 4, 6, 8),
}


@dataclass(frozen=True)
class ShopPurchaseReference:
    """Local cartridge and favorite-button calibration used by the buy flow."""

    shop_id: str
    label: str
    cartridge_templates: tuple[str, ...]
    favorite_slots: tuple[int, ...]


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


SHOP_PURCHASE_REFERENCES: dict[str, ShopPurchaseReference] = {
    shop_id: ShopPurchaseReference(
        shop_id=shop_id,
        label=SHOP_CARTRIDGE_LABELS[shop_id],
        cartridge_templates=_shop_cartridge_templates(shop_id),
        favorite_slots=SHOP_FAVORITE_SLOTS[shop_id],
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

# 商店卡带列表从上到下为剧情→角色→活动递增，与分页表顺序一致；出售会话按
# 此行号（从 0 起）估算滚动距离，一格滚轮约移动一行。
SHOP_CARTRIDGE_ORDER: tuple[str, ...] = tuple(
    shop_id for page in SHOP_CARTRIDGE_PAGES for shop_id in page.shop_ids
)
SHOP_CARTRIDGE_ROW_INDEX: dict[str, int] = {
    shop_id: index for index, shop_id in enumerate(SHOP_CARTRIDGE_ORDER)
}
SHOP_CARTRIDGE_PAGE_INDEX: dict[str, int] = {
    shop_id: page_index
    for page_index, page in enumerate(SHOP_CARTRIDGE_PAGES)
    for shop_id in page.shop_ids
}


def shop_purchase_reference(shop: str) -> ShopPurchaseReference:
    """Return a local reference for either ``S1`` or a label such as ``S1:血骑士``."""

    shop_id = str(shop).strip().split(":", 1)[0].upper()
    try:
        return SHOP_PURCHASE_REFERENCES[shop_id]
    except KeyError as exc:
        raise KeyError(f"未知商品卡带：{shop}") from exc

ITEM_ALIASES: dict[str, tuple[str, ...]] = {
    "姜黄": ("姜黃", "薑黄", "薑黃"),
    "秃鹫羽毛": ("秃鷲羽毛", "禿鹫羽毛", "禿鷲羽毛"),
    "蜂蜜黄油杏仁": ("蜂蜜奶油杏仁", "Honey Butter Almond"),
    "炸三文鱼便当": ("炸鲑鱼便当", "Salmon Cutlet Lunchbox"),
    "卢戈山参烤串": ("卢戈烤山参串", "Lugo Ginseng Grilled Skewer"),
    "闪闪铁板虾": ("闪耀西班牙蒜味虾", "Shiny Gambas", "Sparkling Griddle Shrimp"),
    "透明沙拉": ("透明化沙拉", "Transparent Salad"),
    "地狱火紫菜包饭": ("地狱火海苔饭卷", "Fire of Hell Kimbab"),
    "巧克力鸡尾酒": ("Chocolat Cocktail",),
    "鱼子酱蛋包饭": ("Caviar Omelette",),
}

# OCR 模型在简体界面小字/弹窗标题上可能将单个汉字识别为繁体或康熙异体字
# （例如 禿 U+79BF / 鷲 U+9DF2 / 黃 U+9EC3 / 鱗 U+9C57）。
# 商品识别与保留量配置共用此表，以同一商品身份执行出售保护。
TRADE_ITEM_VARIANT_CHAR_TABLE: dict[int, str] = str.maketrans(
    {
        "禿": "秃",
        "鷲": "鹫",
        "薑": "姜",
        "黃": "黄",
        "鱗": "鳞",
        "鮭": "鲑",
        "魚": "鱼",
        "飯": "饭",
        "糰": "团",
        "團": "团",
        "蘋": "苹",
        "獸": "兽",
        "鎮": "镇",
        "點": "点",
        "凱": "凯",
        "養": "养",
        "湯": "汤",
        "藥": "药",
        "盧": "卢",
        "蔘": "参",
        "參": "参",
        "獄": "狱",
        "麥": "麦",
        "麵": "面",
        "雞": "鸡",
        "欖": "榄",
        "義": "义",
        "頭": "头",
        "靈": "灵",
        "鮮": "鲜",
        "當": "当",
        "醬": "酱",
        "維": "维",
        "濃": "浓",
        "縮": "缩",
        "蘿": "萝",
        "蔔": "卜",
        "纓": "缨",
        "紅": "红",
        "蔥": "葱",
        "槍": "枪",
        "閃": "闪",
        "蝦": "虾",
        "燉": "炖",
        "鍋": "锅",
        "藍": "蓝",
        "餅": "饼",
        "乾": "干",
        "絲": "丝",
        "鐵": "铁",
        "馬": "马",
        "鈴": "铃",
        "壽": "寿",
    }
)
