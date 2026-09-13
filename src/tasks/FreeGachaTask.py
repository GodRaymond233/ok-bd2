from time import monotonic

from qfluentwidgets import FluentIcon

from src.tasks.BaseBD2Task import BaseBD2Task
from src.tasks.task_vision_mixin import LOADING_TEMPLATE, TaskVisionMixin
from src.utils.ocr_utils import keyword_match_count

KEYWORD_MATCH_RATIO = 0.9

# 免费入口重试前对同一新帧的状态判定结果（BUG-20260913-01）。
FREE_ENTRY_RETRY_READY = "ready"
FREE_ENTRY_RETRY_DIALOG = "dialog"
FREE_ENTRY_RETRY_LOADING = "loading"
FREE_ENTRY_RETRY_CHANGED = "changed"

# 以下点击点均为 1920×1080 参考（_click_reference 按当前客户区归一）。
GACHA_ENTRY_REFERENCE_POINT = (162, 986)
EQUIPMENT_TAB_REFERENCE_POINT = (175, 432)
BACK_BUTTON_REFERENCE_POINT = (105, 51)
FREE_GACHA_BUTTON_REFERENCE_POINT = (347, 973)
CONFIRM_DIALOG_OK_REFERENCE_POINT = (1045, 649)
RESULT_PAGE_CLOSE_REFERENCE_POINT = (1420, 326)
SKIP_BUTTON_REFERENCE_POINT = (1770, 60)


