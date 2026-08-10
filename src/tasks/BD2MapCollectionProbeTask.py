from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from qfluentwidgets import FluentIcon

from src.tasks.map_trade.audit import CollectionVisualAuditStore
from src.tasks.map_trade.card_status import CardActionState
from src.tasks.map_trade.models import (
    CARD_BY_ID,
    COLLECTABLE_CARDS,
    CardSpec,
    MatchResult,
    NavigationResult,
)
from src.tasks.map_trade.navigator import (
    PROBE_QUICK_SWITCH_SCROLL_STEPS,
    TELEPORT_MAP_BACKWARD_TEMPLATE,
    TELEPORT_MAP_FORWARD_TEMPLATE,
    TELEPORT_MAP_RETURN_RELATIVE_POINT,
    TELEPORT_MAP_TITLE_OCR_RELATIVE_ROI,
    Navigator,
    StoryBadgeDetection,
)
from src.tasks.map_trade.progress import UTC_PLUS_8
from src.tasks.map_trade.vision import Vision, normalize_text
from src.tasks.MapTradeTask import (
    MAP_OCR_THRESHOLD_KEY,
    MAP_VISION_THRESHOLD_KEY,
    MapAutomationTaskBase,
)

ALL_COLLECTION_CARDS_OPTION = "全部17张"
COLLECTION_CARD_OPTIONS = tuple(card.card_id for card in COLLECTABLE_CARDS)
DEFAULT_COLLECTION_START = COLLECTION_CARD_OPTIONS[0]
DEFAULT_COLLECTION_END = COLLECTION_CARD_OPTIONS[-1]
TELEPORT_MAP_FORWARD_CLICK_LIMIT = 7
TELEPORT_MAP_BACKWARD_CLICK_LIMIT = 7
TELEPORT_MAP_FORWARD_CLICK_INTERVAL = 0.2
TELEPORT_MAP_PAGE_SETTLE_SECONDS = 0.5
TELEPORT_MAP_TITLE_RETRY_INTERVAL = 0.2


def _state_label(value: str) -> str:
    return {
        CardActionState.PENDING.value: "待完成",
        CardActionState.COMPLETED.value: "已完成",
        CardActionState.UNKNOWN.value: "未知",
    }.get(str(value), str(value))


def _overall_state(absorb: str, suppress: str) -> str:
    states = (str(absorb), str(suppress))
    if CardActionState.PENDING.value in states:
        return CardActionState.PENDING.value
    if all(value == CardActionState.COMPLETED.value for value in states):
        return CardActionState.COMPLETED.value
    return CardActionState.UNKNOWN.value


def _evidence_payload(values) -> list[dict]:
    return [
        {
            "match_score": evidence.result.score,
            "pixel_score": evidence.result.pixel_score,
            "zncc_score": evidence.result.zncc_score,
            "center": list(evidence.result.center),
            "green_ratio": evidence.green_ratio,
            "red_ratio": evidence.red_ratio,
            "neutral_ratio": evidence.neutral_ratio,
        }
        for evidence in values
    ]


def _completion_observation(
    card: CardSpec,
    badge: StoryBadgeDetection,
    completion,
) -> dict:
    absorb_state = completion.absorb.state.value
    suppress_state = completion.suppress.state.value
    return {
        "card_id": card.card_id,
        "number": card.number,
        "name": card.name,
        "observed_at": datetime.now(UTC_PLUS_8).isoformat(),
        "complete_region": completion.complete_region,
        "bounds": list(completion.bounds),
        "badge": {
            "match_score": badge.best.result.score,
            "pixel_score": badge.best.result.pixel_score,
            "zncc_score": badge.best.result.zncc_score,
            "margin": badge.margin,
            "ocr_number": badge.ocr_number,
            "ocr_text": badge.ocr_text,
            "center": list(badge.best.result.center),
        },
        "absorb": {
            "state": absorb_state,
            "reason": completion.absorb.reason,
            "pending": _evidence_payload(completion.absorb.pending),
            "completed": _evidence_payload(completion.absorb.completed),
        },
        "suppress": {
            "state": suppress_state,
            "reason": completion.suppress.reason,
            "pending": _evidence_payload(completion.suppress.pending),
            "completed": _evidence_payload(completion.suppress.completed),
        },
        "overall_state": _overall_state(absorb_state, suppress_state),
        "conflict": False,
    }


