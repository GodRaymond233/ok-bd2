from datetime import datetime
from typing import Callable

import numpy as np
from ok.device.intercation import PostMessageInteraction
from ok.util.process import is_admin
from qfluentwidgets import FluentIcon

from src.interaction.BD2Interaction import (
    CLICK_MODE_OPTIONS,
    CLICK_MODE_STANDARD,
)
from src.tasks.BaseBD2Task import BaseBD2Task
from src.utils.calibration import FHD_1080

REFERENCE_WIDTH = FHD_1080.width
REFERENCE_HEIGHT = FHD_1080.height
DEFAULT_WHEEL_REGION = (
    228 / REFERENCE_WIDTH,
    117 / REFERENCE_HEIGHT,
    463 / REFERENCE_WIDTH,
    959 / REFERENCE_HEIGHT,
)
WHEEL_DIRECTIONS = {"向上": 1, "向下": -1}
REFERENCE_POINT_MODE_KEY = "点击单个点位置坐标"
REFERENCE_POINT_X_KEY = "单点横坐标像素"
REFERENCE_POINT_Y_KEY = "单点纵坐标像素"
BACKGROUND_MOUSE_MOVE_KEY = "先发送后台移动消息"
CLICK_MODE_KEY = "点击方式"


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
        action: Callable[[np.ndarray], None],
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
        action(before_frame)
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
        self.log_completion(f"BD2 输入测试完成：{output_path}")
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

    def _add_reference_point_config(self, default_x: int, default_y: int) -> None:
        self.default_config.update(
            {
                REFERENCE_POINT_MODE_KEY: False,
                REFERENCE_POINT_X_KEY: default_x,
                REFERENCE_POINT_Y_KEY: default_y,
            }
        )
        self.config_description.update(
            {
                REFERENCE_POINT_MODE_KEY: (
                    "开启后使用下面的1920×1080参考像素点；关闭时继续使用原百分比配置。"
                ),
                REFERENCE_POINT_X_KEY: "1920×1080参考分辨率下的横坐标，运行时自动换算。",
                REFERENCE_POINT_Y_KEY: "1920×1080参考分辨率下的纵坐标，运行时自动换算。",
            }
        )
        self.config_type.update(
            {
                REFERENCE_POINT_X_KEY: {"min": 0, "max": REFERENCE_WIDTH, "step": 1},
                REFERENCE_POINT_Y_KEY: {"min": 0, "max": REFERENCE_HEIGHT, "step": 1},
            }
        )

    def _configured_reference_point(
        self,
        default_x: int,
        default_y: int,
    ) -> tuple[float, float]:
        x = max(
            0,
            min(REFERENCE_WIDTH, int(self.config.get(REFERENCE_POINT_X_KEY, default_x))),
        )
        y = max(
            0,
            min(REFERENCE_HEIGHT, int(self.config.get(REFERENCE_POINT_Y_KEY, default_y))),
        )
        return x / REFERENCE_WIDTH, y / REFERENCE_HEIGHT

    def _config_value(self, chinese_key: str, legacy_key: str, default):
        return self.config.get(chinese_key, self.config.get(legacy_key, default))

    @staticmethod
    def _action_display_name(action_name: str) -> str:
        names = {
            "mouse_click": "鼠标单击",
            "background_mouse_click": "后台鼠标单击",
            "mouse_wheel_up": "鼠标滚轮向上",
            "mouse_wheel_down": "鼠标滚轮向下",
        }
        return names.get(action_name, action_name)

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
        self._add_reference_point_config(173, 54)

    def _configured_click_point(self) -> tuple[bool, float, float]:
        use_reference_point = bool(self.config.get(REFERENCE_POINT_MODE_KEY, False))
        if use_reference_point:
            click_x, click_y = self._configured_reference_point(173, 54)
        else:
            click_x = self._percent_to_relative(
                self._config_value("点击 X 百分比", "Click X Percent", 9)
            )
            click_y = self._percent_to_relative(
                self._config_value("点击 Y 百分比", "Click Y Percent", 5)
            )
        return use_reference_point, click_x, click_y

    def run(self):
        use_reference_point, click_x, click_y = self._configured_click_point()
        return self.run_input_probe(
            "mouse_click",
            [
                "coordinate_mode="
                + ("reference_pixel_point" if use_reference_point else "percent_point"),
                f"click={click_x:.6f},{click_y:.6f}",
            ],
            lambda _frame: self.operate_click(click_x, click_y),
        )


