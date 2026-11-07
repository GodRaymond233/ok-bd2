import re
from dataclasses import dataclass
from time import monotonic

from ok.task.exceptions import FinishedException, TaskDisabledException
from qfluentwidgets import FluentIcon

from src.diagnostics.runtime import capture_failure_frame
from src.tasks.BaseBD2Task import BaseBD2Task
from src.tasks.map_trade.models import TemplateSpec
from src.tasks.task_vision_mixin import (
    LOADING_TEMPLATE,
    REFERENCE_HEIGHT,
    REFERENCE_WIDTH,
    TaskVisionMixin,
)
from src.utils.ocr_utils import keyword_match_count, normalize_ocr_text

KEYWORD_MATCH_RATIO = 0.9

# 免费入口与耗尽复核共用的单帧状态。
FREE_ENTRY_RETRY_READY = "ready"
FREE_ENTRY_RETRY_DIALOG = "dialog"
FREE_ENTRY_RETRY_LOADING = "loading"
FREE_ENTRY_RETRY_UNKNOWN = "unknown"
FREE_ENTRY_RETRY_BLOCKED = "blocked"
FREE_ENTRY_RETRY_NO_FREE = "no_free"

# 以下点击点均为 1920×1080 参考（_click_reference 按当前客户区归一）。
GACHA_ENTRY_REFERENCE_POINT = (162, 986)
EQUIPMENT_TAB_REFERENCE_POINT = (175, 432)
BACK_BUTTON_REFERENCE_POINT = (105, 51)
RESULT_PAGE_CLOSE_REFERENCE_POINT = (1420, 326)
SKIP_BUTTON_REFERENCE_POINT = (1770, 60)
RESULT_RETURN_ATTEMPTS = 3
RESULT_RETURN_RETRY_INTERVAL = 2.0
RESULT_BACK_TEMPLATE = TemplateSpec(
    "抽抽乐结果返回", "back_return_bar_arrow.png", 0.88,
    relative_roi=(0.03, 0.0, 0.13, 0.12),
    # Keep the arrow's surrounding contrast; a white-only mask also matches bright backgrounds.
    crop=(0.72, 0.16, 0.88, 0.84), scale_ratios=(0.95, 1.0, 1.05),
    min_zncc_score=0.88, minimum_safe_threshold=0.88,
)


@dataclass(frozen=True)
class FreeDrawConfirmation:
    count: int
    point: tuple[float, float]


