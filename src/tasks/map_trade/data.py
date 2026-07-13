from __future__ import annotations

# Configuration values adapted from MFABD2's data-only Arbitrage_ShopBuy_Data
# node; the automation implementation in this package is original. Empty
# tuples intentionally mean that no item should be favourited for that shop.
SHOP_BUY_LISTS: dict[str, tuple[str, ...]] = {
    "S1:血骑士": ("蘑菇", "胡萝卜", "甜辣酱"),
    "S2:苍蓝魔女": ("牛奶", "黄油", "白糖", "鸡蛋"),
    "S3:迷雾神射手": ("苹果", "葡萄", "蜂蜜", "白糖", "黄油"),
    "S4:眼镜与猫": ("牛奶", "咖啡豆", "三文鱼", "香草"),
    "S5:沙漠之花": ("哈密瓜", "白糖", "藏红花"),
    "S6:异教塔": ("甜椒", "奶酪", "鸡蛋"),
    "S7:愤怒天使": ("三文鱼", "番茄", "奶酪", "萝卜缨"),
    "S8:血之狂想曲": ("玉米", "豆子", "奶酪", "料酒"),
    "S9:铁假面": (),
    "S10:霍尔蒙克斯": ("杏仁", "黄油", "白糖"),
    "S11:虚假游戏": ("洋葱", "米", "甜椒", "奶酪", "香草", "鸡蛋", "小麦", "橄榄油"),
    "S12:黑羽毛": ("咖啡豆", "三文鱼", "牛奶", "香草"),
    "S13:雪之歌": ("苹果", "葡萄", "蜂蜜", "白糖", "黄油"),
    "S14:神圣审判": ("杏仁", "黄油", "白糖"),
    "S15:复仇的誓言": ("胡萝卜", "苹果", "香草", "洋葱", "番茄", "芥末"),
    "S16:三国同盟": ("三文鱼", "盐", "料酒", "奶酪", "咖啡豆", "白糖"),
    "S17:试炼之路": ("杏仁", "盐", "白糖"),
    "S18:救赎": ("玉米", "苹果", "鸡蛋", "牛奶"),
    "S19:被遗忘的战争": ("盐", "巧克力", "咖啡豆", "奶酪", "香草"),
    "R1:杰登之门": ("巧克力", "鸡蛋"),
    "R2:火晶片": ("水果罐头",),
    "R3:美丽无望": ("奶酪",),
    "R4:大逃脱": ("米", "袋装鸡胸肉", "调味海苔", "甜椒", "辣椒", "蛋黄酱", "小麦", "辣椒素"),
    "R5:鲁的迷宫": (),
    "R6:御剑传": ("葡萄",),
    "R7:合约之战": ("巧克力",),
    "E1:夏日骑士": ("巧克力",),
    "E2:恶梦之冬": ("水果罐头",),
    "E3:海滨天使": (),
    "E4:记忆边缘": ("水果罐头",),
}

SHOP_OCR_EXCLUDES = frozenset({"材料", "食材", "购买", "出售", "初始化", "还剩"})


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
