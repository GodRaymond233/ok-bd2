from dataclasses import dataclass
from typing import Callable

import cv2
import numpy as np

from src.utils.calibration import FHD_1080
from src.utils.image_utils import relative_roi_frame, to_gray
from src.utils.ocr_utils import normalize_ocr_text

HOME_GACHA_OCR_REFERENCE_ROI = (110, 993, 95, 54)
HOME_GACHA_OCR_RELATIVE_ROI = (
    110 / FHD_1080.width,
    993 / FHD_1080.height,
    205 / FHD_1080.width,
    1047 / FHD_1080.height,
)
HOME_GACHA_OCR_KEYWORD = "抽抽乐"
HOME_GACHA_OCR_SCALES = (1.0, 2.0, 3.0)
HOME_GACHA_OCR_ALIASES = (HOME_GACHA_OCR_KEYWORD,)
HOME_ANNOUNCEMENT_CLEAR_REFERENCE_POINT = (169, 615)
HOME_ANNOUNCEMENT_CLEAR_RELATIVE_POINT = (
    HOME_ANNOUNCEMENT_CLEAR_REFERENCE_POINT[0] / FHD_1080.width,
    HOME_ANNOUNCEMENT_CLEAR_REFERENCE_POINT[1] / FHD_1080.height,
)

# 左上图标列整列大 ROI（我的小屋/格鲁TALK/街机游戏）。检测模型需要上下文，
# 禁止单独拆出单标签小 ROI（实测丢字/返回空）。来源：BUG-20260829-01 实测标定。
HOME_LEFT_COLUMN_OCR_REFERENCE_ROI = (110, 165, 430, 155)
HOME_LEFT_COLUMN_OCR_RELATIVE_ROI = (
    110 / FHD_1080.width,
    165 / FHD_1080.height,
    540 / FHD_1080.width,
    320 / FHD_1080.height,
)
# 每组关键词为简体（OCR 只识别简体中文，2026-08-29 取消繁体识别别名）；
# 子串命中即该组计 1 票。
HOME_LEFT_COLUMN_KEYWORD_GROUPS = (
    ("我的小屋",),
    ("格鲁TALK",),
    ("街机游戏",),
)
HOME_LEFT_COLUMN_REQUIRED_HITS = 2

# 左列灰度 p95：未压暗实测定标 253、0.5x 公告压暗 126，阈值 185 干净分离，
# 且不随主页背景场景变化（旧模板相对亮度比值依赖采集场景，已废弃）。
HOME_DIMMED_P95_THRESHOLD_DEFAULT = 185.0

# 遮挡主页的模态弹窗（登录加成、签到、公告板等）：面板自身亮、四周被压暗。
MODAL_PANEL_BRIGHTNESS_THRESHOLD = 150
MODAL_PANEL_MIN_AREA_RATIO = 0.06
MODAL_PANEL_MAX_AREA_RATIO = 0.85
MODAL_PANEL_MIN_SIDE_RATIO = 0.18
MODAL_PANEL_CENTER_RANGE = (0.20, 0.80, 0.15, 0.85)
MODAL_PANEL_DETECT_MAX_SIDE = 960

# 关闭按钮不再按面板的固定比例估算。2026-11-16 实机对比两种弹窗：
#   「公告板」面板 1600x1220，X 中心在面板内 (0.960, 0.058)；
#   「登录加成」面板 1081x641，X 中心在面板内 (0.977, 0.160)。
# 两者纵向差 3 倍（头部高度不同），用同一个比例必然点到第一条公告上，
# 于是「打开公告 -> 退出 -> 再打开」的死循环。改成在面板右上角区域直接找
# 叉号字形：用局部中值背景差分，深色 ✕（白底）和浅色 ✕（彩色底）都命中。
MODAL_CLOSE_GLYPH_ROI = (0.60, 0.0, 1.0, 0.26)
MODAL_CLOSE_GLYPH_CONTRAST = 32
MODAL_CLOSE_GLYPH_MEDIAN_RATIO = 0.035
MODAL_CLOSE_GLYPH_SIDE_RATIO = (0.010, 0.050)
MODAL_CLOSE_GLYPH_ASPECT_RANGE = (0.6, 1.7)
MODAL_CLOSE_GLYPH_FILL_RANGE = (0.10, 0.65)
# 距面板右上角太近的连通域是圆角/描边伪影，不是按钮字形。
MODAL_CLOSE_GLYPH_EDGE_INSET = (0.018, 0.030)
# 仅在没有传入画面（旧调用/离线标定）时使用的估算比例。
MODAL_CLOSE_RELATIVE_IN_PANEL = (0.977, 0.167)

# 「新闻/公告」整页（从主页公告横幅/活动入口进入）：画面几乎铺满且亮度均匀，
# 不是居中小面板，modal_panel_bbox 会因面积超限拒绝，固定清理点又落在左侧
# 分类栏上（只会切换分类，退不出去）。唯一可靠的退出控件是左上角返回箭头。
# 2026-11-16 实机帧标定（2560x1440 抓帧，游戏内 3840x2160 等比）：
# 标题「新闻／公告」OCR 框 (461,58,420,80)，返回箭头深色笔画 bbox
# (328,73,29,54)、中心 (342,100) -> 相对 (0.1338, 0.0694)；
# 箭头 x 约等于「标题左边界 - 1.5 倍标题高度」，因此有 OCR 框时按标题反推，
# 没有 OCR 框时退回固定相对点（相对坐标与分辨率无关）。
NEWS_PAGE_TITLE_KEYWORD = "新闻公告"
NEWS_PAGE_SUPPORT_KEYWORDS = ("公告事项", "开发者笔记", "查看详情")
NEWS_BACK_REFERENCE_POINT = (342, 100)
NEWS_BACK_REFERENCE_SIZE = (2560, 1440)
NEWS_BACK_RELATIVE_POINT = (
    NEWS_BACK_REFERENCE_POINT[0] / NEWS_BACK_REFERENCE_SIZE[0],
    NEWS_BACK_REFERENCE_POINT[1] / NEWS_BACK_REFERENCE_SIZE[1],
)
NEWS_BACK_TITLE_OFFSET_RATIO = 1.5
NEWS_TITLE_MAX_RELATIVE_Y = 0.15

