"""Recognition and daily completion evidence for the equipment tasks."""

import re
from functools import cached_property
from pathlib import Path

import cv2
import numpy as np

from src.tasks import run_history
from src.utils.ocr_utils import normalize_ocr_text

EQUIPMENT_DAILY_NAME = "装备分解与精炼"
EQUIPMENT_DAILY_OWNER = "EquipmentDailyTask"
DISMANTLE_STEP = "分解装备一次"
REFINE_STEP = "精炼装备一次"
EQUIPMENT_STEPS = (DISMANTLE_STEP, REFINE_STEP)
_GRADE_ASSETS = (
    Path(__file__).resolve().parents[2] / "recognition-assets/template-assets/equipment_daily"
)
DISMANTLE_GRADE_FILES = {
    "N": _GRADE_ASSETS / "normal_grade.png",
    "R": _GRADE_ASSETS / "rare_grade.png",
    "SR": _GRADE_ASSETS / "super_rare_grade.png",
}
DISMANTLE_SKIPPED = object()
_DISMANTLE_GRADE_THRESHOLDS = {"N": 0.84, "R": 0.86, "SR": 0.80}
# 装备卡片网格（相对坐标），用于限定装备分数的识别范围。
GRID_BOUNDS = (0.36, 0.10, 1.0, 0.94)
# 顶部五个装备分类页签。全部装备视图没有高亮的分类。
CATEGORY_TAB_POINTS = (
    (0.420, 0.093),
    (0.512, 0.093),
    (0.605, 0.093),
    (0.697, 0.093),
    (0.789, 0.093),
)
# 选择页左上角的返回按钮（圆形 ‹）：这个页面的返回图标和通用返回条模板不一样，
# 模板没命中时按这个固定位置点。
SELECTION_BACK_POINT = (0.0865, 0.0405)
BACKPACK_BACK_POINT = (0.0865, 0.046)
REFINEMENT_BACK_POINT = (0.09, 0.046)
# 装备格子的实际排布（8 列 × 6 行，相对坐标）。空格子内部是均匀暗色，
# 有装备的格子会被立绘和文字点亮，滚动时用它判断有没有翻到列表末端。
GRID_CELL_ORIGIN = (0.3776, 0.1245)
GRID_CELL_SIZE = (0.0653, 0.1208)
GRID_COLUMNS = 8
GRID_ROWS = 6
EMPTY_CELL_MEAN = 45.0
REFINE_BALANCE_BOUNDS = (0.775, 0.022, 0.965, 0.082)
REFINE_SCORE_BOUNDS = ((0.875, 0.226, 0.935, 0.292), (0.738, 0.719, 0.803, 0.775))
REFINE_MATERIAL_LABEL = r"所需材料([1-9]\d*)?"
REFINE_MATERIAL_BOUNDS = (0.71, 0.80, 0.789, 0.874)
MAX_REFINABLE_SCORE = 23


def equipment_daily_cycle():
    return str(run_history.day_start_ts())


def equipment_step_done(step, cycle):
    record = run_history.default_store().stage(EQUIPMENT_DAILY_OWNER, step)
    return record.get("state") == "done" and record.get("scope") == cycle


def equipment_daily_done():
    cycle = equipment_daily_cycle()
    return all(equipment_step_done(step, cycle) for step in EQUIPMENT_STEPS)


def equipment_ready_for_season_pass() -> bool:
    """Require both confirmed operations, in order, from the same daily attempt."""
    store = run_history.default_store()
    dismantle = store.stage(EQUIPMENT_DAILY_OWNER, DISMANTLE_STEP)
    refine = store.stage(EQUIPMENT_DAILY_OWNER, REFINE_STEP)
    cycle = equipment_daily_cycle()
    if any(
        record.get("state") != "done" or record.get("scope") != cycle
        for record in (dismantle, refine)
    ):
        return False
    before, after = dismantle["evidence"], refine["evidence"]
    attempt = before.get("attempt")
    return bool(
        attempt
        and attempt == after.get("attempt")
        and before.get("inventory")
        and after.get("remaining")
        and dismantle.get("time", float("inf")) < refine.get("time", 0)
    )


class RefinementConflict(ValueError):
    """Incompatible numeric readings on the same refinement page."""


def refinement_value(label, readings):
    values = sorted({value for value in readings if value is not None})
    if len(values) > 1:
        raise RefinementConflict(f"{label}读数冲突：{values}")
    return values[0] if values else None


