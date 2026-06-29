import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np
from ok import BaseTask, Box, Logger, og
from PIL import Image

from src.scene.BD2Scene import BD2Scene
from src.scene.ScreenPosition import ScreenPosition

logger = Logger.get_logger(__name__)
PROBE_OUTPUT_DIR = Path("probe_outputs")
HOTKEY_CONFIG_NAME = "游戏按键设置"
HOTKEY_CONFIG_LEGACY_NAME = "Game Hotkey Config"
GREEN_MASK_TOLERANCE = 0


def green_mask_from_template(template: np.ndarray, tolerance: int = GREEN_MASK_TOLERANCE) -> np.ndarray:
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
        self.key_config = self.get_global_config(HOTKEY_CONFIG_NAME)
        if self.key_config is None:
            self.key_config = self.get_global_config(HOTKEY_CONFIG_LEGACY_NAME)
        self.default_box = ScreenPosition(self)
        self._last_interval_action_time = {}
        self._action_interval_lock = threading.Lock()

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

    def send_key(self, key, down_time=0.02, interval=-1, after_sleep=0, action_name=None) -> Any:
        if action_name is not None:
            if not self._check_action_interval(action_name, interval):
                return False
            interval = -1
        return super().send_key(
            key,
            down_time=down_time,
            interval=interval,
            after_sleep=after_sleep,
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
        self.sleep(after_sleep)
        return result

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
