import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from time import monotonic
from typing import Any, Callable

import cv2
import numpy as np
from ok import BaseTask, Box, Logger
from PIL import Image

from src.scene.BD2Scene import BD2Scene
from src.scene.ScreenPosition import ScreenPosition
from src.tasks.task_notifications import log_task_completion
from src.utils.home_confirmation import (
    HOME_ANNOUNCEMENT_CLEAR_RELATIVE_POINT,
    home_temporary_announcement_detected,
)
from src.utils.image_utils import (
    best_pixel_valid_match,
    green_mask_from_template,
    masked_zncc,
    pixel_similarity,
    resize_mask,
    resize_template,
    template_match_response,
    to_gray,
)
from src.utils.ocr_utils import normalize_ocr_text
from src.utils.template_resolution import offline_template_scale

logger = Logger.get_logger(__name__)
PROBE_OUTPUT_DIR = Path("probe_outputs")
GREEN_MASK_TOLERANCE = 0
CARTRIDGE_RECENT_ENTRY_POINT = (0.7875, 0.9111111111111111)
RECENT_CARTRIDGE_SPECIAL_PAGE_SECONDS = 3.0
RECENT_CARTRIDGE_SPECIAL_PAGE_MAX_ACTIONS = 3
FIEND_HUNT_REWARD_TITLE = "魔兽追踪者赛季奖励"
FIEND_HUNT_REWARD_DISMISS_TEXT = "奖励已发放至背包"
BEIJING_TIMEZONE = timezone(timedelta(hours=8))
RECENT_PVP_CARTRIDGE_TEMPLATE_FILE = "cartridge-image2-left-lower-cutout.png"
RECENT_PVP_CARTRIDGE_TEMPLATE_THRESHOLD = 0.95
RECENT_PVP_CARTRIDGE_PIXEL_THRESHOLD = 0.95
RECENT_PVP_CARTRIDGE_ZNCC_THRESHOLD = 0.85
PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = PROJECT_ROOT / "recognition-assets" / "template-assets"
# Shared lock for task instances whose ``__init__`` never ran (object.__new__);
# see ``BaseBD2Task._task_info_lock``.
_INFO_FALLBACK_LOCK = threading.RLock()


class CartridgeSpecialPageResult(Enum):
    ABSENT = "absent"
    HANDLED = "handled"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class RecentPvpCartridgeMatch:
    score: float = -1.0
    pixel_score: float = -1.0
    zncc_score: float = -1.0
    position: tuple[int, int] = (0, 0)
    size: tuple[int, int] = (0, 0)

    @property
    def passed(self) -> bool:
        return (
            self.score >= RECENT_PVP_CARTRIDGE_TEMPLATE_THRESHOLD
            and self.pixel_score >= RECENT_PVP_CARTRIDGE_PIXEL_THRESHOLD
            and self.zncc_score >= RECENT_PVP_CARTRIDGE_ZNCC_THRESHOLD
        )