# 场景界面（格鲁菲餐厅等可走动场景）右上角的「返回主页」控件：
# 半透明圆形按钮里一枚空心房子图标，正下方贴着一个白底黑字的小方块（H 键徽标）。
# 2026-11-16 在真实「格鲁菲餐厅」4K 帧上重新标定（此前按主页位置估算是错的）：
#   房子字形 (3558,82)-(3634,151) 76x69，中心 (3596,116) -> 相对 (0.9365, 0.0537)
#   H 方块   (3578,169)-(3614,207) 36x38，中心 (3596,188) -> 相对 (0.9365, 0.0870)
# 主页同位置是「房子+齿轮」的设置按钮（相对 x 0.869~0.890），既没有 H 方块，
# 字形也不同，所以「白方块 + 上方比它宽一倍多的白色房子」是场景界面独有的证据。
RETURN_HOME_BADGE_RELATIVE_ROI = (0.900, 0.005, 0.990, 0.120)
RETURN_HOME_BADGE_ICON_RELATIVE_POINT = (0.9365, 0.0537)
RETURN_HOME_BADGE_LETTERS = ("H",)
# 实测 2x 放大最稳：3x 时 H 被放大到超出检测框的舒适尺度，反而读不出。
RETURN_HOME_BADGE_OCR_SCALE = 2.0

# 场景返回控件改用几何判据（不依赖 OCR、不依赖模板文件）。所有尺寸都以
# 帧高为基准做归一化，1080p / 1440p / 4K 共用同一套阈值。
SCENE_HOME_BUTTON_RELATIVE_ROI = (0.895, 0.010, 0.995, 0.140)
SCENE_HOME_BUTTON_WHITE_MIN = 200
SCENE_HOME_BUTTON_WHITE_MAX_SPREAD = 30
# H 方块的宽/高（占帧高的比例）：4K 实测 36x38 -> 0.0167 / 0.0176；
# 720p 实测 12x13 -> 0.0167 / 0.0181，所以归一化后两个分辨率落在同一点上。
SCENE_HOME_BUTTON_PLATE_WIDTH_RATIO = (0.008, 0.030)
SCENE_HOME_BUTTON_PLATE_HEIGHT_RATIO = (0.010, 0.034)
# 绝对像素下限：低于这个尺寸连 H 都读不出来（640x360 下「我的小屋」的「分享设置」
# 胶囊会缩成 6x7 的白块并冒充方块），宁可判「没有控件」也不猜。
SCENE_HOME_BUTTON_PLATE_MIN_PIXELS = (8, 10)
SCENE_HOME_BUTTON_PLATE_ASPECT = (0.72, 1.40)
SCENE_HOME_BUTTON_PLATE_MIN_FILL = 0.55
SCENE_HOME_BUTTON_PLATE_DARK_MAX = 110
SCENE_HOME_BUTTON_PLATE_DARK_RATIO = (0.05, 0.50)
# 房子中心相对方块中心的纵向偏移（以方块高为单位）。两处实测一致：
# 4K 房子中心 y=116 / 方块中心 y=188 / 方块高 38 -> 71.8/38 = 1.89；
# 720p 房子中心 y=38 / 方块中心 y=62.5 / 方块高 13 -> 24.5/13 = 1.88。
# 直接用这个偏移反推点击点，不再依赖「房子连通块」——餐厅亮桌布会把房子和背景
# 糊成一块 172px 宽的白斑，任何基于房子外接框的判据都会在那里失效。
SCENE_HOME_BUTTON_HOUSE_OFFSET = 1.89
# 方块上方条带里白色像素占比的下限，用来证明方块上方确实压着一枚白色房子图标。
# 实测：4K 暗背景 0.28、4K 亮桌布 0.58、720p 0.26；只有方块没有房子时约 0.0。
SCENE_HOME_BUTTON_ABOVE_WHITE_MIN = 0.20
# 方块上方条带的取样高度（以方块高为单位）。
SCENE_HOME_BUTTON_ABOVE_WHITE_SPAN = (0.10, 2.30)
SCENE_HOME_BUTTON_ABOVE_WHITE_WIDTH = 1.60
# H 字形判据（方块高度够大时才启用）：左右两根竖笔 + 中间一道竖谷 + 一根横笔。
# 阈值来自 4K 真实帧：两根竖笔列占比 1.00、中缝 0.00~0.17、横笔连通两竖。
SCENE_HOME_BUTTON_H_MIN_PLATE_H = 14
SCENE_HOME_BUTTON_H_STROKE_MIN = 0.55
# 列占比超过峰值的这个比例才算「竖笔列」。
SCENE_HOME_BUTTON_H_RUN_FACTOR = 0.60
# 竖谷允许的最大高度（相对峰值）：H 的中缝几乎全亮，实心块则处处等高。
SCENE_HOME_BUTTON_H_VALLEY_MAX = 0.45
# 横笔最少要盖住竖谷高度的这个比例。
SCENE_HOME_BUTTON_H_CROSSBAR_MIN = 0.10
SCENE_HOME_BUTTON_H_INSET = (0.12, 0.10)
# 备用锚点：方块与亮背景连成一片、白连通块被吞掉时，改从「黑色 H 字形 + 四周白底」
# 反推方块。黑字形阈值、外扩倍数与白环占比下限都按真实帧标定。
SCENE_HOME_BUTTON_GLYPH_DARK_MAX = 90
SCENE_HOME_BUTTON_GLYPH_HEIGHT_RATIO = (0.005, 0.030)
SCENE_HOME_BUTTON_GLYPH_ASPECT = (0.30, 1.30)
SCENE_HOME_BUTTON_GLYPH_MIN_FILL = 0.15
SCENE_HOME_BUTTON_GLYPH_PAD = (0.90, 0.65)
SCENE_HOME_BUTTON_GLYPH_RING_WHITE_MIN = 0.55
# 两次采样得到的点击点相差超过帧宽的这个比例，就认定画面在动，不做点击。
SCENE_HOME_BUTTON_STABILITY_RATIO = 0.020

