from dataclasses import dataclass
from pathlib import Path
from time import monotonic

import cv2
import numpy as np

from src.tasks.BaseBD2Task import BaseBD2Task

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


@dataclass(frozen=True)
class MatchResult:
    score: float
    pixel_score: float
    position: tuple[int, int]
    size: tuple[int, int]


class AutoLoginTask(BaseBD2Task):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "自动登录"
        self.description = "启动游戏后自动处理登录、BrownDustX Confirm 和登录后公告。"
        self.visible = False
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
                "小屋亮度比例阈值": 0.75,
                "主页 UI 等待宽限秒数": 10.0,
                "主页连续确认秒数": 3.0,
                "BDXConfirm 点击 X 百分比": 50.0,
                "BDXConfirm 点击 Y 百分比": 67.5926,
                "登录按钮点击 X 百分比": 72.2396,
                "登录按钮点击 Y 百分比": 65.0926,
                "小屋按钮点击 X 百分比": 8.6979,
                "小屋按钮点击 Y 百分比": 14.3519,
            }
        )
        self._templates: dict[str, np.ndarray] = {}
        self._state = "waiting"
        self._home_bright_since: float | None = None
        self._waiting_home_since: float | None = None
        self._last_clear_click_at = 0.0
        self._finished = False
        self._missing_template_names: set[str] = set()

    def on_create(self):
        self._enabled = True
        self.config["_enabled"] = True
        self._set_stage("等待登录页")
        self._set_action("自动登录已启用，等待启动游戏后的画面识别。")

    def run(self):
        frame = self.capture_frame()

        if self._finished:
            if self._home_brightness_ratio(frame) >= self._home_ratio_threshold():
                self._set_stage("已完成")
                return False
            self._reset_login_state()

        if self._state in ("clearing", "force_clearing"):
            return self._clear_popups_until_home(frame)
        if self._state in ("loading", "waiting_home"):
            return self._wait_loading_then_home(frame)

        if self._detect_entered_game(frame):
            return False

        confirm = self._match(frame, CONFIRM_TEMPLATE)
        touch_to_start = self._match(frame, TOUCH_TO_START_TEMPLATE)
        browndustx = self._match(frame, BROWNDUSTX_TEMPLATE)

        self.info_set("BrownDustX", f"{browndustx.score:.3f}")
        self.info_set("BrownDustX 像素", f"{browndustx.pixel_score:.3f}")
        self.info_set("BrownDustX Confirm", f"{confirm.score:.3f}")
        self.info_set("BrownDustX Confirm 像素", f"{confirm.pixel_score:.3f}")
        self.info_set("TOUCH TO START", f"{touch_to_start.score:.3f}")

        if self._is_browndustx_confirm(frame, confirm):
            self._set_stage("BrownDustX 异常确认")
            self._set_action("检测到 BrownDustX Confirm，点击确认按钮。")
            self.log_info(f"自动登录：检测到 BrownDustX Confirm，score={confirm.score:.3f}")
            self.operate_click(
                self._percent_config("BDXConfirm 点击 X 百分比"),
                self._percent_config("BDXConfirm 点击 Y 百分比"),
                after_sleep=1.0,
            )
            return False

        if self._passes(touch_to_start, TOUCH_TO_START_TEMPLATE):
            self._set_stage("点击登录")
            self._set_action("检测到 TOUCH TO START，点击登录按钮。")
            self.log_info(f"自动登录：检测到 TOUCH TO START，score={touch_to_start.score:.3f}")
            self.operate_click(
                self._percent_config("登录按钮点击 X 百分比"),
                self._percent_config("登录按钮点击 Y 百分比"),
                after_sleep=2.0,
            )
            self._state = "loading"
            self._home_bright_since = None
            self._waiting_home_since = None
            self._last_clear_click_at = 0.0
            return False

        if self._is_browndustx_loading(frame, browndustx):
            self._set_stage("BrownDustX 加载")
            self._set_action("检测到 BrownDustX，等待 Mod 管理器完成加载。")
            self.log_info(f"自动登录：BrownDustX 正在加载，score={browndustx.score:.3f}")
        else:
            self._set_stage("等待登录页")
            self._set_action("等待 BrownDustX、Confirm 或 TOUCH TO START 画面。")

        return False

    def _detect_entered_game(self, frame) -> bool:
        loading = self._match(frame, LOADING_TEMPLATE)
        home_button = self._match(frame, HOME_BUTTON_TEMPLATE)
        self.info_set("加载页面", f"{loading.score:.3f}")
        self.info_set("小屋按钮", f"{home_button.score:.3f}")

        if self._passes(loading, LOADING_TEMPLATE):
            self._state = "loading"
            self._home_bright_since = None
            self._waiting_home_since = None
            self._set_stage("登录加载中")
            self._set_action("已进入登录后加载页，停止 BrownDustX Confirm 判断。")
            return True

        if self._passes(home_button, HOME_BUTTON_TEMPLATE):
            self._state = "clearing"
            self._waiting_home_since = None
            self._set_stage("清理公告")
            self._set_action("已检测到 home.png，进入公告清理/主页确认流程。")
            self._clear_popups_until_home(frame)
            return True

        return False

    def _wait_loading_then_home(self, frame) -> bool:
        loading = (
            MatchResult(score=-1.0, pixel_score=-1.0, position=(0, 0), size=(0, 0))
            if self._state == "waiting_home"
            else self._match(frame, LOADING_TEMPLATE)
        )
        home_button = self._match(frame, HOME_BUTTON_TEMPLATE)
        self.info_set("加载页面", f"{loading.score:.3f}")
        self.info_set("小屋按钮", f"{home_button.score:.3f}")

        if self._passes(loading, LOADING_TEMPLATE):
            self._home_bright_since = None
            self._waiting_home_since = None
            self._set_stage("登录加载中")
            self._set_action("检测到 loading.png，等待过场页面结束。")
            self.log_info(f"自动登录：登录加载中，score={loading.score:.3f}")
            return False

        if self._passes(home_button, HOME_BUTTON_TEMPLATE):
            self._state = "clearing"
            self._waiting_home_since = None
            return self._clear_popups_until_home(frame)

        self._home_bright_since = None
        self._state = "waiting_home"
        now = monotonic()
        if self._waiting_home_since is None:
            self._waiting_home_since = now

        grace_seconds = float(self.config.get("主页 UI 等待宽限秒数", 10.0))
        elapsed = now - self._waiting_home_since
        if elapsed >= grace_seconds:
            self._state = "force_clearing"
            self._set_stage("尝试清理公告")
            self._set_action(
                f"等待 home.png {elapsed:.1f} 秒仍未出现，开始尝试点击小屋位置清理公告。"
            )
            return self._clear_popups_until_home(frame)

        self._set_stage("等待主页 UI")
        self._set_action(
            f"loading.png 已消失，等待 home.png 出现 {elapsed:.1f}/{grace_seconds:.1f} 秒。"
        )
        self.log_info(
            "自动登录：等待登录加载结束并出现小屋按钮，"
            f"loading={loading.score:.3f}, home={home_button.score:.3f}"
        )
        return False

    def _clear_popups_until_home(self, frame) -> bool:
        home_button = self._match(frame, HOME_BUTTON_TEMPLATE)
        self.info_set("小屋按钮", f"{home_button.score:.3f}")
        require_home_match = self._state != "force_clearing"
        if require_home_match and not self._passes(home_button, HOME_BUTTON_TEMPLATE):
            self._home_bright_since = None
            self._state = "loading"
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
        now = monotonic()
        if now - self._last_clear_click_at >= 1.0:
            self._set_stage("清理公告")
            self._set_action(f"主页亮度不足，点击小屋按钮清理公告，ratio={ratio:.3f}。")
            self.log_info(f"自动登录：主页未恢复，点击小屋按钮清理公告，ratio={ratio:.3f}")
            self.operate_click(
                self._percent_config("小屋按钮点击 X 百分比"),
                self._percent_config("小屋按钮点击 Y 百分比"),
                after_sleep=0.2,
            )
            self._last_clear_click_at = now
        return False

    def _home_brightness_ratio(self, frame) -> float:
        template = self._load_template(HOME_TEMPLATE)
        frame_gray = self._to_gray(frame)
        frame_height, frame_width = frame_gray.shape[:2]
        scale = frame_width / REFERENCE_WIDTH
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

        template_mean = float(np.mean(template))
        if template_mean <= 0:
            return 0.0
        return float(np.mean(region) / template_mean)

    def _match(self, frame, spec: TemplateSpec) -> MatchResult:
        try:
            template = self._load_template(spec)
        except RuntimeError as exc:
            if spec.name not in self._missing_template_names:
                self._missing_template_names.add(spec.name)
                self.log_warning(str(exc), notify=True)
            return MatchResult(score=-1.0, pixel_score=-1.0, position=(0, 0), size=(0, 0))

        frame_gray = self._to_gray(frame)
        frame_height, frame_width = frame_gray.shape[:2]
        base_scale = frame_width / REFERENCE_WIDTH
        scales = self._candidate_scales(base_scale)
        best = MatchResult(score=-1.0, pixel_score=-1.0, position=(0, 0), size=(0, 0))

        for scale in scales:
            scaled_template = self._resize_template(template, scale)
            height, width = scaled_template.shape[:2]
            if height < 8 or width < 8 or height > frame_height or width > frame_width:
                continue

            result = cv2.matchTemplate(frame_gray, scaled_template, cv2.TM_CCOEFF_NORMED)
            _, max_value, _, max_location = cv2.minMaxLoc(result)
            if max_value > best.score:
                x, y = int(max_location[0]), int(max_location[1])
                region = frame_gray[y : y + height, x : x + width]
                best = MatchResult(
                    score=float(max_value),
                    pixel_score=self._pixel_similarity(region, scaled_template),
                    position=(x, y),
                    size=(int(width), int(height)),
                )

        return best

    def _load_template(self, spec: TemplateSpec) -> np.ndarray:
        if spec.name in self._templates:
            return self._templates[spec.name]

        path = TEMPLATE_DIR / spec.file_name
        template = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if template is None:
            raise RuntimeError(f"自动登录模板不存在或无法读取：{path}")

        if spec.crop is not None:
            template = self._crop_relative(template, spec.crop)

        self._templates[spec.name] = template
        return template

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

    def _is_browndustx_loading(self, frame, browndustx: MatchResult) -> bool:
        if not self._passes_strict(browndustx, BROWNDUSTX_TEMPLATE):
            return False

        text = self._ocr_match_region_text(frame, browndustx, name="browndustx_loading_ocr")
        self.info_set("BrownDustX OCR", text or "-")
        normalized = self._normalize_ocr_text(text)
        return (
            "browndustx" in normalized
            or "正在加载" in text
            or "请稍候" in text
            or "请稍侯" in text
            or "mod" in normalized
        )

    def _is_browndustx_confirm(
        self,
        frame,
        confirm: MatchResult,
    ) -> bool:
        if not self._passes_strict(confirm, CONFIRM_TEMPLATE):
            return False

        button_text = self._ocr_match_region_text(frame, confirm, name="browndustx_confirm_ocr")
        self.info_set("BrownDustX Confirm OCR", button_text or "-")
        return "confirm" in self._normalize_ocr_text(button_text)

    def _home_ratio_threshold(self) -> float:
        return float(self.config.get("小屋亮度比例阈值", 0.75))

    def _percent_config(self, key: str) -> float:
        return max(0.0, min(1.0, float(self.config[key]) / 100.0))

    def _reset_login_state(self):
        self._state = "waiting"
        self._home_bright_since = None
        self._waiting_home_since = None
        self._last_clear_click_at = 0.0
        self._finished = False
        self._set_stage("等待登录页")
        self._set_action("主页状态变化，重新进入自动登录识别。")

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
        offsets = (0.0, -0.08, 0.08, -0.16, 0.16)
        candidates = [1.0]
        candidates.extend(max(0.2, base_scale + offset) for offset in offsets)

        unique = []
        for scale in candidates:
            rounded = round(scale, 3)
            if rounded not in unique:
                unique.append(rounded)
        return unique

    @staticmethod
    def _resize_template(template: np.ndarray, scale: float) -> np.ndarray:
        if abs(scale - 1.0) < 0.001:
            return template
        interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
        return cv2.resize(template, None, fx=scale, fy=scale, interpolation=interpolation)

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
    def _pixel_similarity(region: np.ndarray, template: np.ndarray) -> float:
        if region.shape != template.shape:
            return -1.0
        diff = np.mean(np.abs(region.astype(np.float32) - template.astype(np.float32)))
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
    crop=(0.03, 0.80, 0.97, 0.95),
)

TOUCH_TO_START_TEMPLATE = TemplateSpec(
    name="touch_to_start",
    file_name="touch-to-start.png",
    threshold_key="TOUCH TO START 阈值",
    default_threshold=0.78,
)

LOADING_TEMPLATE = TemplateSpec(
    name="loading",
    file_name="loading.png",
    threshold_key="加载页面阈值",
    default_threshold=0.72,
    crop=(0.40, 0.44, 0.61, 0.58),
)

HOME_BUTTON_TEMPLATE = TemplateSpec(
    name="home_button",
    file_name="home.png",
    threshold_key="小屋按钮阈值",
    default_threshold=0.78,
)

HOME_TEMPLATE = TemplateSpec(
    name="home",
    file_name="home.png",
    threshold_key="小屋亮度比例阈值",
    default_threshold=0.75,
)
