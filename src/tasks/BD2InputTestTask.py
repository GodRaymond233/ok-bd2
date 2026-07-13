from datetime import datetime
from typing import Callable

import numpy as np
from ok.util.process import is_admin
from qfluentwidgets import FluentIcon

from src.tasks.BaseBD2Task import BaseBD2Task


class _BD2InputProbeTask(BaseBD2Task):
    icon = FluentIcon.GAME
    output_prefix = "bd2_input_test"
    output_latest = "bd2_input_test_latest.txt"
    input_test_label = "输入测试文件"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.visible = True
        self.default_config.update(
            {
                "每步等待秒数": 1.0,
                "OCR 识别阈值": 0.2,
            }
        )
        self.config_description.update(
            {
                "每步等待秒数": "每次输入后等待多久再截图。",
                "OCR 识别阈值": "每一步记录 OCR 文本时使用的最低可信度。",
            }
        )

    def run_input_probe(
        self,
        action_name: str,
        details: list[str],
        action: Callable[[], None],
    ) -> bool:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        step_sleep = float(self._config_value("每步等待秒数", "Step Sleep Seconds", 1.0))
        ocr_threshold = float(self._config_value("OCR 识别阈值", "OCR Threshold", 0.2))
        lines = [
            f"timestamp={timestamp}",
            f"test={action_name}",
            f"capture_method={self.capture_method_name}",
            *self._diagnostic_lines(),
            *details,
            "",
        ]

        before_frame = self._capture_step(
            timestamp,
            "00_before",
            lines,
            previous_frame=None,
            ocr_threshold=ocr_threshold,
        )

        self.log_info(f"输入测试：{self._action_display_name(action_name)}")
        action()
        self.sleep(step_sleep)

        self._capture_step(
            timestamp,
            "01_after",
            lines,
            previous_frame=before_frame,
            ocr_threshold=ocr_threshold,
        )
        lines.append(f"result={action_name}")

        output_path = self.write_probe_text(
            self.output_latest,
            lines,
            info_label=self.input_test_label,
        )
        self.info_set(self.input_test_label, str(output_path))
        self.log_info(f"BD2 输入测试完成：{output_path}", notify=True)
        return True

    def _diagnostic_lines(self) -> list[str]:
        interaction = getattr(self.executor, "interaction", None)
        hwnd_window = getattr(self.executor.device_manager, "hwnd_window", None)
        lines = [
            f"interaction={interaction.__class__.__name__ if interaction else '<none>'}",
            f"is_admin={bool(is_admin())}",
        ]
        if hwnd_window is not None:
            lines.extend(
                [
                    f"hwnd={getattr(hwnd_window, 'hwnd', 0)}",
                    f"hwnd_title={getattr(hwnd_window, 'hwnd_title', '')}",
                    f"hwnd_exists={bool(getattr(hwnd_window, 'exists', False))}",
                    f"hwnd_foreground={bool(getattr(hwnd_window, 'visible', False))}",
                    f"hwnd_pos={getattr(hwnd_window, 'x', 0)},{getattr(hwnd_window, 'y', 0)}",
                    (
                        f"hwnd_size={getattr(hwnd_window, 'width', 0)}x"
                        f"{getattr(hwnd_window, 'height', 0)}"
                    ),
                ]
            )
        return lines

    @staticmethod
    def _percent_to_relative(value) -> float:
        return max(0.0, min(1.0, float(value) / 100.0))

    def _config_value(self, chinese_key: str, legacy_key: str, default):
        return self.config.get(chinese_key, self.config.get(legacy_key, default))

    @staticmethod
    def _action_display_name(action_name: str) -> str:
        return "鼠标单击" if action_name == "mouse_click" else action_name

    def _capture_step(
        self,
        timestamp: str,
        step_name: str,
        lines: list[str],
        previous_frame,
        ocr_threshold: float,
    ):
        frame = self.capture_frame(f"{self.output_prefix}_{timestamp}_{step_name}")
        boxes = self.ocr_frame(frame=frame, threshold=ocr_threshold)
        texts = [box.name for box in boxes if getattr(box, "name", "")]
        lines.append(f"[{step_name}]")
        lines.append(f"ocr_text_count={len(texts)}")
        if texts:
            lines.append("ocr_texts=" + " | ".join(texts[:30]))
        if previous_frame is not None:
            delta = float(np.mean(np.abs(frame.astype(np.int16) - previous_frame.astype(np.int16))))
            lines.append(f"visual_delta_mean={delta:.4f}")
        lines.append("")
        return frame


class BD2MouseClickInputTestTask(_BD2InputProbeTask):
    output_prefix = "bd2_mouse_click_input_test"
    output_latest = "bd2_mouse_click_input_test_latest.txt"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "BD2 鼠标单击测试"
        self.description = "测试在指定屏幕百分比位置单击鼠标。"
        self.group_name = "测试"
        self.group_icon = FluentIcon.BOOK_SHELF
        self.default_config.update(
            {
                "点击 X 百分比": 9,
                "点击 Y 百分比": 5,
            }
        )
        self.config_description.update(
            {
                "点击 X 百分比": "鼠标点击位置的横向百分比，范围 0 到 100。",
                "点击 Y 百分比": "鼠标点击位置的纵向百分比，范围 0 到 100。",
            }
        )

    def run(self):
        click_x = self._percent_to_relative(
            self._config_value("点击 X 百分比", "Click X Percent", 9)
        )
        click_y = self._percent_to_relative(
            self._config_value("点击 Y 百分比", "Click Y Percent", 5)
        )
        return self.run_input_probe(
            "mouse_click",
            [f"click={click_x:.3f},{click_y:.3f}"],
            lambda: self.operate_click(click_x, click_y),
        )
