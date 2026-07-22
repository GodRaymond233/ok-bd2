import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import monotonic
from typing import Any, Callable

import cv2
import numpy as np
from ok import BaseTask, Box, Logger, og
from PIL import Image

from src.scene.BD2Scene import BD2Scene
from src.scene.ScreenPosition import ScreenPosition
from src.utils.ocr_utils import normalize_ocr_text

logger = Logger.get_logger(__name__)
PROBE_OUTPUT_DIR = Path("probe_outputs")
GREEN_MASK_TOLERANCE = 0
CARTRIDGE_RECENT_ENTRY_POINT = (0.7875, 0.9111111111111111)
RECENT_CARTRIDGE_SPECIAL_PAGE_SECONDS = 3.0
RECENT_CARTRIDGE_SPECIAL_PAGE_MAX_ACTIONS = 3


def green_mask_from_template(
    template: np.ndarray,
    tolerance: int = GREEN_MASK_TOLERANCE,
) -> np.ndarray:
    if template.ndim < 3:
        return np.full(template.shape[:2], 255, dtype=np.uint8)

    color = template[:, :, :3]
    tolerance = max(0, int(tolerance))
    green_pixels = (
        (color[:, :, 0] <= tolerance)
        & (color[:, :, 1] >= 255 - tolerance)
        & (color[:, :, 2] <= tolerance)
    )
    if template.shape[2] >= 4:
        green_pixels |= template[:, :, 3] == 0
    return np.where(green_pixels, 0, 255).astype(np.uint8)


class BaseBD2Task(BaseTask):
    DEFAULT_MOVE = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.visible = False
        self.scene: BD2Scene | None = None
        self.default_box = ScreenPosition(self)
        self._last_interval_action_time = {}
        self._action_interval_lock = threading.Lock()
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

    @property
    def thread_pool_executor(self) -> ThreadPoolExecutor | None:
        if og.my_app is None:
            return None
        return og.my_app.get_thread_pool_executor()

    @staticmethod
    def submit_periodic_task(delay: float, task: Callable, *args, **kwargs):
        if og.my_app is None:
            return None
        return og.my_app.submit_periodic_task(delay, task, *args, **kwargs)

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
        Image.fromarray(frame).save(output_path)
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

    def find_one_green_mask(
        self,
        *args,
        green_tolerance: int = GREEN_MASK_TOLERANCE,
        match_method=cv2.TM_CCORR_NORMED,
        **kwargs,
    ):
        kwargs["mask_function"] = lambda template: green_mask_from_template(
            template,
            tolerance=green_tolerance,
        )
        kwargs["match_method"] = match_method
        return self.find_one(*args, **kwargs)

    def find_green_mask_features(
        self,
        *args,
        green_tolerance: int = GREEN_MASK_TOLERANCE,
        match_method=cv2.TM_CCORR_NORMED,
        **kwargs,
    ):
        kwargs["mask_function"] = lambda template: green_mask_from_template(
            template,
            tolerance=green_tolerance,
        )
        kwargs["match_method"] = match_method
        return self.find_feature(*args, **kwargs)

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
            block=True,
            restore_cursor=restore_cursor,
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

    def _sleep_after_recognition(self) -> None:
        seconds = float(self.config.get("识别成功后等待秒数", 1.0))
        if seconds > 0:
            self.sleep(seconds)

    def open_cartridge_quick_switcher(
        self,
        ensure_home: Callable[[], bool],
        click_quick_switch: Callable[[], bool],
        confirm_quick_switch_page: Callable[[], bool],
    ) -> bool:
        """Open the recent cartridge, click quick-switch, and confirm its page."""
        if not ensure_home():
            return False

        # Fixed common flow: confirmed home -> recognition settle delay
        # -> recent cartridge -> OCR special pages for 3 seconds, timed from the click
        # -> recognize the quick-switch icon -> click the recognized center
        # -> confirm the cartridge selection page.
        self._sleep_after_recognition()
        self.info_set("当前阶段", "点击最近卡带")
        self.operate_click(*CARTRIDGE_RECENT_ENTRY_POINT, after_sleep=0.0)
        self._handle_recent_cartridge_special_pages()
        self.info_set("当前阶段", "寻找快速切换按钮")
        if not click_quick_switch():
            handled_after_timeout = self._handle_recent_cartridge_special_pages()
            if not handled_after_timeout:
                return False
            self.info_set("当前阶段", "特殊页面后重试快速切换按钮")
            if not click_quick_switch():
                return False
        return bool(confirm_quick_switch_page())

    def _handle_recent_cartridge_special_pages(
        self,
        timeout: float = RECENT_CARTRIDGE_SPECIAL_PAGE_SECONDS,
        interval: float = 0.25,
    ) -> bool:
        """OCR and dismiss PVP promotion, demotion, and season reward pages."""
        end_at = monotonic() + max(0.0, float(timeout))
        handled: set[str] = set()
        action_count = 0

        while True:
            boxes = self._recent_cartridge_ocr_boxes()
            text = " ".join(
                str(getattr(box, "name", ""))
                for box in boxes
                if getattr(box, "name", "")
            )
            self.info_set("最近卡带特殊页面 OCR", text or "-")
            normalized = normalize_ocr_text(text)

            action_name = ""
            target_box = None
            if "赛季奖励" in normalized and "点击画面即可返回" in normalized:
                action_name = "赛季奖励"
                target_box = self._find_ocr_box(boxes, "点击画面即可返回")
            elif "恭喜晋级" in normalized and "确认" in normalized:
                action_name = "恭喜晋级"
                target_box = self._find_ocr_box(boxes, "确认")
            elif "段位下滑" in normalized and "确认" in normalized:
                action_name = "段位下滑"
                target_box = self._find_ocr_box(boxes, "确认")

            if action_name and action_name not in handled and target_box is not None:
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

            if (
                monotonic() >= end_at
                or action_count >= RECENT_CARTRIDGE_SPECIAL_PAGE_MAX_ACTIONS
            ):
                break
            self.sleep(max(0.0, float(interval)))

        return bool(handled)

    def _recent_cartridge_ocr_boxes(self) -> list:
        try:
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
                name="最近卡带特殊页面",
            )
        except Exception as exc:
            self.info_set("最近卡带特殊页面 OCR 错误", str(exc))
            return []
        return list(boxes)

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

    def run_with_interval(
        self,
        func: Callable,
        interval: float,
        *args,
        action_name=None,
        **kwargs,
    ) -> Any:
        action_name = action_name or getattr(func, "__qualname__", repr(func))
        if not self._check_action_interval(action_name, interval):
            return False
        return func(*args, **kwargs)

    def mark_logged_in(self) -> None:
        if self.scene is not None:
            self.scene.set_logged_in(True)

    def is_main(self) -> bool:
        return bool(self.scene and self.scene.logged_in())

    def wait_main(self, time_out: float = 30, raise_if_not_found: bool = False):
        return self.wait_until(
            self.is_main,
            time_out=time_out,
            raise_if_not_found=raise_if_not_found,
        )