# 深色模态弹窗（例如「我的小屋 → 分享设置」）：面板本身就是深蓝灰，实测底色
# 只有 gray≈45，远低于 MODAL_PANEL_BRIGHTNESS_THRESHOLD，几何亮面板检测不成立；
# 面板上也没有右上角 ✕，只有底部按钮行里的「取消 / 确认」。
# 这类弹窗唯一的出口是「取消」，所以判据是「整屏被遮罩压暗 + OCR 明确读到取消
# 类按钮文字」，两者同时成立才点，绝不按比例盲点。阈值只用来省掉亮页面的 OCR：
# 实测 主页 219、我的小屋场景 113（夜晚房间本身就很暗）、分享设置弹窗 48，
# 真正的安全闸门是 OCR 那一条，不是亮度。
MODAL_OVERLAY_P95_MAX = 150.0
MODAL_OVERLAY_DETECT_MAX_SIDE = 480
MODAL_CANCEL_KEYWORDS = (
    "取消",
    "关闭",
    "以后再说",
    "稍后再说",
    "稍后",
    "暂不",
    "放弃",
)
# 「确认/领取/购买」这类词一旦命中说明这是操作确认框，绝不能点。
MODAL_CANCEL_FORBIDDEN_KEYWORDS = (
    "确认",
    "确定",
    "领取",
    "购买",
    "立即",
    "前往",
    "分享",
)
MODAL_CANCEL_MIN_CONFIDENCE = 0.75
MODAL_CANCEL_MAX_TEXT_LENGTH = 8
MODAL_CANCEL_RELATIVE_REGION = (0.08, 0.10, 0.92, 0.96)
MODAL_CANCEL_OCR_TARGET_HEIGHT = 720


@dataclass(frozen=True)
class HomeGachaOcrResult:
    text: str
    selected_scale: float | None
    attempts: tuple[tuple[float, str], ...]

    @property
    def matched(self) -> bool:
        return self.selected_scale is not None

    @property
    def trace(self) -> str:
        attempts = ", ".join(
            f"x{scale:g}={text or '-'}" for scale, text in self.attempts
        )
        selected = (
            f"采用x{self.selected_scale:g}"
            if self.selected_scale is not None
            else "未命中"
        )
        return f"{selected}; {attempts}"


def home_gacha_ocr_with_fallback(
    read_text: Callable[[float], object],
    scales: tuple[float, ...] = HOME_GACHA_OCR_SCALES,
) -> HomeGachaOcrResult:
    """Read the fixed gacha ROI with bounded same-frame upscale fallbacks."""
    attempts: list[tuple[float, str]] = []
    first_nonempty = ""
    for scale in scales:
        text = str(read_text(float(scale)) or "").strip()
        attempts.append((float(scale), text))
        if text and not first_nonempty:
            first_nonempty = text
        if home_gacha_ocr_matches(text):
            return HomeGachaOcrResult(text, float(scale), tuple(attempts))
    return HomeGachaOcrResult(first_nonempty, None, tuple(attempts))


def home_gacha_ocr_matches(text: object) -> bool:
    normalized_text = normalize_ocr_text(text)
    return any(
        normalize_ocr_text(keyword) in normalized_text
        for keyword in HOME_GACHA_OCR_ALIASES
    )


def home_left_column_hits(text: object) -> int:
    """Count left-column keyword groups present in the same-frame OCR text."""
    normalized_text = normalize_ocr_text(text)
    hits = 0
    for aliases in HOME_LEFT_COLUMN_KEYWORD_GROUPS:
        if any(
            normalize_ocr_text(alias) in normalized_text
            for alias in aliases
        ):
            hits += 1
    return hits


def return_home_badge_hit(boxes, frame_w: int, frame_h: int) -> bool:
    """Detect the ``H`` key badge under the scene return-home button.

    The badge is a small white rounded square with a black ``H``; the OCR
    model only reads it reliably on an upscaled local ROI, which is why the
    caller crops ``RETURN_HOME_BADGE_RELATIVE_ROI`` instead of relying on the
    full-frame pass. A hit means "a return control is on screen right now",
    not "we are on the home page".
    """
    if not boxes or frame_w <= 0 or frame_h <= 0:
        return False
    left, top, right, bottom = RETURN_HOME_BADGE_RELATIVE_ROI
    min_x, max_x = left * frame_w, right * frame_w
    min_y, max_y = top * frame_h, bottom * frame_h
    for box in boxes:
        name = normalize_ocr_text(getattr(box, "name", "") or "").upper()
        if name not in RETURN_HOME_BADGE_LETTERS:
            continue
        try:
            center_x = float(box.x) + float(box.width) / 2
            center_y = float(box.y) + float(box.height) / 2
        except Exception:
            continue
        if min_x <= center_x <= max_x and min_y <= center_y <= max_y:
            return True
    return False


