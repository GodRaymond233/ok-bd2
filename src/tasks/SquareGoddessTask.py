import re
import time
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

import cv2
import numpy as np
from qfluentwidgets import FluentIcon

from src.tasks.BaseBD2Task import BaseBD2Task, green_mask_from_template
from src.utils.template_resolution import offline_template_scale

REFERENCE_WIDTH = 1920
REFERENCE_HEIGHT = 1080
MFABD2_REFERENCE_WIDTH = 1280
MFABD2_REFERENCE_HEIGHT = 720
ENTRY_REFERENCE_WIDTH = 2560
ENTRY_REFERENCE_HEIGHT = 1440
SQUARE_CARD_LIST_SWIPE_COUNT = 1
PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = PROJECT_ROOT / "offline-train" / "train-source-screenshots"


@dataclass(frozen=True)
class SquareTemplateSpec:
    name: str
    file_name: str
    threshold_key: str
    default_threshold: float
    roi: tuple[int, int, int, int] | None = None
    green_mask: bool = False


@dataclass(frozen=True)
class SquareMatchResult:
    score: float
    pixel_score: float
    position: tuple[int, int]
    size: tuple[int, int]


class SquareGoddessTask(BaseBD2Task):
    status_keys = [
        "启用",
        "状态",
        "当前阶段",
        "主页亮度",
        "广场选关 OCR",
        "广场左侧滑动 OCR",
        "恶魔城卡带",
        "广场入口卡带",
        "广场入口小图标",
        "广场标签 OCR",
        "广场入场_loading_appear",
        "广场入场_loading_gone",
        "梦幻广场",
        "女神像许愿 OCR",
        "广场每日导航",
        "广场导航文本 OCR",
        "广场导航中",
        "女神像许愿结果",
        "匹配错误",
        "Log",
        "Warning",
        "Error",
    ]

    status_key_labels = {
        "恶魔城卡带": "恶魔城卡带模板",
        "广场入口卡带": "广场卡带模板",
        "广场入口小图标": "广场小图标模板",
        "梦幻广场": "梦幻广场模板",
        "广场每日导航": "每日导航模板",
        "广场导航中": "导航中模板",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "广场女神像"
        self.description = "复用 PVP 入场链路进入梦幻广场，后续补女神像祈祷。"
        self.icon = FluentIcon.GAME
        self.group_name = "日常/周常"
        self.group_icon = FluentIcon.CALENDAR
        self.visible = True
        self._templates: dict[str, tuple[np.ndarray, np.ndarray | None]] = {}
        self._missing_template_names: set[str] = set()
        self._match_error_names: set[str] = set()
        self._match_pause_until = 0.0
        self.default_config.update(
            {
                "启用": True,
                "加载页面阈值": 0.72,
                "主页亮度比例阈值": 0.75,
                "广场 OCR 阈值": 0.2,
                "广场选关页等待秒数": 12.0,
                "广场左侧滑动确认秒数": 6.0,
                "广场入场等待秒数": 30.0,
                "女神像许愿等待秒数": 8.0,
                "女神像导航入口等待秒数": 8.0,
                "女神像导航最长等待秒数": 90.0,
                "女神像许愿最多点击次数": 3,
                "恶魔城卡带阈值": 0.70,
                "广场入口卡带阈值": 0.78,
                "梦幻广场阈值": 0.78,
                "广场每日导航阈值": 0.76,
                "广场导航中阈值": 0.76,
                "loading 出现等待秒数": 6.0,
                "loading 消失等待秒数": 35.0,
            }
        )
        self.config_description.update(
            {
                "广场 OCR 阈值": "广场入场流程 OCR 使用的最低可信度。",
                "恶魔城卡带阈值": "游戏卡珍藏集里定位恶魔城卡带的模板匹配阈值。",
                "广场入口卡带阈值": "游戏卡珍藏集里定位广场卡带的模板匹配阈值。",
                "广场入场等待秒数": "点击广场卡带后等待梦幻广场场景出现的最长时间。",
                "女神像导航最长等待秒数": "点击每日导航后，等待角色靠近女神像的最长时间。",
                "女神像许愿最多点击次数": "OCR 仍识别到许愿提示时最多重复点击几次。",
            }
        )

    def _status_set(self, key: str, value) -> None:
        try:
            self.info_set(key, value)
        except AttributeError:
            pass

    def run(self):
        if not bool(self.config.get("启用", True)):
            self.info_set("状态", "广场女神像已禁用。")
            self.log_info("广场女神像已禁用。")
            return True

        self.info_set("状态", "广场女神像启动。")
        self.log_info("广场女神像：开始从主页进入梦幻广场。")
        if not self._enter_square_from_home():
            self.info_set("状态", "未能进入梦幻广场。")
            return False

        self.info_set("状态", "已进入梦幻广场，开始寻找女神像。")
        if not self._pray_at_goddess():
            self.info_set("状态", "未能完成女神像许愿。")
            self._status_set("女神像许愿结果", "失败")
            return False

        self.info_set("状态", "女神像许愿完成。")
        self._status_set("女神像许愿结果", "完成")
        return True

    def _enter_square_from_home(self) -> bool:
        self.info_set("当前阶段", "确认主页")
        ratio = self._home_brightness_ratio(self.capture_frame())
        self.info_set("主页亮度", f"{ratio:.3f}")
        if ratio < self._home_ratio_threshold():
            self.log_info(f"广场女神像：当前不在主页或主页亮度不足，ratio={ratio:.3f}。")
            return False

        self.info_set("当前阶段", "打开游戏卡珍藏集")
        self._click_entry_reference(2258, 1307, after_sleep=1.0)
        found_cards, text = self._wait_for_ocr_requirements(
            [
                (r"游戏卡珍藏[集级]", 0.90),
                (r"角色游戏卡", 0.70),
                (r"玩法游戏卡", 0.70),
            ],
            timeout=float(self.config.get("广场选关页等待秒数", 12.0)),
            name="广场选关",
        )
        self.info_set("广场选关 OCR", text or "-")
        if not found_cards:
            self.log_info("广场女神像：未确认进入游戏卡选关页面。")
            return False

        self.info_set("当前阶段", "滑动到玩法游戏卡区域")
        for _ in range(SQUARE_CARD_LIST_SWIPE_COUNT):
            self._drag_entry_reference((94, 1067), (94, 333), duration=0.7, after_sleep=0.5)

        cleared, text = self._wait_for_ocr_absent(
            [r"店长游戏卡\s*\d+\s*/\s*\d+", r"剧情游戏卡\s*\d+\s*/\s*20"],
            timeout=float(self.config.get("广场左侧滑动确认秒数", 6.0)),
            name="广场左侧滑动",
        )
        self.info_set("广场左侧滑动 OCR", text or "-")
        if not cleared:
            self.log_info("广场女神像：左侧列表仍检测到店长游戏卡或剧情游戏卡分类标题。")
            return False

        self.info_set("当前阶段", "定位恶魔城卡带")
        reference_card, frame_shape = self._find_template_until(
            REFERENCE_CARD_TEMPLATE,
            timeout=10.0,
            name="恶魔城卡带",
        )
        if reference_card is not None and frame_shape is not None:
            _frame_height, frame_width = frame_shape
            center_x = reference_card.position[0] + reference_card.size[0] // 2
            center_y = reference_card.position[1] + reference_card.size[1] // 2
            target_x = max(1, center_x - frame_width // 2)
            self._drag_client(
                (center_x, center_y), (target_x, center_y), duration=0.8, after_sleep=1.0
            )
        else:
            self.log_info("广场女神像：未检测到恶魔城卡带，直接尝试定位广场卡带。")

        self.info_set("当前阶段", "选择广场卡带")
        square_card, frame_shape = self._find_template_until(
            SQUARE_ENTRY_CARD_TEMPLATE,
            timeout=10.0,
            name="广场入口卡带",
        )
        if square_card is None or frame_shape is None:
            square_card, frame_shape = self._find_template_until(
                SQUARE_QCARD_TEMPLATE,
                timeout=5.0,
                name="广场入口小图标",
            )

        if square_card is None or frame_shape is None:
            square_label, frame_shape = self._find_square_label_until(timeout=5.0, name="广场标签")
            if square_label is None or frame_shape is None:
                self.log_info("广场女神像：未检测到广场卡带，也未通过 OCR 定位广场标签。")
                return False

            frame_height, frame_width = frame_shape
            self._click_client(
                square_label[0],
                square_label[1],
                frame_width,
                frame_height,
                after_sleep=2.0,
            )
        else:
            frame_height, frame_width = frame_shape
            self._click_client(
                square_card.position[0] + square_card.size[0] // 2,
                square_card.position[1] + square_card.size[1] // 2,
                frame_width,
                frame_height,
                after_sleep=2.0,
            )

        self._wait_loading_if_present("广场入场")
        if self._wait_for_template(
            FANTASIA_SQUARE_TEMPLATE,
            timeout=float(self.config.get("广场入场等待秒数", 30.0)),
            name="梦幻广场",
        ):
            return True

        return False

    def _pray_at_goddess(self) -> bool:
        self.info_set("当前阶段", "寻找女神像许愿")
        if self._click_pray_until_gone(timeout=float(self.config.get("女神像许愿等待秒数", 8.0))):
            return True

        self.info_set("当前阶段", "启动女神像导航")
        if not self._start_goddess_navigation(
            timeout=float(self.config.get("女神像导航入口等待秒数", 8.0))
        ):
            self.log_info("广场女神像：未找到女神像每日导航入口。")
            return False

        end_at = monotonic() + float(self.config.get("女神像导航最长等待秒数", 90.0))
        while monotonic() <= end_at:
            if self._click_pray_until_gone(timeout=2.0):
                return True

            if self._wait_for_template(
                SQUARE_MISSION_NAVI_TEMPLATE,
                timeout=2.0,
                name="广场导航中",
            ):
                self.sleep(5.0)
                continue

            self._start_goddess_navigation(timeout=2.0)

        self.log_info("广场女神像：等待女神像许愿提示超时。")
        return False

    def _click_pray_until_gone(self, timeout: float) -> bool:
        clicked = False
        max_clicks = max(1, int(self.config.get("女神像许愿最多点击次数", 3)))
        for click_index in range(max_clicks):
            point, frame_shape, text = self._find_ocr_click_point_until(
                GODDESS_PRAY_PATTERNS,
                timeout=timeout if click_index == 0 else 1.0,
                name="女神像许愿",
                roi=GODDESS_PRAY_OCR_ROI,
            )
            self.info_set("女神像许愿 OCR", text or "-")
            if point is None or frame_shape is None:
                return clicked

            frame_height, frame_width = frame_shape
            self._click_client(point[0], point[1], frame_width, frame_height, after_sleep=2.0)
            clicked = True

        return clicked

    def _start_goddess_navigation(self, timeout: float) -> bool:
        if self._click_template_until(
            SQUARE_DAILY_ICON_TEMPLATE,
            timeout=timeout,
            name="广场每日导航",
            target_offset_mf=(3, 0),
            after_sleep=2.0,
        ):
            return True

        return self._click_ocr_pattern_until(
            GODDESS_NAVIGATION_PATTERNS,
            timeout=timeout,
            name="广场导航文本",
            roi=GODDESS_NAVIGATION_OCR_ROI,
            target_offset_mf=(3, 0),
            after_sleep=2.0,
        )

    def _click_template_until(
        self,
        spec: SquareTemplateSpec,
        timeout: float,
        name: str,
        target_offset_mf: tuple[int, int] = (0, 0),
        after_sleep: float = 0.0,
        interval: float = 0.35,
    ) -> bool:
        end_at = monotonic() + max(0.0, timeout)
        last_score = -1.0
        while monotonic() <= end_at:
            frame = self.capture_frame()
            frame_height, frame_width = frame.shape[:2]
            result = self._match(frame, spec)
            last_score = result.score
            self.info_set(name, f"{result.score:.3f}")
            if self._passes(result, spec):
                offset_x, offset_y = self._mf_offset_for_frame(
                    target_offset_mf[0],
                    target_offset_mf[1],
                    frame_width,
                    frame_height,
                )
                x = result.position[0] + result.size[0] // 2 + offset_x
                y = result.position[1] + result.size[1] // 2 + offset_y
                self._click_client(x, y, frame_width, frame_height, after_sleep=after_sleep)
                return True
            self.sleep(interval)

        self.info_set(name, f"{last_score:.3f}")
        return False

    def _click_ocr_pattern_until(
        self,
        patterns: list[str],
        timeout: float,
        name: str,
        roi: tuple[int, int, int, int] | None = None,
        target_offset_mf: tuple[int, int] = (0, 0),
        after_sleep: float = 0.0,
        interval: float = 0.5,
    ) -> bool:
        end_at = monotonic() + max(0.0, timeout)
        last_text = ""
        while monotonic() <= end_at:
            frame = self.capture_frame()
            frame_height, frame_width = frame.shape[:2]
            point, text = self._ocr_pattern_click_point(frame, patterns, name=name, roi=roi)
            last_text = text or last_text
            self.info_set(f"{name} OCR", text or "-")
            if point is not None:
                offset_x, offset_y = self._mf_offset_for_frame(
                    target_offset_mf[0],
                    target_offset_mf[1],
                    frame_width,
                    frame_height,
                )
                self._click_client(
                    point[0] + offset_x,
                    point[1] + offset_y,
                    frame_width,
                    frame_height,
                    after_sleep=after_sleep,
                )
                return True
            self.sleep(interval)

        self.info_set(f"{name} OCR", last_text or "-")
        return False

    def _find_ocr_click_point_until(
        self,
        patterns: list[str],
        timeout: float,
        name: str,
        roi: tuple[int, int, int, int] | None = None,
        interval: float = 0.5,
    ) -> tuple[tuple[int, int] | None, tuple[int, int] | None, str]:
        end_at = monotonic() + max(0.0, timeout)
        last_text = ""
        while monotonic() <= end_at:
            frame = self.capture_frame()
            frame_height, frame_width = frame.shape[:2]
            point, text = self._ocr_pattern_click_point(frame, patterns, name=name, roi=roi)
            last_text = text or last_text
            if point is not None:
                return point, (frame_height, frame_width), text
            self.sleep(interval)

        return None, None, last_text

    def _ocr_pattern_click_point(
        self,
        frame,
        patterns: list[str],
        name: str,
        roi: tuple[int, int, int, int] | None = None,
    ) -> tuple[tuple[int, int] | None, str]:
        left, top, _crop = self._roi_frame(frame, roi)
        boxes = self._ocr_boxes(frame, name=name, roi=roi)
        text = " ".join(getattr(box, "name", "") for box in boxes if getattr(box, "name", ""))
        matched_boxes = [
            box for box in boxes if self._matches_any(getattr(box, "name", ""), patterns)
        ]
        if not matched_boxes and text and self._matches_any(text, patterns):
            matched_boxes = [box for box in boxes if getattr(box, "name", "")]
        if not matched_boxes:
            return None, text

        box = min(
            matched_boxes,
            key=lambda item: (
                float(getattr(item, "y", 0)),
                float(getattr(item, "x", 0)),
            ),
        )
        x = getattr(box, "x", None)
        y = getattr(box, "y", None)
        width = getattr(box, "width", None)
        height = getattr(box, "height", None)
        if None in (x, y, width, height):
            return None, text

        center_x = int(round(left + float(x) + float(width) / 2))
        center_y = int(round(top + float(y) + float(height) / 2))
        return (center_x, center_y), text

    def _find_template_until(
        self,
        spec: SquareTemplateSpec,
        timeout: float,
        name: str,
        interval: float = 0.35,
    ) -> tuple[SquareMatchResult | None, tuple[int, int] | None]:
        end_at = monotonic() + max(0.0, timeout)
        last_score = -1.0
        while monotonic() <= end_at:
            frame = self.capture_frame()
            result = self._match(frame, spec)
            last_score = result.score
            self.info_set(name, f"{result.score:.3f}")
            if self._passes(result, spec):
                frame_height, frame_width = frame.shape[:2]
                return result, (frame_height, frame_width)
            self.sleep(interval)

        self.info_set(name, f"{last_score:.3f}")
        return None, None

    def _find_square_label_until(
        self,
        timeout: float,
        name: str,
        interval: float = 0.5,
    ) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
        end_at = monotonic() + max(0.0, timeout)
        last_text = ""
        while monotonic() <= end_at:
            frame = self.capture_frame()
            frame_height, frame_width = frame.shape[:2]
            boxes = self._ocr_boxes(frame, name=name)
            text = " ".join(getattr(box, "name", "") for box in boxes if getattr(box, "name", ""))
            last_text = text or last_text
            self.info_set(f"{name} OCR", text or "-")
            point = self._square_label_click_point(boxes, frame_width, frame_height)
            if point is not None:
                return point, (frame_height, frame_width)
            self.sleep(interval)

        self.info_set(f"{name} OCR", last_text or "-")
        return None, None

    def _wait_for_template(
        self,
        spec: SquareTemplateSpec,
        timeout: float,
        name: str,
        interval: float = 0.35,
    ) -> bool:
        end_at = monotonic() + max(0.0, timeout)
        last_score = -1.0
        while monotonic() <= end_at:
            frame = self.capture_frame()
            result = self._match(frame, spec)
            last_score = result.score
            self.info_set(name, f"{result.score:.3f}")
            if self._passes(result, spec):
                return True
            self.sleep(interval)

        self.info_set(name, f"{last_score:.3f}")
        return False

    def _wait_for_ocr_requirements(
        self,
        requirements: list[tuple[str, float]],
        timeout: float,
        name: str,
        roi: tuple[int, int, int, int] | None = None,
        interval: float = 0.5,
    ) -> tuple[bool, str]:
        end_at = monotonic() + max(0.0, timeout)
        last_text = ""
        while monotonic() <= end_at:
            frame = self.capture_frame()
            entries = self._ocr_entries(frame, name=name, roi=roi)
            text = " ".join(label for label, _confidence in entries)
            last_text = text or last_text
            self.info_set(f"{name} OCR", text or "-")
            if self._ocr_requirements_met(entries, requirements):
                return True, text
            self.sleep(interval)

        return False, last_text

    def _wait_for_ocr_absent(
        self,
        patterns: list[str],
        timeout: float,
        name: str,
        roi: tuple[int, int, int, int] | None = None,
        interval: float = 0.5,
    ) -> tuple[bool, str]:
        end_at = monotonic() + max(0.0, timeout)
        last_text = ""
        while monotonic() <= end_at:
            frame = self.capture_frame()
            text = self._ocr_text(frame, name=name, roi=roi)
            last_text = text or last_text
            self.info_set(f"{name} OCR", text or "-")
            if text and not self._matches_any(text, patterns):
                return True, text
            self.sleep(interval)

        return False, last_text

    def _wait_loading_if_present(self, name: str, interval: float = 0.5) -> None:
        found_loading = self._wait_for_template(
            LOADING_TEMPLATE,
            timeout=float(self.config.get("loading 出现等待秒数", 6.0)),
            name=f"{name}_loading_appear",
            interval=interval,
        )
        if not found_loading:
            return

        end_at = monotonic() + float(self.config.get("loading 消失等待秒数", 35.0))
        while monotonic() <= end_at:
            frame = self.capture_frame()
            result = self._match(frame, LOADING_TEMPLATE)
            self.info_set(f"{name}_loading_gone", f"{result.score:.3f}")
            if not self._passes(result, LOADING_TEMPLATE):
                return
            self.sleep(interval)

    def _match(self, frame, spec: SquareTemplateSpec) -> SquareMatchResult:
        empty = SquareMatchResult(score=-1.0, pixel_score=-1.0, position=(0, 0), size=(0, 0))
        if monotonic() < self._match_pause_until:
            return empty

        try:
            template, mask = self._load_template(spec)
        except RuntimeError as exc:
            if spec.name not in self._missing_template_names:
                self._missing_template_names.add(spec.name)
                self.log_warning(str(exc), notify=True)
            return empty

        try:
            frame_gray = self._to_gray(frame)
            roi_left, roi_top, roi_frame = self._roi_frame(frame_gray, spec.roi)
            frame_height, frame_width = roi_frame.shape[:2]
            base_scale = offline_template_scale(
                spec.file_name,
                frame_gray.shape[1],
                frame_gray.shape[0],
            )
            best = empty

            for scale in self._candidate_scales(base_scale):
                scaled_template = self._resize_template(template, scale)
                scaled_mask = self._resize_mask(mask, scale) if mask is not None else None
                height, width = scaled_template.shape[:2]
                if height < 5 or width < 5 or height > frame_height or width > frame_width:
                    continue

                method = cv2.TM_CCORR_NORMED if scaled_mask is not None else cv2.TM_CCOEFF_NORMED
                result = cv2.matchTemplate(roi_frame, scaled_template, method, mask=scaled_mask)
                _, max_value, _, max_location = cv2.minMaxLoc(result)
                if max_value > best.score:
                    x, y = int(max_location[0]), int(max_location[1])
                    region = roi_frame[y : y + height, x : x + width]
                    best = SquareMatchResult(
                        score=float(max_value),
                        pixel_score=self._pixel_similarity(region, scaled_template, scaled_mask),
                        position=(roi_left + x, roi_top + y),
                        size=(int(width), int(height)),
                    )
        except (cv2.error, MemoryError) as exc:
            self._match_pause_until = monotonic() + 2.0
            message = f"图像匹配内存不足，暂停识别2秒：{spec.name}"
            self.info_set("匹配错误", message)
            if spec.name not in self._match_error_names:
                self._match_error_names.add(spec.name)
                self.log_warning(f"{message}；{exc}", notify=True)
            return empty

        return best

    def _home_brightness_ratio(self, frame) -> float:
        return max(self._home_brightness_ratio_for_template(frame, spec) for spec in HOME_TEMPLATES)

    def _home_brightness_ratio_for_template(self, frame, spec: SquareTemplateSpec) -> float:
        template, mask = self._load_template(spec)
        frame_gray = self._to_gray(frame)
        frame_height, frame_width = frame_gray.shape[:2]
        scale = offline_template_scale(spec.file_name, frame_width, frame_height)
        template_height, template_width = template.shape[:2]
        roi_width = max(8, round(template_width * scale))
        roi_height = max(8, round(template_height * scale))
        center_x = round(frame_width * (222 / ENTRY_REFERENCE_WIDTH))
        center_y = round(frame_height * (211 / ENTRY_REFERENCE_HEIGHT))
        left = max(0, center_x - roi_width // 2)
        top = max(0, center_y - roi_height // 2)
        right = min(frame_width, left + roi_width)
        bottom = min(frame_height, top + roi_height)
        region = frame_gray[top:bottom, left:right]
        if region.size == 0:
            return 0.0

        scaled_template = self._resize_template(template, scale)
        scaled_mask = self._resize_mask(mask, scale) if mask is not None else None
        match_height = min(region.shape[0], scaled_template.shape[0])
        match_width = min(region.shape[1], scaled_template.shape[1])
        if match_height <= 0 or match_width <= 0:
            return 0.0
        region = region[:match_height, :match_width]
        scaled_template = scaled_template[:match_height, :match_width]
        if scaled_mask is not None:
            scaled_mask = scaled_mask[:match_height, :match_width]
            active = scaled_mask > 0
            if not np.any(active):
                return 0.0
            template_mean = float(np.mean(scaled_template[active]))
            region_mean = float(np.mean(region[active]))
        else:
            template_mean = float(np.mean(scaled_template))
            region_mean = float(np.mean(region))
        if template_mean <= 0:
            return 0.0
        return float(region_mean / template_mean)

    def _load_template(self, spec: SquareTemplateSpec) -> tuple[np.ndarray, np.ndarray | None]:
        if spec.name in self._templates:
            return self._templates[spec.name]

        path = TEMPLATE_DIR / spec.file_name
        raw = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if raw is None:
            raise RuntimeError(f"广场女神像模板不存在或无法读取：{path}")

        mask = green_mask_from_template(raw) if spec.green_mask else None
        template = self._to_gray(raw)
        if mask is not None and np.count_nonzero(mask) == mask.size:
            mask = None

        self._templates[spec.name] = (template, mask)
        return self._templates[spec.name]

    def _passes(self, result: SquareMatchResult, spec: SquareTemplateSpec) -> bool:
        threshold = float(self.config.get(spec.threshold_key, spec.default_threshold))
        return result.score >= threshold

    def _ocr_text(
        self,
        frame,
        name: str,
        roi: tuple[int, int, int, int] | None = None,
    ) -> str:
        return " ".join(label for label, _confidence in self._ocr_entries(frame, name, roi))

    def _ocr_entries(
        self,
        frame,
        name: str,
        roi: tuple[int, int, int, int] | None = None,
    ) -> list[tuple[str, float]]:
        boxes = self._ocr_boxes(frame, name=name, roi=roi)
        entries = []
        for box in boxes:
            label = getattr(box, "name", "")
            if not label:
                continue
            confidence = float(getattr(box, "confidence", 1.0))
            if confidence > 1.0:
                confidence /= 100.0
            entries.append((label, confidence))
        return entries

    def _ocr_boxes(
        self,
        frame,
        name: str,
        roi: tuple[int, int, int, int] | None = None,
    ):
        ocr_frame = self._crop_reference(frame, roi) if roi is not None else frame
        try:
            return self.ocr(
                frame=ocr_frame,
                threshold=float(self.config.get("广场 OCR 阈值", 0.2)),
                target_height=720,
                log=False,
                name=name,
            )
        except Exception as exc:
            self.info_set(f"{name} OCR 错误", str(exc))
            return []

    def _click_entry_reference(self, x: int, y: int, after_sleep: float = 0.0):
        self.operate_click(
            max(0.0, min(1.0, x / ENTRY_REFERENCE_WIDTH)),
            max(0.0, min(1.0, y / ENTRY_REFERENCE_HEIGHT)),
            after_sleep=after_sleep,
        )

    def _click_client(
        self,
        x: int,
        y: int,
        frame_width: int,
        frame_height: int,
        after_sleep: float = 0.0,
    ):
        self.operate_click(
            max(0.0, min(1.0, x / max(1, frame_width))),
            max(0.0, min(1.0, y / max(1, frame_height))),
            after_sleep=after_sleep,
        )

    def _drag_entry_reference(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
        duration: float = 0.7,
        after_sleep: float = 0.0,
    ) -> None:
        frame = self.capture_frame()
        frame_height, frame_width = frame.shape[:2]
        start_client = (
            round(frame_width * start[0] / ENTRY_REFERENCE_WIDTH),
            round(frame_height * start[1] / ENTRY_REFERENCE_HEIGHT),
        )
        end_client = (
            round(frame_width * end[0] / ENTRY_REFERENCE_WIDTH),
            round(frame_height * end[1] / ENTRY_REFERENCE_HEIGHT),
        )
        self._drag_client(start_client, end_client, duration=duration, after_sleep=after_sleep)

    def _drag_client(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
        duration: float = 0.7,
        after_sleep: float = 0.0,
    ) -> None:
        def action():
            import win32api
            import win32con

            interaction = getattr(self.executor, "interaction", None)
            if interaction is not None and hasattr(interaction, "force_activate"):
                interaction.force_activate()
            elif interaction is not None and hasattr(interaction, "try_activate"):
                interaction.try_activate()

            capture = getattr(interaction, "capture", None)

            def to_screen(point: tuple[int, int]) -> tuple[int, int]:
                if capture is not None and hasattr(capture, "get_abs_cords"):
                    return capture.get_abs_cords(point[0], point[1])
                return point

            start_abs = to_screen(start)
            end_abs = to_screen(end)
            steps = max(6, round(duration / 0.03))
            win32api.SetCursorPos(start_abs)
            time.sleep(0.03)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            try:
                for index in range(1, steps + 1):
                    ratio = index / steps
                    x = round(start_abs[0] + (end_abs[0] - start_abs[0]) * ratio)
                    y = round(start_abs[1] + (end_abs[1] - start_abs[1]) * ratio)
                    win32api.SetCursorPos((x, y))
                    time.sleep(duration / steps)
            finally:
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

        self.operate(action, block=True, restore_cursor=True)
        self.sleep(after_sleep)

    def _home_ratio_threshold(self) -> float:
        return float(self.config.get("主页亮度比例阈值", 0.75))

    @staticmethod
    def _mf_point(x: int, y: int) -> tuple[int, int]:
        return (
            round(x * REFERENCE_WIDTH / MFABD2_REFERENCE_WIDTH),
            round(y * REFERENCE_HEIGHT / MFABD2_REFERENCE_HEIGHT),
        )

    @staticmethod
    def _mf_roi(x: int, y: int, width: int, height: int) -> tuple[int, int, int, int]:
        left, top = SquareGoddessTask._mf_point(x, y)
        right, bottom = SquareGoddessTask._mf_point(x + width, y + height)
        return left, top, max(1, right - left), max(1, bottom - top)

    @staticmethod
    def _mf_offset_for_frame(
        x: int,
        y: int,
        frame_width: int,
        frame_height: int,
    ) -> tuple[int, int]:
        return (
            round(x * frame_width / MFABD2_REFERENCE_WIDTH),
            round(y * frame_height / MFABD2_REFERENCE_HEIGHT),
        )

    def _ocr_requirements_met(
        self,
        entries: list[tuple[str, float]],
        requirements: list[tuple[str, float]],
    ) -> bool:
        combined_text = " ".join(label for label, _confidence in entries)
        for pattern, min_confidence in requirements:
            if not self._ocr_requirement_met(entries, combined_text, pattern, min_confidence):
                return False
        return True

    def _ocr_requirement_met(
        self,
        entries: list[tuple[str, float]],
        combined_text: str,
        pattern: str,
        min_confidence: float,
    ) -> bool:
        normalized_pattern = self._normalize_text(pattern)
        for label, confidence in entries:
            if re.search(normalized_pattern, self._normalize_text(label), flags=re.IGNORECASE):
                return confidence >= min_confidence

        if not re.search(
            normalized_pattern,
            self._normalize_text(combined_text),
            flags=re.IGNORECASE,
        ):
            return False
        return any(confidence >= min_confidence for _label, confidence in entries)

    @staticmethod
    def _matches_any(text: str, patterns: list[str]) -> bool:
        normalized = SquareGoddessTask._normalize_text(text)
        for pattern in patterns:
            normalized_pattern = SquareGoddessTask._normalize_text(pattern)
            if re.search(normalized_pattern, normalized, flags=re.IGNORECASE):
                return True
        return False

    @staticmethod
    def _square_label_click_point(
        boxes,
        frame_width: int,
        frame_height: int,
    ) -> tuple[int, int] | None:
        candidates = []
        for box in boxes:
            text = SquareGoddessTask._normalize_text(getattr(box, "name", ""))
            if not text or "广场" not in text:
                continue
            x = getattr(box, "x", None)
            y = getattr(box, "y", None)
            width = getattr(box, "width", None)
            height = getattr(box, "height", None)
            if None in (x, y, width, height):
                continue

            center_x = int(round(float(x) + float(width) / 2))
            center_y = int(round(float(y) + float(height) / 2))
            if center_y < frame_height * 0.50:
                continue
            candidates.append((center_x, center_y, float(x)))

        if not candidates:
            return None

        center_x, center_y, _left = min(candidates, key=lambda item: item[2])
        click_y = int(round(center_y - frame_height * 0.085))
        return center_x, max(0, click_y)

    @staticmethod
    def _normalize_text(text: str) -> str:
        return "".join(str(text).lower().split())

    @staticmethod
    def _candidate_scales(base_scale: float) -> list[float]:
        return [round(max(0.2, base_scale), 3)]

    @staticmethod
    def _resize_template(template: np.ndarray, scale: float) -> np.ndarray:
        if abs(scale - 1.0) < 0.001:
            return template
        interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
        return cv2.resize(template, None, fx=scale, fy=scale, interpolation=interpolation)

    @staticmethod
    def _resize_mask(mask: np.ndarray | None, scale: float) -> np.ndarray | None:
        if mask is None:
            return None
        if abs(scale - 1.0) < 0.001:
            return mask
        resized = cv2.resize(mask, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
        return np.where(resized > 0, 255, 0).astype(np.uint8)

    @staticmethod
    def _to_gray(frame) -> np.ndarray:
        if len(frame.shape) == 2:
            return frame
        if frame.shape[2] == 4:
            return cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY)
        return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    @staticmethod
    def _pixel_similarity(
        region: np.ndarray,
        template: np.ndarray,
        mask: np.ndarray | None = None,
    ) -> float:
        if region.shape != template.shape:
            return -1.0
        diff = np.abs(region.astype(np.float32) - template.astype(np.float32))
        if mask is not None:
            active = mask > 0
            if not np.any(active):
                return -1.0
            diff = diff[active]
        return float(1.0 - np.mean(diff) / 255.0)

    @staticmethod
    def _roi_frame(
        frame: np.ndarray,
        roi: tuple[int, int, int, int] | None,
    ) -> tuple[int, int, np.ndarray]:
        if roi is None:
            return 0, 0, frame
        height, width = frame.shape[:2]
        x, y, w, h = roi
        scale_x = width / REFERENCE_WIDTH
        scale_y = height / REFERENCE_HEIGHT
        left = max(0, round(x * scale_x))
        top = max(0, round(y * scale_y))
        right = min(width, round((x + w) * scale_x))
        bottom = min(height, round((y + h) * scale_y))
        return left, top, frame[top:bottom, left:right]

    @staticmethod
    def _crop_reference(frame, roi: tuple[int, int, int, int] | None):
        if roi is None:
            return frame
        _, _, crop = SquareGoddessTask._roi_frame(frame, roi)
        return crop


LOADING_TEMPLATE = SquareTemplateSpec(
    name="loading",
    file_name="loading.png",
    threshold_key="加载页面阈值",
    default_threshold=0.72,
)

HOME_TEMPLATE = SquareTemplateSpec(
    name="home",
    file_name="home.png",
    threshold_key="主页亮度比例阈值",
    default_threshold=0.75,
)

HOME_ICE_TEMPLATE = SquareTemplateSpec(
    name="home_ice",
    file_name="MainHomeIceGE.png",
    threshold_key="主页亮度比例阈值",
    default_threshold=0.75,
    green_mask=True,
)

HOME_RICE_TEMPLATE = SquareTemplateSpec(
    name="home_rice",
    file_name="MainHomeRIceGE.png",
    threshold_key="主页亮度比例阈值",
    default_threshold=0.75,
    green_mask=True,
)

HOME_TEMPLATES = (HOME_TEMPLATE, HOME_ICE_TEMPLATE, HOME_RICE_TEMPLATE)

REFERENCE_CARD_TEMPLATE = SquareTemplateSpec(
    name="reference_card",
    file_name="Q_evilcastle.png",
    threshold_key="恶魔城卡带阈值",
    default_threshold=0.70,
)

SQUARE_ENTRY_CARD_TEMPLATE = SquareTemplateSpec(
    name="square_entry_card",
    file_name="Q_square.png",
    threshold_key="广场入口卡带阈值",
    default_threshold=0.78,
)

SQUARE_QCARD_TEMPLATE = SquareTemplateSpec(
    name="square_qcard",
    file_name="image/Qcard_Square.png",
    threshold_key="广场入口卡带阈值",
    default_threshold=0.78,
    roi=SquareGoddessTask._mf_roi(18, 588, 1227, 79),
    green_mask=True,
)

FANTASIA_SQUARE_TEMPLATE = SquareTemplateSpec(
    name="fantasia_square",
    file_name="image/Mirror_FantasiaSquare_Ico.png",
    threshold_key="梦幻广场阈值",
    default_threshold=0.78,
    roi=SquareGoddessTask._mf_roi(656, 622, 77, 66),
)

SQUARE_DAILY_ICON_TEMPLATE = SquareTemplateSpec(
    name="square_daily_icon",
    file_name="image/Square_DailyIco.png",
    threshold_key="广场每日导航阈值",
    default_threshold=0.76,
    roi=SquareGoddessTask._mf_roi(968, 76, 56, 256),
)

SQUARE_MISSION_NAVI_TEMPLATE = SquareTemplateSpec(
    name="square_mission_navigation",
    file_name="image/Square_misstion_Nvi.png",
    threshold_key="广场导航中阈值",
    default_threshold=0.76,
    roi=SquareGoddessTask._mf_roi(1168, 106, 69, 247),
)

GODDESS_NAVIGATION_OCR_ROI = SquareGoddessTask._mf_roi(968, 76, 203, 256)
GODDESS_PRAY_OCR_ROI = SquareGoddessTask._mf_roi(545, 75, 201, 91)
GODDESS_NAVIGATION_PATTERNS = [r"(?=.*移动至)(?=.*艾力克史)(?=.*温女)"]
GODDESS_PRAY_PATTERNS = [r"向女神像许愿|女神像许愿|许愿"]
