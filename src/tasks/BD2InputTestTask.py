from datetime import datetime
from typing import Callable

import numpy as np
from ok.util.process import is_admin
from qfluentwidgets import FluentIcon

from src.tasks.BaseBD2Task import HOTKEY_CONFIG_NAME, BaseBD2Task


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

    def _hotkey_value(self, chinese_key: str, legacy_key: str, default):
        return self.key_config.get(chinese_key, self.key_config.get(legacy_key, default))

    @staticmethod
    def _action_display_name(action_name: str) -> str:
        action_names = {
            "short_key": "短按键",
            "mouse_click": "鼠标单击",
            "long_key": "长按键",
            "keyboard_debug": "键盘调试",
        }
        return action_names.get(action_name, action_name)

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


class BD2ShortKeyInputTestTask(_BD2InputProbeTask):
    output_prefix = "bd2_short_key_input_test"
    output_latest = "bd2_short_key_input_test_latest.txt"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "BD2 短按键测试"
        self.description = "测试一次短按键盘输入。"
        self.default_config.update(
            {
                "短按按键": "",
                HOTKEY_CONFIG_NAME: {},
            }
        )
        self.config_description.update(
            {
                "短按按键": "要短按的键。留空时使用全局设置里的返回键。",
                HOTKEY_CONFIG_NAME: "打开共享的游戏按键设置。",
            }
        )
        self.config_type.update(
            {
                HOTKEY_CONFIG_NAME: {"type": "global"},
            }
        )

    def run(self):
        short_key = self._config_value("短按按键", "Short Press Key", "")
        short_key = short_key or self._hotkey_value("返回键", "Back Key", "esc")
        return self.run_input_probe(
            "short_key",
            [f"short_key={short_key}"],
            lambda: self.send_key(short_key),
        )


class BD2MouseClickInputTestTask(_BD2InputProbeTask):
    output_prefix = "bd2_mouse_click_input_test"
    output_latest = "bd2_mouse_click_input_test_latest.txt"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "BD2 鼠标单击测试"
        self.description = "测试在指定屏幕百分比位置单击鼠标。"
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


class BD2LongKeyInputTestTask(_BD2InputProbeTask):
    output_prefix = "bd2_long_key_input_test"
    output_latest = "bd2_long_key_input_test_latest.txt"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "BD2 长按键测试"
        self.description = "测试按住一个键指定时长。"
        self.default_config.update(
            {
                "长按按键": "w",
                "长按秒数": 0.5,
            }
        )
        self.config_description.update(
            {
                "长按按键": "长按测试要按住的键。",
                "长按秒数": "长按按键需要保持多久。",
            }
        )

    def run(self):
        long_key = self._config_value("长按按键", "Long Press Key", "w")
        hold_seconds = float(self._config_value("长按秒数", "Long Press Seconds", 0.5))

        def hold_key():
            try:
                self.send_key_down(long_key)
                self.sleep(hold_seconds)
            finally:
                self.send_key_up(long_key)

        return self.run_input_probe(
            "long_key",
            [
                f"long_key={long_key}",
                f"hold_seconds={hold_seconds}",
            ],
            hold_key,
        )


class BD2KeyboardDebugInputTestTask(_BD2InputProbeTask):
    output_prefix = "bd2_keyboard_debug_input_test"
    output_latest = "bd2_keyboard_debug_input_test_latest.txt"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "BD2 键盘调试测试"
        self.description = "逐个尝试多种键盘输入方式并截图记录结果。"
        self.default_config.update(
            {
                "调试按键": "",
                "调试模式": (
                    "MAA受管按键,标准,激活后标准,字符消息,固定参数字符,"
                    "全部窗口,全部窗口字符"
                ),
                "按下保持秒数": 0.05,
                HOTKEY_CONFIG_NAME: {},
            }
        )
        self.config_description.update(
            {
                "调试按键": "要测试的键。留空时使用全局设置里的返回键。",
                "调试模式": "要测试的键盘发送方式，用英文逗号分隔。",
                "按下保持秒数": "每次按键按下后保持多久。",
                HOTKEY_CONFIG_NAME: "打开共享的游戏按键设置。",
            }
        )
        self.config_type.update(
            {
                HOTKEY_CONFIG_NAME: {"type": "global"},
            }
        )

    def run(self):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        debug_key = self._config_value("调试按键", "Debug Key", "")
        debug_key = debug_key or self._hotkey_value("返回键", "Back Key", "esc")
        modes = [
            self._debug_mode_value(mode.strip())
            for mode in str(self._config_value("调试模式", "Debug Modes", "")).split(",")
            if mode.strip()
        ]
        down_time = float(self._config_value("按下保持秒数", "Key Down Seconds", 0.05))
        step_sleep = float(self._config_value("每步等待秒数", "Step Sleep Seconds", 1.0))
        ocr_threshold = float(self._config_value("OCR 识别阈值", "OCR Threshold", 0.2))
        interaction = getattr(self.executor, "interaction", None)

        lines = [
            f"timestamp={timestamp}",
            "test=keyboard_debug",
            f"capture_method={self.capture_method_name}",
            *self._diagnostic_lines(),
            f"debug_key={debug_key}",
            f"debug_modes={','.join(modes)}",
            f"down_time={down_time}",
        ]
        if interaction is not None and hasattr(interaction, "describe_key_debug_targets"):
            lines.append("key_targets=" + " | ".join(interaction.describe_key_debug_targets()))
        lines.append("")

        previous_frame = self._capture_step(
            timestamp,
            "00_before",
            lines,
            previous_frame=None,
            ocr_threshold=ocr_threshold,
        )

        for index, mode in enumerate(modes, start=1):
            self.log_info(f"键盘调试：{mode} {debug_key}")
            try:
                if interaction is not None and hasattr(interaction, "send_key_debug"):
                    interaction.send_key_debug(debug_key, mode=mode, down_time=down_time)
                else:
                    self.send_key(debug_key, down_time=down_time)
                lines.append(f"mode_{index}={mode}:sent")
            except Exception as e:
                lines.append(f"mode_{index}={mode}:error:{e}")
            self.sleep(step_sleep)
            previous_frame = self._capture_step(
                timestamp,
                f"{index:02d}_after_{mode}",
                lines,
                previous_frame=previous_frame,
                ocr_threshold=ocr_threshold,
            )

        output_path = self.write_probe_text(
            self.output_latest,
            lines,
            info_label=self.input_test_label,
        )
        self.info_set(self.input_test_label, str(output_path))
        self.log_info(f"BD2 键盘调试完成：{output_path}", notify=True)
        return True

    @staticmethod
    def _debug_mode_value(mode: str) -> str:
        mode_map = {
            "MAA受管按键": "maa_managed",
            "MAA 受管按键": "maa_managed",
            "标准": "standard",
            "激活后标准": "activate_standard",
            "字符消息": "char",
            "固定参数字符": "fixed_char",
            "全部窗口": "all_windows",
            "全部窗口字符": "all_windows_char",
        }
        return mode_map.get(mode, mode)