class BD2BackgroundMouseClickInputTestTask(BD2MouseClickInputTestTask):
    output_prefix = "bd2_background_mouse_click_input_test"
    output_latest = "bd2_background_mouse_click_input_test_latest.txt"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "BD2 后台鼠标点击测试"
        self.description = (
            "仅向游戏窗口发送鼠标消息，不移动物理鼠标，也不屏蔽用户键盘或鼠标输入。"
        )
        self.default_config[BACKGROUND_MOUSE_MOVE_KEY] = True
        self.config_description[BACKGROUND_MOUSE_MOVE_KEY] = (
            "开启时先发送后台鼠标移动消息再点击；关闭时只发送按下和抬起消息。"
        )

    def run(self):
        use_reference_point, click_x, click_y = self._configured_click_point()
        send_move = bool(self.config.get(BACKGROUND_MOUSE_MOVE_KEY, True))
        return self.run_input_probe(
            "background_mouse_click",
            [
                "coordinate_mode="
                + ("reference_pixel_point" if use_reference_point else "percent_point"),
                f"click={click_x:.6f},{click_y:.6f}",
                f"post_mouse_move={str(send_move).lower()}",
                "physical_cursor_move=false",
                "block_input=false",
            ],
            lambda frame: self._perform_background_click(frame, click_x, click_y, send_move),
        )

    def _perform_background_click(
        self,
        frame: np.ndarray,
        relative_x: float,
        relative_y: float,
        send_move: bool,
    ) -> None:
        interaction = getattr(self.executor, "interaction", None)
        if not isinstance(interaction, PostMessageInteraction):
            raise RuntimeError("当前交互对象不支持 PostMessage 后台点击")

        height, width = frame.shape[:2]
        x = int(width * relative_x)
        y = int(height * relative_y)
        PostMessageInteraction.click(
            interaction,
            x,
            y,
            move_back=False,
            name="bd2_background_mouse_click",
            down_time=0.02,
            move=send_move,
            key="left",
        )


class BD2ClickModeSelectorTask(BaseBD2Task):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "点击方式切换"
        self.description = (
            "开发版全局点击方式。后台点击为测试功能，只切换点击，不改变拖拽和滚轮。"
        )
        self.icon = FluentIcon.GAME
        self.visible = True
        self.default_config.update(
            {
                "_enabled": True,
                CLICK_MODE_KEY: CLICK_MODE_STANDARD,
            }
        )
        self.config_description[CLICK_MODE_KEY] = (
            "正式版方案会按现有流程占用鼠标；测试版方案只向游戏窗口发送后台鼠标消息。"
        )
        self.config_type[CLICK_MODE_KEY] = {
            "type": "drop_down",
            "options": list(CLICK_MODE_OPTIONS),
        }

    def on_create(self):
        self._enabled = bool(self.config.get("_enabled", True))
        self._bound_interaction = None
        self._bind_interaction()

    def _bind_interaction(self) -> None:
        interaction = getattr(self.executor, "interaction", None)
        if interaction is None or interaction is self._bound_interaction:
            return
        if not hasattr(interaction, "set_click_mode_provider"):
            raise RuntimeError("当前交互对象不支持点击方式切换")
        interaction.set_click_mode_provider(self._selected_click_mode)
        self._bound_interaction = interaction

    def _selected_click_mode(self) -> str:
        if not self.enabled:
            return CLICK_MODE_STANDARD
        return str(self.config.get(CLICK_MODE_KEY, CLICK_MODE_STANDARD))

    def should_trigger(self):
        self._bind_interaction()
        return False

    def on_destroy(self):
        interaction = self._bound_interaction
        if interaction is not None and hasattr(interaction, "set_click_mode_provider"):
            interaction.set_click_mode_provider(None)
        self._bound_interaction = None


