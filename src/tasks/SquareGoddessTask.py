import re
from pathlib import Path
from time import monotonic

import cv2
import numpy as np
from qfluentwidgets import FluentIcon

from src.tasks.BaseBD2Task import BaseBD2Task
from src.tasks.map_trade.models import MatchResult, TemplateSpec
from src.utils import task_vision
from src.utils.calibration import FHD_1080, HD_720, QHD_1440
from src.utils.cartridge_quick_switch import (
    CHARACTER_CATEGORY_LABEL,
    EVENT_CATEGORY_LABEL,
    FIXED_CARTRIDGE_SLOT_PRE_CLICK_DELAY_SECONDS,
    GAMEPLAY_CATEGORY_HIGHLIGHT_MIN_RATIO,
    LIFE_GAMEPLAY_CATEGORY_HIGHLIGHT_REGION,
    LIFE_GAMEPLAY_CATEGORY_LABEL,
    LIFE_GAMEPLAY_CATEGORY_OCR_ROI,
    LIFE_GAMEPLAY_CATEGORY_POINT,
    SHOPKEEPER_CATEGORY_LABEL,
    category_highlight_ratio,
)
from src.utils.home_confirmation import (
    HOME_DIMMED_P95_THRESHOLD_DEFAULT,
    HOME_GACHA_OCR_REFERENCE_ROI,
    HOME_LEFT_COLUMN_OCR_REFERENCE_ROI,
    HOME_LEFT_COLUMN_REQUIRED_HITS,
    home_confirmation_passes,
    home_gacha_ocr_with_fallback,
    home_left_column_hits,
    home_left_column_p95_brightness,
)
from src.utils.image_utils import (
    reference_roi_frame,
    stabilize_template_match,
)
from src.utils.ocr_utils import normalize_ocr_text

REFERENCE_WIDTH = FHD_1080.width
REFERENCE_HEIGHT = FHD_1080.height
HD720_REFERENCE_WIDTH = HD_720.width
HD720_REFERENCE_HEIGHT = HD_720.height
ENTRY_REFERENCE_WIDTH = QHD_1440.width
ENTRY_REFERENCE_HEIGHT = QHD_1440.height
SQUARE_CARTRIDGE_SLOT_POINT = (331 / REFERENCE_WIDTH, 970 / REFERENCE_HEIGHT)
SQUARE_HOME_POINT = (1797 / REFERENCE_WIDTH, 63 / REFERENCE_HEIGHT)
QUICK_SWITCH_PAGE_PATTERNS = (
    SHOPKEEPER_CATEGORY_LABEL,
    CHARACTER_CATEGORY_LABEL,
    LIFE_GAMEPLAY_CATEGORY_LABEL,
    EVENT_CATEGORY_LABEL,
)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = PROJECT_ROOT / "recognition-assets" / "template-assets"