def merge_completion_observations(observations: list[dict]) -> dict | None:
    complete = [value for value in observations if value.get("complete_region")]
    if not complete:
        return None
    selected = max(
        complete,
        key=lambda value: (
            float(value["badge"]["match_score"]),
            float(value["badge"]["pixel_score"]),
            float(value["badge"]["zncc_score"]),
        ),
    )
    merged = dict(selected)
    merged["observation_count"] = len(complete)
    absorb_states = {str(value["absorb"]["state"]) for value in complete}
    suppress_states = {str(value["suppress"]["state"]) for value in complete}
    conflict = len(absorb_states) > 1 or len(suppress_states) > 1
    if len(absorb_states) > 1:
        merged["absorb"] = {
            "state": CardActionState.UNKNOWN.value,
            "reason": "同轮多帧吸取状态冲突：" + ",".join(sorted(absorb_states)),
            "pending": [],
            "completed": [],
        }
    if len(suppress_states) > 1:
        merged["suppress"] = {
            "state": CardActionState.UNKNOWN.value,
            "reason": "同轮多帧压制状态冲突：" + ",".join(sorted(suppress_states)),
            "pending": [],
            "completed": [],
        }
    merged["conflict"] = conflict
    merged["overall_state"] = (
        CardActionState.UNKNOWN.value
        if conflict
        else _overall_state(
            merged["absorb"]["state"],
            merged["suppress"]["state"],
        )
    )
    return merged


def resolve_collection_map_titles(raw_text: str) -> tuple[dict, ...]:
    """Resolve one OCR sample against all formal story-map titles."""

    normalized_text = normalize_text(raw_text)
    matches: list[tuple[int, str, str, str]] = []
    for card in COLLECTABLE_CARDS:
        for target in card.targets:
            title_lengths = [
                len(normalized_title)
                for title in target.titles
                if (normalized_title := normalize_text(title))
                and normalized_title in normalized_text
            ]
            if title_lengths:
                matches.append((max(title_lengths), card.card_id, target.key, target.title))
    if not matches:
        return ()
    longest = max(value[0] for value in matches)
    return tuple(
        {
            "card_id": card_id,
            "target_key": target_key,
            "title": title,
        }
        for _length, card_id, target_key, title in sorted(
            {value for value in matches if value[0] == longest},
            key=lambda value: (value[1], value[2]),
        )
    )


def _match_payload(result: MatchResult, passed: bool) -> dict:
    return {
        "passed": bool(passed),
        "center": list(result.center),
        "match_score": result.score,
        "pixel_score": result.pixel_score,
        "zncc_score": result.zncc_score,
    }


def _merged_completion_records(observations: dict[str, list[dict]]) -> dict[str, dict]:
    return {
        card_id: merged
        for card_id, values in observations.items()
        if (merged := merge_completion_observations(values)) is not None
    }


def _completion_scan_state(records: dict[str, dict]) -> tuple[list[str], list[str]]:
    missing = [card.card_id for card in COLLECTABLE_CARDS if card.card_id not in records]
    conflicts = [card_id for card_id, value in records.items() if value.get("conflict")]
    return missing, conflicts


@dataclass(frozen=True)
class TeleportMapScanResult:
    success: bool
    pages: tuple[dict, ...]
    forward_clicks: int
    backward_clicks: int
    message: str = ""


def _combined_report_lines(payload: dict) -> list[str]:
    lines = [
        "# 剧情卡带完成度与地图读取测试",
        "",
        f"- 时间：{payload['timestamp']}",
        f"- 完成度周表：`{payload['weekly_table']}`",
        "- 说明：完成度视觉表独立于正式跑图进度；地图页保留原始 OCR。",
        "",
        "| 卡带 | 吸取 | 压制 | 地图页 | 地图名 OCR | 状态 |",
        "|---|---|---|---:|---|---|",
    ]
    for value in payload["cards"]:
        completion = value.get("completion") or {}
        absorb = _state_label((completion.get("absorb") or {}).get("state", "未识别"))
        suppress = _state_label((completion.get("suppress") or {}).get("state", "未识别"))
        maps = value.get("maps") or []
        titles = " / ".join(str(item.get("ocr_text") or "-") for item in maps)
        lines.append(
            f"| {value['card_id']} | {absorb} | {suppress} | {len(maps)} | "
            f"{titles.replace('|', '/')} | {value['status']} |"
        )
        if value.get("error"):
            lines.append(f"  - {value['card_id']}：{value['error']}")
    return lines


