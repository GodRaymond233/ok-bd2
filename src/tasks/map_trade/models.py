from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from src.utils.calibration import FHD_1080
from src.utils.vision_models import MatchResult, TemplateSpec

__all__ = [
    "MatchResult",
    "TemplateSpec",
]

# Runtime coordinates are calibrated at 1920x1080. Template source resolution
# remains an independent concern handled by ``template_resolution``.
MAP_TRADE_REFERENCE = FHD_1080
MERCHANT_CARD_ID = "Q_sp6"
PINNED_CARD_IDS = frozenset({"Q_sp6", "Q_sp18", "Q_sp20"})
COLLECTION_ACTION_NAMES = frozenset({"吸收", "召集", "压制"})


def collection_action_name(action: object) -> str:
    """Resolve a persisted name, action enum or skill descriptor to its action name."""
    value = getattr(action, "value", None)
    return str(getattr(action, "name", action) if value is None else value)


def collection_action_policy(actions: Iterable[object] | None = None) -> tuple[str, ...]:
    """Normalize executable actions, keeping order and the required absorb action."""
    if actions is None:
        return ("吸收", "召集", "压制")
    names = tuple(dict.fromkeys(
        name for action in actions
        if (name := collection_action_name(action)) in COLLECTION_ACTION_NAMES
    ))
    return names if "吸收" in names else ("吸收", *names)


class ScreenState(str, Enum):
    HOME = "home"
    CARD_MENU = "card_menu"
    SANDBOX = "sandbox"
    AREA_MAP = "area_map"
    SANDBOX_MAP = "sandbox_map"
    MERCHANT_DIALOG = "merchant_dialog"
    SHOP = "shop"
    COOKING = "cooking"
    LOADING = "loading"
    UNKNOWN = "unknown"


class MapPageMode(str, Enum):
    """Strict visual identity of the three map pages used by story navigation."""

    UNKNOWN = "unknown"
    DIRECT_TELEPORT = "direct_teleport"
    GENERATE_TELEPORT = "generate_teleport"
    SANDBOX_LARGE_MAP = "sandbox_large_map"

    @property
    def is_teleport_map(self) -> bool:
        return self in {
            MapPageMode.DIRECT_TELEPORT,
            MapPageMode.GENERATE_TELEPORT,
        }


class TeleportScope(str, Enum):
    UNKNOWN = "unknown"
    CURRENT_MAP = "current_map"
    CURRENT_CARTRIDGE = "current_cartridge"


class CollectionMapRole(str, Enum):
    MAIN_AREA = "main_area"
    BATTLE_AREA_1 = "battle_area_1"
    BATTLE_AREA_2 = "battle_area_2"

    @property
    def label(self) -> str:
        return {
            CollectionMapRole.MAIN_AREA: "主城区",
            CollectionMapRole.BATTLE_AREA_1: "战斗区域1",
            CollectionMapRole.BATTLE_AREA_2: "战斗区域2",
        }[self]


class CollectionActionState(str, Enum):
    """Durable lifecycle for one daily/card/map-role action.

    ``pending`` means the map-local action is proven but its absolute daily
    counter has not yet been reconciled.  These values are persisted as plain
    strings so old progress files remain easy to inspect and repair.
    """

    PREEXISTING_USED = "preexisting_used"
    ARMED = "armed"
    CLICKED = "clicked"
    LOCAL_DONE = "local_done"
    PENDING = "pending"
    SETTLED = "settled"
    BLOCKED = "blocked"
    VOID = "void"
    ARCHIVED = "archived"


@dataclass(frozen=True)
class CollectionMapTarget:
    role: CollectionMapRole
    title: str
    aliases: tuple[str, ...] = ()
    # Other maps whose names contain this one (角色游戏卡5: 童话镇 inside
    # 童话镇旅馆): a text naming one of them is not this map.
    excludes: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return self.role.value

    @property
    def titles(self) -> tuple[str, ...]:
        return (self.title, *self.aliases)