def _scene_white_mask(crop) -> np.ndarray:
    """纯白掩膜：三通道都 >= 200 且通道极差 <= 30（排除彩色图标与高光边缘）。"""
    channels = [crop[..., index].astype(np.int16) for index in range(3)]
    brightest = np.maximum(np.maximum(channels[0], channels[1]), channels[2])
    darkest = np.minimum(np.minimum(channels[0], channels[1]), channels[2])
    return (
        (darkest >= SCENE_HOME_BUTTON_WHITE_MIN)
        & ((brightest - darkest) <= SCENE_HOME_BUTTON_WHITE_MAX_SPREAD)
    ).astype(np.uint8)


def _scene_h_like(plate_gray) -> bool | None:
    """方块里那团深色笔画是不是字母 H（左右竖笔 + 中间横笔）。

    返回 ``None`` 表示方块太小、像素级别不足以下结论（此时只靠几何约束）。
    """
    height, width = plate_gray.shape[:2]
    if height < SCENE_HOME_BUTTON_H_MIN_PLATE_H or width < int(
        SCENE_HOME_BUTTON_H_MIN_PLATE_H * 0.7
    ):
        return None
    inset_x = int(round(SCENE_HOME_BUTTON_H_INSET[0] * width))
    inset_y = int(round(SCENE_HOME_BUTTON_H_INSET[1] * height))
    inner = plate_gray[inset_y : height - inset_y, inset_x : width - inset_x]
    if inner.size == 0:
        return None
    dark = (inner <= SCENE_HOME_BUTTON_PLATE_DARK_MAX).astype(np.uint8)
    if int(dark.sum()) < 12:
        return False
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(dark, 8)
    if count < 2:
        return False
    for index in range(1, count):
        x = int(stats[index][cv2.CC_STAT_LEFT])
        y = int(stats[index][cv2.CC_STAT_TOP])
        glyph_w = int(stats[index][cv2.CC_STAT_WIDTH])
        glyph_h = int(stats[index][cv2.CC_STAT_HEIGHT])
        if glyph_h < 6 or glyph_w < 5:
            continue
        if not (0.30 <= glyph_h / float(height) <= 0.90):
            continue
        if not (0.15 <= glyph_w / float(width) <= 0.80):
            continue
        # 字形要大致居中：方块的圆角在四角外侧留了深色，别把它当成 H。
        glyph_cx = x + glyph_w / 2.0
        glyph_cy = y + glyph_h / 2.0
        if abs(glyph_cx - width / 2.0) > 0.25 * width:
            continue
        if abs(glyph_cy - height / 2.0) > 0.25 * height:
            continue
        glyph = dark[y : y + glyph_h, x : x + glyph_w]
        if _h_stroke_pattern(glyph):
            return True
    return False


def _h_stroke_pattern(glyph) -> bool:
    """字形列剖面是否是「两根竖笔 + 中间一道竖谷 + 至少一根横笔」。

    用竖谷而不是「左中右三段」来判，是因为真实控件在 1440p 下 H 只有 8px 宽，
    按三等分切会把左竖笔的最后一列算进中段，硬阈值立刻误判。
    """
    glyph_h, glyph_w = glyph.shape[:2]
    columns = glyph.sum(axis=0) / float(glyph_h)
    peak = float(columns.max())
    if peak < SCENE_HOME_BUTTON_H_STROKE_MIN:
        return False
    strong = columns >= SCENE_HOME_BUTTON_H_RUN_FACTOR * peak
    runs = []  # [(start, end)]，以 strong 的连续段为准
    start = None
    for index, flag in enumerate(strong):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            runs.append((start, index - 1))
            start = None
    if start is not None:
        runs.append((start, glyph_w - 1))
    if len(runs) != 2:
        return False
    gap_left, gap_right = runs[0][1] + 1, runs[1][0] - 1
    if gap_right < gap_left:
        return False
    valley = float(columns[gap_left : gap_right + 1].min())
    if valley > SCENE_HOME_BUTTON_H_VALLEY_MAX * peak:
        return False
    # 横笔：竖谷那几列里必须有若干行是深色（H 的横杠把两根竖笔连起来）。
    bridge = glyph[:, gap_left : gap_right + 1].sum(axis=1).max()
    return bool(bridge >= max(1, SCENE_HOME_BUTTON_H_CROSSBAR_MIN * glyph_h))


def _scene_above_white_density(crop, white, x: int, y: int, width: int, height: int) -> float:
    """H 方块正上方那一条里白色像素的占比——用来证明房子图标确实在。"""
    near, far = SCENE_HOME_BUTTON_ABOVE_WHITE_SPAN
    strip_top = max(0, y - int(round(far * height)))
    strip_bottom = max(strip_top + 1, y - int(round(near * height)))
    center_x = x + width // 2
    span = int(round(SCENE_HOME_BUTTON_ABOVE_WHITE_WIDTH * width))
    strip_left = max(0, center_x - span)
    strip_right = min(crop.shape[1], center_x + span)
    if strip_right <= strip_left:
        return 0.0
    strip = white[strip_top:strip_bottom, strip_left:strip_right]
    if strip.size == 0:
        return 0.0
    return float(strip.mean())


ScenePlateBox = tuple[int, int, int, int, bool | None]