class EquipmentScreen:
    def __init__(self, frame, boxes):
        self.frame = frame
        height, width = frame.shape[:2]
        self.labels = [
            (
                normalize_ocr_text(box.name),
                (box.x + box.width / 2) / width,
                (box.y + box.height / 2) / height,
            )
            for box in boxes
            if box.width > 0 and box.height > 0
        ]

    def matches(self, pattern, bounds):
        left, top, right, bottom = bounds
        found = [
            (re.fullmatch(pattern, text), (x, y))
            for text, x, y in self.labels
            if left < x < right and top < y < bottom
        ]
        return [(match, point) for match, point in found if match]

    def find(self, pattern, bounds):
        matches = self.matches(pattern, bounds)
        return matches[0] if len(matches) == 1 else (None, None)

    def _light_button(self, point):
        height, width = self.frame.shape[:2]
        x, y = point
        fill = self.frame[
            round((y - 0.015) * height) : round((y - 0.011) * height),
            round((x - 0.009) * width) : round((x + 0.009) * width),
        ]
        return fill.size > 0 and np.median(fill) > 145

    @property
    def bag_open(self):
        """背包任意大类页面的计数（装备 / 消耗品 / 道具 X/Y）。"""
        return self.inventory is not None or self._bag_count(r"消耗品|道具") is not None

    @property
    def inventory(self):
        return self._bag_count("装备")

    @property
    def special_offer_shop(self):
        labels = {text for text, _, _ in self.labels}
        return {"特别礼包", "内容物详情"} <= labels

    def _bag_count(self, heading):
        bounds = (0.11, 0.01, 0.30, 0.095)
        # The expansion button sometimes merges with the OCR count.
        count_pattern = r"(\d+)/(\d+)[+⊕（(]*"
        combined, _ = self.find(rf"(?:{heading}){count_pattern}", bounds)
        if combined:
            return tuple(map(int, combined.groups()))
        _, title = self.find(heading, bounds)
        count, point = self.find(count_pattern, bounds)
        if title and point and title[0] < point[0] and abs(title[1] - point[1]) < 0.025:
            return tuple(map(int, count.groups()))
        return None

    @property
    def active_category(self):
        """Find the selected category by the blue fill to the left of its icon."""
        height, width = self.frame.shape[:2]
        selected = []
        for x, y in CATEGORY_TAB_POINTS:
            fill = self.frame[
                round((y + 0.002) * height) : round((y + 0.015) * height),
                round((x - 0.030) * width) : round((x - 0.018) * width),
            ]
            if fill.size:
                blue, green, red = np.median(fill, axis=(0, 1))
                if blue >= 22 and blue - green >= 9 and green - red >= 4:
                    selected.append((x, y))
        return selected[0] if len(selected) == 1 else None

    @property
    def dismantle_entry(self):
        if self.inventory is None or self.selection_page or self.dismantle_result_visible:
            return None
        _, point = self.find(r"[一\-―]?键分解", (0.83, 0.90, 0.95, 0.98))
        return point if point and self._light_button(point) else None

    @property
    def selection_page(self):
        """「请选择需要…的装备」选择页（分解/强化/制作共用同一套布局）。"""
        for text, x, y in self.labels:
            if (
                re.fullmatch(r"请选择需要(?:分解|强化|制作|升级)的装备[。.]?", text)
                and 0.30 < x < 0.60
                and 0.87 < y < 0.96
            ):
                return (x, y)
        return None

    @property
    def selection_count(self):
        if self.inventory is None:
            return None
        prompt, _ = self.find(r"请选择需要分解的装备[。.]?", (0.34, 0.89, 0.55, 0.945))
        match, _ = self.find(r"(\d+)/[1-9]\d*", (0.33, 0.93, 0.47, 0.99))
        return int(match.group(1)) if prompt and match else None

    @property
    def dismantle_dialog(self):
        _, cancel = self.find(r"取消", (0.36, 0.57, 0.49, 0.69))
        _, confirm = self.find(r"分解", (0.49, 0.57, 0.62, 0.69))
        if cancel is None or confirm is None or abs(cancel[1] - confirm[1]) > 0.02:
            return None

        # The warning for upgraded equipment changes the height of the dialog.
        button_y = (cancel[1] + confirm[1]) / 2
        _, title = self.find(r"分解", (0.39, button_y - 0.35, 0.56, button_y - 0.13))
        if title is None:
            return None
        count, _ = self.find(
            r".*共分解(\d+)个装备.*",
            (0.31, title[1] + 0.015, 0.68, button_y - 0.075),
        )
        return (int(count.group(1)), confirm) if count else None

    @cached_property
    def _dismantle_result_labels(self):
        _, title = self.find(r"result", (0.41, 0.25, 0.59, 0.36))
        _, count = self.find(r"获得[1-9]\d*种", (0.42, 0.32, 0.58, 0.42))
        _, dismiss = self.find(r"点击画面即可返回[。.]?", (0.39, 0.88, 0.61, 0.97))
        return title, count, dismiss

    @property
    def dismantle_result_visible(self):
        return any(self._dismantle_result_labels)

    @property
    def dismantle_result(self):
        title, count, dismiss = self._dismantle_result_labels
        if dismiss and (title or count):
            return dismiss
        if title and count:
            return title
        return None

    def scored_gear(self):
        """Read eligible score badges with their gear card positions, lowest score first."""
        frame = cv2.resize(self.frame, (1920, 1080), interpolation=cv2.INTER_AREA)
        result = []
        for label, x, y in self.labels:
            if not (
                GRID_BOUNDS[0] < x < GRID_BOUNDS[2]
                and GRID_BOUNDS[1] < y < GRID_BOUNDS[3]
                and label.isascii()
                and label.isdecimal()
                and 6 <= int(label) <= MAX_REFINABLE_SCORE
            ):
                continue
            px, py = round(x * 1920), round(y * 1080)
            badge = frame[py - 19 : py + 19, px - 19 : px + 19].astype(np.int16)
            blue, green, red = badge.transpose(2, 0, 1)
            outline = (red - blue > 27) & (green - blue > 12) & (green > 119)
            if np.count_nonzero(outline) >= 22:
                result.append((int(label), (x - 0.02, y - 0.015)))
        return sorted(result)

    @property
    def detail_refine(self):
        marker, _ = self.find(r"设置自定义标记", (0.25, 0.40, 0.42, 0.49))
        _, button = self.find(r"精炼", (0.65, 0.63, 0.77, 0.73))
        if not marker or not button or not self._light_button(button):
            return None
        level, _ = self.find(r"基本能力\+9", (0.23, 0.45, 0.38, 0.56))
        exclusive, _ = self.find(r"专用装备", (0.23, 0.50, 0.38, 0.58))
        return button if level or exclusive else None

    @property
    def detail_close(self):
        marker, _ = self.find(r"设置自定义标记", (0.25, 0.40, 0.42, 0.49))
        _, close = self.find(r"[x×]", (0.72, 0.27, 0.76, 0.33))
        if marker and close:
            return close
        return None

    @property
    def refinement_page(self):
        title, _ = self.find(r"精炼装备分数", (0.115, 0.01, 0.27, 0.078))
        if title:
            return True
        # The title and item name can remain hidden after the forge animation.
        score, _ = self.find(r"装备分数.*", (0.58, 0.22, 0.71, 0.29))
        material = self.matches(REFINE_MATERIAL_LABEL, REFINE_MATERIAL_BOUNDS)
        trace, _ = self.find(r"精炼痕迹\d*", (0.08, 0.899, 0.205, 0.966))
        return bool(score and material and trace)

    @property
    def refine_balances(self):
        return self.refine_currency_amounts() if self.refinement_page else None

    def refine_currency_amounts(self):
        amount = r"([0-9]+(?:,[0-9]{3})*)"
        powder, _ = self.find(amount, (0.782, 0.026, 0.854, 0.079))
        gold, _ = self.find(amount, (0.865, 0.026, 0.959, 0.079))
        if powder and gold:
            return tuple(int(match.group(1).replace(",", "")) for match in (powder, gold))
        return None

    def refinement_score_badges(self):
        height, width = self.frame.shape[:2]
        for left, top, right, bottom in REFINE_SCORE_BOUNDS:
            badge = self.frame[
                round(top * height) : round(bottom * height),
                round(left * width) : round(right * width),
            ]
            yield cv2.resize(badge, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)

    @property
    def refine_once(self):
        if self.refine_balances is None:
            return None
        score, _ = self.find(r"装备分数.*", (0.58, 0.22, 0.71, 0.29))
        material = self.matches(REFINE_MATERIAL_LABEL, REFINE_MATERIAL_BOUNDS)
        quantities = self.matches(r"([1-9]\d*)", (0.787, 0.80, 0.847, 0.874))
        powder = refinement_value("精炼材料", (int(m[1]) for m, _ in material + quantities if m[1]))
        gold, point = self.find(r"精炼[^\d]{0,4}([1-9]\d*)", (0.78, 0.90, 0.91, 0.97))
        if score and material and powder and gold and self._light_button(point):
            return (powder, int(gold.group(1))), point
        return None