@dataclass(frozen=True)
class CardSpec:
    card_id: str
    number: int
    name: str
    template: str
    shop_label: str
    collectable: bool = True
    targets: tuple[CollectionMapTarget, ...] = ()
    # The quick bar tab it sits in: "story" (剧情游戏卡) or "character" (角色游戏卡).
    category: str = "story"

    @property
    def label(self) -> str:
        """How messages name the card: 第14章 / 角色卡1."""
        if self.category == "event":
            return f"活动卡{self.number}·{self.name}"
        return f"角色卡{self.number}" if self.category == "character" else f"第{self.number}章"



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
    map_page_mode: MapPageMode = MapPageMode.UNKNOWN
    # An event card confirmed absent: every scanned frame was valid with the
    # cover below threshold, and the quick switch page and event tab were
    # reconfirmed after the scan.
    card_not_found: bool = False


@dataclass(frozen=True)
class CollectionResult:
    success: bool
    depleted: bool = False
    completed_submaps: int = 0
    message: str = ""
    retryable: bool = True
    partial: bool = False
    # An event card absent from its confirmed quick switch tab; the caller skips it.
    card_missing: bool = False


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

STORY_COLLECTION_MAPS = {
    1: ("卢戈镇", "卢戈森林", "卢戈森林深处"),
    2: ("达雷普镇", "封锁矿山", "禁地矿山"),
    3: ("布伦城", "布伦下水道", "布伦下水道深处"),
    4: ("凯洛镇", "凯洛山", "凯洛山中心处"),
    5: ("亚拉里克城", "亚拉里克遗迹", "亚拉里克遗迹深处"),
    7: ("拉勒凯镇", "洞穴神殿", "隐藏神殿"),
    8: ("罗戴夫镇", "贫民区", "盗贼据点"),
    9: ("雷瓦汀基地", "加尔顿山前段", "加尔顿山后段"),
    10: ("埃兰城", "官邸地下", "官邸大殿"),
    11: ("罗坦王城", "罗坦城门", "城下小镇"),
    12: ("埃克夏城", "埃克夏森林", "埃克夏森林深处"),
    13: ("华特波尔镇", "华特波尔雪山", "记忆裂缝"),
    # 第14章按居住区域、左侧回廊、中央回廊采集；中央回廊没有已激活传送阵，须徒步进入。
    14: ("阿尔卡迪亚居住区域", "剑之神殿左侧回廊", "剑之神殿中央回廊"),
    15: ("科库托斯研究设施", "血刻印量产设施", "重要保管区域"),
    16: ("迪尔索特港口城市", "迪尔索特黑市", "黑市深处"),
    17: ("古代遗迹前哨基地", "试炼之路", "封印祭坛"),
    # 暗黑神殿没有传送阵，先采集安全区，再通过出口徒步进入圣域。
    18: ("科库托斯暗黑神殿", "科库托斯圣域", "第一记忆空间"),
    19: ("亡者安息处", "亡灵游地", "通往神殿之路"),
}
# 第18章的吸收总数包含路线外地图，整卡完成以压制角标为准。
SUPPRESS_ONLY_VERIFIED_CARD_IDS = frozenset({"Q_sp18"})

# 这些页面用于定位或恢复，不作为技能采集目标写入账本。
COLLECTION_TRANSIT_MAP_TITLES = (
    "记忆岔路口", "儿童房", "阿札尔之屋", "第四记忆空间", "第五记忆空间",
    "第二记忆空间", "第三记忆空间", "学校2楼", "学校3楼", "学校操场",
    "拍卖仓库", "商场1楼", "童话镇旅馆", "妖怪镇", "凯那尔工业2楼", "封印指定室",
    "水上乐园物资仓库",
)