class BD2MouseWheelInputTestTask(_BD2InputProbeTask):
    output_prefix = "bd2_mouse_wheel_input_test"
    output_latest = "bd2_mouse_wheel_input_test_latest.txt"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "BD2 鼠标滚轮测试"
        self.description = "在指定区域内单击聚焦，并测试向上或向下滚动鼠标滚轮。"
        self.group_name = "测试"
        self.group_icon = FluentIcon.BOOK_SHELF
        left, top, right, bottom = DEFAULT_WHEEL_REGION
        self.default_config.update(
            {
                "滚轮方向": "向上",
                "滚轮次数": 9,
                "滚轮间隔秒数": 0.1,
                "区域左 X 百分比": left * 100,
                "区域上 Y 百分比": top * 100,
                "区域右 X 百分比": right * 100,
                "区域下 Y 百分比": bottom * 100,
            }
        )
        self.config_description.update(
            {
                "滚轮方向": "选择向上或向下滚动。",
                "滚轮次数": "发送独立滚轮事件的次数，默认 9 次。",
                "滚轮间隔秒数": "相邻滚轮事件之间的等待时间，默认 0.1 秒。",
                "区域左 X 百分比": "滚轮测试区域左边界相对游戏客户区的横向百分比。",
                "区域上 Y 百分比": "滚轮测试区域上边界相对游戏客户区的纵向百分比。",
                "区域右 X 百分比": "滚轮测试区域右边界相对游戏客户区的横向百分比。",
                "区域下 Y 百分比": "滚轮测试区域下边界相对游戏客户区的纵向百分比。",
            }
        )
        self.config_type.update(
            {
                "滚轮方向": {"type": "drop_down", "options": list(WHEEL_DIRECTIONS)},
                "滚轮次数": {"min": 1, "max": 100, "step": 1},
                "滚轮间隔秒数": {"min": 0.0, "max": 2.0, "step": 0.05},
                "区域左 X 百分比": {"min": 0.0, "max": 100.0, "step": 0.1},
                "区域上 Y 百分比": {"min": 0.0, "max": 100.0, "step": 0.1},
                "区域右 X 百分比": {"min": 0.0, "max": 100.0, "step": 0.1},
                "区域下 Y 百分比": {"min": 0.0, "max": 100.0, "step": 0.1},
            }
        )
        self._add_reference_point_config(960, 1067)

    def run(self):
        direction_name = str(self.config.get("滚轮方向", "向上"))
        if direction_name not in WHEEL_DIRECTIONS:
            direction_name = "向上"
            self.log_warning("滚轮方向配置无效，已改用向上。")
        count = max(1, min(100, int(self.config.get("滚轮次数", 9))))
        interval = max(0.0, min(2.0, float(self.config.get("滚轮间隔秒数", 0.1))))
        region = self._configured_wheel_region()
        use_reference_point = bool(self.config.get(REFERENCE_POINT_MODE_KEY, False))
        if use_reference_point:
            center = self._configured_reference_point(960, 1067)
        else:
            center = ((region[0] + region[2]) / 2, (region[1] + region[3]) / 2)
        reference_region = tuple(
            round(value * (REFERENCE_WIDTH if index % 2 == 0 else REFERENCE_HEIGHT))
            for index, value in enumerate(region)
        )
        reference_center = (
            round(center[0] * REFERENCE_WIDTH),
            round(center[1] * REFERENCE_HEIGHT),
        )
        action_name = "mouse_wheel_up" if direction_name == "向上" else "mouse_wheel_down"
        details = [
            "click_before_scroll=true",
            "coordinate_mode="
            + ("reference_pixel_point" if use_reference_point else "percent_region_center"),
            "region_reference=" + ",".join(str(value) for value in reference_region),
            f"scroll_point_reference={reference_center[0]},{reference_center[1]}",
            f"scroll_point_relative={center[0]:.6f},{center[1]:.6f}",
            f"scroll_direction={direction_name}",
            f"scroll_amount={WHEEL_DIRECTIONS[direction_name]}",
            f"scroll_count={count}",
            f"scroll_interval_seconds={interval:.3f}",
        ]
        return self.run_input_probe(
            action_name,
            details,
            lambda frame: self._perform_wheel_action(
                frame,
                center,
                WHEEL_DIRECTIONS[direction_name],
                count,
                interval,
            ),
        )

    def _configured_wheel_region(self) -> tuple[float, float, float, float]:
        defaults = tuple(value * 100 for value in DEFAULT_WHEEL_REGION)
        values = (
            self._percent_to_relative(
                self.config.get("区域左 X 百分比", defaults[0])
            ),
            self._percent_to_relative(
                self.config.get("区域上 Y 百分比", defaults[1])
            ),
            self._percent_to_relative(
                self.config.get("区域右 X 百分比", defaults[2])
            ),
            self._percent_to_relative(
                self.config.get("区域下 Y 百分比", defaults[3])
            ),
        )
        left, right = sorted((values[0], values[2]))
        top, bottom = sorted((values[1], values[3]))
        return left, top, right, bottom

    def _perform_wheel_action(
        self,
        frame: np.ndarray,
        relative_point: tuple[float, float],
        scroll_amount: int,
        count: int,
        interval: float,
    ) -> None:
        interaction = getattr(self.executor, "interaction", None)
        if interaction is None or not hasattr(interaction, "scroll"):
            raise RuntimeError("当前交互对象不支持鼠标滚轮测试")

        height, width = frame.shape[:2]
        x = round(relative_point[0] * width)
        y = round(relative_point[1] * height)

        def action() -> None:
            interaction.click(
                x,
                y,
                move_back=False,
                name="bd2_mouse_wheel_focus",
                down_time=0.02,
                move=True,
            )
            for index in range(count):
                interaction.scroll(x, y, scroll_amount)
                if index + 1 < count:
                    self.sleep(interval)

        self.operate(action, block=True, restore_cursor=True)