def grid_blank_rows(frame):
    """逐行判断网格里是不是空格子。

    实测空格子内部的灰度只有 24~39，放装备的格子会被立绘点亮，明显更亮。
    返回长度 6 的布尔列表；帧尺寸不对时返回空列表。
    """
    height, width = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    left0, top0 = GRID_CELL_ORIGIN
    cell_width, cell_height = GRID_CELL_SIZE
    rows = []
    for row in range(GRID_ROWS):
        blank = True
        for column in range(GRID_COLUMNS):
            left = int((left0 + cell_width * (column + 0.2)) * width)
            right = int((left0 + cell_width * (column + 0.8)) * width)
            top = int((top0 + cell_height * (row + 0.2)) * height)
            bottom = int((top0 + cell_height * (row + 0.8)) * height)
            cell = gray[top:bottom, left:right]
            if cell.size and float(cell.mean()) >= EMPTY_CELL_MEAN:
                blank = False
                break
        rows.append(blank)
    return rows


def _grade_mask(frame, grade):
    blue, green, red = frame.astype(np.int16).transpose(2, 0, 1)
    if grade == "N":
        mask = (green > 126) & (green - red > 20) & (green - blue > 20) & (red > 54) & (blue > 45)
    elif grade == "R":
        mask = (
            (blue > 165)
            & (green > 120)
            & (green - red > 23)
            & (blue - red > 45)
            & (blue >= green - 8)
        )
    elif grade == "SR":
        mask = (
            (blue > 172)
            & (red > 115)
            & (red - green > 10)
            & (blue - green > 44)
            & (blue - red > 26)
        )
    else:
        raise ValueError(f"Unexpected dismantle grade: {grade}")
    return mask.astype(np.uint8)