def _scene_plate_candidates(crop, gray, frame_h: int) -> list[ScenePlateBox]:
    """主锚点：右上角那块「白底 + 黑 H」的小方块（按白色连通块找）。"""
    white = _scene_white_mask(crop)
    if not white.any():
        return []
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(white, 8)
    min_w, max_w = SCENE_HOME_BUTTON_PLATE_WIDTH_RATIO
    min_h, max_h = SCENE_HOME_BUTTON_PLATE_HEIGHT_RATIO
    min_aspect, max_aspect = SCENE_HOME_BUTTON_PLATE_ASPECT
    min_dark, max_dark = SCENE_HOME_BUTTON_PLATE_DARK_RATIO
    min_px_w, min_px_h = SCENE_HOME_BUTTON_PLATE_MIN_PIXELS
    found = []
    for index in range(1, count):
        x = int(stats[index][cv2.CC_STAT_LEFT])
        y = int(stats[index][cv2.CC_STAT_TOP])
        width = int(stats[index][cv2.CC_STAT_WIDTH])
        height = int(stats[index][cv2.CC_STAT_HEIGHT])
        area = int(stats[index][cv2.CC_STAT_AREA])
        if width < min_px_w or height < min_px_h:
            continue
        # 尺寸以帧高归一化，换分辨率不用改阈值。
        if not (min_w <= width / frame_h <= max_w):
            continue
        if not (min_h <= height / frame_h <= max_h):
            continue
        if not (min_aspect <= width / float(height) <= max_aspect):
            continue
        if area / float(width * height) < SCENE_HOME_BUTTON_PLATE_MIN_FILL:
            continue
        plate_gray = gray[y : y + height, x : x + width]
        if plate_gray.size == 0:
            continue
        dark_ratio = float(
            np.count_nonzero(plate_gray <= SCENE_HOME_BUTTON_PLATE_DARK_MAX)
        ) / plate_gray.size
        if not (min_dark <= dark_ratio <= max_dark):
            continue
        h_like = _scene_h_like(plate_gray)
        # 只有「确认读出 H」才认这块方块。方块太小、读不出笔画时一律判无控件：
        # 宁可这一趟不退出（任务会报失败），也不能在「我的小屋」把白胶囊当成
        # 返回控件点开分享设置。
        if h_like is not True:
            continue
        if _scene_above_white_density(crop, white, x, y, width, height) < (
            SCENE_HOME_BUTTON_ABOVE_WHITE_MIN
        ):
            continue
        found.append((x, y, width, height, h_like))
    return found


def _scene_glyph_candidates(crop, gray, frame_h: int) -> list[ScenePlateBox]:
    """备用锚点：从黑色 H 字形反推方块。

    亮桌布/亮地面会把白方块和背景连成一片、把白连通块撑到 172px 宽，此时主锚点
    找不到候选；而黑字形对亮背景是强局部对比，外圈一白就把方块框住了。
    """
    dark = (gray <= SCENE_HOME_BUTTON_GLYPH_DARK_MAX).astype(np.uint8)
    if not dark.any():
        return []
    min_h, max_h = SCENE_HOME_BUTTON_GLYPH_HEIGHT_RATIO
    min_aspect, max_aspect = SCENE_HOME_BUTTON_GLYPH_ASPECT
    pad_x_scale, pad_y_scale = SCENE_HOME_BUTTON_GLYPH_PAD
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(dark, 8)
    found = []
    for index in range(1, count):
        x = int(stats[index][cv2.CC_STAT_LEFT])
        y = int(stats[index][cv2.CC_STAT_TOP])
        width = int(stats[index][cv2.CC_STAT_WIDTH])
        height = int(stats[index][cv2.CC_STAT_HEIGHT])
        area = int(stats[index][cv2.CC_STAT_AREA])
        if width < 6 or height < 7:
            continue
        if not (min_h <= height / frame_h <= max_h):
            continue
        if not (min_aspect <= width / float(height) <= max_aspect):
            continue
        if area / float(width * height) < SCENE_HOME_BUTTON_GLYPH_MIN_FILL:
            continue
        pad_x = int(round(pad_x_scale * width))
        pad_y = int(round(pad_y_scale * height))
        left = max(0, x - pad_x)
        top = max(0, y - pad_y)
        right = min(crop.shape[1], x + width + pad_x)
        bottom = min(crop.shape[0], y + height + pad_y)
        if right <= left or bottom <= top:
            continue
        ring = crop[top:bottom, left:right]
        if float(_scene_white_mask(ring).mean()) < SCENE_HOME_BUTTON_GLYPH_RING_WHITE_MIN:
            continue
        plate_w = right - left
        plate_h = bottom - top
        if _scene_h_like(gray[top:bottom, left:right]) is not True:
            continue
        found.append((left, top, plate_w, plate_h, True))
    return found


def wide_upper_right_button_at(frame, point: tuple[int, int]) -> bool:
    """Reject a house match that lands on a wide, pale text button such as 比较."""
    height, width = frame.shape[:2]
    left = round(width * .80)
    top = round(height * .005)
    roi = frame[top:round(height * .10), left:width]
    if roi.size == 0:
        return False
    lower = roi.min(axis=2)
    pale = ((lower >= 115) & (roi.max(axis=2) - lower <= 36)).astype(np.uint8)
    _, _, stats, _ = cv2.connectedComponentsWithStats(pale, connectivity=8)
    x, y = point
    for box_x, box_y, box_w, box_h, area in stats[1:]:
        if (box_w >= width * .063 and height * .022 <= box_h <= height * .062
                and area >= box_w * box_h * .65
                and box_x <= x - left < box_x + box_w
                and box_y <= y - top < box_y + box_h):
            return True
    return False


