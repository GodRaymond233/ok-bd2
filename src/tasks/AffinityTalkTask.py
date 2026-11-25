"""Run mouse-driven affinity conversations for a selected costume."""

import json
import re
from pathlib import Path
from time import monotonic

from src.icons import TaskIcon
from src.tasks import run_history
from src.tasks.HomeRewardTasks import HomePageTask
from src.tasks.mission_rewards import reward_popup
from src.utils.ocr_utils import normalize_ocr_text
from src.utils.vision_models import TemplateSpec

BANK_PATH = Path(".local-dev/upstream/YES-BD2/src/tasks/affinity/data/counseling.json")
REMAINING = re.compile(r"今日剩[余餘][谈談]心次[数數](?:1)?([0-3])")
RECORD = re.compile(r"[谈談]心[记記][录錄](\d{1,2})[/／](15)")
DAILY = re.compile(r"(?<![0-9])([01])[/／]1(?![0-9])")
HEADINGS = ("亲密度", "親密度")
LEVEL = re.compile(r"lv(20|1[0-9]|[1-9])")
PAGE_LIMIT = "服装列表扫描页数上限"
DETAIL_BACK = TemplateSpec(
    "亲密度详情返回", "image/green/BackButGe.png", .90,
    min_pixel_score=.85, min_zncc_score=.80, relative_roi=(0, 0, .15, .12),
)


def _normalize(text):
    return normalize_ocr_text(text, alnum_only=True)


