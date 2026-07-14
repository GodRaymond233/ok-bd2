from pathlib import Path
from time import monotonic

import cv2
import numpy as np
from qfluentwidgets import FluentIcon

from src.tasks.BaseBD2Task import BaseBD2Task
from src.utils.template_resolution import offline_template_scale

REFERENCE_WIDTH = 1920
TEMPLATE_PATH = (
    Path(__file__).resolve().parents[2]
    / "offline-train"
    / "train-source-screenshots"
    / "image"
    / "UI_loading_black.png"
)

# All interaction positions are ratios derived from the supplied 1920x1080 points.
SUPPRESSION_POINTS = {
    "4号位": (0.875, 0.6851851851851852),
    "5号位": (0.9333333333333333, 0.7324074074074074),
}
QUICK_SWITCH_POINT = (0.4739583333333333, 0.9259259259259259)
CHAPTER_TWO_POINT = (0.45364583333333336, 0.8981481481481481)
CHAPTER_ONE_POINT = (0.359375, 0.9018518518518519)
NEW_CARTRIDGE_NOTICE = "立刻前往探索告示板"


class QuickSuppressionTask(BaseBD2Task):
    status_keys = [
        "启用",
        "状态",
        "刷几号位",
        "当前阶段",
        "完成循环数",
        "loading 出现",
        "loading 消失",
        "卡带确认 OCR",
        "Log",
        "Warning",
        "Error",
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "刷压制等级"
        self.description = "在第一章战斗地图中开始，同时要求第二章也处于战斗地图中"
        self.icon = FluentIcon.SYNC
        self.group_name = "自动刷级"
        self.group_icon = FluentIcon.SYNC
        self.visible = True
        self._loading_template: np.ndarray | None = None
        self.default_config.update(
            {
                "启用": True,
                "刷几号位": "4号位",
                "加载页面阈值": 0.72,
                "压制 OCR 阈值": 0.2,
                "loading 出现等待秒数": 10.0,
                "loading 消失等待秒数": 45.0,
                "loading 识别间隔秒数": 0.5,
            }
        )
        self.config_description.update(
            {
                "刷几号位": "选择循环开启4号位或5号位压制，默认4号位。",
                "加载页面阈值": "UI_loading_black.png 的最低匹配可信度。",
                "压制 OCR 阈值": "确认新卡带提示文字时使用的最低可信度。",
                "loading 出现等待秒数": "点击章节后等待加载画面出现的最长时间。",
                "loading 消失等待秒数": "加载画面出现后等待其消失的最长时间。",
                "loading 识别间隔秒数": "连续识别加载画面的时间间隔。",
            }
        )
        self.config_type.update(
            {
                "刷几号位": {
                    "type": "drop_down",
                    "options": ["4号位", "5号位"],
                },
                "加载页面阈值": {"min": 0.5, "max": 0.95, "step": 0.01},
                "压制 OCR 阈值": {"min": 0.05, "max": 0.95, "step": 0.01},
                "loading 出现等待秒数": {"min": 2.0, "max": 60.0, "step": 1.0},
                "loading 消失等待秒数": {"min": 10.0, "max": 120.0, "step": 1.0},
                "loading 识别间隔秒数": {"min": 0.1, "max": 2.0, "step": 0.1},
            }
        )

    def run(self):
        if not bool(self.config.get("启用", True)):
            self.info_set("状态", "刷压制等级已禁用。")
            self.log_info("刷压制等级已禁用。")
            return True

        slot = str(self.config.get("刷几号位", "4号位"))
        if slot not in SUPPRESSION_POINTS:
            slot = "4号位"
            self.log_warning("刷几号位配置无效，已改用4号位。")

        self.info_set("刷几号位", slot)
        self.info_set("完成循环数", 0)
        self.info_set("状态", "刷压制等级运行中。")
        self.log_info(f"刷压制等级：启动{slot}循环。")
        self.sleep(1.0)

        completed_cycles = 0
        while True:
            if not self._run_cycle(slot):
                self.info_set("状态", "刷压制等级确认失败，任务结束。")
                return False
            completed_cycles += 1
            self.info_set("完成循环数", completed_cycles)
            self.log_info(f"刷压制等级：已完成 {completed_cycles} 个循环。")

    def _run_cycle(self, slot: str) -> bool:
        suppression_point = SUPPRESSION_POINTS[slot]

        self.info_set("当前阶段", f"开启{slot}压制并切换第二章")
        self.operate_click(*suppression_point, after_sleep=2.0)
        if not self._switch_chapter("第二章", CHAPTER_TWO_POINT):
            return False

        self.info_set("当前阶段", f"开启{slot}压制并切换第一章")
        self.operate_click(*suppression_point, after_sleep=2.0)
        return self._switch_chapter("第一章", CHAPTER_ONE_POINT)

    def _switch_chapter(self, chapter_name: str, chapter_point: tuple[float, float]) -> bool:
        self.operate_click(*QUICK_SWITCH_POINT, after_sleep=2.0)
        self.operate_click(*chapter_point)

        if not self._wait_for_loading_cycle(chapter_name):
            return False

        self.sleep(2.0)
        frame = self.capture_frame()
        text = self._ocr_text(frame, name=f"刷压制等级_{chapter_name}")
        self.info_set("卡带确认 OCR", text or "-")
        if self._contains_notice(text):
            self.log_info(f"刷压制等级：已确认进入{chapter_name}新卡带。")
            return True

        self.log_info(f"刷压制等级：{chapter_name}未识别到“{NEW_CARTRIDGE_NOTICE}”。")
        return False

    def _wait_for_loading_cycle(self, chapter_name: str) -> bool:
        interval = max(0.1, float(self.config.get("loading 识别间隔秒数", 0.5)))
        appear_end = monotonic() + float(self.config.get("loading 出现等待秒数", 10.0))
        while monotonic() <= appear_end:
            score = self._loading_score(self.capture_frame())
            self.info_set("loading 出现", f"{score:.3f}")
            if score >= self._loading_threshold():
                break
            self.sleep(interval)
        else:
            self.log_info(f"刷压制等级：切换{chapter_name}时未检测到加载画面。")
            return False

        disappear_end = monotonic() + float(self.config.get("loading 消失等待秒数", 45.0))
        while monotonic() <= disappear_end:
            score = self._loading_score(self.capture_frame())
            self.info_set("loading 消失", f"{score:.3f}")
            if score < self._loading_threshold():
                return True
            self.sleep(interval)

        self.log_info(f"刷压制等级：切换{chapter_name}后加载画面未在限定时间内消失。")
        return False

    def _loading_score(self, frame) -> float:
        template = self._load_loading_template()
        frame_gray = self._to_gray(frame)
        scale = offline_template_scale(
            TEMPLATE_PATH,
            frame_gray.shape[1],
            frame_gray.shape[0],
        )
        if abs(scale - 1.0) >= 0.001:
            interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
            template = cv2.resize(
                template,
                None,
                fx=scale,
                fy=scale,
                interpolation=interpolation,
            )

        height, width = template.shape[:2]
        if height < 1 or width < 1 or height > frame_gray.shape[0] or width > frame_gray.shape[1]:
            return -1.0

        result = cv2.matchTemplate(frame_gray, template, cv2.TM_CCOEFF_NORMED)
        _, maximum, _, _ = cv2.minMaxLoc(result)
        return float(maximum) if np.isfinite(maximum) else -1.0

    def _load_loading_template(self) -> np.ndarray:
        if self._loading_template is not None:
            return self._loading_template

        source = cv2.imread(str(TEMPLATE_PATH), cv2.IMREAD_UNCHANGED)
        if source is None:
            raise RuntimeError(f"刷压制等级模板不存在或无法读取：{TEMPLATE_PATH}")
        self._loading_template = self._to_gray(source)
        return self._loading_template

    def _ocr_text(self, frame, name: str) -> str:
        try:
            boxes = self.ocr(
                frame=frame,
                threshold=float(self.config.get("压制 OCR 阈值", 0.2)),
                target_height=720,
                log=False,
                name=name,
            )
        except Exception as exc:
            self.info_set("卡带确认 OCR 错误", str(exc))
            return ""
        return " ".join(box.name for box in boxes if getattr(box, "name", ""))

    def _loading_threshold(self) -> float:
        return float(self.config.get("加载页面阈值", 0.72))

    @staticmethod
    def _contains_notice(text: str) -> bool:
        normalized_text = "".join(str(text).split())
        normalized_notice = "".join(NEW_CARTRIDGE_NOTICE.split())
        return normalized_notice in normalized_text

    @staticmethod
    def _to_gray(image) -> np.ndarray:
        if len(image.shape) == 2:
            return image
        if image.shape[2] == 4:
            return cv2.cvtColor(image, cv2.COLOR_BGRA2GRAY)
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