def scene_home_button_point(frame) -> tuple[float, float] | None:
    """Locate the scene return-home button and return a click point.

    The control is a white house glyph sitting on top of a small white plate
    that carries a black ``H``. The plate is the anchor: it is small, its
    aspect ratio is close to 1, and the black ``H`` inside it survives even on
    a bright background (the restaurant tablecloth). The house above is used
    only as *evidence* (it has to be there) and never as a bounding box,
    because on bright scenes the glyph merges with the background and any
    extent-based measurement of it becomes meaningless.

    The click point is derived from the plate geometry:
    ``(plate_cx, plate_cy - SCENE_HOME_BUTTON_HOUSE_OFFSET * plate_h)``.
    That offset was measured on two resolutions and matched the verified live
    click point to within one pixel.

    Returns relative ``(x, y)`` on the frame, or ``None``.
    """
    if frame is None:
        return None
    frame_h, frame_w = frame.shape[:2]
    if frame_h <= 0 or frame_w <= 0:
        return None
    left, top, crop = relative_roi_frame(frame, SCENE_HOME_BUTTON_RELATIVE_ROI)
    if crop.size == 0:
        return None
    crop_h, crop_w = crop.shape[:2]
    if crop_h < 16 or crop_w < 16:
        return None

    gray = to_gray(crop)
    plates = _scene_plate_candidates(crop, gray, frame_h)
    if not plates:
        # 方块被亮背景吞掉时退回「黑字形 + 白环」锚点。
        plates = _scene_glyph_candidates(crop, gray, frame_h)
    best: tuple[float, float, float] | None = None
    for x, y, width, height, h_like in plates:
        center_x = x + width / 2.0
        center_y = y + height / 2.0 - SCENE_HOME_BUTTON_HOUSE_OFFSET * height
        point_x = (left + center_x) / frame_w
        point_y = (top + center_y) / frame_h
        if not (SCENE_HOME_BUTTON_RELATIVE_ROI[0] - 0.03 <= point_x <= 1.0):
            continue
        if not (0.0 <= point_y <= SCENE_HOME_BUTTON_RELATIVE_ROI[3]):
            continue
        # 方块越大、H 字形可确认的（而不是「太小判不了」的）越可信。
        score = width / float(frame_h) + (0.01 if h_like else 0.0)
        if best is None or score > best[0]:
            best = (score, point_x, point_y)

    if best is None:
        return None
    _score, point_x, point_y = best
    return (point_x, point_y)


def home_left_column_p95_brightness(frame) -> float:
    """Scene-independent 95th-percentile grayscale of the left-column ROI."""
    if frame is None:
        return 0.0
    _left, _top, crop = relative_roi_frame(frame, HOME_LEFT_COLUMN_OCR_RELATIVE_ROI)
    if crop.size == 0:
        return 0.0
    return float(np.percentile(to_gray(crop), 95))


def home_confirmation_passes(
    *,
    left_hits: int,
    required_left_hits: int,
    brightness: float,
    brightness_threshold: float,
    gacha_ocr_text: object,
) -> bool:
    """Require all three same-frame signals before confirming the global home page.

    brightness is a scene-independent dimming metric compared against
    brightness_threshold: the OCR pipeline passes the left-column grayscale
    p95 (0-255, threshold 185); the legacy template bridge passes the
    template-relative brightness ratio (0-1, threshold 0.75).
    """
    return (
        int(left_hits) >= int(required_left_hits)
        and float(brightness) >= float(brightness_threshold)
        and home_gacha_ocr_matches(gacha_ocr_text)
    )


def home_temporary_announcement_detected(
    *,
    left_hits: int,
    required_left_hits: int,
    brightness: float,
    brightness_threshold: float,
    gacha_ocr_text: object,
) -> bool:
    """Detect a dimmed global home page covered by a temporary announcement."""
    return (
        int(left_hits) >= int(required_left_hits)
        and float(brightness) < float(brightness_threshold)
        and home_gacha_ocr_matches(gacha_ocr_text)
    )