# 无传送阵的相邻地图通过出口徒步切换。坐标为 1920×1080 地图出口标签中心，
# 仅在角色图标遮挡标签、OCR 无法定位时使用；None 表示仅通过本机 OCR 定位出口。
WALK_LABEL_EDGES = {
    ("Q_sp14", "battle_area_1", "battle_area_2"): (609, 719),
    ("Q_sp14", "battle_area_2", "battle_area_1"): (484, 460),
    # 角色卡3的禁闭室没有传送阵，与地下秘密拍卖场之间需要徒步。
    ("Q_cp3", "battle_area_1", "battle_area_2"): (631, 333),
    ("Q_cp3", "battle_area_2", "battle_area_1"): (739, 563),
    ("Q_sp18", "main_area", "battle_area_1"): None,
    ("Q_sp18", "battle_area_1", "main_area"): None,
}
# These destinations have no active circle; approach through the registered walking edge.
COLLECTION_CIRCLE_APPROACHES = {
    ("Q_sp14", "battle_area_2"): "battle_area_1",
    ("Q_cp3", "battle_area_2"): "battle_area_1",
    ("Q_sp18", "main_area"): "battle_area_1",
}
# 返城优先使用导航菜单中的艾琳，避开回传送阵途中可能出现的巡逻怪。
WALKED_EVENT_CARD_IDS = frozenset({"Q_ep1", "Q_ep3", "Q_ep5", "Q_ep7"})
TOWN_NAV_ENTRIES = {
    "Q_sp14": "艾琳", "Q_cp3": "艾琳", "Q_sp18": "艾琳",
    **dict.fromkeys(WALKED_EVENT_CARD_IDS, "艾琳"),
}
# 角色卡3重启路线时先经艾琳回到起点，即使当前已站在传送阵旁。
RESTART_NAV_ENTRIES = {
    "Q_cp3": "艾琳", "Q_sp18": "艾琳", **dict.fromkeys(WALKED_EVENT_CARD_IDS, "艾琳"),
}
# 角色卡3被巡逻驱逐后会自动继续行走，应等待恢复，避免重复点击打断移动。
RESUMING_WALK_CARD_IDS = frozenset({"Q_cp3"}) | WALKED_EVENT_CARD_IDS
# 在禁闭室出口处反向点击可能无响应；末图采集后经导航菜单回城，再向前补齐路线。
LAST_MAP_THEN_TOWN_CARD_IDS = frozenset({"Q_cp3"})
# 自动续走的总等待上限，包含被驱逐后的加载时间。
RESUMING_WALK_TIMEOUT = 60.0


def _story_collection_targets(number: int) -> tuple[CollectionMapTarget, ...]:
    titles = STORY_COLLECTION_MAPS.get(number)
    if titles is None:
        return ()
    return tuple(
        CollectionMapTarget(
            role, title, aliases=("科库托斯圣城",) if title == "科库托斯圣域" else (),
            excludes=("雷瓦汀基地救护站",)
            if number == 9 and role is CollectionMapRole.MAIN_AREA else (),
        )
        for role, title in zip(CollectionMapRole, titles, strict=True)
    )


STORY_CARDS = tuple(
    CardSpec(
        card_id=f"Q_sp{number}",
        number=number,
        name=name,
        template=f"image/Cartridges/Q_sp{number}.png",
        shop_label=f"S{number}:{name}",
        collectable=number in STORY_COLLECTION_MAPS,
        targets=_story_collection_targets(number),
    )
    for number, name in enumerate(STORY_CARD_NAMES, start=1)
)

STORY_COLLECTABLE_CARDS = tuple(card for card in STORY_CARDS if card.collectable)


