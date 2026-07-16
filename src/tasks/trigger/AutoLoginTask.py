from dataclasses import dataclass
from pathlib import Path
from time import monotonic

import cv2
import numpy as np
from qfluentwidgets import FluentIcon

from src.tasks.BaseBD2Task import BaseBD2Task
from src.utils.template_resolution import (
    offline_template_requires_green_mask,
    offline_template_scale,
)

REFERENCE_WIDTH = 1920
REFERENCE_HEIGHT = 1080
PROJECT_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_DIR = PROJECT_ROOT / "offline-train" / "train-source-screenshots"


@dataclass(frozen=True)
class TemplateSpec:
    name: str
    file_name: str
    threshold_key: str
    default_threshold: float
    crop: tuple[float, float, float, float] | None = None
    green_mask: bool = False


@dataclass(frozen=True)
class MatchResult:
    score: float
    pixel_score: float
    position: tuple[int, int]
    size: tuple[int, int]


class AutoLoginTask(BaseBD2Task):
    status_keys = [
        "阶段",
        "内部状态",
        "最后动作",
        "BrownDustX",
        "BrownDustX 阈值",
        "BrownDustX 像素",
        "BrownDustX 像素阈值",
        "BrownDustX OCR",
        "BrownDustX Confirm",
        "BrownDustX Confirm 阈值",
        "BrownDustX Confirm 像素",
        "BrownDustX Confirm 像素阈值",
        "BrownDustX Confirm OCR",
        "BrownDustX Confirm 点击",
        "TOUCH TO START",
        "TOUCH TO START 阈值",
        "加载页面",
        "加载页面阈值",
        "小屋按钮",
        "小屋按钮阈值",
        "小屋按钮遮挡阈值",
        "小屋亮度比例",
        "小屋亮度比例阈值",
        "主页 UI 等待宽限秒数",
        "主页连续确认秒数",
        "BDXConfirm 点击 X 百分比",
        "BDXConfirm 点击 Y 百分比",
        "登录按钮点击 X 百分比",
        "登录按钮点击 Y 百分比",
        "小屋按钮点击 X 百分比",
        "小屋按钮点击 Y 百分比",
        "公告清理点击 X 百分比",
        "公告清理点击 Y 百分比",
        "匹配错误",
        "Log",
        "Warning",
        "Error",
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "自动登录游戏"
        self.description = "游戏启动后自动登录游戏"
        self.icon = FluentIcon.ACCEPT
        self.visible = True
        self.trigger_interval = 1.0
        self.default_config.update(
            {
                "_enabled": True,
                "BrownDustX 阈值": 0.82,
                "BrownDustX 像素阈值": 0.86,
                "BrownDustX Confirm 阈值": 0.82,
                "BrownDustX Confirm 像素阈值": 0.86,
                "BrownDustX OCR 阈值": 0.2,
                "TOUCH TO START 阈值": 0.78,
                "加载页面阈值": 0.72,
                "小屋按钮阈值": 0.78,
                "小屋按钮遮挡阈值": 0.62,
                "小屋亮度比例阈值": 0.75,
                "主页 UI 等待宽限秒数": 15.0,
                "主页连续确认秒数": 3.0,
                "BDXConfirm 点击 X 百分比": 50.0,
                "BDXConfirm 点击 Y 百分比": 67.5926,
                "登录按钮点击 X 百分比": 72.2396,
                "登录按钮点击 Y 百分比": 65.0926,
                "小屋按钮点击 X 百分比": 8.6979,
                "小屋按钮点击 Y 百分比": 14.3519,
                "公告清理点击 X 百分比": 8.8020833333,
                "公告清理点击 Y 百分比": 56.9444444444,
            }
        )
        self._templates: dict[str, np.ndarray] = {}
        self._template_masks: dict[str, np.ndarray | None] = {}
        self._state = "waiting"
        self._home_bright_since: float | None = None
        self._login_clicked_at: float | None = None
        self._waiting_home_since: float | None = None
        self._last_clear_click_at = 0.0
        self._finished = False
        self._missing_template_names: set[str] = set()
        self._match_error_names: set[str] = set()
        self._match_pause_until = 0.0

    def on_create(self):
        self._enabled = bool(self.config.get("_enabled", True))
        self._set_stage("等待登录页")
        self._set_action("自动登录已启用，等待启动游戏后的画面识别。")

    def enable(self):
        was_enabled = self._enabled
        super().enable()
        self.config["_enabled"] = True
        if not was_enabled and self._finished:
            self._reset_login_state("自动登录已重新启用，等待启动游戏后的画面识别。")

    def disable(self):
        super().disable()
        self.config["_enabled"] = False

    def should_trigger(self):
        if self._finished:
            return False
        return super().should_trigger()

    def run(self):
        if self._finished:
            return False

        frame = self.capture_frame()

        if self._state == "browndustx":
            return self._wait_browndustx_then_login(frame)
        if self._state == "clearing":
            return self._clear_popups_until_home(frame)
        if self._state in ("waiting_loading", "loading", "waiting_home"):
            return self._wait_loading_then_home(frame)

        browndustx = self._match(frame, BROWNDUSTX_TEMPLATE)
        self.info_set("BrownDustX", f"{browndustx.score:.3f}")
        self.info_set("BrownDustX 像素", f"{browndustx.pixel_score:.3f}")

        if self._is_browndustx_present(browndustx):
            self._state = "browndustx"
            return self._wait_browndustx_then_login(frame, browndustx)

        self.info_set("BrownDustX Confirm", "-")
        self.info_set("BrownDustX Confirm 像素", "-")
        self.info_set("BrownDustX Confirm OCR", "-")

        touch_to_start = self._match(frame, TOUCH_TO_START_TEMPLATE)
        self.info_set("TOUCH TO START", f"{touch_to_start.score:.3f}")
        self.info_set("加载页面", "-")
        self.info_set("小屋按钮", "-")

        if self._passes(touch_to_start, TOUCH_TO_START_TEMPLATE):
            self._click_login_after_touch(touch_to_start)
            return False

        self._set_stage("等待登录页")
        self._set_action("等待 BrownDustX 或 TOUCH TO START 画面。")

        return False

    def _wait_browndustx_then_login(
        self,
        frame,
        browndustx: MatchResult | None = None,
    ) -> bool:
        if browndustx is None:
            browndustx = self._match(frame, BROWNDUSTX_TEMPLATE)
            self.info_set("BrownDustX", f"{browndustx.score:.3f}")
            self.info_set("BrownDustX 像素", f"{browndustx.pixel_score:.3f}")

        confirm = self._match(frame, CONFIRM_TEMPLATE)
        self.info_set("BrownDustX Confirm", f"{confirm.score:.3f}")
        self.info_set("BrownDustX Confirm 像素", f"{confirm.pixel_score:.3f}")
        if self._is_browndustx_confirm(frame, confirm):
            self._set_stage("BrownDustX 异常确认")
            self._set_action("检测到 BrownDustX Confirm，点击确认按钮。")
            self.log_info(f"自动登录：检测到 BrownDustX Confirm，score={confirm.score:.3f}")
            self._sleep_after_recognition()
            self._click_match_center(confirm, after_sleep=1.0)
            return False

        touch_to_start = self._match(frame, TOUCH_TO_START_TEMPLATE)
        self.info_set("TOUCH TO START", f"{touch_to_start.score:.3f}")
        self.info_set("加载页面", "-")
        self.info_set("小屋按钮", "-")
        if self._passes(touch_to_start, TOUCH_TO_START_TEMPLATE):
            self._click_login_after_touch(touch_to_start)
            return False

        if self._is_browndustx_present(browndustx):
            self._record_browndustx_text(frame, browndustx)

        self._set_stage("BrownDustX 加载")
        self._set_action("等待 BrownDustX Confirm 或 TOUCH TO START。")
        self.log_info(
            "自动登录：BrownDustX 等待中，"
            f"browndustx={browndustx.score:.3f}, confirm={confirm.score:.3f}, "
            f"touch={touch_to_start.score:.3f}"
        )
        return False

    def _click_login_after_touch(self, touch_to_start: MatchResult) -> None:
        self._set_stage("点击登录")
        self._set_action("检测到 TOUCH TO START，点击登录按钮。")
        self.log_info(f"自动登录：检测到 TOUCH TO START，score={touch_to_start.score:.3f}")
        self._sleep_after_recognition()
        self.operate_click(
            self._percent_config("登录按钮点击 X 百分比"),
            self._percent_config("登录按钮点击 Y 百分比"),
            after_sleep=2.0,
        )
        self._state = "waiting_loading"
        self._home_bright_since = None
        self._login_clicked_at = None
        self._waiting_home_since = monotonic()
        self._last_clear_click_at = 0.0

    def _wait_loading_then_home(self, frame) -> bool:
        now = monotonic()

        if self._state == "waiting_loading":
            self._login_clicked_at = None
            if self._waiting_home_since is None:
                self._waiting_home_since = now

        self.info_set("TOUCH TO START", "-")

        home_button, home_spec = self._match_home_button(frame)
        self.info_set("小屋按钮", f"{home_button.score:.3f}")
        if self._passes(home_button, home_spec):
            self._state = "clearing"
            self._login_clicked_at = None
            self._waiting_home_since = None
            self.info_set("加载页面", "-")
            return self._clear_popups_until_home(frame, home_button)

        loading = self._match(frame, LOADING_TEMPLATE)
        self.info_set("加载页面", f"{loading.score:.3f}")

        if self._passes(loading, LOADING_TEMPLATE):
            self._state = "loading"
            self._home_bright_since = None
            self._set_stage("登录加载中")
            self._set_action("检测到 UI_loading_black.png，同时等待 home.png 出现。")
            self.log_info(
                "自动登录：登录加载中，"
                f"loading={loading.score:.3f}, home={home_button.score:.3f}"
            )
            return False

        if self._state in ("waiting_loading", "loading"):
            self._state = "waiting_home"

        self._home_bright_since = None
        if self._waiting_home_since is None:
            self._waiting_home_since = now

        grace_seconds = float(self.config.get("主页 UI 等待宽限秒数", 15.0))
        elapsed = now - self._waiting_home_since

        if elapsed >= grace_seconds and self._passes_dimmed_home(home_button):
            self._state = "clearing"
            self._login_clicked_at = None
            self._waiting_home_since = None
            self.info_set("加载页面", "-")
            self._set_stage("清理公告")
            self._set_action(
                f"疑似主页被公告遮挡，home={home_button.score:.3f}，尝试关闭公告。"
            )
            self.log_info(
                "自动登录：疑似主页被公告遮挡，"
                f"home={home_button.score:.3f}, threshold={self._home_dimmed_threshold():.3f}"
            )
            return self._clear_popups_until_home(frame, home_button, allow_dimmed=True)

        self._set_stage("等待主页 UI")
        self._set_action(f"登录后等待 home.png 出现 {elapsed:.1f}/{grace_seconds:.1f} 秒。")
        self.log_info(f"自动登录：登录后等待小屋按钮出现，home={home_button.score:.3f}")
        return False

    def _clear_popups_until_home(
        self,
        frame,
        home_button: MatchResult | None = None,
        allow_dimmed: bool = False,
    ) -> bool:
        if home_button is None:
            home_button, home_spec = self._match_home_button(frame)
        else:
            home_spec = HOME_BUTTON_TEMPLATE
        self.info_set("小屋按钮", f"{home_button.score:.3f}")
        home_found = self._passes(home_button, home_spec)
        dimmed_home_found = (
            (allow_dimmed or self._state == "clearing") and self._passes_dimmed_home(home_button)
        )
        if not home_found and not dimmed_home_found:
            if self._state == "clearing":
                ratio = self._home_brightness_ratio(frame)
                self.info_set("小屋亮度比例", f"{ratio:.3f}")
                if ratio < self._home_ratio_threshold():
                    self._clear_home_popup(ratio)
                    return False

            self._home_bright_since = None
            self._state = "waiting_home"
            self._set_stage("等待主页 UI")
            self._set_action("home.png 尚未出现，继续等待。")
            self.log_info(f"自动登录：小屋按钮尚未出现，score={home_button.score:.3f}")
            return False

        ratio = self._home_brightness_ratio(frame)
        self.info_set("小屋亮度比例", f"{ratio:.3f}")

        if ratio >= self._home_ratio_threshold():
            now = monotonic()
            if self._home_bright_since is None:
                self._home_bright_since = now
                self._set_stage("主页确认中")
                self._set_action("home.png 亮度已恢复，开始连续确认。")
                return False

            stable_seconds = float(self.config.get("主页连续确认秒数", 3.0))
            elapsed = now - self._home_bright_since
            self._set_stage("主页确认中")
            self._set_action(f"主页亮度持续正常 {elapsed:.1f}/{stable_seconds:.1f} 秒。")
            if now - self._home_bright_since >= stable_seconds:
                self.mark_logged_in()
                self._finished = True
                self._state = "done"
                self._set_stage("已完成")
                self._set_action("主页亮度已连续确认，自动登录流程结束。")
                self.log_info("自动登录：主页亮度已连续确认，流程结束。", notify=True)
            return False

        self._home_bright_since = None
        self._clear_home_popup(ratio)
        return False

    def _clear_home_popup(self, ratio: float) -> None:
        self._home_bright_since = None
        now = monotonic()
        if now - self._last_clear_click_at < 1.0:
            return

        self._state = "clearing"
        self._set_stage("清理公告")
        clear_x = self._percent_config("公告清理点击 X 百分比")
        clear_y = self._percent_config("公告清理点击 Y 百分比")
        self._set_action(f"主页亮度不足，点击公告清理位置，ratio={ratio:.3f}。")
        self.log_info(
            "自动登录：主页未恢复，点击公告清理位置，"
            f"ratio={ratio:.3f}, x={clear_x:.2%}, y={clear_y:.2%}"
        )
        self._sleep_after_recognition()
        self.operate_click(clear_x, clear_y, after_sleep=0.2)
        self._last_clear_click_at = now

    def _home_brightness_ratio(self, frame) -> float:
        return max(self._home_brightness_ratio_for_template(frame, spec) for spec in HOME_TEMPLATES)

    def _home_brightness_ratio_for_template(
        self,
        frame,
        spec: TemplateSpec,
    ) -> float:
        template = self._load_template(spec)
        mask = self._load_template_mask(spec)
        frame_gray = self._to_gray(frame)
        frame_height, frame_width = frame_gray.shape[:2]
        scale = offline_template_scale(spec.file_name, frame_width, frame_height)
        template_height, template_width = template.shape[:2]
        roi_width = max(8, round(template_width * scale))
        roi_height = max(8, round(template_height * scale))
        center_x = round(frame_width * self._percent_config("小屋按钮点击 X 百分比"))
        center_y = round(frame_height * self._percent_config("小屋按钮点击 Y 百分比"))
        left = max(0, center_x - roi_width // 2)
        top = max(0, center_y - roi_height // 2)
        right = min(frame_width, left + roi_width)
        bottom = min(frame_height, top + roi_height)
        region = frame_gray[top:bottom, left:right]
        if region.size == 0:
            return 0.0

        scaled_template = self._resize_template(template, scale)
        scaled_mask = self._resize_mask(mask, scale)
        match_height = min(region.shape[0], scaled_template.shape[0])
        match_width = min(region.shape[1], scaled_template.shape[1])
        if match_height <= 0 or match_width <= 0:
            return 0.0
        region = region[:match_height, :match_width]
        scaled_template = scaled_template[:match_height, :match_width]
        if scaled_mask is not None:
            scaled_mask = scaled_mask[:match_height, :match_width]
            valid = scaled_mask > 0
            if not np.any(valid):
                return 0.0
            template_mean = float(np.mean(scaled_template[valid]))
            region_mean = float(np.mean(region[valid]))
        else:
            template_mean = float(np.mean(scaled_template))
            region_mean = float(np.mean(region))
        if template_mean <= 0:
            return 0.0
        return float(region_mean / template_mean)

    @staticmethod
    def _empty_match() -> MatchResult:
        return MatchResult(score=-1.0, pixel_score=-1.0, position=(0, 0), size=(0, 0))

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

    def _match_home_button(self, frame) -> tuple[MatchResult, TemplateSpec]:
        return self._match_best(frame, HOME_BUTTON_TEMPLATES)

    def _match(self, frame, spec: TemplateSpec) -> MatchResult:
        empty = self._empty_match()
        if monotonic() < self._match_pause_until:
            return empty

        try:
            template = self._load_template(spec)
            mask = self._load_template_mask(spec)
        except RuntimeError as exc:
            if spec.name not in self._missing_template_names:
                self._missing_template_names.add(spec.name)
                self.log_warning(str(exc), notify=True)
            return empty

        try:
            frame_gray = self._to_gray(frame)
            frame_height, frame_width = frame_gray.shape[:2]
            base_scale = offline_template_scale(spec.file_name, frame_width, frame_height)
            scales = self._candidate_scales(base_scale)
            best = empty

            for scale in scales:
                scaled_template = self._resize_template(template, scale)
                scaled_mask = self._resize_mask(mask, scale)
                height, width = scaled_template.shape[:2]
                if height < 8 or width < 8 or height > frame_height or width > frame_width:
                    continue

                if scaled_mask is None:
                    result = cv2.matchTemplate(frame_gray, scaled_template, cv2.TM_CCOEFF_NORMED)
                else:
                    result = cv2.matchTemplate(
                        frame_gray,
                        scaled_template,
                        cv2.TM_CCORR_NORMED,
                        mask=scaled_mask,
                    )
                _, max_value, _, max_location = cv2.minMaxLoc(result)
                if not np.isfinite(max_value):
                    continue
                if max_value > best.score:
                    x, y = int(max_location[0]), int(max_location[1])
                    region = frame_gray[y : y + height, x : x + width]
                    best = MatchResult(
                        score=float(max_value),
                        pixel_score=self._pixel_similarity(region, scaled_template, scaled_mask),
                        position=(x, y),
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

    def _load_template(self, spec: TemplateSpec) -> np.ndarray:
        if spec.name in self._templates:
            return self._templates[spec.name]

        template, mask = self._read_template_and_mask(spec)
        self._templates[spec.name] = template
        self._template_masks[spec.name] = mask
        return template

    def _load_template_mask(self, spec: TemplateSpec) -> np.ndarray | None:
        if spec.name not in self._templates:
            self._load_template(spec)
        return self._template_masks.get(spec.name)

    def _read_template_and_mask(self, spec: TemplateSpec) -> tuple[np.ndarray, np.ndarray | None]:
        path = TEMPLATE_DIR / spec.file_name
        source = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if source is None:
            raise RuntimeError(f"自动登录模板不存在或无法读取：{path}")

        if spec.crop is not None:
            source = self._crop_relative(source, spec.crop)

        mask = None
        if len(source.shape) == 2:
            template = source
        else:
            if source.shape[2] == 4:
                template = cv2.cvtColor(source, cv2.COLOR_BGRA2GRAY)
            else:
                template = cv2.cvtColor(source, cv2.COLOR_BGR2GRAY)

            if spec.green_mask or offline_template_requires_green_mask(spec.file_name):
                color = source[:, :, :3]
                green_pixels = (
                    (color[:, :, 0] <= 4)
                    & (color[:, :, 1] >= 251)
                    & (color[:, :, 2] <= 4)
                )
                if source.shape[2] == 4:
                    green_pixels |= source[:, :, 3] == 0
                mask = np.where(green_pixels, 0, 255).astype(np.uint8)

        return template, mask

    def _passes(self, result: MatchResult, spec: TemplateSpec) -> bool:
        threshold = float(self.config.get(spec.threshold_key, spec.default_threshold))
        return result.score >= threshold

    def _passes_strict(self, result: MatchResult, spec: TemplateSpec) -> bool:
        if not self._passes(result, spec):
            return False
        pixel_key = f"{spec.threshold_key.removesuffix('阈值')}像素阈值"
        pixel_threshold = self.config.get(pixel_key)
        if pixel_threshold is None:
            return True
        return result.pixel_score >= float(pixel_threshold)

    def _is_browndustx_present(self, browndustx: MatchResult) -> bool:
        if self._passes_strict(browndustx, BROWNDUSTX_TEMPLATE):
            return True
        pixel_threshold = float(self.config.get("BrownDustX 像素阈值", 0.86))
        return browndustx.pixel_score >= pixel_threshold

    def _record_browndustx_text(self, frame, browndustx: MatchResult) -> None:
        text = self._ocr_match_region_text(frame, browndustx, name="browndustx_loading_ocr")
        self.info_set("BrownDustX OCR", text or "-")

    def _is_browndustx_confirm(
        self,
        frame,
        confirm: MatchResult,
    ) -> bool:
        if not self._passes_strict(confirm, CONFIRM_TEMPLATE):
            self.info_set("BrownDustX Confirm OCR", "-")
            return False

        button_text = self._ocr_match_region_text(frame, confirm, name="browndustx_confirm_ocr")
        self.info_set("BrownDustX Confirm OCR", button_text or "-")
        return "confirm" in self._normalize_ocr_text(button_text)

    def _home_ratio_threshold(self) -> float:
        return float(self.config.get("小屋亮度比例阈值", 0.75))

    def _home_dimmed_threshold(self) -> float:
        return float(self.config.get("小屋按钮遮挡阈值", 0.62))

    def _passes_dimmed_home(self, home_button: MatchResult) -> bool:
        return home_button.score >= self._home_dimmed_threshold()

    def _percent_config(self, key: str) -> float:
        return max(0.0, min(1.0, float(self.config[key]) / 100.0))

    def _click_match_center(self, result: MatchResult, after_sleep: float = 0.0) -> None:
        x = round(result.position[0] + result.size[0] / 2)
        y = round(result.position[1] + result.size[1] / 2)
        self.info_set("BrownDustX Confirm 点击", f"{x},{y}")
        self.operate_click(x, y, after_sleep=after_sleep)

    def _reset_login_state(self, action: str = "重新进入自动登录识别。"):
        self._state = "waiting"
        self._home_bright_since = None
        self._login_clicked_at = None
        self._waiting_home_since = None
        self._last_clear_click_at = 0.0
        self._finished = False
        self._set_stage("等待登录页")
        self._set_action(action)

    def _set_stage(self, stage: str) -> None:
        self.info_set("阶段", stage)
        self.info_set("内部状态", self._state)

    def _set_action(self, action: str) -> None:
        self.info_set("最后动作", action)

    def _ocr_match_region_text(self, frame, result: MatchResult, name: str) -> str:
        x, y = result.position
        width, height = result.size
        if width <= 0 or height <= 0:
            return ""

        frame_height, frame_width = frame.shape[:2]
        left = max(0, x)
        top = max(0, y)
        right = min(frame_width, x + width)
        bottom = min(frame_height, y + height)
        if right <= left or bottom <= top:
            return ""

        crop = frame[top:bottom, left:right]
        try:
            boxes = self.ocr(
                frame=crop,
                threshold=float(self.config.get("BrownDustX OCR 阈值", 0.2)),
                target_height=720,
                log=False,
                name=name,
            )
        except Exception as exc:
            self.info_set(f"{name} 错误", str(exc))
            return ""

        return " ".join(box.name for box in boxes if getattr(box, "name", ""))

    @staticmethod
    def _normalize_ocr_text(text: str) -> str:
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
        return cv2.resize(mask, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)

    @staticmethod
    def _crop_relative(
        image: np.ndarray,
        crop: tuple[float, float, float, float],
    ) -> np.ndarray:
        height, width = image.shape[:2]
        left, top, right, bottom = crop
        x1 = round(width * left)
        y1 = round(height * top)
        x2 = round(width * right)
        y2 = round(height * bottom)
        return image[y1:y2, x1:x2]

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
        diff_image = np.abs(region.astype(np.float32) - template.astype(np.float32))
        if mask is not None:
            valid = mask > 0
            if not np.any(valid):
                return -1.0
            diff = np.mean(diff_image[valid])
        else:
            diff = np.mean(diff_image)
        return float(1.0 - diff / 255.0)


BROWNDUSTX_TEMPLATE = TemplateSpec(
    name="browndustx",
    file_name="browndustx.png",
    threshold_key="BrownDustX 阈值",
    default_threshold=0.82,
)

CONFIRM_TEMPLATE = TemplateSpec(
    name="browndustx_confirm",
    file_name="browndustx-confirm.png",
    threshold_key="BrownDustX Confirm 阈值",
    default_threshold=0.82,
)

TOUCH_TO_START_TEMPLATE = TemplateSpec(
    name="touch_to_start",
    file_name="touch-to-start.png",
    threshold_key="TOUCH TO START 阈值",
    default_threshold=0.78,
)

LOADING_TEMPLATE = TemplateSpec(
    name="ui_loading_black",
    file_name="image/UI_loading_black.png",
    threshold_key="加载页面阈值",
    default_threshold=0.72,
)

HOME_BUTTON_TEMPLATE = TemplateSpec(
    name="home_button",
    file_name="home.png",
    threshold_key="小屋按钮阈值",
    default_threshold=0.78,
)

HOME_BUTTON_ICE_TEMPLATE = TemplateSpec(
    name="home_button_ice",
    file_name="image/green/MainHomeIceGE.png",
    threshold_key="小屋按钮阈值",
    default_threshold=0.78,
    green_mask=True,
)

HOME_BUTTON_RICE_TEMPLATE = TemplateSpec(
    name="home_button_rice",
    file_name="image/green/MainHomeRIceGE.png",
    threshold_key="小屋按钮阈值",
    default_threshold=0.78,
    green_mask=True,
)

HOME_BUTTON_TEMPLATES = (
    HOME_BUTTON_TEMPLATE,
    HOME_BUTTON_ICE_TEMPLATE,
    HOME_BUTTON_RICE_TEMPLATE,
)

HOME_TEMPLATE = TemplateSpec(
    name="home",
    file_name="home.png",
    threshold_key="小屋亮度比例阈值",
    default_threshold=0.75,
)

HOME_TEMPLATES = (
    HOME_TEMPLATE,
    HOME_BUTTON_ICE_TEMPLATE,
    HOME_BUTTON_RICE_TEMPLATE,
)