def modal_panel_bbox(frame) -> tuple[int, int, int, int] | None:
    """Return the (left, top, width, height) of a centred bright modal panel.

    A popup that covers the home screen renders as one large bright rectangle on
    an otherwise dimmed frame, so a brightness mask plus one morphological pass
    is enough.  The panel has to cross the frame centre and stay inside the
    configured area range; anything else (HUD numbers, side banners, artwork
    strips) is rejected so callers never click a guessed position.
    Detection runs on a downscaled copy because the mask work is O(pixels) and
    this path only fires while the home page is dimmed.
    """
    if frame is None:
        return None
    gray = to_gray(frame)
    if gray.size == 0:
        return None
    height, width = gray.shape[:2]
    if height <= 0 or width <= 0:
        return None

    scale = min(1.0, MODAL_PANEL_DETECT_MAX_SIDE / float(max(height, width)))
    work = gray
    if scale < 1.0:
        work = cv2.resize(
            gray,
            (
                max(1, int(round(width * scale))),
                max(1, int(round(height * scale))),
            ),
            interpolation=cv2.INTER_AREA,
        )
    work_height, work_width = work.shape[:2]
    if work_height <= 0 or work_width <= 0:
        return None

    mask = (work >= MODAL_PANEL_BRIGHTNESS_THRESHOLD).astype(np.uint8)
    kernel_size = max(3, int(round(min(work_height, work_width) * 0.02)))
    if kernel_size % 2 == 0:
        kernel_size += 1
    kernel = np.ones((kernel_size, kernel_size), dtype=np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

    try:
        count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(
            mask, 8,
        )
    except cv2.error:
        return None

    frame_area = float(work_height * work_width)
    min_center_x, max_center_x, min_center_y, max_center_y = (
        MODAL_PANEL_CENTER_RANGE
    )
    best_area = 0.0
    best_bbox: tuple[int, int, int, int] | None = None
    for index in range(1, count):
        left, top, panel_width, panel_height, area = (
            int(value) for value in stats[index][:5]
        )
        if panel_width <= 0 or panel_height <= 0:
            continue
        area_ratio = float(area) / frame_area
        if not (
            MODAL_PANEL_MIN_AREA_RATIO <= area_ratio <= MODAL_PANEL_MAX_AREA_RATIO
        ):
            continue
        if (
            panel_width / work_width < MODAL_PANEL_MIN_SIDE_RATIO
            or panel_height / work_height < MODAL_PANEL_MIN_SIDE_RATIO
        ):
            continue
        center_x = (left + panel_width / 2) / work_width
        center_y = (top + panel_height / 2) / work_height
        if not (min_center_x <= center_x <= max_center_x):
            continue
        if not (min_center_y <= center_y <= max_center_y):
            continue
        # The panel must cover the frame centre, not sit off to one side.
        if not (
            left < work_width * 0.5 < left + panel_width
            and top < work_height * 0.5 < top + panel_height
        ):
            continue
        if float(area) > best_area:
            best_area = float(area)
            best_bbox = (left, top, panel_width, panel_height)
    if best_bbox is None or scale >= 1.0:
        return best_bbox

    left, top, panel_width, panel_height = best_bbox
    return (
        min(width - 1, int(round(left / scale))),
        min(height - 1, int(round(top / scale))),
        max(1, int(round(panel_width / scale))),
        max(1, int(round(panel_height / scale))),
    )


def modal_close_glyph(
    frame,
    bbox: tuple[int, int, int, int] | None,
) -> tuple[float, float] | None:
    """Return the relative position of the panel's ✕ close button.

    The button is a small, roughly square glyph near the panel's top-right
    corner.  Instead of assuming one fixed inset (different popups have
    different header heights and the guess lands on content), the glyph is
    found by local-contrast blobs: the crop is compared against its own median
    background, so a dark ✕ on white and a light ✕ on a coloured bar both
    survive.  Corner-radius shadows are dropped by an edge-inset rule.

    Returns ``None`` when no confident glyph exists — callers must then not
    click at all instead of guessing, because a wrong click lands on the
    popup's own list rows and opens them.
    """
    if frame is None or bbox is None:
        return None
    gray = to_gray(frame)
    if gray.size == 0:
        return None
    frame_height, frame_width = gray.shape[:2]
    try:
        left, top, panel_width, panel_height = (int(value) for value in bbox)
    except (TypeError, ValueError):
        return None
    if panel_width <= 0 or panel_height <= 0:
        return None

    roi_x0 = max(0, left + int(panel_width * MODAL_CLOSE_GLYPH_ROI[0]))
    roi_x1 = min(frame_width, left + int(panel_width * MODAL_CLOSE_GLYPH_ROI[2]))
    roi_y0 = max(0, top + int(panel_height * MODAL_CLOSE_GLYPH_ROI[1]))
    roi_y1 = min(frame_height, top + int(panel_height * MODAL_CLOSE_GLYPH_ROI[3]))
    if roi_x1 - roi_x0 < 16 or roi_y1 - roi_y0 < 16:
        return None

    crop = gray[roi_y0:roi_y1, roi_x0:roi_x1]
    kernel = max(
        15, min(81, int(round(panel_width * MODAL_CLOSE_GLYPH_MEDIAN_RATIO)) | 1),
    )
    if min(crop.shape[:2]) <= kernel:
        return None
    background = cv2.medianBlur(crop, kernel).astype(np.int16)
    difference = np.abs(crop.astype(np.int16) - background)
    mask = (difference >= MODAL_CLOSE_GLYPH_CONTRAST).astype(np.uint8)
    try:
        count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(
            mask, 8,
        )
    except cv2.error:
        return None

    crop_height, crop_width = crop.shape[:2]
    min_side, max_side = MODAL_CLOSE_GLYPH_SIDE_RATIO
    min_aspect, max_aspect = MODAL_CLOSE_GLYPH_ASPECT_RANGE
    min_fill, max_fill = MODAL_CLOSE_GLYPH_FILL_RANGE
    inset_x, inset_y = MODAL_CLOSE_GLYPH_EDGE_INSET
    best_key: float | None = None
    best_point: tuple[float, float] | None = None
    for index in range(1, count):
        x, y, width, height, area = (
            int(value) for value in stats[index][:5]
        )
        if width <= 0 or height <= 0:
            continue
        # 贴住裁剪边界的是面板边框/圆角，不是按钮。
        if (
            x <= 1
            or y <= 1
            or x + width >= crop_width - 1
            or y + height >= crop_height - 1
        ):
            continue
        side = max(width, height)
        if not (min_side * panel_width <= side <= max_side * panel_width):
            continue
        aspect = width / float(height)
        if not (min_aspect <= aspect <= max_aspect):
            continue
        fill = area / float(width * height)
        if not (min_fill <= fill <= max_fill):
            continue
        center_x = roi_x0 + x + width / 2.0
        center_y = roi_y0 + y + height / 2.0
        relative_x = (center_x - left) / float(panel_width)
        relative_y = (center_y - top) / float(panel_height)
        if (1.0 - relative_x) < inset_x or relative_y < inset_y:
            continue
        # 关闭按钮是离右上角最近的那个字形。
        key = (left + panel_width - center_x) + (center_y - top) * 0.5
        if best_key is None or key < best_key:
            best_key = key
            best_point = (
                min(1.0, max(0.0, center_x / frame_width)),
                min(1.0, max(0.0, center_y / frame_height)),
            )
    return best_point


def modal_close_point(
    bbox: tuple[int, int, int, int] | None,
    width: int,
    height: int,
    *,
    frame=None,
) -> tuple[float, float] | None:
    """Map a modal panel bbox to the relative position of its close button.

    With ``frame`` the position comes from the ✕ glyph itself and returns
    ``None`` when it cannot be located (never guess).  Without ``frame`` the
    legacy fixed inset is used, which stays for offline calibration only.
    """
    if bbox is None or width <= 0 or height <= 0:
        return None
    left, top, panel_width, panel_height = (float(value) for value in bbox)
    if panel_width <= 0 or panel_height <= 0:
        return None
    if frame is not None:
        return modal_close_glyph(frame, bbox)
    offset_x, offset_y = MODAL_CLOSE_RELATIVE_IN_PANEL
    x = left + panel_width * offset_x
    y = top + panel_height * offset_y
    return (
        min(1.0, max(0.0, x / width)),
        min(1.0, max(0.0, y / height)),
    )


def news_page_detected(ocr_text: object) -> bool:
    """Detect the full-screen 新闻/公告 page (a sub page, not a modal).

    Only OCR evidence is used: the merged title reads 新闻／公告 and the left
    category column always carries 公告事项 / 开发者笔记, so the check cannot
    fire on the home screen where none of those strings exist.
    """
    text = normalize_ocr_text(ocr_text, alnum_only=True)
    if not text:
        return False
    if NEWS_PAGE_TITLE_KEYWORD in text:
        return True
    if "新闻" not in text or "公告" not in text:
        return False
    return any(keyword in text for keyword in NEWS_PAGE_SUPPORT_KEYWORDS)


def _box_geometry(box) -> tuple[float, float, float, float] | None:
    values = [getattr(box, key, None) for key in ("x", "y", "width", "height")]
    if any(value is None for value in values):
        raw = getattr(box, "box", None)
        if raw is None or len(raw) < 4:
            return None
        values = list(raw[:4])
    try:
        x, y, width, height = (float(value) for value in values)
    except (TypeError, ValueError):
        return None
    return x, y, width, height


def news_back_point(
    boxes: object = None,
    width: int = 0,
    height: int = 0,
) -> tuple[float, float]:
    """Return the relative position of the 新闻/公告 back arrow.

    The arrow sits on the same row as the title, just left of it, so the OCR
    title box gives a scale-independent anchor. Without a usable title box the
    calibrated constant is used instead of guessing a fresh position.
    """
    if boxes and width > 0 and height > 0:
        best: tuple[float, float] | None = None
        for box in boxes:
            name = normalize_ocr_text(
                getattr(box, "name", ""), alnum_only=True,
            )
            if "新闻" not in name and "公告" not in name:
                continue
            geometry = _box_geometry(box)
            if geometry is None:
                continue
            x, y, _box_width, box_height = geometry
            if box_height <= 0:
                continue
            center_y = (y + box_height / 2) / height
            if center_y > NEWS_TITLE_MAX_RELATIVE_Y:
                continue
            candidate_x = (x - NEWS_BACK_TITLE_OFFSET_RATIO * box_height) / width
            if best is None or center_y < best[1]:
                best = (candidate_x, center_y)
        if best is not None and 0.0 < best[0] < 0.5:
            return best
    return NEWS_BACK_RELATIVE_POINT


def modal_overlay_p95(frame) -> float:
    """Return the whole-frame 95th-percentile gray level (subsampled).

    A modal dialog dims everything behind its panel, so this value collapses
    (measured: 我的小屋 41 -> 分享设置弹窗 11) while a normal page keeps it
    above ``MODAL_OVERLAY_P95_MAX``.
    """
    if frame is None:
        return 0.0
    try:
        gray = to_gray(frame)
    except Exception:
        return 0.0
    if gray is None or getattr(gray, "size", 0) == 0:
        return 0.0
    step = max(1, max(gray.shape[:2]) // MODAL_OVERLAY_DETECT_MAX_SIDE)
    return float(np.percentile(gray[::step, ::step], 95))


def modal_overlay_dimmed(
    frame,
    threshold: float = MODAL_OVERLAY_P95_MAX,
) -> bool:
    """Return whether the frame looks like it is behind a dimming modal mask."""
    p95 = modal_overlay_p95(frame)
    if p95 <= 0.0:
        # 空帧/纯黑帧没有信息，不能当成“被遮罩压暗”而去点任何按钮。
        return False
    return p95 < float(threshold)


def modal_cancel_point(
    boxes: object,
    frame_w: int,
    frame_h: int,
) -> tuple[float, float] | None:
    """Locate the 取消/关闭 button of a dark modal dialog.

    Only 取消-like text qualifies, and confirmation-like text disqualifies the
    frame entirely: clicking 确认 on an unknown dialog would commit whatever it
    asks about. Among several candidates the lowest one wins, because dialog
    button rows sit at the bottom of the panel.
    """
    if not boxes or frame_w <= 0 or frame_h <= 0:
        return None
    left, top, right, bottom = MODAL_CANCEL_RELATIVE_REGION
    best: tuple[float, float] | None = None
    best_key: tuple[float, float] | None = None
    for box in boxes:
        name = normalize_ocr_text(getattr(box, "name", ""), alnum_only=True)
        if not name or len(name) > MODAL_CANCEL_MAX_TEXT_LENGTH:
            continue
        if any(word in name for word in MODAL_CANCEL_FORBIDDEN_KEYWORDS):
            continue
        if not any(word in name for word in MODAL_CANCEL_KEYWORDS):
            continue
        geometry = _box_geometry(box)
        if geometry is None:
            continue
        x, y, box_width, box_height = geometry
        if box_width <= 0 or box_height <= 0:
            continue
        try:
            confidence = float(getattr(box, "confidence", 1.0))
        except (TypeError, ValueError):
            confidence = 1.0
        if confidence < MODAL_CANCEL_MIN_CONFIDENCE:
            continue
        center_x = (x + box_width / 2) / float(frame_w)
        center_y = (y + box_height / 2) / float(frame_h)
        if not (left <= center_x <= right and top <= center_y <= bottom):
            continue
        key = (center_y, confidence)
        if best_key is None or key > best_key:
            best_key = key
            best = (center_x, center_y)
    return best