def dismantle_grade_points(frame, grade_templates):
    """Find bright N/R/SR badges and order their cards from the bottom right."""
    scaled = cv2.resize(frame, (1920, 1080), interpolation=cv2.INTER_AREA)
    points = []
    for grade, source in grade_templates.items():
        template = _grade_mask(source, grade)
        height, width = template.shape
        for center in range(821, 1704, 126):
            left = center - 44
            rank_strip = _grade_mask(scaled[133:924, left : center + 30], grade)
            scores = cv2.matchTemplate(rank_strip, template, cv2.TM_CCORR_NORMED)
            while True:
                _, score, _, (x, y) = cv2.minMaxLoc(scores)
                if score < _DISMANTLE_GRADE_THRESHOLDS[grade]:
                    break
                badge_x = left + x + width / 2
                badge_y = 133 + y + height / 2
                points.append(((badge_x - 40) / 1920, (badge_y + 44) / 1080))
                scores[max(0, y - height) : y + height, :] = 0
    return sorted(points, key=lambda point: (-round(point[1] / 0.006), -point[0]))


def dismantle_card_selected(frame, point):
    """Verify the gold selection outline on the card clicked by the scanner."""
    scaled = cv2.resize(frame, (1920, 1080), interpolation=cv2.INTER_AREA)
    x, y = round(point[0] * 1920), round(point[1] * 1080)
    if not (66 <= x < 1920 - 66 and 66 <= y < 1080 - 66):
        return False
    patch = scaled[y - 66 : y + 67, x - 66 : x + 67].astype(np.int16)
    blue, green, red = patch.transpose(2, 0, 1)
    gold = (red > 145) & (green > 105) & (red - blue > 25) & (green - blue > 11)
    for edge in (gold[:16, 27:106], gold[-16:, 27:106], gold[27:106, :16].T, gold[27:106, -16:].T):
        if np.count_nonzero(np.any(edge, axis=0)) < 55:
            return False
    return True


def dismantle_check_point(frame):
    """Require the green check mark on the selection toolbar."""
    scaled = cv2.resize(frame, (1920, 1080), interpolation=cv2.INTER_AREA)
    patch = scaled[985:1030, 1735:1805].astype(np.int16)
    blue, green, red = patch.transpose(2, 0, 1)
    mark = (green - red > 19) & (green - blue > 19) & (green > 45)
    y, x = np.where(mark)
    if len(x) < 32 or np.ptp(x) < 12 or np.ptp(y) < 7:
        return None
    return (1735 + float(x.mean())) / 1920, (985 + float(y.mean())) / 1080
