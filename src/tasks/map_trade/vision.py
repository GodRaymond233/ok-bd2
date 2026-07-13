from __future__ import annotations

import re
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
from typing import Iterable

import cv2
import numpy as np
from opencc import OpenCC

from src.tasks.BaseBD2Task import green_mask_from_template
from src.tasks.map_trade.models import (
    MF_REFERENCE_HEIGHT,
    MF_REFERENCE_WIDTH,
    MatchResult,
    TemplateSpec,
)
from src.utils.template_resolution import offline_template_scale

PROJECT_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_DIR = PROJECT_ROOT / "offline-train" / "train-source-screenshots"
EMPTY_MATCH = MatchResult(-1.0, (0, 0), (0, 0))
COUNT_PATTERN = re.compile(r"(?<!\d)(\d+)\s*[/：:|\-~]\s*(\d+)(?!\d)")


def normalize_text(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff%]", "", str(value).lower())


def parse_used_limit(text: str) -> tuple[int, int] | None:
    match = COUNT_PATTERN.search(str(text))
    if match is None:
        return None
    used, limit = int(match.group(1)), int(match.group(2))
    if limit <= 0 or used < 0 or used > limit:
        return None
    return used, limit


class Vision:
    def __init__(self, task) -> None:
        self.task = task
        self._templates: dict[str, tuple[np.ndarray, np.ndarray | None]] = {}
        self._opencc = OpenCC("t2s")

    @staticmethod
    def reference_point(x: float, y: float, width: int, height: int) -> tuple[int, int]:
        return (
            round(width * x / MF_REFERENCE_WIDTH),
            round(height * y / MF_REFERENCE_HEIGHT),
        )

    @staticmethod
    def reference_roi(
        roi: tuple[int, int, int, int], width: int, height: int
    ) -> tuple[int, int, int, int]:
        x, y, roi_width, roi_height = roi
        left, top = Vision.reference_point(x, y, width, height)
        right, bottom = Vision.reference_point(x + roi_width, y + roi_height, width, height)
        return left, top, max(1, right - left), max(1, bottom - top)

    def capture(self):
        return self.task.capture_frame()

    def threshold_for(self, spec: TemplateSpec) -> float:
        key = getattr(self.task, "vision_threshold_key", "跑图跑商识图阈值")
        try:
            value = float(
                self.task.config.get(
                    key,
                    self.task.config.get("跑图跑商识图阈值", spec.threshold),
                )
            )
        except (TypeError, ValueError):
            return spec.threshold
        return max(0.05, min(0.99, value))

    def click_reference(self, x: float, y: float, after_sleep: float = 0.0) -> None:
        self.task.operate_click(
            max(0.0, min(1.0, x / MF_REFERENCE_WIDTH)),
            max(0.0, min(1.0, y / MF_REFERENCE_HEIGHT)),
            after_sleep=after_sleep,
        )

    def click_client(
        self, point: tuple[int, int], frame_shape: tuple[int, ...], after_sleep: float = 0.0
    ) -> None:
        height, width = frame_shape[:2]
        self.task.operate_click(
            max(0.0, min(1.0, point[0] / max(1, width))),
            max(0.0, min(1.0, point[1] / max(1, height))),
            after_sleep=after_sleep,
        )

    def drag_reference(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
        duration: float = 0.7,
        after_sleep: float = 0.0,
    ) -> None:
        frame = self.capture()
        height, width = frame.shape[:2]
        self.task._drag_client(
            self.reference_point(*start, width, height),
            self.reference_point(*end, width, height),
            duration=duration,
            after_sleep=after_sleep,
        )

    def _load(self, spec: TemplateSpec) -> tuple[np.ndarray, np.ndarray | None]:
        if spec.file_name in self._templates:
            return self._templates[spec.file_name]
        path = TEMPLATE_DIR / spec.file_name
        raw = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if raw is None:
            raise RuntimeError(f"跑图跑商模板不存在或无法读取：{path}")
        mask = green_mask_from_template(raw) if spec.green_mask else None
        if raw.ndim == 2:
            gray = raw
        elif raw.shape[2] == 4:
            gray = cv2.cvtColor(raw, cv2.COLOR_BGRA2GRAY)
        else:
            gray = cv2.cvtColor(raw, cv2.COLOR_BGR2GRAY)
        if mask is not None and np.count_nonzero(mask) == mask.size:
            mask = None
        self._templates[spec.file_name] = (gray, mask)
        return gray, mask

    @staticmethod
    def _gray(frame: np.ndarray) -> np.ndarray:
        if frame.ndim == 2:
            return frame
        if frame.shape[2] == 4:
            return cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY)
        return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    @staticmethod
    def _candidate_scales(base_scale: float) -> tuple[float, ...]:
        return (round(max(0.2, base_scale), 3),)

    def match(self, frame: np.ndarray, spec: TemplateSpec) -> MatchResult:
        template, mask = self._load(spec)
        gray = self._gray(frame)
        frame_height, frame_width = gray.shape[:2]
        left = top = 0
        search = gray
        if spec.roi is not None:
            left, top, width, height = self.reference_roi(spec.roi, frame_width, frame_height)
            search = gray[top : top + height, left : left + width]
        if search.size == 0:
            return EMPTY_MATCH

        best = EMPTY_MATCH
        base_scale = offline_template_scale(spec.file_name, frame_width, frame_height)
        for scale in self._candidate_scales(base_scale):
            interpolation = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
            scaled = cv2.resize(template, None, fx=scale, fy=scale, interpolation=interpolation)
            scaled_mask = None
            if mask is not None:
                scaled_mask = cv2.resize(
                    mask, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST
                )
                scaled_mask = np.where(scaled_mask > 0, 255, 0).astype(np.uint8)
            height, width = scaled.shape[:2]
            if height < 4 or width < 4 or height > search.shape[0] or width > search.shape[1]:
                continue
            method = cv2.TM_CCORR_NORMED if scaled_mask is not None else cv2.TM_CCOEFF_NORMED
            try:
                result = cv2.matchTemplate(search, scaled, method, mask=scaled_mask)
            except cv2.error:
                continue
            _min_value, max_value, _min_location, max_location = cv2.minMaxLoc(result)
            if float(max_value) > best.score:
                best = MatchResult(
                    score=float(max_value),
                    position=(left + int(max_location[0]), top + int(max_location[1])),
                    size=(width, height),
                )
        return best

    def find_all(
        self,
        frame: np.ndarray,
        spec: TemplateSpec,
        threshold: float | None = None,
        max_results: int = 30,
    ) -> list[MatchResult]:
        template, mask = self._load(spec)
        gray = self._gray(frame)
        frame_height, frame_width = gray.shape[:2]
        left = top = 0
        search = gray
        if spec.roi is not None:
            left, top, width, height = self.reference_roi(spec.roi, frame_width, frame_height)
            search = gray[top : top + height, left : left + width]
        scale = offline_template_scale(spec.file_name, frame_width, frame_height)
        interpolation = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
        scaled = cv2.resize(template, None, fx=scale, fy=scale, interpolation=interpolation)
        scaled_mask = None
        if mask is not None:
            scaled_mask = cv2.resize(
                mask, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST
            )
        height, width = scaled.shape[:2]
        if search.size == 0 or height > search.shape[0] or width > search.shape[1]:
            return []
        method = cv2.TM_CCORR_NORMED if scaled_mask is not None else cv2.TM_CCOEFF_NORMED
        result = cv2.matchTemplate(search, scaled, method, mask=scaled_mask)
        wanted = self.threshold_for(spec) if threshold is None else threshold
        candidates = []
        ys, xs = np.where(result >= wanted)
        for y, x in zip(ys.tolist(), xs.tolist()):
            candidates.append((float(result[y, x]), x, y))
        candidates.sort(reverse=True)
        matches: list[MatchResult] = []
        for score, x, y in candidates:
            center_x, center_y = x + width / 2, y + height / 2
            if any(
                abs(center_x - (item.position[0] - left + item.size[0] / 2)) < width * 0.65
                and abs(center_y - (item.position[1] - top + item.size[1] / 2)) < height * 0.65
                for item in matches
            ):
                continue
            matches.append(MatchResult(score, (left + x, top + y), (width, height)))
            if len(matches) >= max_results:
                break
        return matches

    def wait_template(
        self, spec: TemplateSpec, timeout: float, interval: float = 0.4
    ) -> MatchResult | None:
        end_at = monotonic() + max(0.0, timeout)
        while monotonic() <= end_at:
            frame = self.capture()
            result = self.match(frame, spec)
            self._status(spec.name, f"{result.score:.3f}")
            if result.score >= self.threshold_for(spec):
                return result
            self.task.sleep(interval)
        return None

    def click_template(
        self,
        spec: TemplateSpec,
        timeout: float = 2.0,
        after_sleep: float = 0.8,
    ) -> bool:
        match = self.wait_template(spec, timeout)
        if match is None:
            return False
        frame = self.capture()
        self.click_client(match.center, frame.shape, after_sleep=after_sleep)
        return True

    def ocr_boxes(
        self,
        frame: np.ndarray,
        name: str,
        roi: tuple[int, int, int, int] | None = None,
    ) -> list:
        offset_x = offset_y = 0
        target = frame
        if roi is not None:
            height, width = frame.shape[:2]
            offset_x, offset_y, roi_width, roi_height = self.reference_roi(roi, width, height)
            target = frame[offset_y : offset_y + roi_height, offset_x : offset_x + roi_width]
        try:
            key = getattr(self.task, "ocr_threshold_key", "跑图跑商 OCR 阈值")
            boxes = self.task.ocr(
                frame=target,
                threshold=float(
                    self.task.config.get(
                        key,
                        self.task.config.get("跑图跑商 OCR 阈值", 0.2),
                    )
                ),
                target_height=720,
                log=False,
                name=name,
            )
        except Exception as exc:
            self._status(f"{name} OCR错误", str(exc))
            return []
        if not offset_x and not offset_y:
            return list(boxes)
        adjusted = []
        for box in boxes:
            values = {
                "name": getattr(box, "name", ""),
                "confidence": getattr(box, "confidence", getattr(box, "score", None)),
                "x": getattr(box, "x", None),
                "y": getattr(box, "y", None),
                "width": getattr(box, "width", None),
                "height": getattr(box, "height", None),
            }
            raw_box = getattr(box, "box", None)
            if any(values[key] is None for key in ("x", "y", "width", "height")):
                if raw_box is not None and len(raw_box) >= 4:
                    values["x"], values["y"], values["width"], values["height"] = raw_box[:4]
            if values["x"] is not None:
                values["x"] = float(values["x"]) + offset_x
            if values["y"] is not None:
                values["y"] = float(values["y"]) + offset_y
            if all(values[key] is not None for key in ("x", "y", "width", "height")):
                values["box"] = (
                    values["x"],
                    values["y"],
                    values["width"],
                    values["height"],
                )
            adjusted.append(SimpleNamespace(**values))
        return adjusted

    def ocr_text(
        self,
        frame: np.ndarray,
        name: str,
        roi: tuple[int, int, int, int] | None = None,
    ) -> str:
        values = [str(getattr(box, "name", "")) for box in self.ocr_boxes(frame, name, roi)]
        text = " ".join(value for value in values if value)
        self._status(f"{name} OCR", text or "-")
        return text

    def wait_ocr(
        self,
        patterns: Iterable[str],
        timeout: float,
        name: str,
        roi: tuple[int, int, int, int] | None = None,
        interval: float = 0.5,
    ) -> str | None:
        compiled = [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
        end_at = monotonic() + max(0.0, timeout)
        while monotonic() <= end_at:
            text = self.ocr_text(self.capture(), name, roi)
            normalized = self.simplify(text)
            if any(pattern.search(normalized) for pattern in compiled):
                return text
            self.task.sleep(interval)
        return None

    def click_ocr(
        self,
        patterns: Iterable[str],
        roi: tuple[int, int, int, int] | None = None,
        after_sleep: float = 0.8,
        name: str = "跑图跑商",
    ) -> bool:
        frame = self.capture()
        compiled = [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
        for box in self.ocr_boxes(frame, name, roi):
            text = self.simplify(str(getattr(box, "name", "")))
            if not any(pattern.search(text) for pattern in compiled):
                continue
            attrs = tuple(getattr(box, key, None) for key in ("x", "y", "width", "height"))
            if any(value is None for value in attrs):
                continue
            x, y, width, height = (float(value) for value in attrs)
            self.click_client(
                (round(x + width / 2), round(y + height / 2)), frame.shape, after_sleep
            )
            return True
        return False

    def simplify(self, text: str) -> str:
        return self._opencc.convert(str(text))

    @staticmethod
    def star_is_yellow(image: np.ndarray, match: MatchResult) -> bool:
        x, y = match.position
        width, height = match.size
        crop = image[max(0, y) : y + height, max(0, x) : x + width]
        if crop.size == 0:
            return False
        if crop.shape[2] == 4:
            crop = crop[:, :, :3]
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        saturation_ratio = float(np.count_nonzero(hsv[:, :, 1] > 77)) / hsv[:, :, 1].size
        return saturation_ratio >= 0.15

    def _status(self, key: str, value) -> None:
        try:
            self.task.info_set(key, value)
        except AttributeError:
            pass