CHARACTER_CARD_NAMES = (
    "杰登之门",
    "火晶片",
    "美丽无望",
    "大逃脱",
    "鲁的迷宫",
    "御剑传",
    "合约之战",
)
_MAIN = CollectionMapRole.MAIN_AREA
_BATTLE_1 = CollectionMapRole.BATTLE_AREA_1
_BATTLE_2 = CollectionMapRole.BATTLE_AREA_2
# 角色卡1至7有吸收/压制目标；采集首个安全区及至多两个战斗区。
# “废墟地道”的括号容易被 OCR 误读，使用“前段/后段”别名匹配。
CHARACTER_COLLECTION_TARGETS = {
    1: (
        CollectionMapTarget(_MAIN, "学校1楼"),
        CollectionMapTarget(_BATTLE_1, "学校地下"),
    ),
    2: (
        CollectionMapTarget(_MAIN, "避难所166", aliases=("避难所",)),
        CollectionMapTarget(_BATTLE_1, "废墟地道（前段）", aliases=("前段",)),
        CollectionMapTarget(_BATTLE_2, "废墟地道（后段）", aliases=("后段",)),
    ),
    # 禁闭室通过 WALK_LABEL_EDGES 中的徒步路线进入。
    3: (
        CollectionMapTarget(_MAIN, "上流社会派对会场"),
        CollectionMapTarget(_BATTLE_1, "地下秘密拍卖场"),
        CollectionMapTarget(_BATTLE_2, "禁闭室"),
    ),
    4: (
        CollectionMapTarget(_MAIN, "商场4楼"),
        CollectionMapTarget(_BATTLE_1, "商场3楼"),
        CollectionMapTarget(_BATTLE_2, "商场2楼"),
    ),
    5: (
        CollectionMapTarget(_MAIN, "童话镇", excludes=("童话镇旅馆",)),
        CollectionMapTarget(_BATTLE_1, "魔女居林"),
    ),
    6: (
        CollectionMapTarget(_MAIN, "西风镇"),
        CollectionMapTarget(_BATTLE_1, "西风竹林"),
        CollectionMapTarget(_BATTLE_2, "妖怪森林"),
    ),
    7: (
        CollectionMapTarget(_MAIN, "凯那尔工业1楼"),
        CollectionMapTarget(_BATTLE_1, "卡勒塔市"),
    ),
}
CHARACTER_CARDS = tuple(
    CardSpec(
        card_id=f"Q_cp{number}",
        number=number,
        name=name,
        template=f"image/Cartridges/Q_cp{number}.png",
        shop_label=f"R{number}:{name}",
        collectable=number in CHARACTER_COLLECTION_TARGETS,
        targets=CHARACTER_COLLECTION_TARGETS.get(number, ()),
        category="character",
    )
    for number, name in enumerate(CHARACTER_CARD_NAMES, start=1)
)

# The weekly map run: story chapters, then character cards.
COLLECTABLE_CARDS = STORY_COLLECTABLE_CARDS + tuple(
    card for card in CHARACTER_CARDS if card.collectable
)
EVENT_COLLECTION_TARGETS = {
    1: (
        CollectionMapTarget(_MAIN, "泳池派对场"),
        CollectionMapTarget(_BATTLE_1, "派对会场角落"),
    ),
    2: (
        CollectionMapTarget(_MAIN, "雷瓦汀据点"),
        CollectionMapTarget(_BATTLE_1, "恶梦之城1", aliases=("恶梦之城I",),
                            excludes=("恶梦之城2", "恶梦之城II")),
        CollectionMapTarget(_BATTLE_2, "恶梦之城2", aliases=("恶梦之城II",),
                            excludes=("恶梦之城1",)),
    ),
    3: (
        CollectionMapTarget(_MAIN, "海岸沙滩"),
        CollectionMapTarget(_BATTLE_1, "海岸椰林"),
    ),
    5: (
        CollectionMapTarget(_MAIN, "底层居住区"),
        CollectionMapTarget(_BATTLE_1, "第4区", aliases=("第４区",)),
    ),
    7: (
        CollectionMapTarget(_MAIN, "水上乐园室内泳池", aliases=("室内泳池",)),
        CollectionMapTarget(_BATTLE_1, "泼水音乐节活动现场", aliases=("音乐节活动现场",)),
    ),
}
EVENT_CARD_NAMES = {1: "夏日骑士", 2: "恶梦之冬", 3: "海滨天使", 5: "记忆边缘", 7: "戏水女王"}
EVENT_CARDS = tuple(
    CardSpec(
        card_id=f"Q_ep{number}", number=number, name=name,
        template=f"quick_switch_cartridges/event_cartridge_art_{number:02d}.png",
        shop_label=f"E{number}:{name}", targets=EVENT_COLLECTION_TARGETS[number], category="event",
    )
    for number, name in EVENT_CARD_NAMES.items()
)
# 每周跑图按剧情→角色→活动的顺序采集；COLLECTABLE_CARDS 只含剧情与角色卡。
REGISTERED_COLLECTION_CARDS = COLLECTABLE_CARDS + EVENT_CARDS
CARD_BY_ID = {card.card_id: card for card in (*STORY_CARDS, *CHARACTER_CARDS, *EVENT_CARDS)}
# 卡带完成图标只证明吸取周常，不代表不存在的压制动作已经执行。
ABSORB_ONLY_VERIFIED_CARD_IDS = frozenset({"Q_ep2"})
EVENT_WALK_EDGES = {
    ("Q_ep1", "main_area", "battle_area_1"): ((682, 392),),
    ("Q_ep1", "battle_area_1", "main_area"): ((492, 768), (492, 745)),
    ("Q_ep3", "main_area", "battle_area_1"): ((733, 689), (690, 727)),
    ("Q_ep3", "battle_area_1", "main_area"): ((326, 411), (371, 365)),
    ("Q_ep5", "main_area", "battle_area_1"): ((480, 253), (480, 272), (736, 449)),
    ("Q_ep5", "battle_area_1", "main_area"): ((537, 718), (537, 735), (392, 548), (392, 567)),
    ("Q_ep7", "battle_area_1", "main_area"): ((396, 384), (395, 402)),
    ("Q_ep7", "main_area", "battle_area_1"): ((748, 585), (748, 565)),
}