class BaseBD2Task(BaseTask):
    DEFAULT_MOVE = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.visible = False
        self.scene: BD2Scene | None = None
        self.default_box = ScreenPosition(self)
        self._last_interval_action_time = {}
        self._action_interval_lock = threading.Lock()
        # ``task.info`` is a plain dict shared between the TaskExecutor worker
        # thread (info_set/info_clear/log_* and DailyBatchTask child resets)
        # and UI/diagnostics readers.  All mutations go through the mutators
        # below, which serialize on this lock so readers can take a consistent
        # copy via ``info_snapshot``/``task_info_snapshot``.  ``run_task_by_class``
        # rebinds ``task.info`` as a raw attribute, so every locked section must
        # re-read the attribute instead of caching the dict.
        self._info_lock = threading.RLock()
        self._last_home_announcement_clear_at = 0.0
        self._recent_pvp_cartridge_template_cache: (
            tuple[np.ndarray, np.ndarray] | None
        ) = None
        self.default_config.update(
            {
                "识别成功后等待秒数": 1.0,

            }
        )
        self.config_description.update(
            {
                "识别成功后等待秒数": "识别成功后，执行下一步点击或切换操作前等待多久。",
            }
        )

    def log_completion(self, message: str) -> None:
        """Publish a completion popup unless this task is running as a batch child."""

        log_task_completion(self, message)

    def _task_info_lock(self) -> threading.RLock:
        """The per-instance info lock, or a shared fallback for odd instances.

        Tests routinely build tasks via ``object.__new__`` without running
        ``__init__``; those instances have no ``_info_lock`` yet still go through
        these overrides, so they fall back to one process-wide lock instead of
        raising ``AttributeError``.
        """
        lock = self.__dict__.get("_info_lock")
        return _INFO_FALLBACK_LOCK if lock is None else lock

    def info_clear(self) -> None:
        with self._task_info_lock():
            super().info_clear()

    def info_incr(self, key, inc=1):
        with self._task_info_lock():
            return super().info_incr(key, inc)

    def info_add_to_list(self, key, item):
        with self._task_info_lock():
            return super().info_add_to_list(key, item)

    def info_set(self, key, value):
        with self._task_info_lock():
            return super().info_set(key, value)

    def info_add(self, key, count=1):
        with self._task_info_lock():
            return super().info_add(key, count)

    def info_snapshot(self) -> dict:
        """A consistent point-in-time copy of ``self.info``.

        The copy is taken under the same lock as every mutator, so it can never
        observe a half-applied update or hit "dictionary changed size during
        iteration".  ``self.info`` is re-read inside the lock because
        ``run_task_by_class`` rebinds the attribute as a whole.
        """
        with self._task_info_lock():
            return dict(getattr(self, "info", None) or {})

    @property
    def main_viewport(self) -> Box:
        return self.box_of_screen(0.05, 0.05, 0.95, 0.95, name="main_viewport")

    @property
    def capture_method_name(self) -> str:
        capture_method = getattr(self.executor.device_manager, "capture_method", None)
        return str(capture_method) if capture_method is not None else "<none>"

    def capture_frame(self, screenshot_name: str | None = None):
        frame = self.next_frame()
        if frame is None:
            raise RuntimeError("未能从 BD2 截取画面。")

        height, width = frame.shape[:2]
        self.info_set("截图方式", self.capture_method_name)
        self.info_set("游戏分辨率", f"{width}x{height}")
        if screenshot_name:
            self.save_frame(screenshot_name, frame)
        return frame

    def save_frame(self, name: str, frame) -> Path:
        PROBE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = PROBE_OUTPUT_DIR / f"{name}.png"
        if frame.ndim == 2:
            image = Image.fromarray(frame)
        elif frame.ndim == 3 and frame.shape[2] == 4:
            image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGRA2RGBA))
        elif frame.ndim == 3 and frame.shape[2] == 3:
            image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        else:
            raise ValueError(f"Unsupported screenshot frame shape: {frame.shape}")
        image.save(output_path)
        self.info_set("截图文件", str(output_path))
        return output_path

    def ocr_frame(self, frame=None, threshold: float = 0.2, screenshot: bool = False):
        frame = frame if frame is not None else self.capture_frame()
        boxes = self.ocr(frame=frame, threshold=threshold, log=True, screenshot=screenshot)
        texts = [box.name for box in boxes if getattr(box, "name", "")]
        self.info_set("OCR 文本数量", len(texts))
        self.info_set("OCR 文本", ", ".join(texts[:30]))
        return boxes

    def write_probe_text(
        self,
        name: str,
        lines: list[str],
        info_label: str = "OCR 文本文件",
    ) -> Path:
        PROBE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = PROBE_OUTPUT_DIR / name
        output_path.write_text("\n".join(lines), encoding="utf-8")
        self.info_set(info_label, str(output_path))
        return output_path

    @staticmethod
    def green_mask(template: np.ndarray, tolerance: int = GREEN_MASK_TOLERANCE) -> np.ndarray:
        return green_mask_from_template(template, tolerance=tolerance)

    def click(
        self,
        x: int | float | Box | list[Box] = -1,
        y=-1,
        move_back=None,
        name=None,
        interval=-1,
        move=None,
        down_time=0.02,
        after_sleep=0,
        key="left",
        hcenter=False,
        vcenter=False,
        action_name=None,
    ) -> Any:
        if action_name is not None:
            if not self._check_action_interval(action_name, interval):
                return False
            interval = -1

        if move is None:
            move = self.DEFAULT_MOVE
        if move_back is None:
            move_back = move

        return super().click(
            x,
            y,
            move_back=move_back,
            name=name,
            interval=interval,
            move=move,
            down_time=down_time,
            after_sleep=after_sleep,
            key=key,
            hcenter=hcenter,
            vcenter=vcenter,
        )

    def operate(self, func: Callable, block: bool = True, restore_cursor: bool = True):
        interaction = getattr(self.executor, "interaction", None)
        if interaction is not None and hasattr(interaction, "operate"):
            return interaction.operate(func, block=block, restore_cursor=restore_cursor)
        return func()

    def attempt_return_home(self, name: str = "自动返回主页") -> bool:
        """点右上角“房子”按钮一次（需调用方已确认不在主页）。正式路径会移动真实光标。"""
        try:
            x = float(self.config.get("返回主页按钮 X 百分比", 93.75)) / 100.0
            y = float(self.config.get("返回主页按钮 Y 百分比", 5.9)) / 100.0
            self.log_info(f"{name}：点击右上角主页按钮返回主界面。")
            self.operate_click(x, y, name=name, after_sleep=2.0, down_time=0.02)
            return True
        except Exception as exc:
            self.log_warning(f"{name}：点击返回主页按钮失败：{exc}")
            return False

    def auto_return_main_home(
        self,
        max_steps: int | None = None,
        *,
        notify_failure: bool = True,
    ) -> bool:
        """自动返回主页面（仅主页明确 / 控件正面命中时才动作）。

        规则：
        - 主页判定用 OCR 三要素（抽抽乐 + 左列任一入口）；已确认主页直接成功。
        - 非主页时：优先用房子按钮图像识别，OCR ``H`` 仅作辅助；也可命中左上角
          “返回”模板。证据不足时安全停止，绝不盲点。
        - 重试过程只记普通日志；失败后进入冷却，避免连续弹系统提示。
        """
        now = monotonic()
        cooldown_until = float(
            getattr(self, "_auto_return_home_cooldown_until", 0.0)
        )
        if now < cooldown_until:
            remaining = max(0.0, cooldown_until - now)
            message = f"自动返回主页：失败冷却中，{remaining:.1f} 秒后重试。"
            self.info_set("自动返回主页", message)
            self.log_info(message)
            return False

        step_limit = (
            int(self.config.get("自动返回主页最大步数", 6))
            if max_steps is None
            else int(max_steps)
        )
        settle = float(self.config.get("返回主页步间等待秒数", 2.5))
        acted = False
        failure_reason = "未能确认主页或返回控件。"

        for step in range(1, step_limit + 1):
            try:
                frame = self.capture_frame()
            except Exception as exc:
                failure_reason = f"截图失败：{exc}"
                break

            is_home, has_house_hint = self._home_scan(frame)
            if is_home:
                self._auto_return_home_cooldown_until = 0.0
                if acted:
                    self.log_info("自动返回主页：已回到主页。")
                return True

            if has_house_hint:
                # 抽抽乐偶发漏识别会把“主页”误判成非主页；先多帧复核主页。
                stable = True
                for _ in range(2):
                    self.sleep(0.4)
                    try:
                        probe_frame = self.capture_frame()
                    except Exception as exc:
                        stable = False
                        failure_reason = f"复核截图失败：{exc}"
                        break
                    probe_home, probe_house = self._home_scan(probe_frame)
                    if probe_home:
                        self._auto_return_home_cooldown_until = 0.0
                        return True
                    if not probe_house:
                        stable = False
                        failure_reason = "房子按钮信号不稳定，安全停止，不点击。"
                        break

                if not stable:
                    break

                house_match = self._match_house_button(frame)
                if house_match is not None:
                    frame_h, frame_w = frame.shape[:2]
                    x = (
                        house_match.position[0] + house_match.size[0] // 2
                    ) / max(1, frame_w)
                    y = (
                        house_match.position[1] + house_match.size[1] // 2
                    ) / max(1, frame_h)
                    detail = (
                        f"房子图像 @({house_match.position[0]},"
                        f"{house_match.position[1]})"
                    )
                else:
                    x = float(self.config.get("返回主页按钮 X 百分比", 93.75)) / 100.0
                    y = float(self.config.get("返回主页按钮 Y 百分比", 5.9)) / 100.0
                    detail = "H OCR 兜底坐标"

                self.log_info(
                    f"自动返回主页：第 {step} 步多帧复核确认非主页，"
                    f"点击{detail}。"
                )
                try:
                    self.operate_click(
                        x,
                        y,
                        name="自动返回主页-房子",
                        after_sleep=settle,
                    )
                except Exception as exc:
                    failure_reason = f"点击房子按钮失败：{exc}"
                    break
                acted = True
                continue

            if self._click_top_left_back(frame, step):
                self.sleep(settle)
                acted = True
                continue

            failure_reason = (
                "未确认到主页，也未正面识别到房子按钮或左上角返回控件。"
            )
            break
        else:
            failure_reason = f"{step_limit} 步内未回到主页。"

        return self._finish_auto_return_failure(
            failure_reason,
            notify=notify_failure,
        )

    def _finish_auto_return_failure(self, reason: str, *, notify: bool) -> bool:
        cooldown = max(
            0.0,
            float(self.config.get("自动返回主页失败冷却秒数", 30.0)),
        )
        self._auto_return_home_cooldown_until = monotonic() + cooldown
        message = f"自动返回主页：{reason}"
        if cooldown > 0:
            message += f"（{cooldown:.0f} 秒内不再自动重试）"
        self.info_set("自动返回主页", message)
        self.log_warning(message, notify=notify)
        return False

    def _match_house_button(self, frame):
        """用专门的 1080p 基准模板识别右上角小房子按钮。"""
        try:
            from src.utils import task_vision
            from src.utils.vision_models import TemplateSpec
        except Exception as exc:
            self.log_warning(f"自动返回主页：加载房子模板匹配依赖失败：{exc}")
            return None

        try:
            threshold = float(self.config.get("返回主页房子模板阈值", 0.85))
            spec = TemplateSpec(
                name="return_home_house_button",
                file_name="return_home_house_button.png",
                default_threshold=threshold,
                candidate_threshold=threshold,
                relative_roi=(0.90, 0.0, 1.0, 0.16),
                candidate_center_roi=(0.91, 0.0, 1.0, 0.13),
                scale_ratios=(0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15),
                min_pixel_score=0.70,
                min_zncc_score=0.70,
            )
            cache = self.__dict__.setdefault("_central_template_cache", {})
            result = task_vision.match_template(
                frame,
                spec,
                self.config,
                TEMPLATE_DIR,
                cache=cache,
                min_size=4,
            )
            if result.score < threshold:
                self.info_set(
                    "返回主页房子模板",
                    f"未命中 {result.score:.3f}/{threshold:.3f}",
                )
                return None
            center_x = result.position[0] + result.size[0] // 2
            center_y = result.position[1] + result.size[1] // 2
            self.info_set(
                "返回主页房子模板",
                f"{result.score:.3f}/{threshold:.3f} @{center_x},{center_y}",
            )
            return result
        except RuntimeError as exc:
            missing = self.__dict__.setdefault(
                "_missing_return_home_templates",
                set(),
            )
            if "return_home_house_button" not in missing:
                missing.add("return_home_house_button")
                self.log_warning(f"自动返回主页：房子模板不可用：{exc}")
            return None
        except Exception as exc:
            self.log_warning(f"自动返回主页：房子模板匹配失败：{exc}")
            return None

    def _ocr_boxes(self, frame, name: str):
        try:
            return list(
                self.ocr(
                    frame=frame,
                    threshold=0.2,
                    log=False,
                    name=name,
                )
            )
        except Exception:
            return []

    def _home_scan(self, frame) -> tuple[bool, bool]:
        """OCR 判定主页，并用房子按钮图像识别非主页返回入口。"""
        frame_h, frame_w = frame.shape[:2]
        tokens: list[str] = []
        boxes = self._ocr_boxes(frame, "自动返回主页")
        for box in boxes:
            name = str(getattr(box, "name", "") or "")
            if name:
                tokens.append(name)

        joined = "".join(tokens)
        left_hits = sum(
            1
            for keyword in ("我的小屋", "格鲁TALK", "街机游戏")
            if keyword in joined
        )
        is_home = "抽抽乐" in joined and left_hits >= 1
        if is_home:
            return True, False

        if self._match_house_button(frame) is not None:
            return False, True

        # 兼容旧 OCR；``H`` 不再是返回主页的硬条件。
        for box in boxes:
            name = str(getattr(box, "name", "") or "").strip().upper()
            if name != "H":
                continue
            try:
                x, y, width, height = (
                    int(box.x),
                    int(box.y),
                    int(box.width),
                    int(box.height),
                )
            except Exception:
                continue
            center_x = x + width // 2
            center_y = y + height // 2
            if center_x / frame_w > 0.7 and center_y / frame_h < 0.25:
                return False, True

        return False, False

    def _click_top_left_back(self, frame, step: int) -> bool:
        """左上角 ROI + 统一模板匹配“返回”条；命中才点击。"""
        try:
            from src.tasks.map_trade.models import TemplateSpec
            from src.utils import task_vision
        except Exception as exc:
            self.log_warning(f"自动返回主页：导入模板匹配失败：{exc}")
            return False
        try:
            threshold = float(self.config.get("左上角返回模板阈值", 0.55))
            spec = TemplateSpec(
                name="top_left_back_bar",
                # root 层 = 1080p 基准；统一 task_vision 按 frame/1920 等比缩放
                file_name="back_return_bar_arrow.png",
                default_threshold=threshold,
                roi=(0, 0, 270, 120),  # 1920x1080 参考：左上角约 300x140(@2560) 区域
                candidate_center_roi=(0.0, 0.0, 0.16, 0.14),
                scale_ratios=(0.95, 1.0, 1.05),
            )
            cache = self.__dict__.setdefault("_central_template_cache", {})

            def loader(_template_dir, template_spec):
                loaded = task_vision.load_template(
                    TEMPLATE_DIR, template_spec, cache=cache
                )
                return loaded[0], loaded[1]

            result = task_vision.match_template(
                frame,
                spec,
                self.config,
                TEMPLATE_DIR,
                cache=cache,
                min_size=8,
                loader=loader,
            )
            frame_h, frame_w = frame.shape[:2]
            if result.score < threshold:
                self.info_set("左上角返回模板", f"未命中 {result.score:.3f}/{threshold:.3f}")
                return False
            position = tuple(result.position)
            size = tuple(result.size)
            center_x = int(position[0] + size[0] // 2)
            center_y = int(position[1] + size[1] // 2)
            self.info_set(
                "左上角返回模板",
                f"{result.score:.3f}/{threshold:.3f} @{center_x},{center_y}",
            )
            self.log_info(
                f"自动返回主页：第 {step} 步 ROI 命中左上角返回 @({center_x},{center_y})，点击。"
            )
            self.operate_click(
                center_x / max(1, frame_w),
                center_y / max(1, frame_h),
                name="自动返回主页-左上返回",
                after_sleep=0.0,
            )
            return True
        except Exception as exc:
            self.log_warning(f"自动返回主页：左上角返回点击失败：{exc}")
            return False

    def operate_click(
        self,
        x: int | float | Box | list[Box] = -1,
        y=-1,
        restore_cursor=True,
        name=None,
        interval=-1,
        down_time=0.02,
        after_sleep=0,
        key="left",
        hcenter=False,
        vcenter=False,
        action_name=None,
    ) -> Any:
        action_name = action_name or "operate_click"
        if not self._check_action_interval(action_name, interval):
            return False
        try:
            click_log = self._click_log_message(
                x,
                y,
                int(self.width),
                int(self.height),
                str(name or action_name),
            )
        except Exception:
            click_log = f"{name or action_name}: target={x!r},{y!r}"
        interaction = getattr(self.executor, "interaction", None)
        background_click = bool(
            interaction is not None
            and hasattr(interaction, "background_click_enabled")
            and interaction.background_click_enabled()
        )
        result = self.operate(
            lambda: self.click(
                x,
                y,
                name=name,
                interval=-1,
                move=True,
                down_time=down_time,
                after_sleep=0,
                key=key,
                hcenter=hcenter,
                vcenter=vcenter,
            ),
            block=not background_click,
            restore_cursor=restore_cursor and not background_click,
        )
        self.info_set("鼠标点击", click_log)
        self.sleep(after_sleep)
        return result

    @staticmethod
    def _click_log_message(x, y, width: int, height: int, action_name: str) -> str:
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            if 0 < x < 1 or 0 < y < 1:
                client_x = int(width * x)
                client_y = int(height * y)
                return (
                    f"{action_name}: client=({client_x},{client_y}), "
                    f"relative=({float(x):.6f},{float(y):.6f})"
                )
            return f"{action_name}: client=({int(x)},{int(y)})"

        if isinstance(x, Box):
            return f"{action_name}: box={x.name or '-'} {x.box}"
        if isinstance(x, list):
            return f"{action_name}: boxes={len(x)}"
        return f"{action_name}: target={x!r},{y!r}"

    def drag_client(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
        duration: float = 0.7,
        after_sleep: float = 0.0,
    ) -> None:
        """Drag with foreground mouse input only; these tasks never send keys."""

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

    def scroll_client(
        self,
        relative_point: tuple[float, float],
        scroll_amount: int,
        count: int = 1,
        interval: float = 0.1,
        after_sleep: float = 0.0,
    ) -> None:
        """Send foreground mouse-wheel events at a relative client point."""

        frame = self.capture_frame()
        height, width = frame.shape[:2]
        x = round(max(0.0, min(1.0, relative_point[0])) * width)
        y = round(max(0.0, min(1.0, relative_point[1])) * height)
        wheel_count = max(1, int(count))
        wheel_interval = max(0.0, float(interval))
        interaction = getattr(self.executor, "interaction", None)
        if interaction is None or not hasattr(interaction, "scroll"):
            raise RuntimeError("当前交互对象不支持鼠标滚轮")

        def action():
            import win32api

            if hasattr(interaction, "force_activate"):
                interaction.force_activate()
            elif hasattr(interaction, "try_activate"):
                interaction.try_activate()
            capture = getattr(interaction, "capture", None)
            if capture is not None and hasattr(capture, "get_abs_cords"):
                win32api.SetCursorPos(capture.get_abs_cords(x, y))
            for index in range(wheel_count):
                interaction.scroll(x, y, int(scroll_amount))
                if index + 1 < wheel_count:
                    time.sleep(wheel_interval)

        self.operate(action, block=True, restore_cursor=True)
        self.sleep(after_sleep)

    def _sleep_after_recognition(self) -> None:
        seconds = float(self.config.get("识别成功后等待秒数", 1.0))
        if seconds > 0:
            self.sleep(seconds)

    def clear_temporary_home_announcement_if_needed(
        self,
        *,
        left_hits: int,
        required_left_hits: int,
        brightness: float,
        brightness_threshold: float,
        gacha_ocr_text: object,
        context: str,
    ) -> bool:
        """Clear a dimming announcement only when the other two home signals pass."""
        if not home_temporary_announcement_detected(
            left_hits=left_hits,
            required_left_hits=required_left_hits,
            brightness=brightness,
            brightness_threshold=brightness_threshold,
            gacha_ocr_text=gacha_ocr_text,
        ):
            return False

        now = monotonic()
        last_click_at = float(
            getattr(self, "_last_home_announcement_clear_at", 0.0)
        )
        if now - last_click_at < 1.0:
            return True
        self._last_home_announcement_clear_at = now

        clear_x, clear_y = HOME_ANNOUNCEMENT_CLEAR_RELATIVE_POINT
        self.info_set(
            "主页临时公告",
            f"{context}：亮度 {brightness:.3f}/{brightness_threshold:.3f}",
        )
        self.log_info(
            f"{context}：左列关键词和抽抽乐 OCR 已命中但亮度不足，"
            f"按登录公告流程点击清理位置，brightness={brightness:.3f}, "
            f"x={clear_x:.2%}, y={clear_y:.2%}。"
        )
        self._sleep_after_recognition()
        self.operate_click(clear_x, clear_y, after_sleep=0.2)
        return True

    def open_cartridge_quick_switcher(
        self,
        ensure_home: Callable[[], bool],
        click_quick_switch: Callable[[], bool],
        confirm_quick_switch_page: Callable[[], bool],
    ) -> bool:
        """Open the recent cartridge, click quick-switch, and confirm its page."""
        if not ensure_home():
            return False

        try:
            recent_cartridge_is_pvp = self._recent_cartridge_is_pvp()
        except RuntimeError as exc:
            self.info_set("最近卡带 PVP 模板错误", str(exc))
            self.log_warning(str(exc), notify=True)
            return False

        self._sleep_after_recognition()
        self.info_set("当前阶段", "点击最近卡带")
        self.operate_click(*CARTRIDGE_RECENT_ENTRY_POINT, after_sleep=0.0)
        special_pages = self._handle_recent_cartridge_special_pages(
            allow_pvp_pages=recent_cartridge_is_pvp,
        )
        if special_pages is CartridgeSpecialPageResult.BLOCKED:
            self._save_flow_diagnostic("cartridge_quick_switch_failed")
            return False
        self.info_set("当前阶段", "寻找快速切换按钮")
        quick_switch_opened = click_quick_switch()
        if not quick_switch_opened:
            special_pages = self._handle_recent_cartridge_special_pages(
                allow_pvp_pages=recent_cartridge_is_pvp,
            )
            if special_pages is CartridgeSpecialPageResult.BLOCKED:
                self._save_flow_diagnostic("cartridge_quick_switch_failed")
                return False
            if special_pages is CartridgeSpecialPageResult.HANDLED:
                self.info_set("当前阶段", "特殊页面后重试快速切换按钮")
                quick_switch_opened = click_quick_switch()
            elif not recent_cartridge_is_pvp:
                quick_switch_opened = self._retry_recent_cartridge_entry(
                    ensure_home=ensure_home,
                    click_quick_switch=click_quick_switch,
                )
        if not quick_switch_opened:
            self._save_flow_diagnostic("cartridge_quick_switch_failed")
            return False
        return bool(confirm_quick_switch_page())

    def _retry_recent_cartridge_entry(
        self,
        ensure_home: Callable[[], bool],
        click_quick_switch: Callable[[], bool],
    ) -> bool:
        """Re-click the recent cartridge once after a lost confirmed-home click.

        The first click can be dropped game-side without opening the panel
        (BUG-20260829-07: three identical clicks timed out for 10s each, the
        same click worked right after an app restart). Guarded by a fresh home
        confirmation so the entry point is never pressed over an open panel.
        """
        self.info_set("当前阶段", "重试点击最近卡带")
        if not ensure_home():
            return False
        self._sleep_after_recognition()
        self.info_set("当前阶段", "点击最近卡带")
        self.operate_click(*CARTRIDGE_RECENT_ENTRY_POINT, after_sleep=0.0)
        if self._handle_recent_cartridge_special_pages(
            allow_pvp_pages=False,
        ) is CartridgeSpecialPageResult.BLOCKED:
            return False
        return click_quick_switch()

    def _save_flow_diagnostic(self, name: str) -> None:
        """Persist a failure frame; report bundles pick up *_failed/_error stems."""
        try:
            self.save_frame(name, self.capture_frame())
        except Exception as exc:
            self.log_warning(f"诊断截图保存失败：{exc}")

    def _recent_cartridge_is_pvp(self) -> bool:
        """Detect whether clicking the recent cartridge may open a PVP page."""
        frame = self.capture_frame()
        result = self._match_recent_pvp_cartridge(frame)
        verdict = "PVP" if result.passed else "非 PVP"
        self.info_set(
            "最近卡带 PVP 模板",
            (
                f"{verdict} m={result.score:.3f} p={result.pixel_score:.3f} "
                f"z={result.zncc_score:.3f} box={result.position}+{result.size}"
            ),
        )
        return result.passed

    def _match_recent_pvp_cartridge(
        self,
        frame: np.ndarray,
    ) -> RecentPvpCartridgeMatch:
        template, mask = self._load_recent_pvp_cartridge_template()
        frame_gray = to_gray(frame)
        frame_height, frame_width = frame_gray.shape[:2]
        scale = offline_template_scale(
            RECENT_PVP_CARTRIDGE_TEMPLATE_FILE,
            frame_width,
            frame_height,
        )
        scaled_template = resize_template(template, scale)
        scaled_mask = resize_mask(mask, scale)
        height, width = scaled_template.shape[:2]
        if (
            height < 5
            or width < 5
            or height > frame_height
            or width > frame_width
        ):
            return RecentPvpCartridgeMatch(size=(width, height))

        try:
            response = template_match_response(
                frame_gray,
                scaled_template,
                scaled_mask,
            )
        except cv2.error as exc:
            raise RuntimeError(f"最近卡带 PVP 模板匹配失败：{exc}") from exc

        candidate = best_pixel_valid_match(
            response,
            frame_gray,
            scaled_template,
            scaled_mask,
            template_threshold=RECENT_PVP_CARTRIDGE_TEMPLATE_THRESHOLD,
            pixel_threshold=RECENT_PVP_CARTRIDGE_PIXEL_THRESHOLD,
            zncc_threshold=RECENT_PVP_CARTRIDGE_ZNCC_THRESHOLD,
        )
        if candidate is not None:
            return RecentPvpCartridgeMatch(
                score=candidate.score,
                pixel_score=candidate.pixel_score,
                zncc_score=candidate.zncc_score,
                position=candidate.location,
                size=(width, height),
            )

        _minimum, score, _minimum_location, location = cv2.minMaxLoc(response)
        x, y = location
        region = frame_gray[y : y + height, x : x + width]
        return RecentPvpCartridgeMatch(
            score=float(score),
            pixel_score=pixel_similarity(region, scaled_template, scaled_mask),
            zncc_score=masked_zncc(region, scaled_template, scaled_mask),
            position=(int(x), int(y)),
            size=(width, height),
        )

    def _load_recent_pvp_cartridge_template(
        self,
    ) -> tuple[np.ndarray, np.ndarray]:
        cached = getattr(self, "_recent_pvp_cartridge_template_cache", None)
        if cached is not None:
            return cached

        path = TEMPLATE_DIR / RECENT_PVP_CARTRIDGE_TEMPLATE_FILE
        raw = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if raw is None:
            raise RuntimeError(f"最近卡带 PVP 模板不存在或无法读取：{path}")
        if raw.ndim != 3 or raw.shape[2] < 4:
            raise RuntimeError(f"最近卡带 PVP 模板缺少 Alpha 通道：{path}")

        mask = np.where(raw[:, :, 3] > 0, 255, 0).astype(np.uint8)
        active_pixels = int(np.count_nonzero(mask))
        if active_pixels <= 0 or active_pixels >= mask.size:
            raise RuntimeError(f"最近卡带 PVP 模板 Alpha 遮罩无效：{path}")

        cached = (to_gray(raw), mask)
        self._recent_pvp_cartridge_template_cache = cached
        return cached

    def _handle_recent_cartridge_special_pages(
        self,
        timeout: float = RECENT_CARTRIDGE_SPECIAL_PAGE_SECONDS,
        interval: float = 0.25,
        allow_season_reward: bool | None = None,
        *,
        allow_pvp_pages: bool = True,
    ) -> CartridgeSpecialPageResult:
        """Dismiss fiend rewards on any cartridge and PVP pages when enabled."""
        if allow_season_reward is None:
            allow_season_reward = self._is_beijing_monday()
        end_at = monotonic() + max(0.0, float(timeout))
        handled: set[str] = set()
        action_count = 0
        fiend_reward_pending = False
        fiend_reward_clear_frames = 0

        while True:
            boxes = self._recent_cartridge_ocr_boxes()
            text = " ".join(
                str(getattr(box, "name", "")) for box in boxes
            )
            normalized = normalize_ocr_text(text)
            action_name, target_box = "", None
            if (
                FIEND_HUNT_REWARD_TITLE in normalized
                or FIEND_HUNT_REWARD_DISMISS_TEXT in normalized
            ):
                fiend_reward_pending = True
                fiend_reward_clear_frames = 0
                if FIEND_HUNT_REWARD_TITLE in normalized:
                    action_name = FIEND_HUNT_REWARD_TITLE
                    target_box = self._find_ocr_box(boxes, FIEND_HUNT_REWARD_DISMISS_TEXT)
            elif fiend_reward_pending:
                # An empty OCR result cannot prove that the overlay closed.
                fiend_reward_clear_frames = fiend_reward_clear_frames + 1 if normalized else 0
                if fiend_reward_clear_frames >= 2:
                    fiend_reward_pending = False
                    self.info_set("魔兽追踪者赛季奖励", "已确认关闭")
            if allow_pvp_pages and not fiend_reward_pending:
                text, action_name, target_box = self._pvp_special_page_action(
                    boxes,
                    allow_season_reward=allow_season_reward,
                )
            self.info_set("最近卡带特殊页面 OCR", text or "-")

            if (
                action_name
                and action_name not in handled
                and target_box is not None
                and action_count < RECENT_CARTRIDGE_SPECIAL_PAGE_MAX_ACTIONS
            ):
                point = self._ocr_box_center(target_box)
                if point is not None:
                    frame_width = max(1, int(self.width))
                    frame_height = max(1, int(self.height))
                    self.info_set("当前阶段", f"处理最近卡带{action_name}")
                    self.operate_click(
                        max(0.0, min(1.0, point[0] / frame_width)),
                        max(0.0, min(1.0, point[1] / frame_height)),
                        after_sleep=0.5,
                    )
                    handled.add(action_name)
                    action_count += 1
                    if action_name == FIEND_HUNT_REWARD_TITLE:
                        end_at = max(end_at, monotonic() + max(0.0, float(timeout)))

            if (
                monotonic() >= end_at
                or (
                    action_count >= RECENT_CARTRIDGE_SPECIAL_PAGE_MAX_ACTIONS
                    and not fiend_reward_pending
                )
            ):
                break
            self.sleep(max(0.0, float(interval)))

        if fiend_reward_pending:
            self.info_set("魔兽追踪者赛季奖励", "关闭未确认，停止卡带切换")
            return CartridgeSpecialPageResult.BLOCKED
        return (
            CartridgeSpecialPageResult.HANDLED
            if handled else CartridgeSpecialPageResult.ABSENT
        )

    @classmethod
    def _pvp_special_page_action(
        cls,
        boxes: list,
        *,
        allow_season_reward: bool,
    ) -> tuple[str, str, object | None]:
        """Return a strict paired PVP special-page action from one OCR frame."""
        text = " ".join(
            str(getattr(box, "name", ""))
            for box in boxes
            if getattr(box, "name", "")
        )
        normalized = normalize_ocr_text(text)
        if (
            allow_season_reward
            and "赛季奖励" in normalized
            and "点击画面即可返回" in normalized
        ):
            return text, "赛季奖励", cls._find_ocr_box(boxes, "点击画面即可返回")
        if "恭喜晋级" in normalized and "确认" in normalized:
            return text, "恭喜晋级", cls._find_ocr_box(boxes, "确认")
        if "段位下滑" in normalized and "确认" in normalized:
            return text, "段位下滑", cls._find_ocr_box(boxes, "确认")
        return text, "", None

    @staticmethod
    def _is_beijing_monday(now: datetime | None = None) -> bool:
        if now is None:
            now = datetime.now(timezone.utc)
        return now.astimezone(BEIJING_TIMEZONE).weekday() == 0

    def _pvp_special_page_ocr_boxes(
        self,
        frame: np.ndarray | None = None,
        *,
        name: str = "PVP 特殊页面",
    ) -> list:
        try:
            if frame is None:
                frame = self.capture_frame()
            config = getattr(self, "config", {})
            threshold = next(
                (
                    float(config[key])
                    for key in (
                        "PVP OCR 阈值",
                        "广场 OCR 阈值",
                        "跑商 OCR 阈值",
                        "跑图 OCR 阈值",
                    )
                    if key in config
                ),
                0.2,
            )
            boxes = self.ocr(
                frame=frame,
                threshold=threshold,
                target_height=720,
                log=False,
                name=name,
            )
        except Exception as exc:
            self.info_set(f"{name} OCR 错误", str(exc))
            return []
        return list(boxes)

    def _recent_cartridge_ocr_boxes(self) -> list:
        return self._pvp_special_page_ocr_boxes(name="最近卡带特殊页面")

    @staticmethod
    def _find_ocr_box(boxes: list, keyword: str):
        normalized_keyword = normalize_ocr_text(keyword)
        for box in boxes:
            if normalized_keyword in normalize_ocr_text(getattr(box, "name", "")):
                return box
        return None

    @staticmethod
    def _ocr_box_center(box) -> tuple[float, float] | None:
        values = tuple(getattr(box, key, None) for key in ("x", "y", "width", "height"))
        if any(value is None for value in values):
            raw_box = getattr(box, "box", None)
            if raw_box is None or len(raw_box) < 4:
                return None
            values = tuple(raw_box[:4])
        x, y, width, height = (float(value) for value in values)
        return x + width / 2, y + height / 2

    def _check_action_interval(self, action_name: Any, interval: float) -> bool:
        if interval <= 0:
            return True
        with self._action_interval_lock:
            now = time.time()
            last_time = self._last_interval_action_time.get(action_name, 0)
            if now - last_time < interval:
                return False
            self._last_interval_action_time[action_name] = now
            return True

    def mark_logged_in(self) -> None:
        if self.scene is not None:
            self.scene.set_logged_in(True)


def task_info_snapshot(task) -> dict:
    """Best-effort thread-safe copy of ``task.info`` for any task object.

    ``BaseBD2Task`` (and subclasses) expose a locked ``info_snapshot``; tasks
    outside that hierarchy — ok-framework tasks such as ``BD2TriggerTask`` or
    plain stubs — only get an unlocked ``dict`` copy.  For those, a concurrent
    mutation can still surface as ``RuntimeError`` mid-copy, which is swallowed
    into an empty snapshot so UI/diagnostics readers stay alive.
    Callers that must know about snapshot failures (diagnostics) perform their
    own strict copy instead of relying on this fallback.
    """
    snapshot = getattr(task, "info_snapshot", None)
    if callable(snapshot):
        return snapshot()
    try:
        return dict(getattr(task, "info", None) or {})
    except RuntimeError:
        return {}
