# ruff: noqa: E501

import os

from ok import Box

from src import GAME_EXE, HWND_CLASS
from src.compat.windows_graphics import enable_windows_10_wgc
from src.game_path import calculate_pc_exe_path
from src.interaction.BD2Interaction import BD2Interaction
from src.process_feature import process_feature
from src.ui.responsive_task_config import install_responsive_task_config_ui

version = "dev"

enable_windows_10_wgc()
install_responsive_task_config_ui()

def blur_area(width, height):
    return Box(width * 0, height * 0.9769, to_x=width * 0.0943, to_y=height * 1)


config = {
    "custom_tasks": True,
    "debug": False,
    "use_gui": True,
    "config_folder": "configs",
    "global_configs": [],
    "blur_area": blur_area,
    "gui_icon": "icons/icon.png",
    "wait_until_before_delay": 0,
    "wait_until_check_delay": 0,
    "wait_until_settle_time": 0,
    "ocr": {
        "default": {
            "lib": "onnxocr",
            "auto_simplify": True,
            "params": {
                "use_openvino": True,
            },
        },
    },
    "windows": {
        "exe": GAME_EXE,
        "hwnd_class": HWND_CLASS,
        "calculate_pc_exe_path": calculate_pc_exe_path,
        "interaction": [BD2Interaction],
        "capture_method": [
            "WGC",
            "BitBlt_RenderFull",
            "ForegroundBitBlt",
        ],
        "check_hdr": False,
        "force_no_hdr": False,
        "require_bg": True,
        "start_exe": True,
    },
    "start_timeout": 120,
    "window_size": {
        "width": 1200,
        "height": 800,
        "min_width": 600,
        "min_height": 450,
    },
    "supported_resolution": {
        "ratio": "16:9",
        "min_size": (1280, 720),
        "resize_to": [
            (3840, 2160),
            (2560, 1440),
            (1920, 1080),
            (1280, 720),
        ],
    },
    "links": {
        "default": {
            "github": "https://github.com/GodRaymond233/ok-bd2",
            "share": "Download from https://github.com/GodRaymond233/ok-bd2",
            "faq": "https://github.com/GodRaymond233/ok-bd2",
        }
    },
    "about": """
        <p style="color:red;">
        <strong>This software is free and open-source.</strong>
        It is intended for personal learning and research around Python,
        computer vision, and UI automation.
        </p>
        <p style="color:red;">
        Use automation only after understanding the risks for your account and game client.
        </p>
    """,
    "log_file": "logs/ok-bd2.log",
    "error_log_file": "logs/ok-bd2_error.log",
    "screenshots_folder": "screenshots",
    "gui_title": "ok-bd2",
    "template_matching": {
        "coco_feature_json": os.path.join("assets", "coco_annotations.json"),
        "default_horizontal_variance": 0.002,
        "default_vertical_variance": 0.002,
        "default_threshold": 0.7,
        "feature_processor": process_feature,
    },
    "template_tab": {
        "generate_label_enum": True,
        "label_enum_relative_path": "src/Labels",
    },
    "version": version,
    "my_app": [
        "src.globals",
        "Globals",
    ],
    "onetime_tasks": [
        ["src.tasks.DailyBatchTask", "DailyBatchTask"],
        ["src.tasks.DailyTask", "DailyTask"],
        ["src.tasks.QuickHuntTask", "QuickHuntTask"],
        ["src.tasks.BargainLevelTask", "BargainLevelTask"],
        ["src.tasks.QuickSuppressionTask", "QuickSuppressionTask"],
        ["src.tasks.SquareGoddessTask", "SquareGoddessTask"],
        ["src.tasks.MapTradeTask", "MapTradeTask"],
        ["src.tasks.MapCollectionTask", "MapCollectionTask"],
        ["src.tasks.FreeGachaTask", "FreeGachaTask"],
        ["src.tasks.PVPTask", "PVPTask"],
        ["src.tasks.BD2InputTestTask", "BD2MouseClickInputTestTask"],
        ["src.tasks.BD2InputTestTask", "BD2MouseWheelInputTestTask"],
        ["src.tasks.BD2ProbeTask", "BD2ProbeTask"],
        ["src.tasks.BD2OneTimeTask", "BD2OneTimeTask"],
        ["src.tasks.LauncherTask", "LauncherTask"],
        ["src.tasks.BD2DiagnosisTask", "BD2DiagnosisTask"],
    ],
    "trigger_tasks": [
        ["src.tasks.trigger.AutoLoginTask", "AutoLoginTask"],
    ],
    "custom_tabs": [
        ["src.ui.BD2StatusTab", "BD2StatusTab"],
        ["src.ui.AutoLoginStatusTab", "AutoLoginStatusTab"],
    ],
    "scene": ["src.scene.BD2Scene", "BD2Scene"],
}