CHARACTER_SHOPS = {
    "R1": "R1:杰登之门",
    "R2": "R2:火晶片",
    "R3": "R3:美丽无望",
    "R4": "R4:大逃脱",
    "R5": "R5:鲁的迷宫",
    "R6": "R6:御剑传",
    "R7": "R7:合约之战",
}
# 活动卡带编号并不连续，须与 data.py 的选择模板映射一致。
EVENT_SHOPS = {
    "E1": "E1:夏日骑士",
    "E2": "E2:恶梦之冬",
    "E3": "E3:海滨天使",
    "E5": "E5:记忆边缘",
    "E7": "E7:戏水女王",
}

KNOWN_SHOPS = {
    # S20 is a non-tradeable placeholder card.  It has a story-card name for
    # collection display, but no shop cartridge or sale/purchase mapping.
    **{
        f"S{card.number}": card.shop_label
        for card in STORY_CARDS
        if card.number != 20
    },
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

# Red, yellow, then blue priority; reference order breaks ties within each group.
DEFAULT_COOKING_RECIPES = (
    "巧克力鸡尾酒",
    "冰镇甜点",
    "香草牛排",
    "蜂蜜黄油杏仁",
    "鱼子酱蛋包饭",
    "火烤鱼板棒",
    "泰瑞丝派",
    "橄榄油意面",
    "三明治便当",
)

OPTIONAL_COOKING_RECIPES = DEFAULT_RECIPES
FINAL_COOKING_RECIPE = "街头烤鸡肉串"

COOKING_RECIPE_TEMPLATES = {
    "巧克力鸡尾酒": "image/Shop/cook_Chocolate Cocktail.png",
    "蜂蜜黄油杏仁": "image/Shop/cook_Honey Butter Almonds.png",
    "鱼子酱蛋包饭": "image/Shop/cook_Caviar Omurice.png",
    "香草牛排": "image/Shop/cook_Vanilla Steak.png",
    "冰镇甜点": "image/Shop/cook_Iced Dessert.png",
    "泰瑞丝派": "image/Shop/cook_Teresse Pie.png",
    "橄榄油意面": "image/Shop/cook_Olive Oil Pasta.png",
    "三明治便当": "image/Shop/cook_Sandwich Lunch Box.png",
    "火烤鱼板棒": "image/Shop/cook_Grilled Fish Cake Skewer.png",
    FINAL_COOKING_RECIPE: "image/Shop/cook_Street Grilled Chicken Skewer.png",
}

RECIPE_TEMPLATES = {
    "卢戈山参烤串": "image/Shop/cook_Lugas Ginseng Skewer.png",
    "煤炭饼干": "image/Shop/cook_Coal Cookies.png",
    "闪闪铁板虾": "image/Shop/cook_Sparkling Griddle Shrimp.png",
    "透明沙拉": "image/Shop/cook_Transparent Salad.png",
    "地狱火紫菜包饭": "image/Shop/cook_Hellfire Gimbap.png",
}
COOKING_RECIPE_TEMPLATES.update(RECIPE_TEMPLATES)

DEFAULT_SALE_WHITELIST = (
    "蜂蜜黄油杏仁",
    "香草牛排",
    "冰镇甜点",
    "三角美乃滋饭团",
    "鱼子酱蛋包饭",
    "炸三文鱼便当",
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
