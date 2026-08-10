"""Shared vision state and template/OCR helpers for daily-style tasks."""

from __future__ import annotations

from pathlib import Path
from time import monotonic

import cv2
import numpy as np

from src.tasks.map_trade.models import MatchResult, TemplateSpec
from src.tasks.map_trade.vision import Vision
from src.utils import task_vision
from src.utils.calibration import FHD_1080
from src.utils.home_confirmation import (
    HOME_GACHA_OCR_RELATIVE_ROI,
    home_confirmation_passes,
)
from src.utils.image_utils import to_gray
from src.utils.ocr_utils import keyword_match_count, normalize_ocr_text

REFERENCE_WIDTH = FHD_1080.width

REFERENCE_HEIGHT = FHD_1080.height

PROJECT_ROOT = Path(__file__).resolve().parents[2]

TEMPLATE_DIR = PROJECT_ROOT / "recognition-assets" / "template-assets"

LOADING_TEMPLATE = TemplateSpec(
    name="ui_loading_black",
    file_name="image/UI_loading_black.png",
    threshold_key="加载页面阈值",
    default_threshold=0.72,
)

HOME_TEMPLATE = TemplateSpec(
    name="home",
    file_name="home.png",
    threshold_key="主页亮度比例阈值",
    default_threshold=0.75,
)

HOME_ICE_TEMPLATE = TemplateSpec(
    name="home_ice",
    file_name="image/green/MainHomeIceGE.png",
    threshold_key="主页亮度比例阈值",
    default_threshold=0.75,
    green_mask=True,
)

HOME_RICE_TEMPLATE = TemplateSpec(
    name="home_rice",
    file_name="image/green/MainHomeRIceGE.png",
    threshold_key="主页亮度比例阈值",
    default_threshold=0.75,
    green_mask=True,
)

HOME_TEMPLATES = (HOME_TEMPLATE, HOME_ICE_TEMPLATE, HOME_RICE_TEMPLATE)


