import re
import time
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

import cv2
import numpy as np
from qfluentwidgets import FluentIcon

from src.tasks.BaseBD2Task import BaseBD2Task, green_mask_from_template
from src.utils.image_utils import (
    best_pixel_valid_match,
    candidate_scales,
    pixel_similarity,
    reference_roi_frame,
    resize_mask,
    resize_template,
    stabilize_template_match,
    template_match_response,
    to_gray,
)
from src.utils.ocr_utils import normalize_ocr_text
from src.utils.template_resolution import (
    offline_template_requires_green_mask,
    offline_template_scale,
    offline_template_search_region,
    offline_template_uses_main_region,
)

REFERENCE_WIDTH = 1920
REFERENCE_HEIGHT = 1080
MFABD2_REFERENCE_WIDTH = 1280
MFABD2_REFERENCE_HEIGHT = 720
ENTRY_REFERENCE_WIDTH = 2560
ENTRY_REFERENCE_HEIGHT = 1440
SQUARE_CARD_LIST_SWIPE_COUNT = 1
GAMEPLAY_CARTRIDGE_POINT = (989 / REFERENCE_WIDTH, 875 / REFERENCE_HEIGHT)
SQUARE_CARTRIDGE_SLOT_POINT = (1230 / REFERENCE_WIDTH, 970 / REFERENCE_HEIGHT)
SQUARE_HOME_POINT = (1797 / REFERENCE_WIDTH, 63 / REFERENCE_HEIGHT)
QUICK_SWITCH_PAGE_PATTERNS = (
    r"店长游戏卡",
    r"角色游戏卡",
    r"玩法游戏卡",
    r"活动游戏卡",
)
GAMEPLAY_CATEGORY_HIGHLIGHT_REGION = (
    876 / REFERENCE_WIDTH,
    840 / REFERENCE_HEIGHT,
    1101 / REFERENCE_WIDTH,
    915 / REFERENCE_HEIGHT,
)
GAMEPLAY_CATEGORY_OCR_ROI = (876, 840, 225, 75)
GAMEPLAY_CATEGORY_HIGHLIGHT_MIN_RATIO = 0.05
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
    scale_ratios: tuple[float, ...] = (1.0,)
    min_pixel_score: float | None = None
    candidate_center_roi: tuple[float, float, float, float] | None = None
    minimum_safe_threshold: float | None = None


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
        "主页小屋按钮",
        "主页亮度",
        "快速切换按钮",
        "卡带选择页 OCR",
        "卡带选择页 OCR 命中",
        "玩法游戏卡 OCR",
        "玩法类别高亮",
        "梦幻广场",
        "广场感叹号",
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
        "梦幻广场": "梦幻广场模板",
        "广场每日导航": "每日导航模板",
        "广场导航中": "导航中模板",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "广场女神像"
        self.description = "从快速切换页的玩法游戏卡7号位进入梦幻广场并完成女神像许愿。"
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
                "主页亮度比例阈值": 0.75,
                "主页确认等待秒数": 10.0,
                "主页小屋按钮阈值": 0.70,
                "快速卡带等待秒数": 10.0,
                "快速切换按钮阈值": 0.90,
                "卡带选择页确认等待秒数": 10.0,
                "玩法类别高亮确认秒数": 3.0,
                "玩法类别高亮像素比例": GAMEPLAY_CATEGORY_HIGHLIGHT_MIN_RATIO,
                "广场 OCR 阈值": 0.2,
                "广场入场等待秒数": 30.0,
                "女神像许愿等待秒数": 8.0,
                "女神像导航入口等待秒数": 8.0,
                "女神像导航最长等待秒数": 90.0,
                "女神像许愿最多点击次数": 3,
                "广场返回主页等待秒数": 15.0,
                "广场感叹号阈值": 0.72,
                "梦幻广场阈值": 0.78,
                "广场每日导航阈值": 0.76,
                "广场导航中阈值": 0.76,
            }
        )
        self.config_description.update(
            {
                "广场 OCR 阈值": "广场入场流程 OCR 使用的最低可信度。",
                "主页小屋按钮阈值": "从主页进入卡带前确认小屋按钮存在的阈值。",
                "快速切换按钮阈值": "识别 QuickCartGeadai.png 快速切换按钮的模板匹配阈值。",
                "玩法类别高亮像素比例": "玩法游戏卡标签确认为高亮状态所需的最低亮色像素占比。",
                "广场入场等待秒数": "点击广场卡带后等待梦幻广场场景出现的最长时间。",
                "女神像导航最长等待秒数": "点击每日导航后，等待角色靠近女神像的最长时间。",
                "女神像许愿最多点击次数": "OCR 仍识别到许愿提示时最多重复点击几次。",
                "广场返回主页等待秒数": "许愿完成后点击主页按钮并确认回到主页的最长时间。",
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
        if not self._return_home_from_square():
            self.info_set("状态", "女神像许愿完成，但未能返回主页。")
            return False
        self.info_set("状态", "女神像许愿完成并返回主页。")
        return True

    def _return_home_from_square(self) -> bool:
        self.info_set("当前阶段", "广场返回主页")
        self.operate_click(*SQUARE_HOME_POINT, after_sleep=1.0)
        return self._wait_for_cartridge_home(
            timeout=float(self.config.get("广场返回主页等待秒数", 15.0))
        )

    def _enter_square_from_home(self) -> bool:
        self.info_set("当前阶段", "打开卡带快速切换")
        if not self.open_cartridge_quick_switcher(
            ensure_home=self._wait_for_cartridge_home,
            click_quick_switch=lambda: self._click_template_until(
                QUICK_SWITCH_TEMPLATE,
                timeout=float(self.config.get("快速卡带等待秒数", 10.0)),
                name="快速切换按钮",
                after_sleep=0.0,
                stabilize=True,
            ),
            confirm_quick_switch_page=self._wait_for_quick_switch_page,
        ):
            self.log_info("广场女神像：未能从主页打开卡带快速切换页面。")
            return False

        self.info_set("当前阶段", "选择玩法游戏卡")
        self.sleep(0.5)
        self.operate_click(*GAMEPLAY_CARTRIDGE_POINT, after_sleep=0.0)
        if not self._wait_for_gameplay_category():
            self.log_info("广场女神像：点击后未确认玩法游戏卡类别高亮。")
            return False

        self.info_set("当前阶段", "选择广场卡带7号位")
        self.operate_click(*SQUARE_CARTRIDGE_SLOT_POINT, after_sleep=0.0)

        if self._wait_for_template(
            FANTASIA_SQUARE_TEMPLATE,
            timeout=float(self.config.get("广场入场等待秒数", 30.0)),
            name="梦幻广场",
        ):
            return True

        return False

    def _wait_for_cartridge_home(
        self,
        interval: float = 0.35,
        timeout: float | None = None,
    ) -> bool:
        self.info_set("当前阶段", "确认主页")
        wait_seconds = (
            float(self.config.get("主页确认等待秒数", 10.0))
            if timeout is None
            else max(0.0, float(timeout))
        )
        end_at = monotonic() + wait_seconds
        last_button_score = -1.0
        last_ratio = 0.0
        while monotonic() <= end_at:
            frame = self.capture_frame()
            home_button = max(
                (self._match(frame, spec) for spec in HOME_TEMPLATES),
                key=lambda result: result.score,
            )
            last_button_score = home_button.score
            last_ratio = self._home_brightness_ratio(frame)
            self.info_set("主页小屋按钮", f"{last_button_score:.3f}")
            self.info_set("主页亮度", f"{last_ratio:.3f}")
            if (
                self._passes(home_button, HOME_TEMPLATE)
                and last_ratio >= self._home_ratio_threshold()
            ):
                return True
            self.sleep(interval)

        self.log_info(
            "广场女神像：未确认主页小屋按钮或亮度不足，"
            f"button={last_button_score:.3f}, ratio={last_ratio:.3f}。"
        )
        return False

    def _wait_for_quick_switch_page(self, interval: float = 0.5) -> bool:
        self.info_set("当前阶段", "确认卡带选择页")
        self.sleep(1.0)
        end_at = monotonic() + float(
            self.config.get("卡带选择页确认等待秒数", 10.0)
        )
        last_text = ""
        while monotonic() <= end_at:
            frame = self.capture_frame()
            text = self._ocr_text(frame, name="卡带选择页")
            last_text = text or last_text
            match_count = sum(
                1 for pattern in QUICK_SWITCH_PAGE_PATTERNS if self._matches_any(text, [pattern])
            )
            self.info_set("卡带选择页 OCR", text or "-")
            self.info_set(
                "卡带选择页 OCR 命中",
                f"{match_count}/{len(QUICK_SWITCH_PAGE_PATTERNS)}",
            )
            if match_count == len(QUICK_SWITCH_PAGE_PATTERNS):
                return True
            self.sleep(interval)

        self.log_info(
            "广场女神像：点击快速切换后未确认卡带选择页，"
            f"OCR={last_text or '-'}。"
        )
        return False

    def _wait_for_gameplay_category(self, interval: float = 0.5) -> bool:
        end_at = monotonic() + float(self.config.get("玩法类别高亮确认秒数", 3.0))
        last_text = ""
        last_highlight_ratio = 0.0
        while monotonic() <= end_at:
            frame = self.capture_frame()
            text = self._ocr_text(
                frame,
                name="玩法游戏卡",
                roi=GAMEPLAY_CATEGORY_OCR_ROI,
            )
            last_text = text or last_text
            last_highlight_ratio = self._bright_neutral_ratio(
                frame,
                GAMEPLAY_CATEGORY_HIGHLIGHT_REGION,
            )
            self.info_set("玩法游戏卡 OCR", text or "-")
            self.info_set("玩法类别高亮", f"{last_highlight_ratio:.3f}")
            if (
                self._matches_any(text, [r"玩法游戏卡"])
                and last_highlight_ratio
                >= float(
                    self.config.get(
                        "玩法类别高亮像素比例",
                        GAMEPLAY_CATEGORY_HIGHLIGHT_MIN_RATIO,
                    )
                )
            ):
                return True
            self.sleep(interval)

        self.log_info(
            "广场女神像：未确认玩法游戏卡类别高亮，"
            f"highlight={last_highlight_ratio:.3f}, OCR={last_text or '-'}。"
        )
        return False

    def _pray_at_goddess(self) -> bool:
        self.info_set("当前阶段", "检查广场感叹号")
        self._click_square_notice_if_present()

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

    def _click_square_notice_if_present(self) -> bool:
        frame = self.capture_frame()
        frame_height, frame_width = frame.shape[:2]
        result = self._match(frame, SQUARE_NOTICE_TEMPLATE)
        self.info_set("广场感叹号", f"{result.score:.3f}")
        if not self._passes(result, SQUARE_NOTICE_TEMPLATE):
            return False

        self._click_client(
            result.position[0] + result.size[0] // 2,
            result.position[1] + result.size[1] // 2,
            frame_width,
            frame_height,
            after_sleep=1.0,
        )
        return True

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
        stabilize: bool = False,
    ) -> bool:
        end_at = monotonic() + max(0.0, timeout)
        last_score = -1.0
        while monotonic() <= end_at:
            frame = self.capture_frame()
            frame_height, frame_width = frame.shape[:2]
            result = self._match(frame, spec)
            last_score = result.score
            self.info_set(name, f"{result.score:.3f}/{result.pixel_score:.3f}")
            if self._passes(result, spec):
                stable_center = None
                if stabilize:

                    def sample_match():
                        sampled_frame = self.capture_frame()
                        return self._match(sampled_frame, spec), sampled_frame.shape

                    stabilized = stabilize_template_match(
                        result,
                        frame.shape,
                        sample_match=sample_match,
                        passes=lambda candidate: self._passes(candidate, spec),
                        sleep=self.sleep,
                        on_sample=lambda candidate: self.info_set(
                            name,
                            f"{candidate.score:.3f}/{candidate.pixel_score:.3f}",
                        ),
                    )
                    if stabilized is None:
                        self.info_set(f"{name}稳定识别", "未形成稳定位置")
                        return False
                    consensus, frame_shape = stabilized
                    frame_height, frame_width = frame_shape[:2]
                    stable_center = consensus.center
                    self.info_set(
                        f"{name}稳定识别",
                        (
                            f"center=({stable_center[0]},{stable_center[1]}), "
                            f"hits={consensus.hit_count}/{consensus.sample_count}, "
                            f"match={consensus.average_score:.3f}, "
                            f"pixel={consensus.average_pixel_score:.3f}, "
                            f"spread={consensus.center_spread:.1f}"
                        ),
                    )
                offset_x, offset_y = self._mf_offset_for_frame(
                    target_offset_mf[0],
                    target_offset_mf[1],
                    frame_width,
                    frame_height,
                )
                center_x, center_y = (
                    stable_center
                    if stable_center is not None
                    else (
                        result.position[0] + result.size[0] // 2,
                        result.position[1] + result.size[1] // 2,
                    )
                )
                x = center_x + offset_x
                y = center_y + offset_y
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
            if offline_template_uses_main_region(spec.file_name) or spec.roi is None:
                frame_height, frame_width = frame_gray.shape[:2]
                roi_left, roi_top, roi_right, roi_bottom = offline_template_search_region(
                    spec.file_name,
                    frame_width,
                    frame_height,
                )
                roi_frame = frame_gray[roi_top:roi_bottom, roi_left:roi_right]
            else:
                roi_left, roi_top, roi_frame = self._roi_frame(frame_gray, spec.roi)
            frame_height, frame_width = roi_frame.shape[:2]
            base_scale = offline_template_scale(
                spec.file_name,
                frame_gray.shape[1],
                frame_gray.shape[0],
            )
            best = empty
            configured_threshold = float(
                getattr(self, "config", {}).get(spec.threshold_key, spec.default_threshold)
            )
            template_threshold = max(
                configured_threshold,
                spec.minimum_safe_threshold
                if spec.minimum_safe_threshold is not None
                else configured_threshold,
            )
            center_bounds = None
            if spec.candidate_center_roi is not None:
                full_height, full_width = frame_gray.shape[:2]
                left, top, right, bottom = spec.candidate_center_roi
                center_bounds = (
                    round(full_width * left) - roi_left,
                    round(full_height * top) - roi_top,
                    round(full_width * right) - roi_left,
                    round(full_height * bottom) - roi_top,
                )

            for scale in self._candidate_scales(base_scale, spec.scale_ratios):
                scaled_template = self._resize_template(template, scale)
                scaled_mask = self._resize_mask(mask, scale) if mask is not None else None
                height, width = scaled_template.shape[:2]
                if height < 5 or width < 5 or height > frame_height or width > frame_width:
                    continue

                result = template_match_response(roi_frame, scaled_template, scaled_mask)
                candidate = best_pixel_valid_match(
                    result,
                    roi_frame,
                    scaled_template,
                    scaled_mask,
                    template_threshold=template_threshold,
                    pixel_threshold=(spec.min_pixel_score or 0.0),
                    center_bounds=center_bounds,
                )
                if candidate is None or candidate.score <= best.score:
                    continue
                x, y = candidate.location
                best = SquareMatchResult(
                    score=candidate.score,
                    pixel_score=candidate.pixel_score,
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

        use_green_mask = spec.green_mask or offline_template_requires_green_mask(spec.file_name)
        mask = green_mask_from_template(raw) if use_green_mask else None
        template = self._to_gray(raw)
        if mask is not None and np.count_nonzero(mask) == mask.size:
            mask = None

        self._templates[spec.name] = (template, mask)
        return self._templates[spec.name]

    def _passes(self, result: SquareMatchResult, spec: SquareTemplateSpec) -> bool:
        threshold = float(self.config.get(spec.threshold_key, spec.default_threshold))
        if spec.minimum_safe_threshold is not None:
            threshold = max(threshold, spec.minimum_safe_threshold)
        if result.score < threshold:
            return False
        if spec.min_pixel_score is None:
            return True
        return result.pixel_score >= spec.min_pixel_score

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

    _normalize_text = staticmethod(normalize_ocr_text)
    _candidate_scales = staticmethod(candidate_scales)
    _resize_template = staticmethod(resize_template)
    _resize_mask = staticmethod(resize_mask)
    _to_gray = staticmethod(to_gray)
    _pixel_similarity = staticmethod(pixel_similarity)

    @staticmethod
    def _roi_frame(
        frame: np.ndarray,
        roi: tuple[int, int, int, int] | None,
    ) -> tuple[int, int, np.ndarray]:
        return reference_roi_frame(frame, roi, (REFERENCE_WIDTH, REFERENCE_HEIGHT))

    @staticmethod
    def _crop_reference(frame, roi: tuple[int, int, int, int] | None):
        return reference_roi_frame(frame, roi, (REFERENCE_WIDTH, REFERENCE_HEIGHT))[2]

    @staticmethod
    def _bright_neutral_ratio(
        frame: np.ndarray,
        relative_roi: tuple[float, float, float, float],
        minimum_gray: int = 170,
        maximum_channel_spread: int = 35,
    ) -> float:
        frame_height, frame_width = frame.shape[:2]
        left = max(0, min(frame_width, round(relative_roi[0] * frame_width)))
        top = max(0, min(frame_height, round(relative_roi[1] * frame_height)))
        right = max(left, min(frame_width, round(relative_roi[2] * frame_width)))
        bottom = max(top, min(frame_height, round(relative_roi[3] * frame_height)))
        region = frame[top:bottom, left:right]
        if region.size == 0:
            return 0.0
        if region.ndim == 2:
            return float(np.mean(region >= minimum_gray))
        color = region[..., :3].astype(np.int16)
        channel_min = np.min(color, axis=2)
        channel_spread = np.max(color, axis=2) - channel_min
        highlighted = (channel_min >= minimum_gray) & (
            channel_spread <= maximum_channel_spread
        )
        return float(np.mean(highlighted))


HOME_TEMPLATE = SquareTemplateSpec(
    name="home",
    file_name="home.png",
    threshold_key="主页小屋按钮阈值",
    default_threshold=0.70,
)

HOME_ICE_TEMPLATE = SquareTemplateSpec(
    name="home_ice",
    file_name="image/green/MainHomeIceGE.png",
    threshold_key="主页小屋按钮阈值",
    default_threshold=0.70,
    green_mask=True,
)

HOME_RICE_TEMPLATE = SquareTemplateSpec(
    name="home_rice",
    file_name="image/green/MainHomeRIceGE.png",
    threshold_key="主页小屋按钮阈值",
    default_threshold=0.70,
    green_mask=True,
)

HOME_TEMPLATES = (HOME_TEMPLATE, HOME_ICE_TEMPLATE, HOME_RICE_TEMPLATE)

QUICK_SWITCH_TEMPLATE = SquareTemplateSpec(
    name="quick_switch",
    file_name="image/green/QuickCartGeadai.png",
    threshold_key="快速切换按钮阈值",
    default_threshold=0.90,
    roi=(480, 918, 768, 162),
    green_mask=True,
    scale_ratios=(0.95, 0.975, 1.0, 1.025, 1.05),
    min_pixel_score=0.82,
    candidate_center_roi=(650 / 1920, 950 / 1080, 1050 / 1920, 1045 / 1080),
    minimum_safe_threshold=0.90,
)

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

SQUARE_NOTICE_TEMPLATE = SquareTemplateSpec(
    name="square_notice",
    file_name="image/green/tanhaoGE.png",
    threshold_key="广场感叹号阈值",
    default_threshold=0.72,
    roi=(1376, 862, 69, 51),
    green_mask=True,
    scale_ratios=(0.90, 0.925, 0.95, 0.975, 1.0),
    min_pixel_score=0.72,
)

SQUARE_DAILY_ICON_TEMPLATE = SquareTemplateSpec(
    name="square_daily_icon",
    file_name="image/Square_DailyIco.png",
    threshold_key="广场每日导航阈值",
    default_threshold=0.76,
    roi=(1548, 203, 26, 25),
    min_pixel_score=0.72,
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
