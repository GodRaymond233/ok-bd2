"""Collect available mission and season pass rewards, then return home."""
from time import monotonic

from src.icons import TaskIcon
from src.tasks.BaseBD2Task import BaseBD2Task
from src.tasks.mission_rewards import (
    DISABLED_BUTTON_FILE,
    SECTIONS,
    home_mission_entry,
    home_pass_entry,
    mission_reward_view,
    season_pass_view,
)
from src.tasks.task_vision_mixin import TaskVisionMixin
from src.utils.image_utils import read_template_image


class MissionRewardTask(TaskVisionMixin, BaseBD2Task):
    recheck_in_batch = True
    ocr_threshold_key = "奖励 OCR 阈值"
    vision_threshold_key = "加载页面阈值"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "每日及每周奖励"
        self.description = "领取每日、每周及赛季通行证当前可领取的奖励，并复核领取状态。"
        self.icon = TaskIcon.CERTIFICATE
        self.group_name = "日常/周常"
        self.group_icon = TaskIcon.CALENDAR
        self.visible = True
        self._init_vision_state()
        self.default_config.update({
            "启用": True, "领取每日奖励": True, "领取每周奖励": True,
            "领取赛季通行证奖励": True,
            "奖励 OCR 阈值": .55, "页面等待秒数": 12.0, "领奖等待秒数": 25.0,
            "最多领取次数": 5, "主页压暗阈值": 185.0,
        })
        self.config_description.update({
            "启用": "领取已完成任务的奖励，不执行尚未完成的任务。",
            "领取每日奖励": "检查每日任务页的全部获得按钮。",
            "领取每周奖励": "每次均检查每周任务页，领取本周后来新增的奖励。",
            "领取赛季通行证奖励": "用全部获得领取各赛季的等级奖励和任务经验，跳过指南通行证。",
            "最多领取次数": "每页最多点击全部获得的次数，防止卡住时无限点击。",
        })
        self._disabled_button = read_template_image(DISABLED_BUTTON_FILE)
        self._last_view_diagnostic = ""

    def _read_reward_frame(self):
        frame = self.capture_frame()
        boxes = self.ocr(frame=frame, threshold=float(self.config.get("奖励 OCR 阈值", .55)),
                         target_height=1080, log=False)
        self.info_set("奖励页面 OCR", " ".join(box.name for box in boxes))
        return frame, boxes

    def _read_view(self):
        frame, boxes = self._read_reward_frame()
        view = mission_reward_view(frame, boxes, self._disabled_button)
        self.info_set("页面判断", f"{view.section or '-'} / {view.state}")
        # 判定失败时留一条线索；只在原因变化时记录，避免每 0.5 秒刷屏。
        reason = view.diagnostics if view.section is None else ""
        if reason and reason != getattr(self, "_last_view_diagnostic", ""):
            self.info_set("页面判定诊断", reason)
        self._last_view_diagnostic = reason
        return view, boxes, frame

    def _fail(self, stage):
        self.info_set("状态", f"每日及每周奖励失败：{stage}。")
        return False

    def _wait_page(self, section=None):
        deadline = monotonic() + max(1.0, float(self.config.get("页面等待秒数", 12)))
        while monotonic() < deadline:
            view, _, _ = self._read_view()
            if view.section in SECTIONS and (section is None or view.section == section):
                return view
            self.sleep(.5)
        return None

    def _claim_section(self, section, *, season_pass=False):
        read_view = self._read_pass_view if season_pass else self._read_view
        deadline = monotonic() + max(1.0, float(self.config.get("领奖等待秒数", 25)))
        limit = max(1, min(20, int(self.config.get("最多领取次数", 5))))
        clear_frames = clicks = closes = 0
        next_click = 0.0
        while monotonic() < deadline:
            view, _, _ = read_view()
            if view.section == section and view.state == "empty":
                clear_frames += 1
                if clear_frames >= 2:
                    self.info_set(f"{section}领奖", "当前可领取奖励已领完")
                    return True
            else:
                clear_frames = 0
            if monotonic() >= next_click:
                if view.section == section and view.state == "claim":
                    if clicks >= limit:
                        return False
                    self.operate_click(*view.point, after_sleep=.6)
                    clicks += 1
                    self.info_set(f"{section}领取点击次数", clicks)
                    next_click = monotonic() + 1.5
                elif view.state == "popup" and (clicks or not season_pass):
                    if closes >= limit * 2:
                        return False
                    self.operate_click(*view.point, after_sleep=.6)
                    closes += 1
                    next_click = monotonic() + 1.0
            self.sleep(.5)
        return False

    def _claim_missions(self, sections):
        for section in sections:
            self.info_set(f"{section}领奖", "待检查")
        if self._disabled_button is None:
            return self._fail("缺少按钮状态识别素材")
        view, _, _ = self._read_view()
        if view.state == "popup":
            self.operate_click(*view.point, after_sleep=.6)
            view = self._wait_page()
            if view is None:
                return self._fail("奖励弹窗返回任务页")
        if view.section not in SECTIONS:
            if not self._wait_for_home_confirmation("奖励领取入口前主页确认"):
                return self._fail("未确认主页或任务页")
            _, boxes, frame = self._read_view()
            if not self._frame_confirms_home(frame, "奖励领取主页复核"):
                return self._fail("任务入口点击前主页复核")
            height, width = frame.shape[:2]
            point = home_mission_entry(boxes, width, height)
            if point is None:
                return self._fail("未找到唯一的任务入口")
            self.operate_click(*point, after_sleep=.8)
            view = self._wait_page()
            if view is None:
                return self._fail("进入任务页")
        for section in sections:
            view = self._wait_page()
            if view is None:
                return self._fail(f"{section}页面检查")
            if view.section != section:
                point = dict(view.tabs).get(section)
                if point is None:
                    return self._fail(f"{section}页签识别")
                self.operate_click(*point, after_sleep=.6)
                if self._wait_page(section) is None:
                    return self._fail(f"切换{section}")
            if not self._claim_section(section):
                return self._fail(f"{section}领取复核")
        return True

    def _read_pass_view(self):
        frame, boxes = self._read_reward_frame()
        view = season_pass_view(frame, boxes)
        self.info_set("通行证页面判断", f"{view.section or '-'} / {view.state}")
        return view, boxes, frame

    def _wait_pass_page(self, section=None):
        deadline = monotonic() + max(1.0, float(self.config.get("页面等待秒数", 12)))
        previous_tabs = None
        while monotonic() < deadline:
            view, _, _ = self._read_pass_view()
            if section is not None and view.section == section and view.state in ("claim", "empty"):
                return view
            if section is None and view.state in ("list", "claim", "empty"):
                tabs = frozenset(name for name, _ in view.tabs)
                if tabs == previous_tabs:
                    return view
                previous_tabs = tabs
            else:
                previous_tabs = None
            self.sleep(.5)
        return None

    def _claim_season_passes(self):
        view, boxes, frame = self._read_pass_view()
        if view.state not in ("list", "claim", "empty"):
            if not self._frame_confirms_home(frame, "通行证入口前主页确认"):
                if not self._wait_for_home_confirmation("通行证入口前主页确认"):
                    return self._fail("未确认主页或通行证页")
                _, boxes, frame = self._read_pass_view()
                if not self._frame_confirms_home(frame, "通行证入口点击前主页复核"):
                    return self._fail("通行证入口点击前主页复核")
            height, width = frame.shape[:2]
            point = home_pass_entry(boxes, width, height)
            if point is None:
                return self._fail("未找到唯一的通行证入口")
            self.operate_click(*point, name="通行证入口", after_sleep=.8)
        view = self._wait_pass_page()
        if view is None:
            return self._fail("进入通行证页")
        sections = tuple(name for name, _ in view.tabs)
        if not sections:
            self.info_set("赛季通行证领奖", "当前没有赛季通行证")
        for index, section in enumerate(sections):
            if index:
                view = self._wait_pass_page()
                if view is None:
                    return self._fail(f"{section}入口识别")
            if view.section != section or view.state == "list":
                point = dict(view.tabs).get(section)
                if point is None:
                    return self._fail(f"{section}入口识别")
                self.operate_click(*point, name=section, after_sleep=.6)
            if self._wait_pass_page(section) is None:
                return self._fail(f"{section}等级奖励页识别")
            if not self._claim_section(section, season_pass=True):
                return self._fail(f"{section}领取复核")
        return True

    def run(self):
        if not bool(self.config.get("启用", True)):
            self.info_set("状态", "每日及每周奖励已禁用。")
            return True
        sections = [section for section, key in zip(SECTIONS, ("领取每日奖励", "领取每周奖励"))
                    if bool(self.config.get(key, True))]
        season_pass = bool(self.config.get("领取赛季通行证奖励", True))
        if not sections and not season_pass:
            self.info_set("状态", "未开启奖励领取。")
            return True
        self.info_set("状态", "开始领取每日及每周奖励。")
        if sections and not self._claim_missions(sections):
            return False
        if sections and not self.auto_return_main_home(notify_failure=False):
            return self._fail("返回主页")
        if season_pass and not self._claim_season_passes():
            return False
        if season_pass and not self.auto_return_main_home(notify_failure=False):
            return self._fail("返回主页")
        self.info_set("状态", "每日及每周奖励完成：当前可领取奖励已领完。")
        self.log_completion("每日及每周奖励：领取检查完成。")
        return True