@dataclass(frozen=True)
class FreePoolObservation:
    state: str
    reason: str
    text: str
    point: tuple[float, float] | None = None
    pool_confirmed: bool = False
    free_hint: bool = False


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
                "确认提交重试次数": 2,
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
                "确认提交重试次数": "确认弹窗点击提交后弹窗仍未关闭时的额外重试次数。",
                "结果跳过连续点击秒数": "抽卡结果页连续点击跳过按钮的最长时间。",
                "结果页 OCR 等待秒数": "连续点击结束后，持续识别抽抽乐券详情页的最长时间。",
                "结果页 OCR 间隔秒数": "等待抽抽乐券详情页时的 OCR 识别间隔。",
                "结果页关闭前等待秒数": "识别到抽抽乐券结果页后，点击右上角关闭前等待的时间。",
                "结果页返回前等待秒数": "点击右上角关闭后，再点击左上角返回前等待的时间。",
                "结果页关键词最低命中数": "结果页返回确认使用 3 个固定 OCR 关键词。",
            }
        )

    def run(self):
        self._gacha_evidence = None
        if not bool(self.config.get("启用", True)):
            self.info_set("状态", "白嫖抽抽乐已禁用。")
            self.log_info("白嫖抽抽乐已禁用。")
            return True

        self.info_set("状态", "启动白嫖抽抽乐。")
        for section in ("服装抽抽乐", "装备抽抽乐"):
            self.info_set(f"{section} 结果", "待检查")
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

        # 通用抽卡页关键词在装备页同样能命中，无法证明首段处于服装池；
        # 没有服装页签参考点无法安全切换，校验失败时只能停止不点击
        # （BUG-20260913-01 合并前 review）。
        if not self._wait_for_clothing_pool_page("确认服装池"):
            return self._fail_run("确认服装池", "服装抽抽乐")

        clothing_ok = self._run_free_section(
            "服装抽抽乐",
            verify_finished=True,
        )
        if not clothing_ok and not self._wait_for_clothing_pool_page("服装未完成后确认卡池"):
            return self._fail_run("服装抽抽乐")

        self._sleep_after_recognition()
        self._click_reference(*EQUIPMENT_TAB_REFERENCE_POINT, after_sleep=0.8)
        # 换池必须验证目标池标题：通用抽卡页关键词在服装页同样能命中，
        # 无法证明实际换池（BUG-20260913-01）。
        if not self._wait_for_equipment_pool_page("切换装备抽卡"):
            return self._fail_run("切换装备抽卡", "装备抽抽乐")

        equipment_ok = self._run_free_section(
            "装备抽抽乐",
            verify_finished=True,
        )
        if not equipment_ok and not self._wait_for_equipment_pool_page("装备未完成后确认卡池"):
            return self._fail_run("装备抽抽乐")

        self._sleep_after_recognition()
        self._click_reference(*BACK_BUTTON_REFERENCE_POINT, after_sleep=1.0)
        if not self._wait_loading_or_home_confirmation("抽抽乐返回主页"):
            return self._fail_run("返回主页")

        unfinished = [name for name, ok in (("服装抽抽乐", clothing_ok),
                                            ("装备抽抽乐", equipment_ok)) if not ok]
        if unfinished:
            self.info_set("状态", f"白嫖抽抽乐未完成：{'、'.join(unfinished)}。已返回主页。")
            return False
        self.info_set("状态", "白嫖抽抽乐完成。")
        self.log_completion("白嫖抽抽乐：流程完成。")
        return True

    def _fail_run(self, stage: str, section_name: str | None = None) -> bool:
        # 失败路径必须把"失败"写进状态，否则 run_history 会把本次运行
        # 记成成功，调度账本当天不再补跑（BUG-20260912-01）。
        self.info_set("状态", f"白嫖抽抽乐{stage}失败。")
        if section_name is not None:
            self.info_set(f"{section_name} 结果", f"未完成：{stage}")
            evidence = getattr(self, "_gacha_evidence", None)
            if evidence is not None and evidence[1]["pool"] == section_name:
                capture_failure_frame(self, stage, evidence[0], lossless=True,
                                      recognition=evidence[1])
            else:
                capture_failure_frame(self, stage)
        return False

    def _run_free_section(
        self,
        section_name: str,
        verify_finished: bool,
    ) -> bool:
        self.info_set(f"{section_name} 结果", "检查中")
        available, _text, judged = self._wait_for_free_gacha(section_name)
        self.info_set(
            f"{section_name} 免费抽",
            "可领取" if available else ("无" if judged else "无法判断"),
        )
        if not available:
            if judged:
                self.info_set(f"{section_name} 结果", "本轮无免费入口，已跳过")
                self.log_info(f"{section_name}：未检测到所有免费抽抽乐，跳过。")
                return True
            self.log_info(f"{section_name}：复查后仍无法判断当前免费入口。")
            return self._fail_run(f"{section_name}免费入口判断", section_name)

        if not self._open_confirm_dialog_with_retry(section_name):
            return self._fail_run(f"{section_name}免费入口复核或开框", section_name)
        self._gacha_evidence = None
        if not self._confirm_dialog_submission(section_name):
            return self._fail_run(f"{section_name}确认提交", section_name)

        if not self._handle_result_until_back(section_name):
            return self._fail_run(f"{section_name}结果处理", section_name)

        if verify_finished:
            still_available, _recheck_text, recheck_judged = self._wait_for_free_gacha(
                section_name,
            )
            if not recheck_judged:
                self.log_info(f"{section_name}：领取复核 OCR 无法判断是否仍有免费抽。")
                return self._fail_run(f"{section_name}领取复核", section_name)
            if still_available:
                self.log_info(f"{section_name}：返回后仍检测到所有免费抽抽乐。")
                return self._fail_run(f"{section_name}领取复核", section_name)
            self.log_info(f"{section_name}：免费抽已结束。")

        self.info_set(f"{section_name} 结果", "本轮免费抽已领取")
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
        """Return availability, last text, and whether the pool's free state is known."""
        name = f"{section_name}_free_gacha"
        window = max(0.0, (
            float(timeout)
            if timeout is not None
            else float(self.config.get("免费抽按钮等待秒数", 3.0))
        ))
        settle_at = monotonic() + window
        end_at = settle_at + window
        last_text = ""
        no_free_frames = 0
        while monotonic() < end_at:
            observation = self._observe_free_pool(section_name, name)
            last_text = observation.text
            if observation.state == FREE_ENTRY_RETRY_READY:
                self.info_set(f"{section_name} 免费抽 OCR", last_text)
                return True, last_text, True
            if observation.state in (FREE_ENTRY_RETRY_BLOCKED, FREE_ENTRY_RETRY_DIALOG):
                break
            # Observe the full settling window, then accept two consecutive current frames.
            # A later unrelated OCR miss must not undo an already confirmed exhaustion.
            no_free_frames = (no_free_frames + 1
                              if monotonic() >= settle_at
                              and observation.state == FREE_ENTRY_RETRY_NO_FREE else 0)
            if no_free_frames >= 2:
                self.info_set(f"{section_name} 免费抽 OCR", last_text)
                return False, last_text, True
            self.sleep(min(0.5, max(0.0, end_at - monotonic())))
        self.info_set(f"{section_name} 免费抽 OCR", last_text or "-")
        return False, last_text, False

    @staticmethod
    def _pool_observation(frame, boxes, section_name: str):
        """Classify one complete OCR view; never join controls from different views."""
        text = " ".join(box.name for box in boxes)
        labels = [normalize_ocr_text(box.name, alnum_only=True) for box in boxes]
        other_pool = "装备抽抽乐" if section_name == "服装抽抽乐" else "服装抽抽乐"
        if any(label in (other_pool, other_pool + "抽抽乐记录") for label in labels):
            return FreePoolObservation(FREE_ENTRY_RETRY_BLOCKED, "出现另一池标题", text)
        if ("确认抽抽乐" in labels and any("是否全部进行" in label for label in labels)):
            return FreePoolObservation(FREE_ENTRY_RETRY_DIALOG, "确认框，待免费核验", text)
        if any(word in label for label in labels
               for word in ("确认", "取消", "抽抽乐券", "查看获取途径")):
            return FreePoolObservation(FREE_ENTRY_RETRY_BLOCKED, "存在覆盖弹窗", text)
        free_hint = any(FREE_GACHA_KEYWORDS[0] in label or "免费1次" in label
                        for label in labels)
        # OCR may combine the adjacent pool title and record label into one header box.
        header = section_name + "抽抽乐记录"
        controls = [[box for box, label in zip(boxes, labels) if label in accepted]
                    for accepted in ((section_name, header), ("抽抽乐记录", header), ("抽1次",))]
        if any(len(group) != 1 for group in controls):
            reason = "、".join(f"{name}数量={len(group)}" for name, group
                              in zip(("池标题", "记录标签", "单抽按钮"), controls)
                              if len(group) != 1)
            return FreePoolObservation(FREE_ENTRY_RETRY_UNKNOWN, reason, text, free_hint=free_hint)
        title, record, single = (group[0] for group in controls)
        if not (all(box.width > 0 and box.height > 0 for box in (title, record, single))
                and (title is record or title.x + title.width <= record.x)
                and abs(title.y - record.y) <= max(title.height, record.height)
                and max(title.y + title.height, record.y + record.height) < single.y):
            return FreePoolObservation(FREE_ENTRY_RETRY_UNKNOWN, "标题栏/单抽布局未通过",
                                       text, free_hint=free_hint)
        free_hint = free_hint or any(
            "免费" in label and box.y + box.height / 2 >= single.y - single.height * 2
            for box, label in zip(boxes, labels))
        entries = [box for box in boxes
                   if normalize_ocr_text(box.name, alnum_only=True) == FREE_GACHA_KEYWORDS[0]]
        if len(entries) != 1:
            state = FREE_ENTRY_RETRY_UNKNOWN if free_hint else FREE_ENTRY_RETRY_NO_FREE
            return FreePoolObservation(state, f"免费入口数量={len(entries)}，免费信号={free_hint}",
                                       text, pool_confirmed=True, free_hint=free_hint)
        entry = entries[0]
        if not (entry.width > 0 and entry.height > 0 and entry.x + entry.width < single.x
                and abs(entry.y - single.y) <= max(entry.height, single.height) * 3):
            return FreePoolObservation(FREE_ENTRY_RETRY_UNKNOWN, "免费入口与单抽行布局未通过",
                                       text, pool_confirmed=True, free_hint=True)
        point = ((entry.x + entry.width / 2) / frame.shape[1],
                 (entry.y + entry.height / 2) / frame.shape[0])
        if not all(0 < value < 1 for value in point):
            return FreePoolObservation(FREE_ENTRY_RETRY_UNKNOWN, "免费入口坐标越界",
                                       text, free_hint=True)
        return FreePoolObservation(FREE_ENTRY_RETRY_READY, "同池免费入口确认", text,
                                   point, pool_confirmed=True, free_hint=True)

    def _observe_free_pool(self, section_name: str, name: str):
        frame = self.capture_frame()
        loading = self._match(frame, LOADING_TEMPLATE)
        evidence = {"pool": section_name, "loading": str(loading)}
        if self._passes(loading, LOADING_TEMPLATE):
            observation = FreePoolObservation(FREE_ENTRY_RETRY_LOADING, "加载中", "")
        else:
            boxes = self._gacha_ocr_boxes(frame, name)
            observation = self._pool_observation(frame, boxes, section_name)
            evidence["720"] = self._pool_box_evidence(boxes)
            evidence["720判断"] = observation.reason
            labels = [normalize_ocr_text(box.name, alnum_only=True) for box in boxes]
            anchors = (any(label in (section_name, "抽抽乐记录", section_name + "抽抽乐记录")
                           for label in labels)
                       and any(label in ("抽1次", FREE_GACHA_KEYWORDS[0]) for label in labels))
            if anchors and observation.state in (
                    FREE_ENTRY_RETRY_UNKNOWN, FREE_ENTRY_RETRY_NO_FREE):
                # Re-read this same frame at a second scale; replace the entire view.
                # A free hint in either view forbids declaring exhaustion.
                boxes = self._gacha_ocr_boxes(frame, name + "_1080", target_height=1080)
                refined = self._pool_observation(frame, boxes, section_name)
                evidence["1080"] = self._pool_box_evidence(boxes)
                evidence["1080判断"] = refined.reason
                if refined.state == FREE_ENTRY_RETRY_NO_FREE and observation.free_hint:
                    refined = FreePoolObservation(FREE_ENTRY_RETRY_UNKNOWN,
                                                  "两次识别存在免费信号冲突", refined.text,
                                                  pool_confirmed=True, free_hint=True)
                observation = refined
        evidence.update(state=observation.state, reason=observation.reason)
        self._gacha_evidence = (frame, evidence)
        self.info_set(f"{section_name} 识别复核", f"{observation.state}: {observation.reason}")
        return observation

    @staticmethod
    def _pool_box_evidence(boxes):
        return "; ".join(
            f"{box.name}@({box.x},{box.y},{box.width},{box.height})" for box in boxes
            if any(word in normalize_ocr_text(box.name)
                   for word in ("抽抽乐", "抽1次", "免费", "确认", "取消")))[:1000]

    def _free_confirmation_in_frame(self, frame, section_name: str):
        if self._passes(self._match(frame, LOADING_TEMPLATE), LOADING_TEMPLATE):
            return None
        boxes = self._gacha_ocr_boxes(frame, f"{section_name}免费确认复核")
        controls = [[box for box in boxes if normalize_ocr_text(box.name) == word]
                    for word in ("确认抽抽乐", "取消", "确认")]
        if any(len(group) != 1 for group in controls):
            return None
        title, cancel, confirm = (group[0] for group in controls)
        if not (cancel.x < confirm.x and title.y < min(cancel.y, confirm.y)
                and abs(cancel.y - confirm.y) <= max(cancel.height, confirm.height)):
            return None
        left = min(title.x, cancel.x)
        right = max(title.x + title.width, confirm.x + confirm.width)
        bottom = max(cancel.y + cancel.height, confirm.y + confirm.height)
        body = "".join(normalize_ocr_text(box.name) for box in boxes
                       if left <= box.x + box.width / 2 <= right
                       and title.y <= box.y + box.height / 2 <= bottom)
        match = re.search(r"目前有([1-9]\d*)次免费(服装|装备)抽抽乐可以抽取", body)
        counts = re.findall(r"免费([1-9]\d*)次", body)
        if (match is None or match[2] + "抽抽乐" != section_name
                or counts != [match[1]] or "是否全部进行" not in body
                or re.search(r"付费|消耗|钻石|购买|抽抽乐券", body)):
            self.info_set(f"{section_name}免费确认复核", "池名、免费次数或费用未通过")
            return None
        point = ((confirm.x + confirm.width / 2) / frame.shape[1],
                 (confirm.y + confirm.height / 2) / frame.shape[0])
        if not all(0 < value < 1 for value in point):
            return None
        return FreeDrawConfirmation(int(match[1]), point)

    def _wait_for_free_confirmation(self, section_name: str, count=None):
        end_at = monotonic() + float(self.config.get("确认弹窗等待秒数", 8.0))
        previous = None
        while monotonic() <= end_at:
            current = self._free_confirmation_in_frame(self.capture_frame(), section_name)
            if current is not None and (count is None or current.count == count):
                if previous is not None and current.count == previous.count:
                    self.info_set(
                        f"{section_name}免费确认复核", f"同池免费{current.count}次，连续2帧"
                    )
                    return current
                previous = current
            else:
                previous = None
            self.sleep(0.25)
        return None

    def _wait_for_confirm_dialog(self, section_name: str, timeout=None) -> bool:
        found, text = self._wait_for_ocr_keywords(
            CONFIRM_DIALOG_KEYWORDS,
            timeout=(float(self.config.get("确认弹窗等待秒数", 8.0))
                     if timeout is None else timeout),
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
        """Recover uncertain observations within fixed budgets; count only actual clicks."""
        retries = max(0, int(self.config.get("确认弹窗重试次数", 2)))
        window = max(0.0, float(self.config.get("免费抽按钮等待秒数", 3.0)))
        end_at = monotonic() + max(0.0, float(self.config.get("loading 消失等待秒数", 35.0)))
        self._sleep_after_recognition()
        observe_until = min(end_at, monotonic() + window)
        clicks = 0
        while monotonic() < end_at and clicks <= retries:
            state, point = self._free_entry_retry_state(section_name)
            if state == FREE_ENTRY_RETRY_DIALOG:
                self.log_info(f"{section_name}：已检测到确认抽抽乐弹窗，转入免费核验。")
                return True
            if state == FREE_ENTRY_RETRY_BLOCKED:
                self.log_info(f"{section_name}：当前未确认原池免费入口（状态 {state}），停止点击。")
                return False
            if state == FREE_ENTRY_RETRY_LOADING:
                observe_until = min(end_at, monotonic() + window)
            elif monotonic() >= observe_until:
                break
            elif state == FREE_ENTRY_RETRY_READY:
                if monotonic() >= end_at:
                    break
                if clicks:
                    self.log_info(f"{section_name}：免费入口仍可见，重试点击（{clicks}/{retries}）。")
                self._gacha_evidence = None
                self.operate_click(*point, after_sleep=0.5)
                clicks += 1
                remaining = end_at - monotonic()
                if remaining <= 0:
                    break
                if self._wait_for_confirm_dialog(section_name, timeout=min(
                        remaining, max(0.0, float(self.config.get("确认弹窗等待秒数", 8.0))))):
                    return True
                self._sleep_after_recognition()
                observe_until = min(end_at, monotonic() + window)
                continue
            wait_until = end_at if state == FREE_ENTRY_RETRY_LOADING else observe_until
            self.sleep(min(0.25, max(0.0, wait_until - monotonic())))
        self.log_info(f"{section_name}：免费入口复核或开框预算耗尽，实际点击{clicks}次。")
        return False

    def _confirm_dialog_submission(self, section_name: str) -> bool:
        """Each press requires fresh, stable evidence of the same free batch."""
        retries = max(0, int(self.config.get("确认提交重试次数", 2)))
        count = None
        for attempt in range(1, retries + 2):
            self._sleep_after_recognition()
            confirmation = self._wait_for_free_confirmation(section_name, count)
            if confirmation is None:
                self.log_info(f"{section_name}：当前免费确认框未通过，停止提交。")
                return False
            count = confirmation.count
            self.operate_click(*confirmation.point, after_sleep=1.0)
            if not self._confirm_dialog_still_open(section_name):
                # An unknown transition permits result observation, never another submission.
                return True
            self.log_info(
                f"{section_name}：确认弹窗未关闭，重试确认点击（{attempt}/{retries}）。"
            )
        self.log_info(f"{section_name}：多次确认点击后弹窗仍未关闭。")
        return False

    def _confirm_dialog_still_open(self, section_name: str) -> bool:
        """A missing dialog only permits observing results; it never proves completion."""
        frame = self.capture_frame()
        loading = self._match(frame, LOADING_TEMPLATE)
        self.info_set(f"{section_name}_submit_loading", f"{loading.score:.3f}")
        if self._passes(loading, LOADING_TEMPLATE):
            return False
        text = self._ocr_text(frame, name=f"{section_name}_submit")
        dialog_hits = self._keyword_match_count(text, CONFIRM_DIALOG_KEYWORDS)
        self.info_set(f"{section_name}_submit 关键字", f"弹窗{dialog_hits}")
        return dialog_hits >= int(self.config.get("确认弹窗关键词最低命中数", 2))

    def _free_entry_retry_state(self, section_name: str):
        observation = self._observe_free_pool(section_name, f"{section_name}_retry")
        self.info_set(f"{section_name}_retry OCR", observation.text or "-")
        return observation.state, observation.point

    def _wait_for_equipment_pool_page(self, name: str) -> bool:
        return self._wait_for_pool_page("装备抽抽乐", name)

    def _wait_for_clothing_pool_page(self, name: str) -> bool:
        return self._wait_for_pool_page("服装抽抽乐", name)

    def _wait_for_pool_page(self, section_name: str, name: str) -> bool:
        end_at = monotonic() + float(self.config.get("抽卡页面等待秒数", 12.0))
        while monotonic() <= end_at:
            observation = self._observe_free_pool(section_name, name)
            self.info_set(f"{name} OCR", observation.text or "-")
            if observation.pool_confirmed:
                self.log_info(f"{name}：已确认{section_name}页面。")
                return True
            self.sleep(0.5)
        self.log_info(f"{name}：未确认可操作的{section_name}页面。")
        return False

    def _handle_result_until_back(self, section_name: str) -> bool:
        found, text = self._click_skip_until_back_page(section_name)
        self.info_set(f"{section_name} 结果 OCR", text or "-")
        if not found:
            self.log_info(f"{section_name}：券详情尚未确认，继续有界观察结果和原卡池。")
        end_at = monotonic() + max(0.0, float(self.config.get("抽卡页面等待秒数", 12.0)))
        clicks = {"detail": 0, "result": 0}
        previous, hits = None, 0
        ready_at = next_click = 0.0
        while monotonic() < end_at:
            frame = self.capture_frame()
            state, point = self._result_return_state(frame, section_name)
            now = monotonic()
            if now >= end_at:
                break
            if state == "pool":
                self.log_info(f"{section_name}：结果收尾已确认原卡池。")
                return True
            if state == "blocked":
                break
            if state != previous:
                hits = 0
                delay_key = ("结果页关闭前等待秒数" if state == "detail"
                             else "结果页返回前等待秒数")
                ready_at = now + max(0.0, float(self.config.get(delay_key, 1.0)))
            previous, hits = state, hits + 1
            # Retry only on fresh, stable evidence; never back out of a returned pool.
            if (point is not None and hits >= 2 and now >= max(ready_at, next_click)
                    and clicks[state] < RESULT_RETURN_ATTEMPTS):
                self.operate_click(*point, after_sleep=0.0)
                clicks[state] += 1
                action = "关闭券详情" if state == "detail" else "返回卡池"
                self.info_set(f"{section_name} 结果收尾", f"{action} {clicks[state]}/3")
                next_click = monotonic() + RESULT_RETURN_RETRY_INTERVAL
                hits = 0
            self.sleep(min(0.25, max(0.0, end_at - monotonic())))
        self.log_info(f"{section_name}：结果收尾未确认原卡池，关闭详情{clicks['detail']}次、"
                      f"返回{clicks['result']}次。")
        self._save_flow_diagnostic("gacha_result_return_failed")
        return False

    def _result_return_state(self, frame, section_name: str):
        if self._passes(self._match(frame, LOADING_TEMPLATE), LOADING_TEMPLATE):
            return "loading", None
        boxes = self._gacha_ocr_boxes(frame, f"{section_name} 结果收尾")
        text = " ".join(box.name for box in boxes)
        self.info_set(f"{section_name} 结果收尾 OCR", text or "-")
        if self._keyword_match_count(text, BACK_PAGE_KEYWORDS) == len(BACK_PAGE_KEYWORDS):
            return "detail", (RESULT_PAGE_CLOSE_REFERENCE_POINT[0] / REFERENCE_WIDTH,
                              RESULT_PAGE_CLOSE_REFERENCE_POINT[1] / REFERENCE_HEIGHT)
        if self._pool_observation(frame, boxes, section_name).pool_confirmed:
            return "pool", None
        other_pool = "装备抽抽乐" if section_name == "服装抽抽乐" else "服装抽抽乐"
        normalized = normalize_ocr_text(text)
        if other_pool in normalized or "确认抽抽乐" in normalized:
            return "blocked", None
        # The result has balances and a /100 counter, but no pool controls or covering dialog.
        if (any(word in normalized for word in ("抽抽乐", "抽1次", "抽10次", "查看概率",
                                                "兑换", "确认", "取消", "获取途径"))
                or not all(word in normalized for word in ("付费", "免费"))):
            return "unknown", None
        counters = [(box, value) for box in boxes for value in re.findall(
            r"(?<!\d)(\d{1,3})/100(?!\d)", normalize_ocr_text(box.name))]
        height, width = frame.shape[:2]
        if (len(counters) != 1 or int(counters[0][1]) > 100
                or counters[0][0].x + counters[0][0].width >= width / 2
                or counters[0][0].y < height * 0.8):
            return "unknown", None
        back = self._match(frame, RESULT_BACK_TEMPLATE)
        if not self._passes(back, RESULT_BACK_TEMPLATE):
            return "unknown", None
        return "result", ((back.position[0] + back.size[0] / 2) / width,
                          (back.position[1] + back.size[1] / 2) / height)

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
        return " ".join(box.name for box in self._gacha_ocr_boxes(frame, name))

    def _gacha_ocr_boxes(self, frame, name: str, *, target_height=720):
        try:
            boxes = self.ocr(
                frame=frame,
                threshold=float(self.config.get("抽卡 OCR 阈值", 0.2)),
                target_height=target_height,
                log=False,
                name=name,
            )
        except (TaskDisabledException, FinishedException):
            raise
        except Exception as exc:
            self.info_set(f"{name} OCR 错误", str(exc))
            return []

        return [box for box in boxes if getattr(box, "name", "")]

    @staticmethod
    def _keyword_match_count(
        text: str,
        keywords: list[str] | tuple[str, ...],
    ) -> int:
        return keyword_match_count(text, keywords, fuzzy_ratio=KEYWORD_MATCH_RATIO)

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
