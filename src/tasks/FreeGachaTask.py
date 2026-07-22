from dataclasses import dataclass
from pathlib import Path
from time import monotonic

import cv2
import numpy as np
from qfluentwidgets import FluentIcon

from src.tasks.BaseBD2Task import BaseBD2Task
from src.utils.image_utils import (
    best_pixel_valid_match,
    candidate_scales,
    pixel_similarity,
    resize_template,
    template_match_response,
    to_gray,
)
from src.utils.ocr_utils import fuzzy_substring_match, keyword_match_count, normalize_ocr_text
from src.utils.template_resolution import offline_template_scale

REFERENCE_WIDTH = 1920
REFERENCE_HEIGHT = 1080
PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = PROJECT_ROOT / "offline-train" / "train-source-screenshots"
KEYWORD_MATCH_RATIO = 0.9


@dataclass(frozen=True)
class GachaTemplateSpec:
    name: str
    file_name: str
    threshold_key: str
    default_threshold: float


@dataclass(frozen=True)
class GachaMatchResult:
    score: float
    pixel_score: float
    position: tuple[int, int]
    size: tuple[int, int]


class FreeGachaTask(BaseBD2Task):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "白嫖抽抽乐"
        self.description = "领取服装和装备的所有免费抽抽乐。"
        self.icon = FluentIcon.GAME
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
                "加载页面阈值": 0.72,
                "返回按钮阈值": 0.76,
                "主页亮度比例阈值": 0.75,
                "抽卡 OCR 阈值": 0.2,
                "loading 出现等待秒数": 6.0,
                "loading 消失等待秒数": 35.0,
                "抽卡页面等待秒数": 12.0,
                "免费抽按钮等待秒数": 3.0,
                "确认弹窗等待秒数": 8.0,
                "结果跳过连续点击秒数": 3.0,
                "结果页 OCR 等待秒数": 5.0,
                "结果页 OCR 间隔秒数": 0.1,
                "主页确认等待秒数": 10.0,
                "跳过点击间隔秒数": 0.2,
                "结果页关闭前等待秒数": 1.0,
                "结果页返回前等待秒数": 1.0,
                "抽卡页面关键词最低命中数": 3,
                "确认弹窗关键词最低命中数": 2,
                "结果页关键词最低命中数": 3,
            }
        )
        self.config_description.update(
            {
                "启用": "是否执行白嫖抽抽乐任务。",
                "抽卡页面关键词最低命中数": "确认已进入抽卡页所需的 OCR 关键词数量。",
                "确认弹窗关键词最低命中数": "确认抽抽乐弹窗所需的 OCR 关键词数量。",
                "结果跳过连续点击秒数": "抽卡结果页连续点击跳过按钮的最长时间。",
                "结果页 OCR 等待秒数": "连续点击结束后，持续识别抽抽乐券详情页的最长时间。",
                "结果页 OCR 间隔秒数": "等待抽抽乐券详情页时的 OCR 识别间隔。",
                "结果页关闭前等待秒数": "识别到抽抽乐券结果页后，点击右上角关闭前等待的时间。",
                "结果页返回前等待秒数": "点击右上角关闭后，再点击左上角返回前等待的时间。",
                "结果页关键词最低命中数": "结果页返回确认使用 3 个固定 OCR 关键词。",
            }
        )

    def run(self):
        if not bool(self.config.get("启用", True)):
            self.info_set("状态", "白嫖抽抽乐已禁用。")
            self.log_info("白嫖抽抽乐已禁用。")
            return True

        self.info_set("状态", "启动白嫖抽抽乐。")
        self._click_reference(162, 986, after_sleep=0.5)
        loading_state, gacha_found, _ = self._wait_loading_or_gacha_page("进入抽卡页")
        if loading_state == "stuck":
            return False
        if not gacha_found and not self._wait_for_gacha_page("进入抽卡页"):
            return False

        if not self._run_free_section(
            "服装抽抽乐",
            verify_finished=True,
        ):
            return False

        self._sleep_after_recognition()
        self._click_reference(175, 432, after_sleep=0.8)
        if not self._wait_for_gacha_page("切换装备抽卡"):
            return False

        if not self._run_free_section(
            "装备抽抽乐",
            verify_finished=False,
        ):
            return False

        self._sleep_after_recognition()
        self._click_reference(105, 51, after_sleep=1.0)
        if not self._wait_loading_or_home_brightness("抽抽乐返回主页"):
            return False

        self.info_set("状态", "白嫖抽抽乐完成。")
        self.log_info("白嫖抽抽乐：流程完成。", notify=True)
        return True

    def _run_free_section(
        self,
        section_name: str,
        verify_finished: bool,
    ) -> bool:
        available, _ = self._wait_for_free_gacha(section_name)
        self.info_set(f"{section_name} 免费抽", "可领取" if available else "无")
        if not available:
            self.log_info(f"{section_name}：未检测到所有免费抽抽乐，跳过。")
            return True

        self._sleep_after_recognition()
        self._click_reference(347, 973, after_sleep=0.5)
        if not self._wait_for_confirm_dialog(section_name):
            return False

        self._sleep_after_recognition()
        self._click_reference(1045, 649, after_sleep=1.0)
        if not self._handle_result_until_back(section_name):
            return False

        if verify_finished:
            still_available, _ = self._wait_for_free_gacha(
                section_name,
                timeout=1.5,
            )
            if still_available:
                self.log_info(f"{section_name}：返回后仍检测到所有免费抽抽乐。")
                return False
            self.log_info(f"{section_name}：免费抽已结束。")

        return True

    def _wait_loading_or_gacha_page(
        self,
        name: str,
        interval: float = 0.5,
    ) -> tuple[str, bool, str]:
        return self._wait_loading_or_ocr_keywords(
            name,
            GACHA_PAGE_KEYWORDS,
            minimum_matches=int(self.config.get("抽卡页面关键词最低命中数", 3)),
            ocr_name=f"{name}_gacha_page",
            interval=interval,
        )

    def _wait_loading_or_ocr_keywords(
        self,
        task_name: str,
        keywords: list[str],
        minimum_matches: int,
        ocr_name: str,
        interval: float = 0.5,
    ) -> tuple[str, bool, str]:
        end_at = monotonic() + float(self.config.get("loading 出现等待秒数", 6.0))
        last_text = ""
        while monotonic() <= end_at:
            frame = self.capture_frame()
            found, text = self._ocr_keywords_in_frame(
                frame,
                keywords,
                minimum_matches,
                ocr_name,
            )
            last_text = text
            if found:
                self.log_info(f"{task_name}：已检测到下一阶段页面。")
                return "target", True, text

            loading = self._match(frame, LOADING_TEMPLATE)
            self.info_set(f"{task_name}_loading_appear", f"{loading.score:.3f}")
            if self._passes(loading, LOADING_TEMPLATE):
                return self._wait_loading_gone_or_ocr_keywords(
                    task_name,
                    keywords,
                    minimum_matches,
                    ocr_name,
                    last_text=last_text,
                    interval=interval,
                )
            self.sleep(interval)

        return "none", False, last_text

    def _wait_loading_gone_or_ocr_keywords(
        self,
        task_name: str,
        keywords: list[str],
        minimum_matches: int,
        ocr_name: str,
        last_text: str = "",
        interval: float = 0.5,
    ) -> tuple[str, bool, str]:
        end_at = monotonic() + float(self.config.get("loading 消失等待秒数", 35.0))
        while monotonic() <= end_at:
            frame = self.capture_frame()
            found, text = self._ocr_keywords_in_frame(
                frame,
                keywords,
                minimum_matches,
                ocr_name,
            )
            last_text = text
            if found:
                self.log_info(f"{task_name}：loading 期间已检测到下一阶段页面。")
                return "target", True, text

            loading = self._match(frame, LOADING_TEMPLATE)
            self.info_set(f"{task_name}_loading_gone", f"{loading.score:.3f}")
            if not self._passes(loading, LOADING_TEMPLATE):
                return "loading", False, last_text
            self.sleep(interval)

        self.log_info(f"{task_name}：UI_loading_black.png 未在限定时间内消失。")
        return "stuck", False, last_text

    def _wait_loading_or_home_brightness(
        self,
        name: str,
        interval: float = 0.35,
    ) -> bool:
        end_at = monotonic() + float(self.config.get("loading 出现等待秒数", 6.0))
        while monotonic() <= end_at:
            frame = self.capture_frame()
            if self._home_brightness_ok(frame, name):
                return True

            loading = self._match(frame, LOADING_TEMPLATE)
            self.info_set(f"{name}_loading_appear", f"{loading.score:.3f}")
            if self._passes(loading, LOADING_TEMPLATE):
                return self._wait_loading_gone_or_home_brightness(name, interval=interval)
            self.sleep(interval)

        self.log_info(f"{name}：未检测到 UI_loading_black.png，继续确认主页。")
        return self._wait_home_brightness(name, interval=interval)

    def _wait_loading_gone_or_home_brightness(
        self,
        name: str,
        interval: float = 0.35,
    ) -> bool:
        end_at = monotonic() + float(self.config.get("loading 消失等待秒数", 35.0))
        while monotonic() <= end_at:
            frame = self.capture_frame()
            if self._home_brightness_ok(frame, name):
                return True

            loading = self._match(frame, LOADING_TEMPLATE)
            self.info_set(f"{name}_loading_gone", f"{loading.score:.3f}")
            if not self._passes(loading, LOADING_TEMPLATE):
                return self._wait_home_brightness(name, interval=interval)
            self.sleep(interval)

        self.log_info(f"{name}：UI_loading_black.png 未在限定时间内消失。")
        return False

    def _wait_for_gacha_page(self, name: str) -> bool:
        found, text = self._wait_for_ocr_keywords(
            GACHA_PAGE_KEYWORDS,
            timeout=float(self.config.get("抽卡页面等待秒数", 12.0)),
            minimum_matches=int(self.config.get("抽卡页面关键词最低命中数", 3)),
            name=f"{name}_gacha_page",
        )
        self.info_set(f"{name} OCR", text or "-")
        if found:
            self.log_info(f"{name}：已确认抽卡页面。")
            return True
        self.log_info(f"{name}：未确认抽卡页面。")
        return False

    def _wait_for_free_gacha(
        self,
        section_name: str,
        timeout: float | None = None,
    ) -> tuple[bool, str]:
        found, text = self._wait_for_ocr_keywords(
            FREE_GACHA_KEYWORDS,
            timeout=(
                float(timeout)
                if timeout is not None
                else float(self.config.get("免费抽按钮等待秒数", 3.0))
            ),
            minimum_matches=1,
            name=f"{section_name}_free_gacha",
        )
        self.info_set(f"{section_name} 免费抽 OCR", text or "-")
        return found, text

    def _wait_for_confirm_dialog(self, section_name: str) -> bool:
        found, text = self._wait_for_ocr_keywords(
            CONFIRM_DIALOG_KEYWORDS,
            timeout=float(self.config.get("确认弹窗等待秒数", 8.0)),
            minimum_matches=int(self.config.get("确认弹窗关键词最低命中数", 2)),
            name=f"{section_name}_confirm",
        )
        self.info_set(f"{section_name} 确认 OCR", text or "-")
        if found:
            self.log_info(f"{section_name}：检测到确认抽抽乐弹窗。")
            return True
        self.log_info(f"{section_name}：未检测到确认抽抽乐弹窗。")
        return False

    def _handle_result_until_back(self, section_name: str) -> bool:
        found, text = self._click_skip_until_back_page(section_name)
        self.info_set(f"{section_name} 结果 OCR", text or "-")
        if not found:
            self.log_info(f"{section_name}：连续点击跳过并持续 OCR 后未检测到抽抽乐券详情页。")
            return False

        self.log_info(f"{section_name}：已确认抽抽乐券详情页，等待后关闭并返回抽卡页面。")
        self.sleep(max(0.0, float(self.config.get("结果页关闭前等待秒数", 1.0))))
        self._click_reference(
            1420,
            326,
            after_sleep=max(0.0, float(self.config.get("结果页返回前等待秒数", 1.0))),
        )
        self._click_reference(105, 51, after_sleep=0.0)
        return self._wait_for_gacha_page(f"{section_name} 返回抽卡页")

    def _click_skip_until_back_page(self, section_name: str) -> tuple[bool, str]:
        duration = max(0.0, float(self.config.get("结果跳过连续点击秒数", 3.0)))
        interval = max(0.05, float(self.config.get("跳过点击间隔秒数", 0.2)))
        minimum_matches = max(
            1,
            min(
                len(BACK_PAGE_KEYWORDS),
                int(self.config.get("结果页关键词最低命中数", len(BACK_PAGE_KEYWORDS))),
            ),
        )
        end_at = monotonic() + duration

        while monotonic() < end_at:
            self._click_reference(1770, 60, after_sleep=0.0)
            remaining = end_at - monotonic()
            if remaining <= 0:
                break
            self.sleep(min(interval, remaining))

        return self._wait_for_ocr_keywords(
            BACK_PAGE_KEYWORDS,
            timeout=max(0.0, float(self.config.get("结果页 OCR 等待秒数", 5.0))),
            minimum_matches=minimum_matches,
            name=f"{section_name}_result",
            interval=max(0.05, float(self.config.get("结果页 OCR 间隔秒数", 0.1))),
        )

    def _wait_for_template(
        self,
        spec: GachaTemplateSpec,
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
            found, text = self._ocr_keywords_in_frame(
                frame,
                keywords,
                minimum_matches,
                name,
            )
            last_text = text
            if found:
                return True, text
            self.sleep(interval)
        return False, last_text

    def _ocr_keywords_in_frame(
        self,
        frame,
        keywords: list[str],
        minimum_matches: int,
        name: str,
    ) -> tuple[bool, str]:
        text = self._ocr_text(frame, name=name)
        count = self._keyword_match_count(text, keywords)
        self.info_set(f"{name} 关键字", f"{count}/{len(keywords)}")
        return count >= minimum_matches, text

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

    def _home_brightness_ok(self, frame, name: str) -> bool:
        ratio = self._home_brightness_ratio(frame)
        self.info_set(f"{name} 亮度", f"{ratio:.3f}")
        return ratio >= self._home_ratio_threshold()

    def _home_brightness_ratio(self, frame) -> float:
        template = self._load_template(HOME_TEMPLATE)
        frame_gray = self._to_gray(frame)
        frame_height, frame_width = frame_gray.shape[:2]
        scale = offline_template_scale(HOME_TEMPLATE.file_name, frame_width, frame_height)
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

    def _match(self, frame, spec: GachaTemplateSpec) -> GachaMatchResult:
        empty = GachaMatchResult(score=-1.0, pixel_score=-1.0, position=(0, 0), size=(0, 0))
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
            base_scale = offline_template_scale(spec.file_name, frame_width, frame_height)
            best = empty
            template_threshold = float(
                getattr(self, "config", {}).get(spec.threshold_key, spec.default_threshold)
            )

            for scale in self._candidate_scales(base_scale):
                scaled_template = self._resize_template(template, scale)
                height, width = scaled_template.shape[:2]
                if height < 8 or width < 8 or height > frame_height or width > frame_width:
                    continue

                result = template_match_response(frame_gray, scaled_template)
                candidate = best_pixel_valid_match(
                    result,
                    frame_gray,
                    scaled_template,
                    None,
                    template_threshold=template_threshold,
                    pixel_threshold=0.0,
                )
                if candidate is None or candidate.score <= best.score:
                    continue
                x, y = candidate.location
                best = GachaMatchResult(
                    score=candidate.score,
                    pixel_score=candidate.pixel_score,
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

    def _load_template(self, spec: GachaTemplateSpec) -> np.ndarray:
        if spec.name in self._templates:
            return self._templates[spec.name]

        path = TEMPLATE_DIR / spec.file_name
        template = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if template is None:
            raise RuntimeError(f"白嫖抽抽乐模板不存在或无法读取：{path}")

        self._templates[spec.name] = template
        return template

    def _passes(self, result: GachaMatchResult, spec: GachaTemplateSpec) -> bool:
        threshold = float(self.config.get(spec.threshold_key, spec.default_threshold))
        return result.score >= threshold

    def _ocr_text(self, frame, name: str) -> str:
        try:
            boxes = self.ocr(
                frame=frame,
                threshold=float(self.config.get("抽卡 OCR 阈值", 0.2)),
                target_height=720,
                log=False,
                name=name,
            )
        except Exception as exc:
            self.info_set(f"{name} OCR 错误", str(exc))
            return ""

        return " ".join(box.name for box in boxes if getattr(box, "name", ""))

    def _home_ratio_threshold(self) -> float:
        return float(self.config.get("主页亮度比例阈值", 0.75))

    def _click_reference(self, x: int, y: int, after_sleep: float = 0.0):
        self.operate_click(
            max(0.0, min(1.0, x / REFERENCE_WIDTH)),
            max(0.0, min(1.0, y / REFERENCE_HEIGHT)),
            after_sleep=after_sleep,
        )

    @staticmethod
    def _keyword_match_count(text: str, keywords: list[str]) -> int:
        return keyword_match_count(text, keywords, fuzzy_ratio=KEYWORD_MATCH_RATIO)

    @staticmethod
    def _keyword_matches(normalized_text: str, normalized_keyword: str) -> bool:
        return fuzzy_substring_match(
            normalized_text,
            normalized_keyword,
            KEYWORD_MATCH_RATIO,
        )

    _normalize_text = staticmethod(normalize_ocr_text)
    _candidate_scales = staticmethod(candidate_scales)
    _resize_template = staticmethod(resize_template)
    _to_gray = staticmethod(to_gray)
    _pixel_similarity = staticmethod(pixel_similarity)


LOADING_TEMPLATE = GachaTemplateSpec(
    name="ui_loading_black",
    file_name="image/UI_loading_black.png",
    threshold_key="加载页面阈值",
    default_threshold=0.72,
)

BACK_TEMPLATE = GachaTemplateSpec(
    name="back",
    file_name="back.png",
    threshold_key="返回按钮阈值",
    default_threshold=0.76,
)

HOME_TEMPLATE = GachaTemplateSpec(
    name="home",
    file_name="home.png",
    threshold_key="主页亮度比例阈值",
    default_threshold=0.75,
)

GACHA_PAGE_KEYWORDS = [
    "服装抽抽乐",
    "服装",
    "装备",
    "本抽抽乐的Pickup对象及日程可能会在后续有所变动",
    "角色和装备在未来可能会通过其他方式重新贩售或发放",
]

FREE_GACHA_KEYWORDS = ["所有免费抽抽乐"]
CONFIRM_DIALOG_KEYWORDS = ["确认抽抽乐", "是否全部进行"]
BACK_PAGE_KEYWORDS = ["抽抽乐券", "可免费抽1次的抽抽乐券", "查看获取途径"]
