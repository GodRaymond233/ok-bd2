import re
from pathlib import Path
from time import monotonic

import cv2
import numpy as np
from qfluentwidgets import FluentIcon

from src.tasks.BaseBD2Task import BaseBD2Task
from src.tasks.map_trade.models import MatchResult, TemplateSpec
from src.utils import task_vision
from src.utils.calibration import FHD_1080
from src.utils.home_confirmation import (
    HOME_GACHA_OCR_RELATIVE_ROI,
    home_confirmation_passes,
)
from src.utils.image_utils import (
    crop_relative,
)
from src.utils.ocr_utils import normalize_ocr_text

REFERENCE_WIDTH = FHD_1080.width
REFERENCE_HEIGHT = FHD_1080.height
PROJECT_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_DIR = PROJECT_ROOT / "recognition-assets" / "template-assets"
PRELOGIN_STATES = frozenset({"waiting", "browndustx", "waiting_update", "downloading"})
DOWNLOAD_PROGRESS_PATTERN = re.compile(
    r"(?<!\d)(?:100(?:[.,]0+)?|\d{1,2}(?:[.,]\d+)?)\s*[%％]"
)


class AutoLoginTask(BaseBD2Task):
    status_keys = [
        "阶段",
        "内部状态",
        "最后动作",
        "状态",
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
        "登录页 OCR",
        "更新下载",
        "下载进度",
        "更新下载点击",
        "TOUCH TO START",
        "TOUCH TO START 阈值",
        "加载页面",
        "加载页面阈值",
        "小屋按钮",
        "小屋按钮阈值",
        "小屋按钮遮挡阈值",
        "小屋亮度比例",
        "小屋亮度比例阈值",
        "主页抽抽乐 OCR",
        "主页 OCR 阈值",
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
                "主页 OCR 阈值": 0.2,
                "主页 UI 等待宽限秒数": 15.0,
                "主页连续确认秒数": 3.0,
                "登录后主页总等待秒数": 300.0,
                "登录超时重试间隔秒数": 60.0,
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
        self._login_retry_not_before = 0.0
        self._last_clear_click_at = 0.0
        self._last_confirm_click_at = 0.0
        self._last_download_click_at = 0.0
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

        if self._login_retry_not_before and monotonic() < self._login_retry_not_before:
            return False

        try:
            frame = self.capture_frame()
        except RuntimeError:
            if self._state == "waiting":
                self._set_stage("等待游戏启动")
                self._set_action("游戏窗口尚未就绪，等待脚本自动启动游戏。")
            return False

        if self._state in PRELOGIN_STATES:
            confirmed, _button, _spec, _ratio, _gacha_text = (
                self._home_confirmation_signals(frame)
            )
            if confirmed:
                self.mark_logged_in()
                self._finished = True
                self._state = "done"
                self._set_stage("已完成")
                self._set_action("主页三项信号已确认，跳过自动登录。")
                self.log_info(
                    "自动登录：主页按钮、亮度和抽抽乐 OCR 已同时命中，跳过登录流程。",
                    notify=True,
                )
                return False

        if self._state == "clearing":
            return self._clear_popups_until_home(frame)
        if self._state in ("waiting_loading", "loading", "waiting_home"):
            return self._wait_loading_then_home(frame)

        return self._wait_browndustx_then_login(frame)

    def _wait_browndustx_then_login(
        self,
        frame,
        browndustx: MatchResult | None = None,
    ) -> bool:
        boxes, login_text = self._login_page_ocr(frame)
        download_button = self._find_update_download_button(boxes)
        if download_button is not None:
            self._handle_update_download_prompt(download_button)
            return False

        progress = self._download_progress_text(boxes)
        if progress:
            self._state = "downloading"
            self.info_set("更新下载", "正在下载")
            self.info_set("下载进度", progress)
            self.info_set("BrownDustX", "-")
            self.info_set("BrownDustX 像素", "-")
            self.info_set("BrownDustX Confirm", "-")
            self.info_set("BrownDustX Confirm 像素", "-")
            self.info_set("BrownDustX Confirm OCR", "-")
            self.info_set("TOUCH TO START", "-")
            self._set_stage("更新下载中")
            self._set_action(f"检测到游戏数据正在下载，当前进度 {progress}，继续等待。")
            self.log_info(f"自动登录：游戏数据正在下载，progress={progress}")
            return False

        if browndustx is None:
            browndustx = self._match(frame, BROWNDUSTX_TEMPLATE)
            self.info_set("BrownDustX", f"{browndustx.score:.3f}")
            self.info_set("BrownDustX 像素", f"{browndustx.pixel_score:.3f}")

        # Confirm must be checked independently. The BrownDustX panel contains
        # version-dependent text, so its coarse template cannot gate the stable
        # Confirm button.
        confirm = self._match(frame, CONFIRM_TEMPLATE)
        self.info_set("BrownDustX Confirm", f"{confirm.score:.3f}")
        self.info_set("BrownDustX Confirm 像素", f"{confirm.pixel_score:.3f}")
        if self._is_browndustx_confirm(frame, confirm):
            self._handle_browndustx_confirm_match(confirm)
            return False

        confirm_box = self._find_browndustx_confirm_ocr_box(boxes)
        if confirm_box is not None:
            self.info_set("BrownDustX Confirm OCR", getattr(confirm_box, "name", "CONFIRM"))
            self._handle_browndustx_confirm_ocr(confirm_box)
            return False

        touch_to_start = self._match(frame, TOUCH_TO_START_TEMPLATE)
        self.info_set("TOUCH TO START", f"{touch_to_start.score:.3f}")
        self.info_set("加载页面", "-")
        self.info_set("小屋按钮", "-")
        if self._passes(touch_to_start, TOUCH_TO_START_TEMPLATE):
            self.info_set("更新下载", "-")
            self.info_set("下载进度", "-")
            self._click_login_after_touch(touch_to_start)
            return False

        if self._is_browndustx_present(browndustx):
            self._state = "browndustx"
            self.info_set("BrownDustX OCR", login_text or "-")
            self._set_stage("BrownDustX 加载")
            self._set_action("等待 BrownDustX Confirm、更新下载提示或 TOUCH TO START。")
        elif self._state == "downloading":
            self._set_stage("等待更新下载完成")
            self._set_action("当前帧未读取到下载进度，等待下载转场或 TOUCH TO START。")
        elif self._state == "waiting_update":
            self._set_stage("等待更新或登录页")
            self._set_action("Confirm 已处理，等待更新下载提示或 TOUCH TO START。")
        else:
            self._state = "waiting"
            self._set_stage("等待登录页")
            self._set_action("等待 BrownDustX、Confirm、更新下载提示或 TOUCH TO START。")
        self.log_info(
            "自动登录：登录前等待中，"
            f"browndustx={browndustx.score:.3f}, confirm={confirm.score:.3f}, "
            f"touch={touch_to_start.score:.3f}, state={self._state}"
        )
        return False

    def _handle_browndustx_confirm_match(self, confirm: MatchResult) -> None:
        self._state = "waiting_update"
        self._set_stage("BrownDustX 异常确认")
        self._set_action("检测到 BrownDustX Confirm，点击确认按钮。")
        self.log_info(f"自动登录：检测到 BrownDustX Confirm，score={confirm.score:.3f}")
        now = monotonic()
        if now - self._last_confirm_click_at < 2.0:
            return
        self._sleep_after_recognition()
        self._click_match_center(confirm, after_sleep=1.0)
        self._last_confirm_click_at = now

    def _handle_browndustx_confirm_ocr(self, confirm_box) -> None:
        self._state = "waiting_update"
        self._set_stage("BrownDustX 异常确认")
        self._set_action("模板未命中，但 OCR 检测到 BrownDustX Confirm，点击文字中心。")
        now = monotonic()
        if now - self._last_confirm_click_at < 2.0:
            return
        self.log_info("自动登录：通过 BrownDustX 上下文与精确 CONFIRM OCR 处理确认页。")
        self._sleep_after_recognition()
        if self._click_ocr_box_center(
            confirm_box,
            status_key="BrownDustX Confirm 点击",
            after_sleep=1.0,
        ):
            self._last_confirm_click_at = now

    def _handle_update_download_prompt(self, download_button) -> None:
        self._state = "downloading"
        self.info_set("更新下载", "等待确认")
        self.info_set("下载进度", "-")
        self._set_stage("确认更新下载")
        self._set_action("检测到游戏数据下载确认页，点击右侧“下载”按钮。")
        now = monotonic()
        if now - self._last_download_click_at < 3.0:
            return
        self.log_info("自动登录：检测到游戏数据下载确认页，点击 OCR 下载按钮中心。")
        self._sleep_after_recognition()
        if self._click_ocr_box_center(
            download_button,
            status_key="更新下载点击",
            after_sleep=1.0,
        ):
            self._last_download_click_at = now
            self.info_set("更新下载", "已点击下载")

    def _login_page_ocr(self, frame) -> tuple[list, str]:
        try:
            boxes = list(
                self.ocr(
                    frame=frame,
                    threshold=float(self.config.get("BrownDustX OCR 阈值", 0.2)),
                    target_height=720,
                    log=False,
                    name="自动登录页面",
                )
            )
        except Exception as exc:
            self.info_set("登录页 OCR", f"错误：{exc}")
            self.info_set("更新下载", "-")
            self.info_set("下载进度", "-")
            return [], ""

        text = " ".join(
            str(getattr(box, "name", ""))
            for box in boxes
            if getattr(box, "name", "")
        )
        self.info_set("登录页 OCR", text or "-")
        self.info_set("更新下载", "-")
        self.info_set("下载进度", "-")
        return boxes, text

    def _find_browndustx_confirm_ocr_box(self, boxes: list):
        combined = self._normalize_ocr_text(
            " ".join(str(getattr(box, "name", "")) for box in boxes)
        )
        if "browndustx" not in combined and "bdx" not in combined:
            return None
        return next(
            (
                box
                for box in boxes
                if self._normalize_ocr_text(getattr(box, "name", "")) == "confirm"
            ),
            None,
        )

    def _find_update_download_button(self, boxes: list):
        combined = self._normalize_ocr_text(
            " ".join(str(getattr(box, "name", "")) for box in boxes)
        )
        if "下载容量" not in combined or "可用空间" not in combined:
            return None

        cancel_box = next(
            (
                box
                for box in boxes
                if self._normalize_ocr_text(getattr(box, "name", "")) == "取消"
            ),
            None,
        )
        if cancel_box is None:
            return None
        cancel_center = self._ocr_box_center(cancel_box)
        if cancel_center is None:
            return None

        candidates = []
        for box in boxes:
            if self._normalize_ocr_text(getattr(box, "name", "")) != "下载":
                continue
            center = self._ocr_box_center(box)
            if center is None or center[0] <= cancel_center[0]:
                continue
            row_tolerance = 1.5 * max(
                12.0,
                float(getattr(cancel_box, "height", 0) or 0),
                float(getattr(box, "height", 0) or 0),
            )
            if abs(center[1] - cancel_center[1]) > row_tolerance:
                continue
            candidates.append((abs(center[1] - cancel_center[1]), center[0], box))
        if not candidates:
            return None
        return min(candidates, key=lambda candidate: (candidate[0], candidate[1]))[2]

    def _download_progress_text(self, boxes: list) -> str:
        combined = self._normalize_ocr_text(
            " ".join(str(getattr(box, "name", "")) for box in boxes)
        )
        if "正在下载" not in combined:
            return ""
        match = DOWNLOAD_PROGRESS_PATTERN.search(combined)
        return match.group(0) if match else ""

    def _click_ocr_box_center(
        self,
        box,
        *,
        status_key: str,
        after_sleep: float,
    ) -> bool:
        point = self._ocr_box_center(box)
        if point is None:
            return False
        x, y = round(point[0]), round(point[1])
        self.info_set(status_key, f"{x},{y}")
        self.operate_click(x, y, after_sleep=after_sleep)
        return True

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
        self._login_clicked_at = monotonic()
        self._waiting_home_since = monotonic()
        self._last_clear_click_at = 0.0
        self._login_retry_not_before = 0.0

    def _wait_loading_then_home(self, frame) -> bool:
        now = monotonic()

        if self._state == "waiting_loading":
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
            return self._clear_popups_until_home(frame, home_button, home_spec)

        if self._login_wait_timed_out(now):
            self._handle_login_wait_timeout(now)
            return False

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
            return self._clear_popups_until_home(
                frame,
                home_button,
                home_spec,
                allow_dimmed=True,
            )

        self._set_stage("等待主页 UI")
        self._set_action(f"登录后等待 home.png 出现 {elapsed:.1f}/{grace_seconds:.1f} 秒。")
        self.log_info(f"自动登录：登录后等待小屋按钮出现，home={home_button.score:.3f}")
        return False

    def _login_wait_timed_out(self, now: float) -> bool:
        if self._login_clicked_at is None:
            return False
        total_seconds = float(self.config.get("登录后主页总等待秒数", 300.0))
        return now - self._login_clicked_at > total_seconds

    def _handle_login_wait_timeout(self, now: float) -> None:
        waited = now - (self._login_clicked_at or 0.0)
        retry_delay = float(self.config.get("登录超时重试间隔秒数", 60.0))
        self.log_warning(
            f"自动登录：登录后等待主页 UI 超时，已等待 {waited:.0f} 秒，"
            f"重置登录流程并延后 {retry_delay:.0f} 秒重试。",
            notify=True,
        )
        self.info_set("状态", "登录后等待主页超时")
        self._reset_login_state()
        self._login_retry_not_before = now + retry_delay

    def _clear_popups_until_home(
        self,
        frame,
        home_button: MatchResult | None = None,
        home_spec: TemplateSpec | None = None,
        allow_dimmed: bool = False,
    ) -> bool:
        if home_button is None:
            home_button, home_spec = self._match_home_button(frame)
        elif home_spec is None:
            home_spec = HOME_BUTTON_TEMPLATE
        assert home_spec is not None
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
        gacha_text = self._home_gacha_ocr_text(frame)
        self.info_set("主页抽抽乐 OCR", gacha_text or "-")

        confirmed = home_confirmation_passes(
            button_found=home_found,
            brightness_ratio=ratio,
            brightness_threshold=self._home_ratio_threshold(),
            gacha_ocr_text=gacha_text,
        )
        if confirmed:
            now = monotonic()
            if self._home_bright_since is None:
                self._home_bright_since = now
                self._set_stage("主页确认中")
                self._set_action("主页三项信号已命中，开始连续确认。")
                return False

            stable_seconds = float(self.config.get("主页连续确认秒数", 3.0))
            elapsed = now - self._home_bright_since
            self._set_stage("主页确认中")
            self._set_action(f"主页三项信号持续正常 {elapsed:.1f}/{stable_seconds:.1f} 秒。")
            if now - self._home_bright_since >= stable_seconds:
                self.mark_logged_in()
                self._finished = True
                self._state = "done"
                self._login_retry_not_before = 0.0
                self._set_stage("已完成")
                self._set_action("主页三项信号已连续确认，自动登录流程结束。")
                self.log_info(
                    "自动登录：主页按钮、亮度和抽抽乐 OCR 已连续确认，流程结束。",
                    notify=True,
                )
            return False

        self._home_bright_since = None
        self._clear_home_popup(ratio)
        return False

    def _home_confirmation_signals(
        self,
        frame,
    ) -> tuple[bool, MatchResult, TemplateSpec, float, str]:
        home_button, home_spec = self._match_home_button(frame)
        ratio = self._home_brightness_ratio(frame)
        gacha_text = self._home_gacha_ocr_text(frame)
        self.info_set("小屋按钮", f"{home_button.score:.3f}")
        self.info_set("小屋亮度比例", f"{ratio:.3f}")
        self.info_set("主页抽抽乐 OCR", gacha_text or "-")
        confirmed = home_confirmation_passes(
            button_found=self._passes(home_button, home_spec),
            brightness_ratio=ratio,
            brightness_threshold=self._home_ratio_threshold(),
            gacha_ocr_text=gacha_text,
        )
        return confirmed, home_button, home_spec, ratio, gacha_text

    def _home_gacha_ocr_text(self, frame) -> str:
        crop = self._crop_relative(frame, HOME_GACHA_OCR_RELATIVE_ROI)
        if crop.size == 0:
            return ""
        try:
            boxes = self.ocr(
                frame=crop,
                threshold=float(self.config.get("主页 OCR 阈值", 0.2)),
                target_height=720,
                log=False,
                name="主页抽抽乐",
            )
        except Exception as exc:
            self.info_set("主页抽抽乐 OCR 错误", str(exc))
            return ""
        return " ".join(box.name for box in boxes if getattr(box, "name", ""))

    def _clear_home_popup(self, ratio: float) -> None:
        self._home_bright_since = None
        now = monotonic()
        if now - self._last_clear_click_at < 1.0:
            return

        self._state = "clearing"
        self._set_stage("清理公告")
        clear_x = self._percent_config("公告清理点击 X 百分比")
        clear_y = self._percent_config("公告清理点击 Y 百分比")
        self._set_action(f"主页三项信号未齐全，点击公告清理位置，ratio={ratio:.3f}。")
        self.log_info(
            "自动登录：主页三项信号未齐全，点击公告清理位置，"
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
        return task_vision.brightness_ratio(
            frame,
            spec,
            (
                self._percent_config("小屋按钮点击 X 百分比"),
                self._percent_config("小屋按钮点击 Y 百分比"),
            ),
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

    def _match_home_button(self, frame) -> tuple[MatchResult, TemplateSpec]:
        return self._match_best(frame, HOME_BUTTON_TEMPLATES)

    def _match(self, frame, spec: TemplateSpec) -> MatchResult:
        empty = self._empty_match()
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
        self._last_confirm_click_at = 0.0
        self._last_download_click_at = 0.0
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

    _normalize_ocr_text = staticmethod(normalize_ocr_text)
    _crop_relative = staticmethod(crop_relative)


BROWNDUSTX_TEMPLATE = TemplateSpec(
    name="browndustx",
    file_name="browndustx.png",
    threshold_key="BrownDustX 阈值",
    default_threshold=0.82,
    candidate_threshold=0.0,
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