class SquareGoddessTask(BaseBD2Task):
    status_keys = [
        "启用",
        "状态",
        "当前阶段",
        "主页小屋按钮",
        "主页亮度",
        "主页抽抽乐 OCR",
        "广场主页点击次数",
        "快速切换按钮",
        "卡带选择页 OCR",
        "卡带选择页 OCR 命中",
        "生活玩法游戏卡带 OCR",
        "生活玩法类别高亮",
        "梦幻广场",
        "广场感叹号",
        "女神像许愿 OCR",
        "广场每日导航",
        "广场导航文本 OCR",
        "广场导航文字命中",
        "广场导航中",
        "女神像许愿结果",
        "匹配错误",
        "Log",
        "Warning",
        "Error",
    ]

    status_key_labels = {
        "梦幻广场": "梦幻广场模板",
        "广场每日导航": "每日导航模板",
        "广场导航中": "导航中模板",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "广场女神像"
        self.description = (
            "从快速切换页的生活玩法游戏卡带2号位进入梦幻广场并完成女神像许愿。"
        )
        self.icon = FluentIcon.GAME
        self.group_name = "日常/周常"
        self.group_icon = FluentIcon.CALENDAR
        self.visible = True
        self._templates: dict[str, tuple[np.ndarray, np.ndarray | None]] = {}
        self._missing_template_names: set[str] = set()
        self._match_error_names: set[str] = set()
        self._match_pause_until = 0.0
        self.default_config.update(
            {
                "启用": True,
                "主页压暗阈值": HOME_DIMMED_P95_THRESHOLD_DEFAULT,
                "主页确认等待秒数": 10.0,
                "快速卡带等待秒数": 10.0,
                "快速切换按钮阈值": 0.88,
                "卡带选择页确认等待秒数": 10.0,
                "玩法类别高亮确认秒数": 3.0,
                "玩法类别高亮像素比例": GAMEPLAY_CATEGORY_HIGHLIGHT_MIN_RATIO,
                "广场 OCR 阈值": 0.2,
                "广场入场等待秒数": 30.0,
                "广场感叹号等待秒数": 3.0,
                "祈祷完成后感叹号等待秒数": 5.0,
                "女神像许愿等待秒数": 8.0,
                "女神像导航入口等待秒数": 8.0,
                "女神像导航最长等待秒数": 90.0,
                "女神像完成确认等待秒数": 8.0,
                "女神像许愿最多点击次数": 3,
                "广场返回主页等待秒数": 15.0,
                "广场返回主页最多点击次数": 3,
                "广场返回主页重试间隔秒数": 2.0,
                "广场感叹号阈值": 0.72,
                "梦幻广场阈值": 0.78,
                "广场每日导航阈值": 0.76,
                "广场导航中阈值": 0.76,
            }
        )
        self.config_description.update(
            {
                "广场 OCR 阈值": "广场入场流程 OCR 使用的最低可信度。",
                "主页压暗阈值": "主页左列灰度 p95 低于该值视为被公告压暗（0-255）。",
                "快速切换按钮阈值": "识别 QuickSwitchPlayIco.png 快速切换按钮的模板匹配阈值。",
                "玩法类别高亮像素比例": (
                    "生活玩法游戏卡带标签确认为高亮状态所需的最低亮色像素占比。"
                ),
                "广场入场等待秒数": "点击广场卡带后等待梦幻广场场景出现的最长时间。",
                "广场感叹号等待秒数": "进入广场后等待并点击感叹号小任务的最长时间。",
                "祈祷完成后感叹号等待秒数": (
                    "确认祈祷完成或今日已完成后，再次等待并点击感叹号小任务的最长时间。"
                ),
                "女神像许愿等待秒数": "等待许愿 OCR；超时后尝试固定祈祷位置的间隔。",
                "女神像导航最长等待秒数": "点击每日导航后，等待角色靠近女神像的最长时间。",
                "女神像完成确认等待秒数": "点击许愿后等待每日导航文字消失的最长时间。",
                "女神像许愿最多点击次数": "OCR 仍识别到许愿提示时最多重复点击几次。",
                "广场返回主页等待秒数": "许愿完成后点击主页按钮并确认回到主页的最长时间。",
                "广场返回主页最多点击次数": (
                    "返回主页点击未生效且仍明确识别到广场聊天输入时，允许的总点击次数。"
                ),
                "广场返回主页重试间隔秒数": (
                    "返回主页点击后仍停留在广场时，再次点击主页按钮前的最短等待时间。"
                ),
            }
        )

    def _status_set(self, key: str, value) -> None:
        try:
            self.info_set(key, value)
        except AttributeError:
            pass

    def run(self):
        if not bool(self.config.get("启用", True)):
            self.info_set("状态", "广场女神像已禁用。")
            self.log_info("广场女神像已禁用。")
            return True

        self.info_set("状态", "广场女神像启动。")
        self.log_info("广场女神像：开始从主页进入梦幻广场。")
        if not self._enter_square_from_home():
            self.info_set("状态", "未能进入梦幻广场。")
            return False

        self.info_set("状态", "已进入梦幻广场，开始寻找女神像。")
        if not self._pray_at_goddess():
            self.info_set("状态", "未能完成女神像许愿。")
            self._status_set("女神像许愿结果", "失败")
            return False

        self.info_set("状态", "女神像许愿完成。")
        self._status_set("女神像许愿结果", "完成")
        if not self._return_home_from_square():
            self.info_set("状态", "女神像许愿完成，但未能返回主页。")
            return False
        self.info_set("状态", "女神像许愿完成并返回主页。")
        self.log_completion("广场女神像：许愿完成并返回主页。")
        return True

    def _return_home_from_square(self) -> bool:
        self.info_set("当前阶段", "广场返回主页")
        max_clicks = max(
            1,
            int(self.config.get("广场返回主页最多点击次数", 3)),
        )
        retry_interval = max(
            0.0,
            float(self.config.get("广场返回主页重试间隔秒数", 2.0)),
        )
        self.info_set("广场主页点击次数", f"1/{max_clicks}")
        self.operate_click(*SQUARE_HOME_POINT, after_sleep=1.0)
        return self._wait_for_cartridge_home(
            timeout=float(self.config.get("广场返回主页等待秒数", 15.0)),
            retry_home_clicks=max_clicks - 1,
            retry_interval=retry_interval,
            total_home_clicks=max_clicks,
        )

    def _enter_square_from_home(self) -> bool:
        self.info_set("当前阶段", "打开卡带快速切换")
        if not self.open_cartridge_quick_switcher(
            ensure_home=self._wait_for_cartridge_home,
            click_quick_switch=lambda: self._click_template_until(
                QUICK_SWITCH_TEMPLATE,
                timeout=float(self.config.get("快速卡带等待秒数", 10.0)),
                name="快速切换按钮",
                after_sleep=0.0,
                stabilize=True,
            ),
            confirm_quick_switch_page=self._wait_for_quick_switch_page,
        ):
            self.log_info("广场女神像：未能从主页打开卡带快速切换页面。")
            return False

        self.info_set("当前阶段", "选择生活玩法游戏卡带")
        self.sleep(0.5)
        self.operate_click(*LIFE_GAMEPLAY_CATEGORY_POINT, after_sleep=0.0)
        if not self._wait_for_life_gameplay_category():
            self.log_info("广场女神像：点击后未确认生活玩法游戏卡带类别高亮。")
            return False

        self.info_set("当前阶段", "选择广场卡带2号位")
        self.sleep(FIXED_CARTRIDGE_SLOT_PRE_CLICK_DELAY_SECONDS)
        self.operate_click(*SQUARE_CARTRIDGE_SLOT_POINT, after_sleep=0.0)

        if self._wait_for_template(
            FANTASIA_SQUARE_TEMPLATE,
            timeout=float(self.config.get("广场入场等待秒数", 30.0)),
            name="梦幻广场",
        ):
            return True

        return False

    def _wait_for_cartridge_home(
        self,
        interval: float = 0.35,
        timeout: float | None = None,
        retry_home_clicks: int = 0,
        retry_interval: float = 2.0,
        total_home_clicks: int = 1,
    ) -> bool:
        self.info_set("当前阶段", "确认主页")
        wait_seconds = (
            float(self.config.get("主页确认等待秒数", 10.0))
            if timeout is None
            else max(0.0, float(timeout))
        )
        end_at = monotonic() + wait_seconds
        last_left_hits = 0
        last_p95 = 0.0
        last_gacha_text = ""
        remaining_home_clicks = max(0, int(retry_home_clicks))
        total_home_clicks = max(
            remaining_home_clicks + 1,
            int(total_home_clicks),
        )
        completed_home_clicks = total_home_clicks - remaining_home_clicks
        retry_interval = max(0.0, float(retry_interval))
        next_home_retry_at = (
            monotonic() + retry_interval
            if remaining_home_clicks > 0
            else float("inf")
        )
        while monotonic() <= end_at:
            frame = self.capture_frame()
            left_text = self._ocr_text(
                frame,
                name="主页左列",
                roi=HOME_LEFT_COLUMN_OCR_REFERENCE_ROI,
            )
            last_left_hits = home_left_column_hits(left_text)
            last_p95 = home_left_column_p95_brightness(frame)
            gacha_result = home_gacha_ocr_with_fallback(
                lambda scale: self._ocr_text(
                    frame,
                    name=f"主页抽抽乐 x{scale:g}",
                    roi=HOME_GACHA_OCR_REFERENCE_ROI,
                    ocr_scale=scale,
                )
            )
            last_gacha_text = gacha_result.text
            self.info_set("主页抽抽乐 OCR 尝试", gacha_result.trace)
            self.info_set(
                "主页左列关键词",
                f"{last_left_hits}/{HOME_LEFT_COLUMN_REQUIRED_HITS}",
            )
            self.info_set(
                "主页亮度p95",
                f"{last_p95:.0f}/{self._home_p95_threshold():.0f}",
            )
            self.info_set("主页抽抽乐 OCR", last_gacha_text or "-")
            if home_confirmation_passes(
                left_hits=last_left_hits,
                required_left_hits=HOME_LEFT_COLUMN_REQUIRED_HITS,
                brightness=last_p95,
                brightness_threshold=self._home_p95_threshold(),
                gacha_ocr_text=last_gacha_text,
            ):
                return True
            self.clear_temporary_home_announcement_if_needed(
                left_hits=last_left_hits,
                required_left_hits=HOME_LEFT_COLUMN_REQUIRED_HITS,
                brightness=last_p95,
                brightness_threshold=self._home_p95_threshold(),
                gacha_ocr_text=last_gacha_text,
                context="广场女神像返回主页",
            )
            normalized_gacha_text = self._normalize_text(last_gacha_text)
            square_chat_visible = (
                normalized_gacha_text.startswith("输入")
                and "抽抽乐" not in normalized_gacha_text
            )
            if (
                remaining_home_clicks > 0
                and square_chat_visible
                and monotonic() >= next_home_retry_at
            ):
                completed_home_clicks += 1
                remaining_home_clicks -= 1
                self.info_set(
                    "广场主页点击次数",
                    f"{completed_home_clicks}/{total_home_clicks}",
                )
                self.log_info(
                    "广场女神像：返回主页点击未生效，"
                    f"仍识别到广场聊天输入，执行第{completed_home_clicks}次点击。"
                )
                self.operate_click(*SQUARE_HOME_POINT, after_sleep=1.0)
                next_home_retry_at = monotonic() + retry_interval
                continue
            self.sleep(interval)

        self.log_info(
            "广场女神像：未同时确认左列关键词、亮度和抽抽乐文字，"
            f"left={last_left_hits}/{HOME_LEFT_COLUMN_REQUIRED_HITS}, "
            f"p95={last_p95:.0f}, ocr={last_gacha_text or '-'}。"
        )
        return False

    def _wait_for_quick_switch_page(self, interval: float = 0.5) -> bool:
        self.info_set("当前阶段", "确认卡带选择页")
        self.sleep(1.0)
        end_at = monotonic() + float(
            self.config.get("卡带选择页确认等待秒数", 10.0)
        )
        last_text = ""
        while monotonic() <= end_at:
            frame = self.capture_frame()
            text = self._ocr_text(frame, name="卡带选择页")
            last_text = text or last_text
            match_count = sum(
                1 for pattern in QUICK_SWITCH_PAGE_PATTERNS if self._matches_any(text, [pattern])
            )
            self.info_set("卡带选择页 OCR", text or "-")
            self.info_set(
                "卡带选择页 OCR 命中",
                f"{match_count}/{len(QUICK_SWITCH_PAGE_PATTERNS)}",
            )
            if match_count == len(QUICK_SWITCH_PAGE_PATTERNS):
                return True
            self.sleep(interval)

        self.log_info(
            "广场女神像：点击快速切换后未确认卡带选择页，"
            f"OCR={last_text or '-'}。"
        )
        return False

    def _wait_for_life_gameplay_category(self, interval: float = 0.5) -> bool:
        end_at = monotonic() + float(self.config.get("玩法类别高亮确认秒数", 3.0))
        last_text = ""
        last_highlight_ratio = 0.0
        while monotonic() <= end_at:
            frame = self.capture_frame()
            text = self._ocr_text(
                frame,
                name="生活玩法游戏卡带",
                roi=LIFE_GAMEPLAY_CATEGORY_OCR_ROI,
            )
            last_text = text or last_text
            last_highlight_ratio = category_highlight_ratio(
                frame,
                LIFE_GAMEPLAY_CATEGORY_HIGHLIGHT_REGION,
            )
            self.info_set("生活玩法游戏卡带 OCR", text or "-")
            self.info_set("生活玩法类别高亮", f"{last_highlight_ratio:.3f}")
            if (
                self._matches_any(text, [LIFE_GAMEPLAY_CATEGORY_LABEL])
                and last_highlight_ratio
                >= float(
                    self.config.get(
                        "玩法类别高亮像素比例",
                        GAMEPLAY_CATEGORY_HIGHLIGHT_MIN_RATIO,
                    )
                )
            ):
                return True
            self.sleep(interval)

        self.log_info(
            "广场女神像：未确认生活玩法游戏卡带类别高亮，"
            f"highlight={last_highlight_ratio:.3f}, OCR={last_text or '-'}。"
        )
        return False

    def _pray_at_goddess(self) -> bool:
        self.info_set("当前阶段", "检查广场感叹号")
        self._click_square_notice_if_present(
            timeout=float(self.config.get("广场感叹号等待秒数", 3.0))
        )

        self.info_set("当前阶段", "检查女神像每日导航")
        if not self._click_goddess_daily_navigation_until(
            timeout=float(self.config.get("女神像导航入口等待秒数", 8.0))
        ):
            self.info_set("女神像许愿 OCR", "每日导航信号未出现，按今日已完成处理")
            self.log_info("广场女神像：每日导航图标与文字未同时出现，按今日已完成处理。")
        else:
            self.info_set("当前阶段", "等待并完成女神像许愿")
            if not self._wait_for_goddess_prayer_completion(
                timeout=float(self.config.get("女神像导航最长等待秒数", 90.0))
            ):
                self.log_info("广场女神像：等待许愿或每日导航文字消失超时。")
                return False

        self.info_set("当前阶段", "祈祷完成后检查广场感叹号")
        self._click_square_notice_if_present(
            timeout=float(self.config.get("祈祷完成后感叹号等待秒数", 5.0))
        )
        return True

    def _click_square_notice_if_present(
        self,
        timeout: float = 3.0,
        interval: float = 0.35,
    ) -> bool:
        end_at = monotonic() + max(0.0, timeout)
        last_score = -1.0
        while monotonic() <= end_at:
            frame = self.capture_frame()
            frame_height, frame_width = frame.shape[:2]
            result = self._match(frame, SQUARE_NOTICE_TEMPLATE)
            last_score = result.score
            self.info_set("广场感叹号", f"{result.score:.3f}")
            if self._passes(result, SQUARE_NOTICE_TEMPLATE):
                self._click_client(
                    result.position[0] + result.size[0] // 2,
                    result.position[1] + result.size[1] // 2,
                    frame_width,
                    frame_height,
                    after_sleep=1.0,
                )
                return True
            self.sleep(interval)

        self.info_set("广场感叹号", f"{last_score:.3f}")
        return False

    def _click_goddess_daily_navigation_until(
        self,
        timeout: float,
        interval: float = 0.35,
    ) -> bool:
        end_at = monotonic() + max(0.0, timeout)
        last_score = -1.0
        last_pixel_score = -1.0
        last_text = ""
        while monotonic() <= end_at:
            frame = self.capture_frame()
            frame_height, frame_width = frame.shape[:2]
            result = self._match(frame, SQUARE_DAILY_ICON_TEMPLATE)
            point, text = self._goddess_navigation_click_point(
                frame,
                name="广场导航文本",
            )
            last_score = result.score
            last_pixel_score = result.pixel_score
            last_text = text or last_text
            self.info_set("广场每日导航", f"{result.score:.3f}/{result.pixel_score:.3f}")
            self.info_set("广场导航文本 OCR", text or "-")
            if self._passes(result, SQUARE_DAILY_ICON_TEMPLATE) and point is not None:
                self._click_client(
                    point[0],
                    point[1],
                    frame_width,
                    frame_height,
                    after_sleep=2.0,
                )
                return True
            self.sleep(interval)

        self.info_set("广场每日导航", f"{last_score:.3f}/{last_pixel_score:.3f}")
        self.info_set("广场导航文本 OCR", last_text or "-")
        return False

    def _wait_for_goddess_prayer_completion(
        self,
        timeout: float,
        interval: float = 0.5,
    ) -> bool:
        end_at = monotonic() + max(0.0, timeout)
        fallback_interval = max(
            0.5,
            float(self.config.get("女神像许愿等待秒数", 8.0)),
        )
        next_fallback_at = monotonic() + fallback_interval
        max_clicks = max(1, int(self.config.get("女神像许愿最多点击次数", 3)))
        click_count = 0

        while monotonic() <= end_at:
            frame = self.capture_frame()
            frame_height, frame_width = frame.shape[:2]
            point, text = self._ocr_pattern_click_point(
                frame,
                GODDESS_PRAY_PATTERNS,
                name="女神像许愿",
                roi=None,
            )
            self.info_set("女神像许愿 OCR", text or "-")

            should_click_fallback = point is None and monotonic() >= next_fallback_at
            if point is not None and click_count < max_clicks:
                self._click_client(
                    point[0],
                    point[1],
                    frame_width,
                    frame_height,
                    after_sleep=2.0,
                )
                click_count += 1
            elif should_click_fallback and click_count < max_clicks:
                self.operate_click(*GODDESS_PRAY_FALLBACK_POINT, after_sleep=2.0)
                click_count += 1
            else:
                self.sleep(interval)
                continue

            if self._wait_for_daily_navigation_to_disappear(
                timeout=float(self.config.get("女神像完成确认等待秒数", 8.0))
            ):
                return True
            next_fallback_at = monotonic() + fallback_interval

        return False

    def _wait_for_daily_navigation_to_disappear(
        self,
        timeout: float,
        interval: float = 0.5,
    ) -> bool:
        end_at = monotonic() + max(0.0, timeout)
        last_text = ""
        while monotonic() <= end_at:
            frame = self.capture_frame()
            point, text = self._goddess_navigation_click_point(
                frame,
                name="广场导航完成确认",
            )
            last_text = text or last_text
            self.info_set("广场导航文本 OCR", text or "-")
            if point is None:
                return True
            self.sleep(interval)

        self.info_set("广场导航文本 OCR", last_text or "-")
        return False

    def _click_pray_until_gone(self, timeout: float) -> bool:
        return self._wait_for_goddess_prayer_completion(timeout=timeout)

    def _start_goddess_navigation(self, timeout: float) -> bool:
        return self._click_goddess_daily_navigation_until(timeout=timeout)

    def _click_template_until(
        self,
        spec: TemplateSpec,
        timeout: float,
        name: str,
        target_offset_mf: tuple[int, int] = (0, 0),
        after_sleep: float = 0.0,
        interval: float = 0.35,
        stabilize: bool = False,
    ) -> bool:
        end_at = monotonic() + max(0.0, timeout)
        last_score = -1.0
        while monotonic() <= end_at:
            frame = self.capture_frame()
            frame_height, frame_width = frame.shape[:2]
            result = self._match(frame, spec)
            last_score = result.score
            self.info_set(name, f"{result.score:.3f}/{result.pixel_score:.3f}")
            if self._passes(result, spec):
                stable_center = None
                if stabilize:

                    def sample_match():
                        sampled_frame = self.capture_frame()
                        return self._match(sampled_frame, spec), sampled_frame.shape

                    stabilized = stabilize_template_match(
                        result,
                        frame.shape,
                        sample_match=sample_match,
                        passes=lambda candidate: self._passes(candidate, spec),
                        sleep=self.sleep,
                        on_sample=lambda candidate: self.info_set(
                            name,
                            f"{candidate.score:.3f}/{candidate.pixel_score:.3f}",
                        ),
                    )
                    if stabilized is None:
                        self.info_set(f"{name}稳定识别", "未形成稳定位置")
                        return False
                    consensus, frame_shape = stabilized
                    frame_height, frame_width = frame_shape[:2]
                    stable_center = consensus.center
                    self.info_set(
                        f"{name}稳定识别",
                        (
                            f"center=({stable_center[0]},{stable_center[1]}), "
                            f"hits={consensus.hit_count}/{consensus.sample_count}, "
                            f"match={consensus.average_score:.3f}, "
                            f"pixel={consensus.average_pixel_score:.3f}, "
                            f"spread={consensus.center_spread:.1f}"
                        ),
                    )
                offset_x, offset_y = self._mf_offset_for_frame(
                    target_offset_mf[0],
                    target_offset_mf[1],
                    frame_width,
                    frame_height,
                )
                center_x, center_y = (
                    stable_center
                    if stable_center is not None
                    else (
                        result.position[0] + result.size[0] // 2,
                        result.position[1] + result.size[1] // 2,
                    )
                )
                x = center_x + offset_x
                y = center_y + offset_y
                self._click_client(x, y, frame_width, frame_height, after_sleep=after_sleep)
                return True
            self.sleep(interval)

        self.info_set(name, f"{last_score:.3f}")
        return False

    def _click_ocr_pattern_until(
        self,
        patterns: list[str],
        timeout: float,
        name: str,
        roi: tuple[int, int, int, int] | None = None,
        target_offset_mf: tuple[int, int] = (0, 0),
        after_sleep: float = 0.0,
        interval: float = 0.5,
    ) -> bool:
        end_at = monotonic() + max(0.0, timeout)
        last_text = ""
        while monotonic() <= end_at:
            frame = self.capture_frame()
            frame_height, frame_width = frame.shape[:2]
            point, text = self._ocr_pattern_click_point(frame, patterns, name=name, roi=roi)
            last_text = text or last_text
            self.info_set(f"{name} OCR", text or "-")
            if point is not None:
                offset_x, offset_y = self._mf_offset_for_frame(
                    target_offset_mf[0],
                    target_offset_mf[1],
                    frame_width,
                    frame_height,
                )
                self._click_client(
                    point[0] + offset_x,
                    point[1] + offset_y,
                    frame_width,
                    frame_height,
                    after_sleep=after_sleep,
                )
                return True
            self.sleep(interval)

        self.info_set(f"{name} OCR", last_text or "-")
        return False

    def _find_ocr_click_point_until(
        self,
        patterns: list[str],
        timeout: float,
        name: str,
        roi: tuple[int, int, int, int] | None = None,
        interval: float = 0.5,
    ) -> tuple[tuple[int, int] | None, tuple[int, int] | None, str]:
        end_at = monotonic() + max(0.0, timeout)
        last_text = ""
        while monotonic() <= end_at:
            frame = self.capture_frame()
            frame_height, frame_width = frame.shape[:2]
            point, text = self._ocr_pattern_click_point(frame, patterns, name=name, roi=roi)
            last_text = text or last_text
            if point is not None:
                return point, (frame_height, frame_width), text
            self.sleep(interval)

        return None, None, last_text

    def _ocr_pattern_click_point(
        self,
        frame,
        patterns: list[str],
        name: str,
        roi: tuple[int, int, int, int] | None = None,
    ) -> tuple[tuple[int, int] | None, str]:
        left, top, _crop = self._roi_frame(frame, roi)
        boxes = self._ocr_boxes(frame, name=name, roi=roi)
        text = " ".join(getattr(box, "name", "") for box in boxes if getattr(box, "name", ""))
        matched_boxes = [
            box for box in boxes if self._matches_any(getattr(box, "name", ""), patterns)
        ]
        if not matched_boxes and text and self._matches_any(text, patterns):
            matched_boxes = [box for box in boxes if getattr(box, "name", "")]
        if not matched_boxes:
            return None, text

        box = min(
            matched_boxes,
            key=lambda item: (
                float(getattr(item, "y", 0)),
                float(getattr(item, "x", 0)),
            ),
        )
        x = getattr(box, "x", None)
        y = getattr(box, "y", None)
        width = getattr(box, "width", None)
        height = getattr(box, "height", None)
        if None in (x, y, width, height):
            return None, text

        center_x = int(round(left + float(x) + float(width) / 2))
        center_y = int(round(top + float(y) + float(height) / 2))
        return (center_x, center_y), text

    def _goddess_navigation_click_point(
        self,
        frame,
        name: str,
    ) -> tuple[tuple[int, int] | None, str]:
        left, top, _crop = self._roi_frame(frame, GODDESS_DAILY_REGION)
        boxes = self._ocr_boxes(
            frame,
            name=name,
            roi=GODDESS_DAILY_REGION,
        )
        text = " ".join(
            getattr(box, "name", "")
            for box in boxes
            if getattr(box, "name", "")
        )
        normalized_text = self._normalize_text(text)
        matched_characters = {
            character
            for character in GODDESS_NAVIGATION_TARGET
            if character in normalized_text
        }
        self.info_set(
            "广场导航文字命中",
            f"{len(matched_characters)}/{len(GODDESS_NAVIGATION_TARGET)}",
        )
        if len(matched_characters) < GODDESS_NAVIGATION_MINIMUM_HITS:
            return None, text

        relevant_boxes = [
            box
            for box in boxes
            if any(
                character in self._normalize_text(getattr(box, "name", ""))
                for character in GODDESS_NAVIGATION_TARGET
            )
        ]
        geometries = []
        for box in relevant_boxes:
            x = getattr(box, "x", None)
            y = getattr(box, "y", None)
            width = getattr(box, "width", None)
            height = getattr(box, "height", None)
            if None in (x, y, width, height):
                continue
            geometries.append(
                (
                    float(x),
                    float(y),
                    float(x) + float(width),
                    float(y) + float(height),
                )
            )
        if not geometries:
            return None, text

        text_left = min(geometry[0] for geometry in geometries)
        text_top = min(geometry[1] for geometry in geometries)
        text_right = max(geometry[2] for geometry in geometries)
        text_bottom = max(geometry[3] for geometry in geometries)
        return (
            int(round(left + (text_left + text_right) / 2)),
            int(round(top + (text_top + text_bottom) / 2)),
        ), text

    def _find_template_until(
        self,
        spec: TemplateSpec,
        timeout: float,
        name: str,
        interval: float = 0.35,
    ) -> tuple[MatchResult | None, tuple[int, int] | None]:
        end_at = monotonic() + max(0.0, timeout)
        last_score = -1.0
        while monotonic() <= end_at:
            frame = self.capture_frame()
            result = self._match(frame, spec)
            last_score = result.score
            self.info_set(name, f"{result.score:.3f}")
            if self._passes(result, spec):
                frame_height, frame_width = frame.shape[:2]
                return result, (frame_height, frame_width)
            self.sleep(interval)

        self.info_set(name, f"{last_score:.3f}")
        return None, None

    def _find_square_label_until(
        self,
        timeout: float,
        name: str,
        interval: float = 0.5,
    ) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
        end_at = monotonic() + max(0.0, timeout)
        last_text = ""
        while monotonic() <= end_at:
            frame = self.capture_frame()
            frame_height, frame_width = frame.shape[:2]
            boxes = self._ocr_boxes(frame, name=name)
            text = " ".join(getattr(box, "name", "") for box in boxes if getattr(box, "name", ""))
            last_text = text or last_text
            self.info_set(f"{name} OCR", text or "-")
            point = self._square_label_click_point(boxes, frame_width, frame_height)
            if point is not None:
                return point, (frame_height, frame_width)
            self.sleep(interval)

        self.info_set(f"{name} OCR", last_text or "-")
        return None, None

    def _wait_for_template(
        self,
        spec: TemplateSpec,
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

    def _wait_for_ocr_requirements(
        self,
        requirements: list[tuple[str, float]],
        timeout: float,
        name: str,
        roi: tuple[int, int, int, int] | None = None,
        interval: float = 0.5,
    ) -> tuple[bool, str]:
        end_at = monotonic() + max(0.0, timeout)
        last_text = ""
        while monotonic() <= end_at:
            frame = self.capture_frame()
            entries = self._ocr_entries(frame, name=name, roi=roi)
            text = " ".join(label for label, _confidence in entries)
            last_text = text or last_text
            self.info_set(f"{name} OCR", text or "-")
            if self._ocr_requirements_met(entries, requirements):
                return True, text
            self.sleep(interval)

        return False, last_text

    def _wait_for_ocr_absent(
        self,
        patterns: list[str],
        timeout: float,
        name: str,
        roi: tuple[int, int, int, int] | None = None,
        interval: float = 0.5,
    ) -> tuple[bool, str]:
        end_at = monotonic() + max(0.0, timeout)
        last_text = ""
        while monotonic() <= end_at:
            frame = self.capture_frame()
            text = self._ocr_text(frame, name=name, roi=roi)
            last_text = text or last_text
            self.info_set(f"{name} OCR", text or "-")
            if text and not self._matches_any(text, patterns):
                return True, text
            self.sleep(interval)

        return False, last_text

    def _match(self, frame, spec: TemplateSpec) -> MatchResult:
        empty = MatchResult(-1.0, (0, 0), (0, 0))
        if monotonic() < self._match_pause_until:
            return empty

        try:
            return task_vision.match_template(
                frame,
                spec,
                self.config,
                TEMPLATE_DIR,
                cache=self._templates,
                min_size=5,
                loader=lambda _template_dir, spec: self._load_template(spec),
            )
        except RuntimeError as exc:
            if spec.name not in self._missing_template_names:
                self._missing_template_names.add(spec.name)
                self.log_warning(str(exc), notify=True)
            return empty
        except (cv2.error, MemoryError) as exc:
            self._match_pause_until = monotonic() + 2.0
            message = f"图像匹配内存不足，暂停识别2秒：{spec.name}"
            self.info_set("匹配错误", message)
            if spec.name not in self._match_error_names:
                self._match_error_names.add(spec.name)
                self.log_warning(f"{message}；{exc}", notify=True)
            return empty

    def _load_template(self, spec: TemplateSpec) -> tuple[np.ndarray, np.ndarray | None]:
        return task_vision.load_template(TEMPLATE_DIR, spec, cache=self._templates)

    def _passes(self, result: MatchResult, spec: TemplateSpec) -> bool:
        return task_vision.passes_match(result, spec, self.config)

    def _ocr_text(
        self,
        frame,
        name: str,
        roi: tuple[int, int, int, int] | None = None,
        ocr_scale: float = 1.0,
    ) -> str:
        return " ".join(
            label
            for label, _confidence in self._ocr_entries(
                frame,
                name,
                roi,
                ocr_scale=ocr_scale,
            )
        )

    def _ocr_entries(
        self,
        frame,
        name: str,
        roi: tuple[int, int, int, int] | None = None,
        ocr_scale: float = 1.0,
    ) -> list[tuple[str, float]]:
        boxes = self._ocr_boxes(
            frame,
            name=name,
            roi=roi,
            ocr_scale=ocr_scale,
        )
        entries = []
        for box in boxes:
            label = getattr(box, "name", "")
            if not label:
                continue
            confidence = float(getattr(box, "confidence", 1.0))
            if confidence > 1.0:
                confidence /= 100.0
            entries.append((label, confidence))
        return entries

    def _ocr_boxes(
        self,
        frame,
        name: str,
        roi: tuple[int, int, int, int] | None = None,
        ocr_scale: float = 1.0,
    ):
        ocr_frame = self._crop_reference(frame, roi) if roi is not None else frame
        try:
            if ocr_scale <= 0:
                raise ValueError("ocr_scale must be positive")
            if ocr_scale != 1.0:
                ocr_frame = cv2.resize(
                    ocr_frame,
                    None,
                    fx=ocr_scale,
                    fy=ocr_scale,
                    interpolation=cv2.INTER_CUBIC,
                )
            return self.ocr(
                frame=ocr_frame,
                threshold=float(self.config.get("广场 OCR 阈值", 0.2)),
                target_height=720,
                log=False,
                name=name,
            )
        except Exception as exc:
            self.info_set(f"{name} OCR 错误", str(exc))
            return []

    def _click_entry_reference(self, x: int, y: int, after_sleep: float = 0.0):
        self.operate_click(
            max(0.0, min(1.0, x / ENTRY_REFERENCE_WIDTH)),
            max(0.0, min(1.0, y / ENTRY_REFERENCE_HEIGHT)),
            after_sleep=after_sleep,
        )

    def _click_client(
        self,
        x: int,
        y: int,
        frame_width: int,
        frame_height: int,
        after_sleep: float = 0.0,
    ):
        self.operate_click(
            max(0.0, min(1.0, x / max(1, frame_width))),
            max(0.0, min(1.0, y / max(1, frame_height))),
            after_sleep=after_sleep,
        )

    def _drag_entry_reference(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
        duration: float = 0.7,
        after_sleep: float = 0.0,
    ) -> None:
        frame = self.capture_frame()
        frame_height, frame_width = frame.shape[:2]
        start_client = (
            round(frame_width * start[0] / ENTRY_REFERENCE_WIDTH),
            round(frame_height * start[1] / ENTRY_REFERENCE_HEIGHT),
        )
        end_client = (
            round(frame_width * end[0] / ENTRY_REFERENCE_WIDTH),
            round(frame_height * end[1] / ENTRY_REFERENCE_HEIGHT),
        )
        self.drag_client(start_client, end_client, duration=duration, after_sleep=after_sleep)

    def _home_p95_threshold(self) -> float:
        return float(self.config.get("主页压暗阈值", HOME_DIMMED_P95_THRESHOLD_DEFAULT))

    @staticmethod
    def _mf_point(x: int, y: int) -> tuple[int, int]:
        return (
            round(x * REFERENCE_WIDTH / HD720_REFERENCE_WIDTH),
            round(y * REFERENCE_HEIGHT / HD720_REFERENCE_HEIGHT),
        )

    @staticmethod
    def _mf_roi(x: int, y: int, width: int, height: int) -> tuple[int, int, int, int]:
        left, top = SquareGoddessTask._mf_point(x, y)
        right, bottom = SquareGoddessTask._mf_point(x + width, y + height)
        return left, top, max(1, right - left), max(1, bottom - top)

    @staticmethod
    def _mf_offset_for_frame(
        x: int,
        y: int,
        frame_width: int,
        frame_height: int,
    ) -> tuple[int, int]:
        return (
            round(x * frame_width / HD720_REFERENCE_WIDTH),
            round(y * frame_height / HD720_REFERENCE_HEIGHT),
        )

    def _ocr_requirements_met(
        self,
        entries: list[tuple[str, float]],
        requirements: list[tuple[str, float]],
    ) -> bool:
        combined_text = " ".join(label for label, _confidence in entries)
        for pattern, min_confidence in requirements:
            if not self._ocr_requirement_met(entries, combined_text, pattern, min_confidence):
                return False
        return True

    def _ocr_requirement_met(
        self,
        entries: list[tuple[str, float]],
        combined_text: str,
        pattern: str,
        min_confidence: float,
    ) -> bool:
        normalized_pattern = self._normalize_text(pattern)
        for label, confidence in entries:
            if re.search(normalized_pattern, self._normalize_text(label), flags=re.IGNORECASE):
                return confidence >= min_confidence

        if not re.search(
            normalized_pattern,
            self._normalize_text(combined_text),
            flags=re.IGNORECASE,
        ):
            return False
        return any(confidence >= min_confidence for _label, confidence in entries)

    @staticmethod
    def _matches_any(text: str, patterns: list[str]) -> bool:
        normalized = SquareGoddessTask._normalize_text(text)
        for pattern in patterns:
            normalized_pattern = SquareGoddessTask._normalize_text(pattern)
            if re.search(normalized_pattern, normalized, flags=re.IGNORECASE):
                return True
        return False

    @staticmethod
    def _square_label_click_point(
        boxes,
        frame_width: int,
        frame_height: int,
    ) -> tuple[int, int] | None:
        candidates = []
        for box in boxes:
            text = SquareGoddessTask._normalize_text(getattr(box, "name", ""))
            if not text or "广场" not in text:
                continue
            x = getattr(box, "x", None)
            y = getattr(box, "y", None)
            width = getattr(box, "width", None)
            height = getattr(box, "height", None)
            if None in (x, y, width, height):
                continue

            center_x = int(round(float(x) + float(width) / 2))
            center_y = int(round(float(y) + float(height) / 2))
            if center_y < frame_height * 0.50:
                continue
            candidates.append((center_x, center_y, float(x)))

        if not candidates:
            return None

        center_x, center_y, _left = min(candidates, key=lambda item: item[2])
        click_y = int(round(center_y - frame_height * 0.085))
        return center_x, max(0, click_y)

    _normalize_text = staticmethod(normalize_ocr_text)
    @staticmethod
    def _roi_frame(
        frame: np.ndarray,
        roi: tuple[int, int, int, int] | None,
    ) -> tuple[int, int, np.ndarray]:
        return reference_roi_frame(frame, roi, (REFERENCE_WIDTH, REFERENCE_HEIGHT))

    @staticmethod
    def _crop_reference(frame, roi: tuple[int, int, int, int] | None):
        return reference_roi_frame(frame, roi, (REFERENCE_WIDTH, REFERENCE_HEIGHT))[2]

QUICK_SWITCH_TEMPLATE = TemplateSpec(
    name="quick_switch",
    file_name="image/green/QuickSwitchPlayIco.png",
    threshold_key="快速切换按钮阈值",
    default_threshold=0.88,
    roi=(480, 918, 768, 162),
    green_mask=True,
    scale_ratios=(0.95, 0.975, 1.0, 1.025, 1.05),
    min_pixel_score=0.85,
    candidate_center_roi=(650 / 1920, 950 / 1080, 1050 / 1920, 1045 / 1080),
    minimum_safe_threshold=0.88,
    min_zncc_score=0.85,
)

FANTASIA_SQUARE_TEMPLATE = TemplateSpec(
    name="fantasia_square",
    file_name="image/Mirror_FantasiaSquare_Ico.png",
    threshold_key="梦幻广场阈值",
    default_threshold=0.78,
    roi=SquareGoddessTask._mf_roi(656, 622, 77, 66),
)

SQUARE_NOTICE_TEMPLATE = TemplateSpec(
    name="square_notice",
    file_name="image/green/tanhaoGE.png",
    threshold_key="广场感叹号阈值",
    default_threshold=0.72,
    roi=(1376, 862, 66, 51),
    green_mask=True,
    scale_ratios=(0.90, 0.925, 0.95, 0.975, 1.0),
    min_pixel_score=0.72,
)

GODDESS_DAILY_REGION = (1546, 199, 311, 63)

SQUARE_DAILY_ICON_TEMPLATE = TemplateSpec(
    name="square_daily_icon",
    file_name="image/Square_DailyIco.png",
    threshold_key="广场每日导航阈值",
    default_threshold=0.76,
    roi=GODDESS_DAILY_REGION,
    min_pixel_score=0.72,
)

SQUARE_MISSION_NAVI_TEMPLATE = TemplateSpec(
    name="square_mission_navigation",
    file_name="image/Square_misstion_Nvi.png",
    threshold_key="广场导航中阈值",
    default_threshold=0.76,
    roi=SquareGoddessTask._mf_roi(1168, 106, 69, 247),
)

GODDESS_NAVIGATION_TARGET = "移动至艾力克史温女"
GODDESS_NAVIGATION_MINIMUM_HITS = 6
GODDESS_PRAY_PATTERNS = [r"向女神像许愿|女神像许愿|许愿"]
GODDESS_PRAY_FALLBACK_POINT = (
    1412 / REFERENCE_WIDTH,
    884 / REFERENCE_HEIGHT,
)
