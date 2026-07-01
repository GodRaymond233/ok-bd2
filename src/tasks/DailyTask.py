from dataclasses import dataclass
from pathlib import Path
from time import monotonic

import cv2
import numpy as np
from qfluentwidgets import FluentIcon

from src.tasks.BaseBD2Task import HOTKEY_CONFIG_NAME, BaseBD2Task

REFERENCE_WIDTH = 1920
REFERENCE_HEIGHT = 1080
PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = PROJECT_ROOT / "offline-train" / "train-source-screenshots"


@dataclass(frozen=True)
class DailyTemplateSpec:
    name: str
    file_name: str
    threshold_key: str
    default_threshold: float
    crop: tuple[float, float, float, float] | None = None


@dataclass(frozen=True)
class DailyMatchResult:
    score: float
    pixel_score: float
    position: tuple[int, int]
    size: tuple[int, int]


class DailyTask(BaseBD2Task):
    status_keys = [
        "启用",
        "状态",
        "当前任务",
        "执行公会签到",
        "公会判断",
        "公会未完成",
        "公会未完成阈值",
        "公会已完成",
        "公会已完成阈值",
        "公会签到 loading 状态",
        "公会签到_loading_appear",
        "公会签到_loading_gone",
        "guild_sign_in_early 模板",
        "guild_sign_in 模板",
        "公会签到成功",
        "公会签到成功阈值",
        "公会签到 OCR",
        "公会签到返回主页 亮度",
        "公会签到返回主页结果",
        "执行小屋签到",
        "小屋签到 loading 状态",
        "小屋签到_loading_appear",
        "小屋签到_loading_gone",
        "my_home_early",
        "my_home",
        "小屋页面检测",
        "小屋页面阈值",
        "小屋签到返回主页 亮度",
        "小屋签到返回主页结果",
        "执行一键收菜",
        "business_collect 关键字",
        "一键收菜弹窗",
        "一键收菜 OCR",
        "一键收菜返回主页 亮度",
        "一键收菜返回主页结果",
        "加载页面阈值",
        "主页亮度比例阈值",
        "日常 OCR 阈值",
        "loading 出现等待秒数",
        "loading 消失等待秒数",
        "公会签到成功等待秒数",
        "小屋页面等待秒数",
        "一键收菜菜单等待秒数",
        "主页确认等待秒数",
        "完成",
        "失败",
        "跳过",
        "匹配错误",
        "Log",
        "Warning",
        "Error",
    ]
    status_key_labels = {
        "公会签到_loading_appear": "公会 loading 出现",
        "公会签到_loading_gone": "公会 loading 消失",
        "guild_sign_in_early 模板": "公会签到成功早期模板",
        "guild_sign_in 模板": "公会签到成功模板",
        "小屋签到_loading_appear": "小屋 loading 出现",
        "小屋签到_loading_gone": "小屋 loading 消失",
        "my_home_early": "小屋页面早期检测",
        "my_home": "小屋页面检测分数",
        "business_collect 关键字": "一键收菜关键字命中",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "日常任务"
        self.description = "执行公会签到、小屋签到和一键收菜。"
        self.icon = FluentIcon.CAR
        self.group_name = "日常/周常"
        self.group_icon = FluentIcon.CALENDAR
        self.visible = True
        self._templates: dict[str, np.ndarray] = {}
        self._missing_template_names: set[str] = set()
        self._match_error_names: set[str] = set()
        self._match_pause_until = 0.0
        self.default_config.update(
            {
                "启用": True,
                "执行公会签到": True,
                "执行小屋签到": True,
                "执行一键收菜": True,
                "公会未完成阈值": 0.78,
                "公会已完成阈值": 0.78,
                "公会签到成功阈值": 0.76,
                "小屋页面阈值": 0.76,
                "加载页面阈值": 0.72,
                "主页亮度比例阈值": 0.75,
                "日常 OCR 阈值": 0.2,
                "loading 出现等待秒数": 6.0,
                "loading 消失等待秒数": 35.0,
                "公会签到成功等待秒数": 8.0,
                "小屋页面等待秒数": 12.0,
                "一键收菜菜单等待秒数": 8.0,
                "主页确认等待秒数": 10.0,
                HOTKEY_CONFIG_NAME: {},
            }
        )
        self.config_description.update(
            {
                "执行公会签到": "从主页进入公会，领取每日签到奖励。",
                "执行小屋签到": "从主页进入小屋，确认到达后返回主页。",
                "执行一键收菜": "打开经营管理弹窗并执行一键获得。",
                HOTKEY_CONFIG_NAME: "打开共享的游戏按键设置。",
            }
        )
        self.config_type.update(
            {
                HOTKEY_CONFIG_NAME: {"type": "global"},
            }
        )

    def _status_set(self, key: str, value) -> None:
        try:
            self.info_set(key, value)
        except AttributeError:
            pass

    def run(self):
        if not bool(self.config.get("启用", True)):
            self.info_set("状态", "日常任务已禁用。")
            self.log_info("日常任务已禁用。")
            return True

        self.info_set("状态", "日常任务启动。")
        steps = [
            ("公会签到", "执行公会签到", self.run_guild_sign_in),
            ("小屋签到", "执行小屋签到", self.run_my_home_sign_in),
            ("一键收菜", "执行一键收菜", self.run_business_collect),
        ]

        success = []
        failed = []
        skipped = []
        stop_remaining = False
        for name, config_key, func in steps:
            if not bool(self.config.get(config_key, True)):
                skipped.append(name)
                continue
            if stop_remaining:
                skipped.append(name)
                continue

            self.info_set("当前任务", name)
            self.log_info(f"开始日常子任务：{name}")
            try:
                if func():
                    success.append(name)
                else:
                    failed.append(name)
                    stop_remaining = True
                    self.log_info(f"{name} 未满足后续触发条件，停止剩余日常任务。")
            except Exception as exc:
                failed.append(name)
                stop_remaining = True
                self.log_error(f"日常子任务失败：{name}", exc)

        self.info_set("完成", str(success))
        self.info_set("失败", str(failed))
        self.info_set("跳过", str(skipped))
        self.info_set("状态", "日常任务结束。")
        self.log_info(
            f"日常任务结束：完成={success}, 失败={failed}, 跳过={skipped}",
            notify=True,
        )
        return not failed

    def run_guild_sign_in(self) -> bool:
        frame = self.capture_frame()
        guild = self._match(frame, GUILD_TEMPLATE)
        finished = self._match(frame, GUILD_FINISHED_TEMPLATE)
        self.info_set("公会未完成", f"{guild.score:.3f}")
        self.info_set("公会已完成", f"{finished.score:.3f}")

        guild_ready = self._passes(guild, GUILD_TEMPLATE)
        finished_ready = self._passes(finished, GUILD_FINISHED_TEMPLATE)
        if finished_ready and (not guild_ready or finished.score >= guild.score):
            self._status_set("公会判断", "已完成，跳过")
            self._status_set("公会签到成功", "已完成")
            self.log_info("公会签到：已检测为完成状态，跳过。")
            return True
        if not guild_ready:
            self._status_set("公会判断", "未识别到公会入口")
            self._status_set("公会签到成功", "否")
            self.log_info("公会签到：未检测到 guild.png 或 guild-finished.png，不点击公会按钮。")
            return False

        self._status_set("公会判断", "可签到")
        self._sleep_after_recognition()
        self._click_reference(370, 155, after_sleep=0.5)
        loading_state, success_found, text = self._wait_loading_or_template_or_ocr(
            "公会签到",
            GUILD_SIGNUP_SUCCESS_TEMPLATE,
            GUILD_SUCCESS_KEYWORDS,
            name="guild_sign_in_early",
        )
        self._status_set("公会签到 loading 状态", loading_state)
        if loading_state == "stuck":
            self._status_set("公会签到成功", "否")
            return False

        if not success_found:
            if loading_state == "none":
                self.log_info("公会签到：未检测到 UI_loading_black.png，继续检测签到结果。")
            success_found, text = self._wait_for_template_or_ocr(
                GUILD_SIGNUP_SUCCESS_TEMPLATE,
                GUILD_SUCCESS_KEYWORDS,
                timeout=float(self.config.get("公会签到成功等待秒数", 8.0)),
                name="guild_sign_in",
            )
        self.info_set("公会签到 OCR", text or "-")
        self._status_set("公会签到成功", "是" if success_found else "否")
        if success_found:
            self.log_info("公会签到：检测到签到成功提示。")
            self._sleep_after_recognition()
            self._click_reference(450, 650, after_sleep=0.5)
        else:
            self.log_info("公会签到：未检测到签到成功提示，按流程返回主页。")

        self._click_reference(100, 50, after_sleep=1.0)
        home_ok = self._wait_home_brightness("公会签到返回主页")
        self._status_set("公会签到返回主页结果", "通过" if home_ok else "失败")
        return home_ok

    def run_my_home_sign_in(self) -> bool:
        self._click_reference(166, 158, after_sleep=0.5)
        loading_state, found = self._wait_loading_or_template(
            "小屋签到",
            MY_HOME_TEMPLATE,
            name="my_home_early",
        )
        self._status_set("小屋签到 loading 状态", loading_state)
        if loading_state == "stuck":
            self._status_set("小屋页面检测", "否")
            return False
        if loading_state == "loading":
            self.sleep(1.0)
        elif loading_state == "none":
            self.log_info("小屋签到：未检测到 UI_loading_black.png，继续检测 my-home.png。")

        if not found:
            found = self._wait_for_template(
                MY_HOME_TEMPLATE,
                timeout=float(self.config.get("小屋页面等待秒数", 12.0)),
                name="my_home",
            )
        self._status_set("小屋页面检测", "是" if found else "否")
        if found:
            self.log_info("小屋签到：已进入小屋页面，返回主页。")
            self._sleep_after_recognition()
            self._click_reference(100, 50, after_sleep=1.0)
        else:
            self.log_info("小屋签到：未检测到 my-home.png，不执行返回点击。")
            self._status_set("小屋签到返回主页结果", "未执行")
            return False

        home_ok = self._wait_home_brightness("小屋签到返回主页")
        self._status_set("小屋签到返回主页结果", "通过" if home_ok else "失败")
        return home_ok

    def run_business_collect(self) -> bool:
        self._click_reference(165, 260, after_sleep=1.0)
        found, text = self._wait_for_ocr_keywords(
            [
                "餐厅营业额现状",
                "鱼笼收获情况",
                "助手工作情况",
                "取消",
                "一键获得",
            ],
            timeout=float(self.config.get("一键收菜菜单等待秒数", 8.0)),
            minimum_matches=2,
            name="business_collect",
        )
        self.info_set("一键收菜 OCR", text or "-")
        self._status_set("一键收菜弹窗", "是" if found else "否")
        if not found:
            self.log_info("一键收菜：未检测到经营管理弹窗关键字，跳过点击。")
            self._status_set("一键收菜返回主页结果", "未执行")
            return False

        self._sleep_after_recognition()
        self._click_reference(1090, 814, after_sleep=3.0)
        self._click_reference(832, 814, after_sleep=1.0)
        self._click_reference(832, 814, after_sleep=1.0)
        home_ok = self._wait_home_brightness("一键收菜返回主页")
        self._status_set("一键收菜返回主页结果", "通过" if home_ok else "失败")
        return home_ok

    def _click_reference(self, x: int, y: int, after_sleep: float = 0.0):
        self.operate_click(
            max(0.0, min(1.0, x / REFERENCE_WIDTH)),
            max(0.0, min(1.0, y / REFERENCE_HEIGHT)),
            after_sleep=after_sleep,
        )

    def _wait_loading_or_template(
        self,
        task_name: str,
        spec: DailyTemplateSpec,
        name: str,
        interval: float = 0.35,
    ) -> tuple[str, bool]:
        end_at = monotonic() + float(self.config.get("loading 出现等待秒数", 6.0))
        while monotonic() <= end_at:
            frame = self.capture_frame()
            result = self._match(frame, spec)
            self.info_set(name, f"{result.score:.3f}")
            if self._passes(result, spec):
                return "target", True

            loading = self._match(frame, LOADING_TEMPLATE)
            self.info_set(f"{task_name}_loading_appear", f"{loading.score:.3f}")
            if self._passes(loading, LOADING_TEMPLATE):
                return self._wait_loading_gone_or_template(
                    task_name,
                    spec,
                    name,
                    interval=interval,
                )
            self.sleep(interval)

        return "none", False

    def _wait_loading_gone_or_template(
        self,
        task_name: str,
        spec: DailyTemplateSpec,
        name: str,
        interval: float = 0.35,
    ) -> tuple[str, bool]:
        end_at = monotonic() + float(self.config.get("loading 消失等待秒数", 35.0))
        while monotonic() <= end_at:
            frame = self.capture_frame()
            result = self._match(frame, spec)
            self.info_set(name, f"{result.score:.3f}")
            if self._passes(result, spec):
                return "target", True

            loading = self._match(frame, LOADING_TEMPLATE)
            self.info_set(f"{task_name}_loading_gone", f"{loading.score:.3f}")
            if not self._passes(loading, LOADING_TEMPLATE):
                return "loading", False
            self.sleep(interval)

        self.log_info(f"{task_name}：UI_loading_black.png 未在限定时间内消失。")
        return "stuck", False

    def _wait_loading_or_template_or_ocr(
        self,
        task_name: str,
        spec: DailyTemplateSpec,
        keywords: list[str],
        name: str,
        interval: float = 0.5,
    ) -> tuple[str, bool, str]:
        end_at = monotonic() + float(self.config.get("loading 出现等待秒数", 6.0))
        last_text = ""
        while monotonic() <= end_at:
            frame = self.capture_frame()
            result = self._match(frame, spec)
            text = self._ocr_text(frame, name=name)
            last_text = text
            self.info_set(f"{name} 模板", f"{result.score:.3f}")
            if self._passes(result, spec) or self._keyword_match_count(text, keywords) >= 1:
                return "target", True, text

            loading = self._match(frame, LOADING_TEMPLATE)
            self.info_set(f"{task_name}_loading_appear", f"{loading.score:.3f}")
            if self._passes(loading, LOADING_TEMPLATE):
                return self._wait_loading_gone_or_template_or_ocr(
                    task_name,
                    spec,
                    keywords,
                    name,
                    last_text=last_text,
                    interval=interval,
                )
            self.sleep(interval)

        return "none", False, last_text

    def _wait_loading_gone_or_template_or_ocr(
        self,
        task_name: str,
        spec: DailyTemplateSpec,
        keywords: list[str],
        name: str,
        last_text: str = "",
        interval: float = 0.5,
    ) -> tuple[str, bool, str]:
        end_at = monotonic() + float(self.config.get("loading 消失等待秒数", 35.0))
        while monotonic() <= end_at:
            frame = self.capture_frame()
            result = self._match(frame, spec)
            text = self._ocr_text(frame, name=name)
            last_text = text
            self.info_set(f"{name} 模板", f"{result.score:.3f}")
            if self._passes(result, spec) or self._keyword_match_count(text, keywords) >= 1:
                return "target", True, text

            loading = self._match(frame, LOADING_TEMPLATE)
            self.info_set(f"{task_name}_loading_gone", f"{loading.score:.3f}")
            if not self._passes(loading, LOADING_TEMPLATE):
                return "loading", False, last_text
            self.sleep(interval)

        self.log_info(f"{task_name}：UI_loading_black.png 未在限定时间内消失。")
        return "stuck", False, last_text

    def _wait_for_template(
        self,
        spec: DailyTemplateSpec,
        timeout: float,
        name: str,
        interval: float = 0.35,
    ) -> bool:
        end_at = monotonic() + max(0.0, timeout)
        last_score = -1.0
        while monotonic() <= end_at:
            frame = self.capture_frame()
            result = self._match(frame, spec)
            last_score = result.score
            self.info_set(name, f"{result.score:.3f}")
            if self._passes(result, spec):
                return True
            self.sleep(interval)

        self.info_set(name, f"{last_score:.3f}")
        return False

    def _wait_for_template_or_ocr(
        self,
        spec: DailyTemplateSpec,
        keywords: list[str],
        timeout: float,
        name: str,
        interval: float = 0.5,
    ) -> tuple[bool, str]:
        end_at = monotonic() + max(0.0, timeout)
        last_text = ""
        while monotonic() <= end_at:
            frame = self.capture_frame()
            result = self._match(frame, spec)
            text = self._ocr_text(frame, name=name)
            last_text = text
            self.info_set(f"{name} 模板", f"{result.score:.3f}")
            if self._passes(result, spec) or self._keyword_match_count(text, keywords) >= 1:
                return True, text
            self.sleep(interval)
        return False, last_text

    def _wait_for_ocr_keywords(
        self,
        keywords: list[str],
        timeout: float,
        minimum_matches: int,
        name: str,
        interval: float = 0.5,
    ) -> tuple[bool, str]:
        end_at = monotonic() + max(0.0, timeout)
        last_text = ""
        while monotonic() <= end_at:
            frame = self.capture_frame()
            text = self._ocr_text(frame, name=name)
            last_text = text
            count = self._keyword_match_count(text, keywords)
            self.info_set(f"{name} 关键字", f"{count}/{len(keywords)}")
            if count >= minimum_matches:
                return True, text
            self.sleep(interval)
        return False, last_text

    def _wait_home_brightness(
        self,
        name: str,
        interval: float = 0.35,
    ) -> bool:
        end_at = monotonic() + float(self.config.get("主页确认等待秒数", 10.0))
        last_ratio = 0.0
        while monotonic() <= end_at:
            frame = self.capture_frame()
            last_ratio = self._home_brightness_ratio(frame)
            self.info_set(f"{name} 亮度", f"{last_ratio:.3f}")
            if last_ratio >= self._home_ratio_threshold():
                return True
            self.sleep(interval)

        self.log_info(f"{name}：主页亮度未达到阈值，ratio={last_ratio:.3f}")
        return False

    def _home_brightness_ratio(self, frame) -> float:
        template = self._load_template(HOME_TEMPLATE)
        frame_gray = self._to_gray(frame)
        frame_height, frame_width = frame_gray.shape[:2]
        scale = frame_width / REFERENCE_WIDTH
        template_height, template_width = template.shape[:2]
        roi_width = max(8, round(template_width * scale))
        roi_height = max(8, round(template_height * scale))
        center_x = round(frame_width * (166 / REFERENCE_WIDTH))
        center_y = round(frame_height * (158 / REFERENCE_HEIGHT))
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

    def _match(self, frame, spec: DailyTemplateSpec) -> DailyMatchResult:
        empty = DailyMatchResult(score=-1.0, pixel_score=-1.0, position=(0, 0), size=(0, 0))
        if monotonic() < self._match_pause_until:
            return empty

        try:
            template = self._load_template(spec)
        except RuntimeError as exc:
            if spec.name not in self._missing_template_names:
                self._missing_template_names.add(spec.name)
                self.log_warning(str(exc), notify=True)
            return empty

        try:
            frame_gray = self._to_gray(frame)
            frame_height, frame_width = frame_gray.shape[:2]
            base_scale = frame_width / REFERENCE_WIDTH
            best = empty

            for scale in self._candidate_scales(base_scale):
                scaled_template = self._resize_template(template, scale)
                height, width = scaled_template.shape[:2]
                if height < 8 or width < 8 or height > frame_height or width > frame_width:
                    continue

                result = cv2.matchTemplate(frame_gray, scaled_template, cv2.TM_CCOEFF_NORMED)
                _, max_value, _, max_location = cv2.minMaxLoc(result)
                if max_value > best.score:
                    x, y = int(max_location[0]), int(max_location[1])
                    region = frame_gray[y : y + height, x : x + width]
                    best = DailyMatchResult(
                        score=float(max_value),
                        pixel_score=self._pixel_similarity(region, scaled_template),
                        position=(x, y),
                        size=(int(width), int(height)),
                    )
        except (cv2.error, MemoryError) as exc:
            self._match_pause_until = monotonic() + 2.0
            message = f"图像匹配内存不足，暂停识别2秒：{spec.name}"
            self.info_set("匹配错误", message)
            if spec.name not in self._match_error_names:
                self._match_error_names.add(spec.name)
                self.log_warning(f"{message}；{exc}", notify=True)
            return empty

        return best

    def _load_template(self, spec: DailyTemplateSpec) -> np.ndarray:
        if spec.name in self._templates:
            return self._templates[spec.name]

        path = TEMPLATE_DIR / spec.file_name
        template = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if template is None:
            raise RuntimeError(f"日常任务模板不存在或无法读取：{path}")

        if spec.crop is not None:
            template = self._crop_relative(template, spec.crop)

        self._templates[spec.name] = template
        return template

    def _passes(self, result: DailyMatchResult, spec: DailyTemplateSpec) -> bool:
        threshold = float(self.config.get(spec.threshold_key, spec.default_threshold))
        return result.score >= threshold

    def _ocr_text(self, frame, name: str) -> str:
        try:
            boxes = self.ocr(
                frame=frame,
                threshold=float(self.config.get("日常 OCR 阈值", 0.2)),
                target_height=720,
                log=False,
                name=name,
            )
        except Exception as exc:
            self.info_set(f"{name} OCR 错误", str(exc))
            return ""

        return " ".join(box.name for box in boxes if getattr(box, "name", ""))

    @staticmethod
    def _keyword_match_count(text: str, keywords: list[str]) -> int:
        normalized = DailyTask._normalize_text(text)
        return sum(1 for keyword in keywords if DailyTask._normalize_text(keyword) in normalized)

    @staticmethod
    def _normalize_text(text: str) -> str:
        return "".join(str(text).lower().split())

    def _home_ratio_threshold(self) -> float:
        return float(self.config.get("主页亮度比例阈值", 0.75))

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


GUILD_TEMPLATE = DailyTemplateSpec(
    name="guild",
    file_name="guild.png",
    threshold_key="公会未完成阈值",
    default_threshold=0.78,
)

GUILD_FINISHED_TEMPLATE = DailyTemplateSpec(
    name="guild_finished",
    file_name="guild-finished.png",
    threshold_key="公会已完成阈值",
    default_threshold=0.78,
)

GUILD_SIGNUP_SUCCESS_TEMPLATE = DailyTemplateSpec(
    name="guild_signup_success",
    file_name="guild-singup-success.png",
    threshold_key="公会签到成功阈值",
    default_threshold=0.76,
)

MY_HOME_TEMPLATE = DailyTemplateSpec(
    name="my_home",
    file_name="my-home.png",
    threshold_key="小屋页面阈值",
    default_threshold=0.76,
)

LOADING_TEMPLATE = DailyTemplateSpec(
    name="ui_loading_black",
    file_name="image/UI_loading_black.png",
    threshold_key="加载页面阈值",
    default_threshold=0.72,
)

HOME_TEMPLATE = DailyTemplateSpec(
    name="home",
    file_name="home.png",
    threshold_key="主页亮度比例阈值",
    default_threshold=0.75,
)

GUILD_SUCCESS_KEYWORDS = ["签到成功", "奖励已发放至邮箱"]