class FreeGachaTask(TaskVisionMixin, BaseBD2Task):
    vision_threshold_key = "加载页面阈值"
    ocr_threshold_key = "抽卡 OCR 阈值"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "白嫖抽抽乐"
        self.description = "领取服装和装备的所有免费抽抽乐。"
        self.icon = FluentIcon.GAME
        self.group_name = "日常/周常"
        self.group_icon = FluentIcon.CALENDAR
        self.visible = True
        self._init_vision_state()
        self.default_config.update(
            {
                "启用": True,
                "加载页面阈值": 0.72,
                "主页压暗阈值": 185.0,
                "抽卡 OCR 阈值": 0.2,
                "loading 出现等待秒数": 6.0,
                "loading 消失等待秒数": 35.0,
                "抽卡页面等待秒数": 12.0,
                "免费抽按钮等待秒数": 3.0,
                "确认弹窗等待秒数": 8.0,
                "确认弹窗重试次数": 2,
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
                "主页压暗阈值": "主页左列灰度 p95 低于该值视为被公告压暗（0-255）。",
                "抽卡页面关键词最低命中数": "确认已进入抽卡页所需的 OCR 关键词数量。",
                "确认弹窗关键词最低命中数": "确认抽抽乐弹窗所需的 OCR 关键词数量。",
                "确认弹窗重试次数": "免费入口点击后确认弹窗未出现时的额外重试次数。",
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
        if not self._wait_for_home_confirmation("白嫖抽抽乐入口前主页确认"):
            self.info_set("状态", "白嫖抽抽乐入口前主页确认失败。")
            self.log_info("白嫖抽抽乐：入口前未同时确认左列关键词、亮度和抽抽乐文字，不点击抽抽乐入口。")
            return False
        self._click_reference(*GACHA_ENTRY_REFERENCE_POINT, after_sleep=0.5)
        loading_state, gacha_found, _ = self._wait_loading_or_gacha_page("进入抽卡页")
        if loading_state == "stuck":
            return self._fail_run("进入抽卡页")
        if not gacha_found and not self._wait_for_gacha_page("进入抽卡页"):
            return self._fail_run("进入抽卡页")

        if not self._run_free_section(
            "服装抽抽乐",
            verify_finished=True,
        ):
            return self._fail_run("服装抽抽乐")

        self._sleep_after_recognition()
        self._click_reference(*EQUIPMENT_TAB_REFERENCE_POINT, after_sleep=0.8)
        # 换池必须验证目标池标题：通用抽卡页关键词在服装页同样能命中，
        # 无法证明实际换池（BUG-20260913-01）。
        if not self._wait_for_equipment_pool_page("切换装备抽卡"):
            return self._fail_run("切换装备抽卡")

        if not self._run_free_section(
            "装备抽抽乐",
            verify_finished=True,
        ):
            return self._fail_run("装备抽抽乐")

        self._sleep_after_recognition()
        self._click_reference(*BACK_BUTTON_REFERENCE_POINT, after_sleep=1.0)
        if not self._wait_loading_or_home_confirmation("抽抽乐返回主页"):
            return self._fail_run("返回主页")

        self.info_set("状态", "白嫖抽抽乐完成。")
        self.log_completion("白嫖抽抽乐：流程完成。")
        return True

    def _fail_run(self, stage: str) -> bool:
        # 失败路径必须把"失败"写进状态，否则 run_history 会把本次运行
        # 记成成功，调度账本当天不再补跑（BUG-20260912-01）。
        self.info_set("状态", f"白嫖抽抽乐{stage}失败。")
        return False

    def _run_free_section(
        self,
        section_name: str,
        verify_finished: bool,
    ) -> bool:
        available, _text, judged = self._wait_for_free_gacha(section_name)
        self.info_set(
            f"{section_name} 免费抽",
            "可领取" if available else ("无" if judged else "无法判断"),
        )
        if not available:
            if judged:
                self.log_info(f"{section_name}：未检测到所有免费抽抽乐，跳过。")
                return True
            # OCR 全程无文本时不能当作"无免费"成功跳过（BUG-20260913-01）。
            self.log_info(
                f"{section_name}：免费入口等待期间 OCR 未产出任何文本，无法判断是否有免费抽。"
            )
            return self._fail_run(f"{section_name}免费入口判断")

        if not self._open_confirm_dialog_with_retry(section_name):
            return self._fail_run(f"{section_name}确认弹窗")

        self._sleep_after_recognition()
        self._click_reference(*CONFIRM_DIALOG_OK_REFERENCE_POINT, after_sleep=1.0)
        if not self._handle_result_until_back(section_name):
            return self._fail_run(f"{section_name}结果处理")

        if verify_finished:
            still_available, _recheck_text, _recheck_judged = self._wait_for_free_gacha(
                section_name,
                timeout=1.5,
            )
            if still_available:
                self.log_info(f"{section_name}：返回后仍检测到所有免费抽抽乐。")
                return self._fail_run(f"{section_name}领取复核")
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

    def _wait_loading_or_home_confirmation(
        self,
        name: str,
        interval: float = 0.35,
    ) -> bool:
        end_at = monotonic() + float(self.config.get("loading 出现等待秒数", 6.0))
        while monotonic() <= end_at:
            frame = self.capture_frame()
            if self._home_confirmation_ok(frame, name):
                return True

            loading = self._match(frame, LOADING_TEMPLATE)
            self.info_set(f"{name}_loading_appear", f"{loading.score:.3f}")
            if self._passes(loading, LOADING_TEMPLATE):
                return self._wait_loading_gone_or_home_confirmation(name, interval=interval)
            self.sleep(interval)

        self.log_info(f"{name}：未检测到 UI_loading_black.png，继续确认主页。")
        return self._wait_for_home_confirmation(name, interval=interval)

    def _wait_loading_gone_or_home_confirmation(
        self,
        name: str,
        interval: float = 0.35,
    ) -> bool:
        end_at = monotonic() + float(self.config.get("loading 消失等待秒数", 35.0))
        while monotonic() <= end_at:
            frame = self.capture_frame()
            if self._home_confirmation_ok(frame, name):
                return True

            loading = self._match(frame, LOADING_TEMPLATE)
            self.info_set(f"{name}_loading_gone", f"{loading.score:.3f}")
            if not self._passes(loading, LOADING_TEMPLATE):
                return self._wait_for_home_confirmation(name, interval=interval)
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
    ) -> tuple[bool, str, bool]:
        """等待免费入口关键词。

        返回是否命中、最后一次 OCR 文本，以及等待期间 OCR 是否产出过
        任何文本；全程空文本不得读作"无免费"（BUG-20260913-01）。
        """
        name = f"{section_name}_free_gacha"
        end_at = monotonic() + (
            float(timeout)
            if timeout is not None
            else float(self.config.get("免费抽按钮等待秒数", 3.0))
        )
        last_text = ""
        saw_text = False
        while monotonic() <= end_at:
            frame = self.capture_frame()
            found, text = self._ocr_keywords_in_frame(
                frame,
                FREE_GACHA_KEYWORDS,
                1,
                name,
            )
            last_text = text
            saw_text = saw_text or bool(text.strip())
            if found:
                self.info_set(f"{section_name} 免费抽 OCR", text or "-")
                return True, text, True
            self.sleep(0.5)
        self.info_set(f"{section_name} 免费抽 OCR", last_text or "-")
        return False, last_text, saw_text

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

    def _open_confirm_dialog_with_retry(self, section_name: str) -> bool:
        """点击免费入口并等待确认弹窗；首击被吞时仅按同帧门禁有界重试。"""
        retries = max(0, int(self.config.get("确认弹窗重试次数", 2)))
        for attempt in range(1, retries + 2):
            if attempt > 1:
                state = self._free_entry_retry_state(section_name)
                if state == FREE_ENTRY_RETRY_DIALOG:
                    self.log_info(f"{section_name}：重试前已检测到确认抽抽乐弹窗，直接确认。")
                    return True
                if state != FREE_ENTRY_RETRY_READY:
                    self.log_info(
                        f"{section_name}：第 {attempt - 1} 次点击后未回到原池免费入口"
                        f"（状态 {state}），停止重试。"
                    )
                    return False
                self.log_info(
                    f"{section_name}：确认弹窗未出现且免费入口仍可见，"
                    f"重试免费入口点击（{attempt - 1}/{retries}）。"
                )
            self._sleep_after_recognition()
            self._click_reference(*FREE_GACHA_BUTTON_REFERENCE_POINT, after_sleep=0.5)
            if self._wait_for_confirm_dialog(section_name):
                return True
        return False

    def _free_entry_retry_state(self, section_name: str) -> str:
        """同一新帧判定能否重试免费入口，四路信号不得跨帧拼接。"""
        frame = self.capture_frame()
        loading = self._match(frame, LOADING_TEMPLATE)
        self.info_set(f"{section_name}_retry_loading", f"{loading.score:.3f}")
        if self._passes(loading, LOADING_TEMPLATE):
            return FREE_ENTRY_RETRY_LOADING
        text = self._ocr_text(frame, name=f"{section_name}_retry")
        page_hits = self._keyword_match_count(text, GACHA_PAGE_KEYWORDS)
        pool_hits = self._keyword_match_count(text, (section_name,))
        free_hits = self._keyword_match_count(text, FREE_GACHA_KEYWORDS)
        dialog_hits = self._keyword_match_count(text, CONFIRM_DIALOG_KEYWORDS)
        self.info_set(
            f"{section_name}_retry 关键字",
            f"页面{page_hits} 池{pool_hits} 免费{free_hits} 弹窗{dialog_hits}",
        )
        if dialog_hits >= int(self.config.get("确认弹窗关键词最低命中数", 2)):
            return FREE_ENTRY_RETRY_DIALOG
        if (
            page_hits >= int(self.config.get("抽卡页面关键词最低命中数", 3))
            and pool_hits >= 1
            and free_hits >= 1
        ):
            return FREE_ENTRY_RETRY_READY
        return FREE_ENTRY_RETRY_CHANGED

    def _wait_for_equipment_pool_page(self, name: str) -> bool:
        found, text = self._wait_for_ocr_keywords(
            EQUIPMENT_POOL_KEYWORDS,
            timeout=float(self.config.get("抽卡页面等待秒数", 12.0)),
            minimum_matches=1,
            name=f"{name}_equipment_pool",
        )
        self.info_set(f"{name} OCR", text or "-")
        if found:
            self.log_info(f"{name}：已确认装备抽抽乐页面。")
            return True
        self.log_info(f"{name}：未确认装备抽抽乐页面，可能仍停留在服装池。")
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
            *RESULT_PAGE_CLOSE_REFERENCE_POINT,
            after_sleep=max(0.0, float(self.config.get("结果页返回前等待秒数", 1.0))),
        )
        self._click_reference(*BACK_BUTTON_REFERENCE_POINT, after_sleep=0.0)
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
            self._click_reference(*SKIP_BUTTON_REFERENCE_POINT, after_sleep=0.0)
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

    def _home_confirmation_ok(self, frame, name: str) -> bool:
        confirmed, _left_hits, _p95_brightness, _text = (
            self._home_confirmation_signals(frame, f"{name} 抽抽乐")
        )
        return confirmed

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

    @staticmethod
    def _keyword_match_count(text: str, keywords: list[str]) -> int:
        return keyword_match_count(text, keywords, fuzzy_ratio=KEYWORD_MATCH_RATIO)

GACHA_PAGE_KEYWORDS = [
    "服装抽抽乐",
    "服装",
    "装备",
    "本抽抽乐的Pickup对象及日程可能会在后续有所变动",
    "角色和装备在未来可能会通过其他方式重新贩售或发放",
]

FREE_GACHA_KEYWORDS = ["所有免费抽抽乐"]
EQUIPMENT_POOL_KEYWORDS = ["装备抽抽乐"]
CONFIRM_DIALOG_KEYWORDS = ["确认抽抽乐", "是否全部进行"]
BACK_PAGE_KEYWORDS = ["抽抽乐券", "可免费抽1次的抽抽乐券", "查看获取途径"]