class AffinityTalkTask(HomePageTask):
    waits_for_login = True

    title = "亲密度谈心"
    description_text = "使用题库为指定或自动选中的服装谈心。"
    title_labels = HEADINGS
    task_icon = TaskIcon.PEOPLE
    status_keys = ["状态", "谈心服装", "今日剩余谈心次数", "Log", "Warning", "Error"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_config.update({
            "谈心服装": "",
            "谈心题库路径": "",
            "补齐Lv.20谈心记录": False,
            "自动选择服装": False,
            PAGE_LIMIT: 1,
        })
        self.config_type[PAGE_LIMIT] = {"min": 1, "max": 10, "step": 1}

    def _home_entry(self, frame, labels):
        points = [(x, y) for text, x, y, _ in labels
                  if text in HEADINGS and .12 < x < .24 and .22 < y < .34]
        return points[0] if len(points) == 1 else None

    @staticmethod
    def _left_today(labels):
        values = [int(match[1]) for text, x, y, _ in labels if x > .70 and y < .10
                  and (match := REMAINING.fullmatch(text))]
        return values[0] if len(values) == 1 else None

    def _read_bank(self):
        path = Path(self.config.get("谈心题库路径") or BANK_PATH)
        try:
            entries = json.loads(path.read_text(encoding="utf-8-sig"))["costumes"]
            return entries if isinstance(entries, list) and all(
                isinstance(entry, dict) for entry in entries
            ) else []
        except (OSError, ValueError, TypeError, KeyError):
            return []

    def _questions(self, costume, bank=None):
        try:
            entries = [
                entry for entry in (self._read_bank() if bank is None else bank) if costume in {
                    _normalize(entry.get("costume_zh_cn", "")),
                    _normalize(entry.get("costume_zh_tw", "")),
                }
            ]
            if len(entries) != 1:
                return None
            questions = []
            entry = entries[0]
            for question in entry["questions"]:
                answers = [
                    (frozenset(_normalize(value) for value in answer["text"].values() if value),
                     int(answer["exp"]))
                    for answer in question["answers"]
                ]
                if len(answers) == 2 and answers[0][1] != answers[1][1]:
                    ranked = sorted(answers, key=lambda answer: answer[1], reverse=True)
                    prompts = frozenset(_normalize(text) for text in question["prompt"].values()
                                        if text)
                    questions.append((ranked[0][0], ranked[1][0], prompts))
            return str(entry["id"]), tuple(questions)
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            return None

    @staticmethod
    def _ranked_card(labels, costume):
        cards = [(x, y) for text, x, y, _ in labels
                 if text == costume and .09 < x < .89 and .22 < y < .73]
        verified = []
        for x, y in cards:
            levels = [int(match[1]) for text, lx, ly, _ in labels
                      if abs(lx - x) < .035 and .016 < ly - y < .070
                      and (match := LEVEL.match(text))]
            unowned = any(text in {"未拥有", "未擁有"} and abs(lx - x) < .045
                          and abs(ly - y) < .09 for text, lx, ly, _ in labels)
            if len(levels) == 1 and not unowned:
                verified.append(((x, y), levels[0]))
        return verified[0] if len(verified) == 1 else None

    def _detail(self, labels, costume):
        if not self._on_page(labels) or not any(
            text == costume and .47 < x < .67 and .105 < y < .167 for text, x, y, _ in labels
        ):
            return None
        levels = [int(match[1]) for text, x, y, _ in labels
                  if .47 < x < .65 and .15 < y < .22
                  and (match := LEVEL.match(text))]
        records = [int(match[1]) for _, x, y, box in labels
                   if box is not None and .47 < x < .73 and .20 < y < .30
                   and (match := RECORD.search(re.sub(r"\s+", "", box.name)))]
        if len(levels) != 1 or len(records) != 1 or records[0] > 15:
            return None
        locked = any(("潜力释放" in text or "解锁下一级" in text) and .45 < x < .95 and y < .35
                     for text, x, y, _ in labels)
        daily = [int(match[1]) for _, x, y, box in labels
                 if box is not None and .74 < x < .845 and .87 < y < .96
                 and (match := DAILY.search(re.sub(r"\s+", "", box.name)))]
        return levels[0], records[0], daily[0] if len(daily) == 1 and not locked else None

    def _detail_page(self, frame, labels, costume):
        if self._detail(labels, costume) is not None:
            return frame, labels
        for _, cards in self._costume_pages((frame, labels)):
            card = cards.get(costume)
            if card is not None:
                break
        else:
            return None
        self.operate_click(*card[0], after_sleep=.7)
        deadline = monotonic() + 7
        while monotonic() < deadline:
            frame, labels = self._read()
            if self._detail(labels, costume) is not None:
                return frame, labels
            self.sleep(.4)
        return None

    @staticmethod
    def _answer(labels, questions):
        choices = [(text, x, y) for text, x, y, _ in labels
                   if .35 < x < .66 and .22 < y < .78]
        candidates = []
        has_choice = False
        for best, other, prompts in questions:
            targets = [(x, y) for text, x, y in choices if text in best]
            alternatives = [(x, y) for text, x, y in choices if text in other]
            has_choice |= bool(targets or alternatives)
            if len(targets) != 1 or len(alternatives) != 1:
                continue
            x, y = targets[0]
            ox, oy = alternatives[0]
            if abs(x - ox) < .09 and .03 < abs(y - oy) < .25:
                prompt = any(text in prompts for text, _, _, _ in labels)
                candidates.append(((x, y), prompt))
        distinct = {point for point, prompt in candidates if prompt}
        if not distinct:
            distinct = {point for point, _ in candidates}
        return (next(iter(distinct)) if len(distinct) == 1 else None), has_choice

    def _confirm_talk(self, costume, character, questions, initial_detail, initial_left):
        deadline = monotonic() + 55
        level, before, _ = initial_detail
        result_before = None
        answered = False
        dismissed = False
        confirmed_dialog = False
        dialog_before = None
        answer_before = None
        advances = 0
        while monotonic() < deadline:
            frame, labels = self._read()
            boxes = [box for _, _, _, box in labels if box is not None]
            popup = reward_popup(frame, boxes)
            if popup is not None:
                if answered and not dismissed:
                    self.operate_click(*popup, after_sleep=.6)
                    dismissed = True
                result_before = None
                self.sleep(.35)
                continue
            detail = self._detail(labels, costume)
            modal = [(text, x, y) for text, x, y, _ in labels
                     if .3 < x < .70 and .30 < y < .68]
            if any("进行谈心" in text or "進行談心" in text for text, _, _ in modal):
                result_before = None
                points = [(x, y) for text, x, y in modal
                          if text in {"确认", "确认开始", "確認"} and .49 < x and .50 < y]
                if len(points) != 1 or not any(
                    costume in text or (character and character in text)
                    for text, _, _ in modal
                ):
                    self._fail("未确认谈心弹窗对象")
                    return None
                point = points[0]
                if not confirmed_dialog and dialog_before is not None and (
                    max(abs(a - b) for a, b in zip(point, dialog_before)) <= .012
                ):
                    self.operate_click(*point, after_sleep=.75)
                    confirmed_dialog = True
                dialog_before = point
                answer_before = None
                self.sleep(.35)
                continue
            dialog_before = None
            left_today = self._left_today(labels)
            consumed_daily = (detail and level < 20 and detail[1] == before and detail[2] == 0
                              and left_today == initial_left - 1)
            record_added = detail and detail[1] == before + 1 and (level < 20 or detail[0] == 20)
            changed = record_added or consumed_daily
            result = (detail, left_today) if answered and changed else None
            if result is not None and result == result_before:
                return labels, detail, left_today
            result_before = result
            point, has_choice = self._answer(labels, questions) if detail is None else (None, False)
            if point is not None and answer_before is not None and (
                not answered and max(abs(a - b) for a, b in zip(point, answer_before)) <= .012
            ):
                self.operate_click(*point, after_sleep=.8)
                answered = True
            elif not has_choice and detail is None and advances < 12:
                controls = [(x, y) for text, x, y, _ in labels if y > .78 and .24 < x < .76
                            and text in {"点击继续", "點擊繼續", "下一句"}]
                if len(controls) == 1:
                    self.operate_click(*controls[0], after_sleep=.7)
                    advances += 1
            answer_before = point
            self.sleep(.45)
        self._fail("未确认谈心选项或结果")
        return None

    def _auto_list(self):
        frame, labels = self._read()
        if self._on_list(labels):
            return frame, labels
        if not self._on_page(labels):
            return None
        back = self._match(frame, DETAIL_BACK)
        if not self._passes(back, DETAIL_BACK):
            return None
        if not any(box is not None and .47 < x < .73 and .20 < y < .30
                   and RECORD.search(re.sub(r"\s+", "", box.name)) for _, x, y, box in labels):
            return None
        height, width = frame.shape[:2]
        self.operate_click(back.center[0] / width, back.center[1] / height, after_sleep=.7)
        deadline = monotonic() + 7
        while monotonic() < deadline:
            frame, labels = self._read()
            if self._on_list(labels):
                return frame, labels
            self.sleep(.4)
        return None

    def _on_list(self, labels):
        return self._on_page(labels) and any(
            .09 < x < .89 and .24 < y < .80 and LEVEL.match(text)
            for text, x, y, _ in labels
        )

    def _costume_pages(self, page):
        limit = min(10, max(1, int(self.config.get(PAGE_LIMIT, 1))))
        seen = set()
        for index in range(limit):
            _, labels = page
            if not self._on_list(labels):
                return
            cards = {
                name: card for name in dict.fromkeys(text for text, _, _, _ in labels)
                if (card := self._ranked_card(labels, name)) is not None
            }
            if index and not (cards.keys() - seen):
                return
            yield page, cards
            if not cards or index + 1 == limit:
                return
            seen.update(cards)
            point, _ = next(iter(cards.values()))
            self.scroll_client(point, -2, after_sleep=.85)
            page = self._read()

    def _run_automatic(self):
        bank = self._read_bank()
        if not bank:
            return self._fail("请核对谈心题库路径")
        if self._start() is None:
            return self._fail("未确认亲密度页面")
        day = str(run_history.day_start_ts())
        fill_records = bool(self.config.get("补齐Lv.20谈心记录", False))
        visited = set()
        completed = 0
        while True:
            page = self._auto_list()
            if page is None:
                return self._fail("未确认返回服装列表")
            candidates = []
            for page, cards in self._costume_pages(page):
                left_today = self._left_today(page[1])
                if left_today is None:
                    return self._fail("未确认今日剩余谈心次数")
                if left_today == 0 and not fill_records:
                    break
                for name, (point, level) in cards.items():
                    if not (fill_records if level == 20 else left_today > 0):
                        continue
                    questions = self._questions(name, bank)
                    if questions is None or not questions[1] or questions[0] in visited:
                        continue
                    step = f"谈心:{day}:{questions[0]}"
                    prior = run_history.read_stage(self, step)
                    if prior.get("state") == "pending" or (
                        prior.get("state") == "done"
                        and (level < 20 or prior["evidence"].get("records") == 15)
                    ):
                        continue
                    candidates.append((level, point, name, questions))
                if candidates:
                    break
            if not candidates:
                if not completed:
                    self.info_set("状态", "已检查的列表页没有可确认的谈心服装。")
                return self._finish()
            _, _, costume, questions = min(
                candidates, key=lambda candidate: (-candidate[0], candidate[1][1], candidate[1][0]),
            )
            visited.add(questions[0])
            result = self._run_costume(costume, questions, day, page=page)
            if result is False:
                return False
            completed += result is True

    def run(self):
        if self.config.get("自动选择服装", False):
            return self._run_automatic()
        costume = _normalize(self.config.get("谈心服装", ""))
        if not costume:
            return self._fail("请填写谈心服装")
        bank = self._questions(costume)
        if bank is None or not bank[1]:
            return self._fail("请核对谈心题库路径和服装名称")
        result = self._run_costume(costume, bank, str(run_history.day_start_ts()))
        if result is None:
            self.info_set("状态", "该服装暂无可用的谈心次数。")
            return self._finish()
        return result

    def _run_costume(self, costume, bank, day, *, page=None):
        automatic = page is not None
        costume_id, questions = bank
        fill_records = bool(self.config.get("补齐Lv.20谈心记录", False))
        step = f"谈心:{day}:{costume_id}"
        prior = run_history.read_stage(self, step).get("state")
        if prior == "done" and not fill_records:
            self.info_set("状态", "该服装今日谈心已确认。")
            return True
        if prior == "pending":
            return self._fail("该服装今日谈心提交待核对")
        if page is None:
            page = self._start()
        if page is None:
            return self._fail("未确认亲密度页面")
        page = self._detail_page(*page, costume)
        if page is None:
            return self._fail("未确认服装详情")
        _, labels = page
        detail = self._detail(labels, costume)
        left_today = self._left_today(labels)
        if detail is None or left_today is None:
            return self._fail("未确认谈心次数")
        level, before, daily = detail
        fill_records = fill_records and level == 20
        if fill_records and before == 15:
            return None if automatic else self._finish()
        if prior == "done" and not fill_records:
            return None if automatic else self._finish()
        if before >= 15 or (level < 20 and (daily == 0 or left_today == 0)):
            return None
        if level < 20 and daily != 1:
            return self._fail("未确认该服装的谈心次数")
        names = [text for text, x, y, _ in labels
                 if .47 < x < .65 and .085 < y < .119 and text != costume]
        character = names[0] if len(names) == 1 else ""
        self.info_set("谈心服装", costume)
        for _ in range(15 - before if fill_records else 1):
            records = detail[1]
            self.info_set("今日剩余谈心次数", left_today)
            talk = [(x, y) for text, x, y, _ in labels if .74 < x < .83 and .88 < y < .96
                    and text in {"谈心", "談心", "谈心11", "談心11"}]
            if len(talk) != 1:
                return self._fail("未确认谈心按钮")
            if day != str(run_history.day_start_ts()):
                return self._fail("日常已刷新，请重新核对")
            run_history.save_stage(self, step, "pending", scope=day, evidence={"records": records})
            self.operate_click(*talk[0], after_sleep=.75)
            result = self._confirm_talk(costume, character, questions, detail, left_today)
            if result is None:
                return False
            labels, detail, left_today = result
            run_history.save_stage(self, step, "done", scope=day,
                                   evidence={"records": detail[1], "remaining": left_today})
        return True if automatic else self._finish()