class BD2MapCollectionProbeTask(MapAutomationTaskBase):
    """Collect completion states and every teleport-map title in one pass."""

    vision_threshold_key = MAP_VISION_THRESHOLD_KEY
    ocr_threshold_key = MAP_OCR_THRESHOLD_KEY

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "剧情卡带完成度与地图读取测试"
        self.description = (
            "按章节读取吸取/压制状态，进入传送阵地图逐页记录地图名，再返回箱庭继续下一章。"
        )
        self.icon = FluentIcon.GLOBE
        self.group_name = "测试"
        self.group_icon = FluentIcon.BOOK_SHELF
        self.visible = True
        self.default_config.update(
            {
                "自动从主页打开剧情卡带页": True,
                "测试起始卡带": DEFAULT_COLLECTION_START,
                "测试终止卡带": DEFAULT_COLLECTION_END,
                "目标角标最大滚轮次数": PROBE_QUICK_SWITCH_SCROLL_STEPS,
                "传送阵地图名 OCR 重试次数": 3,
                "保存完成度截图": False,
                "保存每张传送阵地图截图": True,
                MAP_VISION_THRESHOLD_KEY: 0.72,
                MAP_OCR_THRESHOLD_KEY: 0.20,
                "加载页面等待秒数": 45.0,
            }
        )
        self.config_description.update(
            {
                "自动从主页打开剧情卡带页": (
                    "开启时复用标准主页入口；关闭时要求人工已停在剧情游戏卡快速选择页。"
                ),
                "测试起始卡带": "选择本次测试的第一张卡带；与终止卡带相同即只测试一张。",
                "测试终止卡带": "选择本次测试的最后一张卡带；起止范围包含两端。",
                "目标角标最大滚轮次数": (
                    "目标不在当前快速选择画面时，每批最多向上滚轮5次，"
                    "次间隔0.1秒；每批完成后静置0.5秒再识别。"
                ),
                "传送阵地图名 OCR 重试次数": "同一地图页标题为空时的有限重试次数。",
                "保存完成度截图": "保存用于同帧读取角标、吸取和压制状态的画面。",
                "保存每张传送阵地图截图": "保存每次传送阵地图名 OCR 所使用的同一帧。",
                MAP_VISION_THRESHOLD_KEY: "通用阈值；关键模板仍受各自严格门槛约束。",
                MAP_OCR_THRESHOLD_KEY: "角标辅助 OCR 与传送阵地图名 OCR 的最低阈值。",
                "加载页面等待秒数": "卡带转场和箱庭确认允许的最长时间。",
            }
        )
        self.config_type.update(
            {
                "测试起始卡带": {
                    "type": "drop_down",
                    "options": list(COLLECTION_CARD_OPTIONS),
                },
                "测试终止卡带": {
                    "type": "drop_down",
                    "options": list(COLLECTION_CARD_OPTIONS),
                },
                "目标角标最大滚轮次数": {"min": 1, "max": 30, "step": 1},
                "传送阵地图名 OCR 重试次数": {"min": 1, "max": 10, "step": 1},
                MAP_VISION_THRESHOLD_KEY: {"min": 0.50, "max": 0.95, "step": 0.01},
                MAP_OCR_THRESHOLD_KEY: {"min": 0.05, "max": 0.95, "step": 0.01},
                "加载页面等待秒数": {"min": 10.0, "max": 120.0, "step": 1.0},
            }
        )

    def _selected_cards(self) -> tuple[CardSpec, ...]:
        start_value = self.config.get("测试起始卡带")
        end_value = self.config.get("测试终止卡带")

        # Read old saved configurations without exposing the retired single selector.
        if start_value is None and end_value is None:
            legacy = str(self.config.get("测试卡带范围", ALL_COLLECTION_CARDS_OPTION))
            if legacy == ALL_COLLECTION_CARDS_OPTION:
                return tuple(COLLECTABLE_CARDS)
            legacy_card = CARD_BY_ID.get(legacy)
            if legacy_card is None or not legacy_card.collectable:
                return ()
            return (legacy_card,)

        start_id = str(start_value or DEFAULT_COLLECTION_START)
        end_id = str(end_value or DEFAULT_COLLECTION_END)
        try:
            start_index = COLLECTION_CARD_OPTIONS.index(start_id)
            end_index = COLLECTION_CARD_OPTIONS.index(end_id)
        except ValueError:
            return ()
        if start_index > end_index:
            return ()
        return tuple(COLLECTABLE_CARDS[start_index : end_index + 1])

    def _read_teleport_map_title(
        self,
        vision: Vision,
        card: CardSpec,
        page_number: int,
        retries: int,
    ) -> tuple[object, str]:
        frame = None
        raw_text = ""
        for attempt in range(1, retries + 1):
            frame = vision.capture()
            raw_text = vision.simplify(
                vision.ocr_text(
                    frame,
                    f"{card.card_id}传送阵地图名{page_number}",
                    relative_roi=TELEPORT_MAP_TITLE_OCR_RELATIVE_ROI,
                )
            )
            self.info_set(
                "传送阵地图名 OCR",
                f"{card.card_id} 第{page_number}页 {attempt}/{retries}: {raw_text or '-'}",
            )
            if normalize_text(raw_text):
                break
            if attempt < retries:
                self.sleep(TELEPORT_MAP_TITLE_RETRY_INTERVAL)
        return frame, raw_text

    def _reset_teleport_map_to_front(
        self,
        vision: Vision,
    ) -> tuple[int, str]:
        clicks = 0
        while clicks < TELEPORT_MAP_FORWARD_CLICK_LIMIT:
            frame = vision.capture()
            forward = vision.match(frame, TELEPORT_MAP_FORWARD_TEMPLATE)
            passed = vision.passes(forward, TELEPORT_MAP_FORWARD_TEMPLATE)
            self.info_set(
                "传送阵地图向前",
                (
                    f"{clicks}/{TELEPORT_MAP_FORWARD_CLICK_LIMIT}: "
                    f"match={forward.score:.3f}, pixel={forward.pixel_score:.3f}, "
                    f"zncc={forward.zncc_score:.3f}, passed={passed}"
                ),
            )
            if not passed:
                self.sleep(TELEPORT_MAP_PAGE_SETTLE_SECONDS)
                return clicks, ""
            vision.click_client(
                forward.center,
                frame.shape,
                after_sleep=TELEPORT_MAP_FORWARD_CLICK_INTERVAL,
            )
            clicks += 1

        self.sleep(TELEPORT_MAP_PAGE_SETTLE_SECONDS)
        frame = vision.capture()
        forward = vision.match(frame, TELEPORT_MAP_FORWARD_TEMPLATE)
        if vision.passes(forward, TELEPORT_MAP_FORWARD_TEMPLATE):
            return clicks, "连续向前7次后仍识别到向前按钮，未确认到达最前地图"
        return clicks, ""

    def _scan_teleport_map_pages(
        self,
        vision: Vision,
        card: CardSpec,
        timestamp: str,
        title_retries: int,
    ) -> TeleportMapScanResult:
        forward_clicks, reset_error = self._reset_teleport_map_to_front(vision)
        if reset_error:
            return TeleportMapScanResult(False, (), forward_clicks, 0, reset_error)

        pages: list[dict] = []
        backward_clicks = 0
        while True:
            page_number = len(pages) + 1
            frame, raw_text = self._read_teleport_map_title(
                vision,
                card,
                page_number,
                title_retries,
            )
            if frame is None or not normalize_text(raw_text):
                return TeleportMapScanResult(
                    False,
                    tuple(pages),
                    forward_clicks,
                    backward_clicks,
                    f"第{page_number}页传送阵地图名 OCR 为空",
                )

            forward = vision.match(frame, TELEPORT_MAP_FORWARD_TEMPLATE)
            backward = vision.match(frame, TELEPORT_MAP_BACKWARD_TEMPLATE)
            forward_passed = vision.passes(forward, TELEPORT_MAP_FORWARD_TEMPLATE)
            backward_passed = vision.passes(backward, TELEPORT_MAP_BACKWARD_TEMPLATE)
            candidates = resolve_collection_map_titles(raw_text)
            page = {
                "index": page_number,
                "ocr_text": raw_text,
                "normalized_text": normalize_text(raw_text),
                "candidates": list(candidates),
                "forward": _match_payload(forward, forward_passed),
                "backward": _match_payload(backward, backward_passed),
                "screenshot": "",
            }
            if bool(self.config.get("保存每张传送阵地图截图", True)):
                page["screenshot"] = str(
                    self.save_frame(
                        f"map_collection_probe_{timestamp}_{card.card_id}_{page_number:02d}",
                        frame,
                    )
                )
            pages.append(page)
            self.info_set(
                "传送阵地图记录",
                f"{card.card_id} 第{page_number}页：{raw_text}，向后={backward_passed}",
            )

            if not backward_passed:
                return TeleportMapScanResult(
                    True,
                    tuple(pages),
                    forward_clicks,
                    backward_clicks,
                    "已到达最后一张传送阵地图",
                )
            if backward_clicks >= TELEPORT_MAP_BACKWARD_CLICK_LIMIT:
                return TeleportMapScanResult(
                    False,
                    tuple(pages),
                    forward_clicks,
                    backward_clicks,
                    "向后点击7次后仍识别到向后按钮，停止以避免无限翻页",
                )

            vision.click_client(
                backward.center,
                frame.shape,
                after_sleep=TELEPORT_MAP_PAGE_SETTLE_SECONDS,
            )
            backward_clicks += 1

    def _save_visual_progress(
        self,
        store: CollectionVisualAuditStore,
        observations: dict[str, list[dict]],
    ) -> tuple[dict[str, dict], dict]:
        records = _merged_completion_records(observations)
        missing, conflicts = _completion_scan_state(records)
        state = store.save_scan(
            records,
            missing_card_ids=missing,
            conflict_card_ids=conflicts,
            completed=not missing and not conflicts,
        )
        return records, state

    def _initial_story_quick_switcher(self, navigator: Navigator) -> NavigationResult:
        if bool(self.config.get("自动从主页打开剧情卡带页", True)):
            return navigator._open_story_quick_switcher()
        if not navigator._wait_for_quick_switch_page():
            return NavigationResult(False, navigator.classify(), "当前画面不是快速选择卡带页")
        if not navigator._select_story_category():
            return NavigationResult(False, navigator.classify(), "未确认剧情游戏卡类别高亮")
        return NavigationResult(True, navigator.classify(), "当前剧情快速选择页已确认")

    def run(self):
        cards = self._selected_cards()
        if not cards:
            self.info_set("状态", "未执行：测试卡带起止范围无效")
            return False

        vision = Vision(self)
        navigator = Navigator(self, vision)
        store = CollectionVisualAuditStore()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        title_retries = max(
            1,
            min(10, int(self.config.get("传送阵地图名 OCR 重试次数", 3))),
        )
        scroll_steps = max(
            1,
            min(30, int(self.config.get("目标角标最大滚轮次数", PROBE_QUICK_SWITCH_SCROLL_STEPS))),
        )
        observations: dict[str, list[dict]] = {
            card.card_id: [] for card in COLLECTABLE_CARDS
        }
        results: list[dict] = []

        ready = self._initial_story_quick_switcher(navigator)
        if not ready.success:
            self._save_visual_progress(store, observations)
            self.info_set("每周状态表", str(store.path))
            self.info_set("状态", ready.message)
            self.log_warning(f"剧情卡带完成度与地图读取测试未启动：{ready.message}。")
            return False

        for index, card in enumerate(cards, start=1):
            value = {
                "card_id": card.card_id,
                "number": card.number,
                "name": card.name,
                "completion": None,
                "entry_message": "",
                "map_entry_message": "",
                "forward_clicks": 0,
                "backward_clicks": 0,
                "maps": [],
                "returned_to_sandbox": False,
                "quick_switch_reopened": False,
                "status": "pending",
                "error": "",
            }
            results.append(value)
            self.info_set("当前阶段", f"读取并进入 {card.card_id}（{index}/{len(cards)}）")

            inspected = navigator.locate_probe_story_card(
                card.card_id,
                scan_steps=scroll_steps,
            )
            if inspected is None:
                value["status"] = "card_not_found"
                value["error"] = "未确认剧情游戏卡角标及完整完成度区域"
                break

            completion_value = _completion_observation(
                card,
                inspected.located.badge,
                inspected.completion,
            )
            observations[card.card_id].append(completion_value)
            value["completion"] = completion_value
            self._save_visual_progress(store, observations)
            self.info_set(
                "完成度读取",
                (
                    f"{card.card_id}: 吸取={completion_value['absorb']['state']}, "
                    f"压制={completion_value['suppress']['state']}"
                ),
            )
            if bool(self.config.get("保存完成度截图", False)):
                self.save_frame(
                    f"map_collection_probe_{timestamp}_{card.card_id}_completion",
                    inspected.located.frame,
                )

            entered = navigator.enter_probe_story_card(inspected)
            value["entry_message"] = entered.message
            if not entered.success:
                value["status"] = "entry_failed"
                value["error"] = entered.message
                break

            map_entry = navigator.open_teleport_map_from_sandbox()
            value["map_entry_message"] = map_entry.message
            if not map_entry.success:
                value["status"] = "teleport_map_entry_failed"
                value["error"] = map_entry.message
                break

            scan = self._scan_teleport_map_pages(
                vision,
                card,
                timestamp,
                title_retries,
            )
            value["forward_clicks"] = scan.forward_clicks
            value["backward_clicks"] = scan.backward_clicks
            value["maps"] = list(scan.pages)
            if not scan.success:
                value["status"] = "map_scan_failed"
                value["error"] = scan.message
                break

            returned = navigator.return_teleport_map_to_sandbox(card.number)
            value["returned_to_sandbox"] = returned.success
            if not returned.success:
                value["status"] = "sandbox_return_failed"
                value["error"] = returned.message
                break

            if index < len(cards):
                reopened = navigator.open_story_quick_switcher_from_sandbox(
                    sandbox_already_confirmed=True,
                )
                value["quick_switch_reopened"] = reopened.success
                if not reopened.success:
                    value["status"] = "quick_switch_reopen_failed"
                    value["error"] = reopened.message
                    break
            value["status"] = "completed"

        records, weekly_state = self._save_visual_progress(store, observations)
        missing, conflicts = _completion_scan_state(records)
        payload = {
            "schema_version": 2,
            "timestamp": datetime.now(UTC_PLUS_8).isoformat(),
            "weekly_table": str(store.path),
            "weekly_key": weekly_state["weekly_key"],
            "geometry": {
                "teleport_map_title_roi": list(TELEPORT_MAP_TITLE_OCR_RELATIVE_ROI),
                "teleport_map_return_point": list(TELEPORT_MAP_RETURN_RELATIVE_POINT),
            },
            "requested_card_ids": [card.card_id for card in cards],
            "completion_missing_card_ids": missing,
            "completion_conflict_card_ids": conflicts,
            "cards": results,
        }
        json_report = self.write_probe_text(
            "map_collection_probe_latest.json",
            json.dumps(payload, ensure_ascii=False, indent=2).splitlines(),
            info_label="合并测试 JSON",
        )
        markdown_report = self.write_probe_text(
            "map_collection_probe_latest.md",
            _combined_report_lines(payload),
            info_label="合并测试报告",
        )
        success = len(results) == len(cards) and all(
            value["status"] == "completed" for value in results
        )
        self.info_set("每周状态表", str(store.path))
        self.info_set("合并测试 JSON", str(json_report))
        self.info_set("合并测试报告", str(markdown_report))
        self.info_set(
            "状态",
            f"完成{sum(value['status'] == 'completed' for value in results)}/{len(cards)}。",
        )
        if success:
            self.log_info(f"剧情卡带完成度与地图读取测试完成：{markdown_report}", notify=True)
        else:
            failed = next(
                (value for value in results if value["status"] != "completed"),
                None,
            )
            reason = failed["error"] if failed is not None else "未处理全部目标卡带"
            self.log_warning(f"剧情卡带完成度与地图读取测试停止：{reason}。")
        return success