class TaskVisionMixin:
    def _init_vision_state(self) -> None:
        self._templates: dict[str, np.ndarray] = {}
        self._template_masks: dict[str, np.ndarray | None] = {}
        self._missing_template_names: set[str] = set()
        self._match_error_names: set[str] = set()
        self._match_pause_until = 0.0

    def _quick_vision(self) -> Vision:
        vision = getattr(self, "_quick_hunt_vision", None)
        if vision is None:
            vision = Vision(self)
            self._quick_hunt_vision = vision
        return vision

    _normalize_text = staticmethod(normalize_ocr_text)

    _to_gray = staticmethod(to_gray)

    def _status_set(self, key: str, value) -> None:
        try:
            self.info_set(key, value)
        except AttributeError:
            pass

    def _home_confirmation_signals(
        self,
        frame,
        ocr_name: str,
    ) -> tuple[bool, MatchResult, TemplateSpec, float, str]:
        home_button, home_spec = self._match_best(frame, HOME_TEMPLATES)
        home_ratio = self._home_brightness_ratio(frame)
        gacha_text = self._quick_vision().ocr_text(
            frame,
            ocr_name,
            relative_roi=HOME_GACHA_OCR_RELATIVE_ROI,
        )
        confirmed = home_confirmation_passes(
            button_found=self._passes(home_button, home_spec),
            brightness_ratio=home_ratio,
            brightness_threshold=self._home_ratio_threshold(),
            gacha_ocr_text=gacha_text,
        )
        return confirmed, home_button, home_spec, home_ratio, gacha_text

    def _click_reference(self, x: int, y: int, after_sleep: float = 0.0):
        self.operate_click(
            max(0.0, min(1.0, x / REFERENCE_WIDTH)),
            max(0.0, min(1.0, y / REFERENCE_HEIGHT)),
            after_sleep=after_sleep,
        )

    def _wait_loading_or_template(
        self,
        task_name: str,
        spec: TemplateSpec,
        name: str,
        interval: float = 0.35,
    ) -> tuple[str, bool]:
        end_at = monotonic() + float(self.config.get("loading 出现等待秒数", 6.0))
        while monotonic() <= end_at:
            frame = self.capture_frame()
            result = self._match(frame, spec)
            self.info_set(name, f"{result.score:.3f}")
            if self._passes(result, spec):
                return "target", True

            loading = self._match(frame, LOADING_TEMPLATE)
            self.info_set(f"{task_name}_loading_appear", f"{loading.score:.3f}")
            if self._passes(loading, LOADING_TEMPLATE):
                return self._wait_loading_gone_or_template(
                    task_name,
                    spec,
                    name,
                    interval=interval,
                )
            self.sleep(interval)

        return "none", False

    def _wait_loading_gone_or_template(
        self,
        task_name: str,
        spec: TemplateSpec,
        name: str,
        interval: float = 0.35,
    ) -> tuple[str, bool]:
        end_at = monotonic() + float(self.config.get("loading 消失等待秒数", 35.0))
        while monotonic() <= end_at:
            frame = self.capture_frame()
            result = self._match(frame, spec)
            self.info_set(name, f"{result.score:.3f}")
            if self._passes(result, spec):
                return "target", True

            loading = self._match(frame, LOADING_TEMPLATE)
            self.info_set(f"{task_name}_loading_gone", f"{loading.score:.3f}")
            if not self._passes(loading, LOADING_TEMPLATE):
                return "loading", False
            self.sleep(interval)

        self.log_info(f"{task_name}：UI_loading_black.png 未在限定时间内消失。")
        return "stuck", False

    def _wait_loading_or_template_or_ocr(
        self,
        task_name: str,
        spec: TemplateSpec,
        keywords: list[str],
        name: str,
        interval: float = 0.5,
    ) -> tuple[str, bool, str]:
        end_at = monotonic() + float(self.config.get("loading 出现等待秒数", 6.0))
        last_text = ""
        while monotonic() <= end_at:
            frame = self.capture_frame()
            result = self._match(frame, spec)
            text = self._ocr_text(frame, name=name)
            last_text = text
            self.info_set(f"{name} 模板", f"{result.score:.3f}")
            if self._passes(result, spec) or self._keyword_match_count(text, keywords) >= 1:
                return "target", True, text

            loading = self._match(frame, LOADING_TEMPLATE)
            self.info_set(f"{task_name}_loading_appear", f"{loading.score:.3f}")
            if self._passes(loading, LOADING_TEMPLATE):
                return self._wait_loading_gone_or_template_or_ocr(
                    task_name,
                    spec,
                    keywords,
                    name,
                    last_text=last_text,
                    interval=interval,
                )
            self.sleep(interval)

        return "none", False, last_text

    def _wait_loading_gone_or_template_or_ocr(
        self,
        task_name: str,
        spec: TemplateSpec,
        keywords: list[str],
        name: str,
        last_text: str = "",
        interval: float = 0.5,
    ) -> tuple[str, bool, str]:
        end_at = monotonic() + float(self.config.get("loading 消失等待秒数", 35.0))
        while monotonic() <= end_at:
            frame = self.capture_frame()
            result = self._match(frame, spec)
            text = self._ocr_text(frame, name=name)
            last_text = text
            self.info_set(f"{name} 模板", f"{result.score:.3f}")
            if self._passes(result, spec) or self._keyword_match_count(text, keywords) >= 1:
                return "target", True, text

            loading = self._match(frame, LOADING_TEMPLATE)
            self.info_set(f"{task_name}_loading_gone", f"{loading.score:.3f}")
            if not self._passes(loading, LOADING_TEMPLATE):
                return "loading", False, last_text
            self.sleep(interval)

        self.log_info(f"{task_name}：UI_loading_black.png 未在限定时间内消失。")
        return "stuck", False, last_text

    def _wait_for_template(
        self,
        spec: TemplateSpec,
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

    def _wait_for_template_or_ocr(
        self,
        spec: TemplateSpec,
        keywords: list[str],
        timeout: float,
        name: str,
        interval: float = 0.5,
    ) -> tuple[bool, str]:
        end_at = monotonic() + max(0.0, timeout)
        last_text = ""
        while monotonic() <= end_at:
            frame = self.capture_frame()
            result = self._match(frame, spec)
            text = self._ocr_text(frame, name=name)
            last_text = text
            self.info_set(f"{name} 模板", f"{result.score:.3f}")
            if self._passes(result, spec) or self._keyword_match_count(text, keywords) >= 1:
                return True, text
            self.sleep(interval)
        return False, last_text

    def _wait_for_ocr_keywords(
        self,
        keywords: list[str],
        timeout: float,
        minimum_matches: int,
        name: str,
        interval: float = 0.5,
    ) -> tuple[bool, str]:
        end_at = monotonic() + max(0.0, timeout)
        last_text = ""
        while monotonic() <= end_at:
            frame = self.capture_frame()
            text = self._ocr_text(frame, name=name)
            last_text = text
            count = self._keyword_match_count(text, keywords)
            self.info_set(f"{name} 关键字", f"{count}/{len(keywords)}")
            if count >= minimum_matches:
                return True, text
            self.sleep(interval)
        return False, last_text

    def _wait_for_home_confirmation(
        self,
        name: str,
        interval: float = 0.35,
    ) -> bool:
        end_at = monotonic() + float(self.config.get("主页确认等待秒数", 10.0))
        last_button = self._empty_match()
        last_spec = HOME_TEMPLATE
        last_ratio = 0.0
        last_gacha_text = ""
        while monotonic() <= end_at:
            frame = self.capture_frame()
            (
                confirmed,
                last_button,
                last_spec,
                last_ratio,
                last_gacha_text,
            ) = self._home_confirmation_signals(frame, f"{name} 抽抽乐")
            self.info_set(
                f"{name} 小屋按钮",
                f"{last_spec.file_name}={last_button.score:.3f}/{last_button.pixel_score:.3f}",
            )
            self.info_set(f"{name} 亮度", f"{last_ratio:.3f}")
            self.info_set(f"{name} 抽抽乐 OCR", last_gacha_text or "-")
            if confirmed:
                return True
            self.clear_temporary_home_announcement_if_needed(
                button_found=self._passes(last_button, last_spec),
                brightness_ratio=last_ratio,
                brightness_threshold=self._home_ratio_threshold(),
                gacha_ocr_text=last_gacha_text,
                context=name,
            )
            self.sleep(interval)

        self.log_info(
            f"{name}：未同时确认主页按钮、亮度和抽抽乐文字，"
            f"template={last_spec.file_name}, "
            f"button={last_button.score:.3f}/{last_button.pixel_score:.3f}, "
            f"ratio={last_ratio:.3f}, ocr={last_gacha_text or '-'}。"
        )
        return False

    def _home_brightness_ratio(self, frame) -> float:
        return max(self._home_brightness_ratio_for_template(frame, spec) for spec in HOME_TEMPLATES)

    def _home_brightness_ratio_for_template(
        self,
        frame,
        spec: TemplateSpec,
    ) -> float:
        return task_vision.brightness_ratio(
            frame,
            spec,
            (166 / REFERENCE_WIDTH, 158 / REFERENCE_HEIGHT),
            TEMPLATE_DIR,
            cache=self._templates,
        )

    @staticmethod
    def _empty_match() -> MatchResult:
        return MatchResult(-1.0, (0, 0), (0, 0))

    def _match_best(
        self,
        frame,
        specs: tuple[TemplateSpec, ...],
    ) -> tuple[MatchResult, TemplateSpec]:
        best = self._empty_match()
        best_spec = specs[0]
        for spec in specs:
            result = self._match(frame, spec)
            if result.score > best.score:
                best = result
                best_spec = spec
        return best, best_spec

    def _match(self, frame, spec: TemplateSpec) -> MatchResult:
        empty = MatchResult(-1.0, (0, 0), (0, 0))
        if monotonic() < self._match_pause_until:
            return empty

        try:
            return task_vision.match_template(
                frame,
                spec,
                self.config,
                TEMPLATE_DIR,
                cache=self._templates,
                min_size=8,
                loader=lambda _template_dir, spec: (
                    self._load_template(spec),
                    self._load_template_mask(spec),
                ),
            )
        except RuntimeError as exc:
            if spec.name not in self._missing_template_names:
                self._missing_template_names.add(spec.name)
                self.log_warning(str(exc), notify=True)
            return empty
        except (cv2.error, MemoryError) as exc:
            self._match_pause_until = monotonic() + 2.0
            message = f"图像匹配内存不足，暂停识别2秒：{spec.name}"
            self.info_set("匹配错误", message)
            if spec.name not in self._match_error_names:
                self._match_error_names.add(spec.name)
                self.log_warning(f"{message}；{exc}", notify=True)
            return empty

    def _load_template(self, spec: TemplateSpec) -> np.ndarray:
        return task_vision.load_template(TEMPLATE_DIR, spec, cache=self._templates)[0]

    def _load_template_mask(self, spec: TemplateSpec) -> np.ndarray | None:
        return task_vision.load_template(TEMPLATE_DIR, spec, cache=self._templates)[1]

    def _passes(self, result: MatchResult, spec: TemplateSpec) -> bool:
        return task_vision.passes_match(result, spec, self.config)

    def _ocr_text(self, frame, name: str) -> str:
        try:
            boxes = self.ocr(
                frame=frame,
                threshold=float(self.config.get("日常 OCR 阈值", 0.2)),
                target_height=720,
                log=False,
                name=name,
            )
        except Exception as exc:
            self.info_set(f"{name} OCR 错误", str(exc))
            return ""

        return " ".join(box.name for box in boxes if getattr(box, "name", ""))

    @staticmethod
    def _keyword_match_count(text: str, keywords: list[str]) -> int:
        return keyword_match_count(text, keywords)

    def _home_ratio_threshold(self) -> float:
        return float(self.config.get("主页亮度比例阈值", 0.75))
