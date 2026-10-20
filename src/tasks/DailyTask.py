import re
from time import monotonic

import numpy as np
from ok.task.exceptions import FinishedException, TaskDisabledException
from qfluentwidgets import FluentIcon

from src.tasks import run_history
from src.tasks.BaseBD2Task import BaseBD2Task
from src.tasks.map_trade.models import TemplateSpec
from src.tasks.quick_hunt import QuickHuntConfigMixin
from src.tasks.task_vision_mixin import REFERENCE_HEIGHT, REFERENCE_WIDTH, TaskVisionMixin
from src.utils.home_confirmation import HOME_DIMMED_P95_THRESHOLD_DEFAULT
from src.utils.ocr_utils import normalize_ocr_text

GUILD_TEMPLATE = TemplateSpec(
    name="guild",
    file_name="guild.png",
    threshold_key="公会入口阈值",
    default_threshold=0.78,
)

GUILD_MAIN_ACTIVE_TEMPLATE = TemplateSpec(
    name="guild_main_active",
    file_name="image/green/MainBotmUnionAcGE.png",
    threshold_key="公会入口阈值",
    default_threshold=0.78,
    green_mask=True,
)

GUILD_FINISHED_TEMPLATE = TemplateSpec(
    name="guild_finished",
    file_name="guild-finished.png",
    threshold_key="公会入口阈值",
    default_threshold=0.78,
)

GUILD_MAIN_FINISHED_TEMPLATE = TemplateSpec(
    name="guild_main_finished",
    file_name="image/green/MainBotmUnionGE.png",
    threshold_key="公会入口阈值",
    default_threshold=0.78,
    green_mask=True,
)

GUILD_ENTRY_TEMPLATES = (
    GUILD_TEMPLATE,
    GUILD_MAIN_ACTIVE_TEMPLATE,
    GUILD_FINISHED_TEMPLATE,
    GUILD_MAIN_FINISHED_TEMPLATE,
)

GUILD_SIGNUP_SUCCESS_TEMPLATE = TemplateSpec(
    name="guild_signup_success",
    file_name="guild-singup-success.png",
    threshold_key="公会签到成功阈值",
    default_threshold=0.76,
)

MY_HOME_TEMPLATE = TemplateSpec(
    name="my_home",
    file_name="my-home.png",
    threshold_key="小屋页面阈值",
    default_threshold=0.76,
)

# OCR 只识别简体中文（2026-08-29 用户决策，取消繁体识别），关键字按国服
# 简体客户端实际文案书写。公会签到第二词国服实测为「奖励已发放至邮箱」。
GUILD_SUCCESS_KEYWORDS = ["签到成功", "奖励已发放至邮箱"]

# 公会页面固定文案（左侧公告/按钮与右上商店，RPT-20260901-233554 实测整页
# OCR 均可读到），用于返回主页失败时判断是否仍滞留公会页。
GUILD_PAGE_KEYWORDS = ["公告事项", "进入公会联合战", "公会商店"]

# 经营管理弹窗实际文案（国服简体，BUG-20260829-011 实测转录）：
# 餐馆营业额现状/渔笼收获情况/助手工作情况/取消/一键获得。
BUSINESS_COLLECT_KEYWORDS = [
    "餐馆营业额现状",
    "渔笼收获情况",
    "助手工作情况",
    "取消",
    "一键获得",
]

# 以下点击点均为 1920×1080 参考（_click_reference 按当前客户区归一）。
GUILD_SIGN_IN_ENTRY_REFERENCE_POINT = (370, 155)
GUILD_SIGN_IN_SUCCESS_ACKNOWLEDGE_REFERENCE_POINT = (450, 650)
BACK_BUTTON_REFERENCE_POINT = (100, 50)
MY_HOME_ENTRY_REFERENCE_POINT = (166, 158)
MY_HOME_TITLE_RELATIVE_ROI = (0.11, 0.01, 0.25, 0.10)
MY_HOME_TITLE_OCR_RETRIES = 3
MY_HOME_TITLE_OCR_RETRY_INTERVAL = 0.4
BUSINESS_COLLECT_ENTRY_REFERENCE_POINT = (165, 260)
BUSINESS_COLLECT_CLAIM_REFERENCE_POINT = (1090, 814)
BUSINESS_COLLECT_CLOSE_REFERENCE_POINT = (832, 814)
BUSINESS_COLLECT_TIMEOUT = 20.0
BUSINESS_COLLECT_CLAIM_RETRY_SECONDS = 4.0
BUSINESS_COLLECT_MAX_CLOSE_CLICKS = 8


class DailyTask(TaskVisionMixin, QuickHuntConfigMixin, BaseBD2Task):
    include_quick_hunt_config = False

    vision_threshold_key = "快速狩猎模板阈值"

    ocr_threshold_key = "日常 OCR 阈值"

    status_keys = [
        "启用",
        "状态",
        "当前任务",
        "执行公会签到",
        "公会判断",
        "公会入口",
        "公会入口模板",
        "公会入口阈值",
        "公会签到 loading 状态",
        "公会签到_loading_appear",
        "公会签到_loading_gone",
        "guild_sign_in_early 模板",
        "guild_sign_in 模板",
        "公会签到成功",
        "公会签到成功阈值",
        "公会签到 OCR",
        "公会签到返回主页 小屋按钮",
        "公会签到返回主页 亮度",
        "公会签到返回主页 抽抽乐 OCR",
        "公会签到返回主页结果",
        "执行小屋签到",
        "小屋签到 loading 状态",
        "小屋签到_loading_appear",
        "小屋签到_loading_gone",
        "my_home_early",
        "my_home",
        "小屋页面检测",
        "小屋页面阈值",
        "小屋签到返回主页 小屋按钮",
        "小屋签到返回主页 亮度",
        "小屋签到返回主页 抽抽乐 OCR",
        "小屋签到返回主页结果",
        "执行一键收菜",
        "business_collect 关键字",
        "一键收菜弹窗",
        "一键收菜 OCR",
        "一键收菜领取结果",
        "一键收菜关闭次数",
        "常客圣石结果",
        "常客圣石奖励",
        "一键收菜返回主页 小屋按钮",
        "一键收菜返回主页 亮度",
        "一键收菜返回主页 抽抽乐 OCR",
        "一键收菜返回主页结果",
        "加载页面阈值",
        "主页压暗阈值",
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
        self.name = "公会、小屋、酒馆"
        self.description = "执行公会签到、小屋签到、酒馆一键收菜和常客圣石领取。"
        self.icon = FluentIcon.CAR
        self.group_name = "日常/周常"
        self.group_icon = FluentIcon.CALENDAR
        self.visible = True
        self._init_vision_state()
        self._install_quick_hunt_config()
        self.default_config.update(
            {
                '启用': True,
                '执行公会签到': True,
                '执行小屋签到': True,
                '执行一键收菜': True,
                '小屋进入最大点击次数': 3,
                '公会入口阈值': 0.78,
                '公会签到成功阈值': 0.76,
                '小屋页面阈值': 0.76,
                '加载页面阈值': 0.72,
                '主页压暗阈值': HOME_DIMMED_P95_THRESHOLD_DEFAULT,
                '日常 OCR 阈值': 0.2,
                'loading 出现等待秒数': 6.0,
                'loading 消失等待秒数': 35.0,
                '公会签到成功等待秒数': 8.0,
                '小屋页面等待秒数': 12.0,
                '一键收菜菜单等待秒数': 8.0,
                '主页确认等待秒数': 10.0,
            }
        )
        self.config_description.update(
            {
                '执行公会签到': "从主页进入公会，领取每日签到奖励。",
                '执行小屋签到': "从主页进入小屋，确认到达后返回主页。",
                '小屋进入最大点击次数': "小屋入口点击后未确认到达时的补点上限（转场可能吞点击）。",
                '执行一键收菜': "一键领取经营奖励，随后进入餐厅领取常客圣石。",
            }
        )

    def run(self):
        self.info_set("日常周期", None)
        if not bool(self.config.get("启用", True)):
            self.info_set("状态", "公会、小屋、酒馆已禁用。")
            self.log_info("公会、小屋、酒馆已禁用。")
            return True

        self.info_set("状态", "公会、小屋、酒馆启动。")
        steps = [
            ("公会签到", "执行公会签到", self.run_guild_sign_in),
            ("小屋签到", "执行小屋签到", self.run_my_home_sign_in),
            ("一键收菜", "执行一键收菜", self.run_business_collect),
        ]

        success = []
        failed = []
        skipped = []
        stop_remaining = False
        scope = run_history.stage_scope(self)
        self.info_set("日常周期", float(scope.partition(":")[0]))
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
                record = run_history.read_stage(self, name)
                if (self.config.get("_仅执行未完成步骤", False)
                        and record.get("state") == "done" and record.get("scope") == scope):
                    if self.recover_daily_home():
                        success.append(name)
                        self.log_info(f"{name}：本周期已有完成证据，继续剩余步骤。")
                    else:
                        failed.append(name)
                        stop_remaining = True
                        self.log_info(f"{name}：已完成但未恢复主页，停止剩余步骤。")
                    continue
                if func():
                    success.append(name)
                    run_history.save_stage(self, name, "done", scope=scope)
                else:
                    failed.append(name)
                    self._save_flow_diagnostic("daily_step_failed")
                    stop_remaining = not self.recover_daily_home()
                    self.log_info(f"{name} 失败，" + (
                        "未恢复主页，停止剩余子任务。" if stop_remaining
                        else "已确认主页，继续后续独立子任务。"
                    ))
            except (TaskDisabledException, FinishedException):
                raise
            except Exception as exc:
                if name not in failed:
                    failed.append(name)
                if name in success:
                    success.remove(name)
                self._save_flow_diagnostic("daily_step_error")
                stop_remaining = not self.recover_daily_home()
                self.log_error(f"日常子任务失败：{name}", exc)
            finally:
                # Publish completed steps immediately, including when the user stops later.
                self.info_set("完成", str(success))
                self.info_set("失败", str(failed))
                self.info_set("跳过", str(skipped))

        self.info_set("完成", str(success))
        self.info_set("失败", str(failed))
        self.info_set("跳过", str(skipped))
        self.info_set("状态", "公会、小屋、酒馆结束。")
        self.log_completion(
            f"公会、小屋、酒馆结束：完成={success}, 失败={failed}, 跳过={skipped}"
        )
        return not failed

    def run_guild_sign_in(self) -> bool:
        if not self._wait_for_home_confirmation("公会签到入口前主页确认"):
            return False

        frame = self.capture_frame()
        guild, guild_spec = self._match_best(frame, GUILD_ENTRY_TEMPLATES)
        self.info_set("公会入口", f"{guild.score:.3f}")
        self.info_set("公会入口模板", guild_spec.file_name)

        guild_ready = self._passes(guild, guild_spec)
        if not guild_ready:
            self._status_set("公会判断", "未识别到公会入口")
            self._status_set("公会签到成功", "否")
            self.log_info("公会签到：未检测到公会入口模板，不点击公会按钮。")
            return False

        self._status_set("公会判断", "已识别入口，进入公会")
        self._sleep_after_recognition()
        self._click_reference(*GUILD_SIGN_IN_ENTRY_REFERENCE_POINT, after_sleep=0.5)
        loading_state, success_found, text = self._wait_loading_or_template_or_ocr(
            "公会签到",
            GUILD_SIGNUP_SUCCESS_TEMPLATE,
            GUILD_SUCCESS_KEYWORDS,
            name="guild_sign_in_early",
            reject_template_on_home=True,
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
                reject_template_on_home=True,
            )
        self.info_set("公会签到 OCR", text or "-")
        if not success_found:
            success_found = self._wait_for_existing_guild_visit()
            if success_found:
                self.info_set("公会签到结果", "公会访问完成，无新奖励提示")
                home_ok = self._return_home_from_guild()
                return home_ok
        self._status_set("公会签到成功", "是" if success_found else "否")
        if success_found:
            self.log_info("公会签到：检测到签到成功提示。")
            self._sleep_after_recognition()
            self._click_reference(
                *GUILD_SIGN_IN_SUCCESS_ACKNOWLEDGE_REFERENCE_POINT, after_sleep=0.5
            )
        else:
            self.log_info("公会签到：未检测到签到成功提示，按流程返回主页。")
            self._save_flow_diagnostic("guild_sign_in_failed")

        # 返回主页是清理动作，不能掩盖签到提示缺失；否则失败会被批处理
        # 与运行历史误记为成功，并在当日跳过补跑。
        home_ok = self._return_home_from_guild()
        return bool(success_found and home_ok)

    def _wait_for_existing_guild_visit(self) -> bool:
        deadline = monotonic() + 3.0
        hits = 0
        while True:
            frame = self.capture_frame()
            text = self._ocr_text(frame, name="公会重复访问确认")
            normalized = normalize_ocr_text(text)
            # Membership and persistent page labels distinguish this from the home icon
            # and guild search/join screens. Chat connection text alone is not a blocker.
            member_page = (
                self._keyword_match_count(text, GUILD_PAGE_KEYWORDS) >= 2
                and "公会成员" in normalized and "退出" in normalized
                and not any(word in normalized for word in (
                    "创建公会", "搜索公会", "申请加入", "重新登录", "服务器维护",
                ))
                and not any(normalize_ocr_text(part) in {"确认", "确定"} for part in text.split())
            )
            hits = hits + 1 if member_page else 0
            if hits >= 2:
                self._status_set("公会签到成功", "已访问")
                return True
            if monotonic() >= deadline:
                return False
            self.sleep(0.4)

    def _return_home_from_guild(self) -> bool:
        """Leave the guild page with bounded back-click retries.

        返回键点击可能被切页动画吞掉（BUG-20260901-02：单击定胜负让游戏
        滞留公会页面，本批与后续批次的公会任务全部失败）；主页确认失败且
        全帧 OCR 仍能读到公会页面关键字时才补点返回键，最多 3 次。
        """
        home_ok = False
        for attempt in range(1, 4):
            self._click_reference(*BACK_BUTTON_REFERENCE_POINT, after_sleep=1.0)
            if self._wait_for_home_confirmation("公会签到返回主页", timeout=4.0):
                home_ok = True
                break
            if attempt >= 3:
                break
            text = self._ocr_text(self.capture_frame(), name="公会签到返回页面")
            if self._keyword_match_count(text, GUILD_PAGE_KEYWORDS) < 1:
                self.log_info("公会签到：返回后未见公会页面关键字，不再补点返回键。")
                break
            self.log_info(
                f"公会签到：第{attempt}次返回未生效（仍在公会页面），补点返回键。"
            )
        self._status_set("公会签到返回主页结果", "通过" if home_ok else "失败")
        return home_ok

    def run_my_home_sign_in(self) -> bool:
        if not self._wait_for_home_confirmation("小屋签到入口前主页确认"):
            return False

        entry_clicks = int(self.config.get("小屋进入最大点击次数", 3))
        found = False
        loading_state = "none"
        for attempt in range(1, entry_clicks + 1):
            if attempt > 1:
                self.log_info(
                    f"小屋签到：第 {attempt} 次点击小屋入口（转场可能吞掉点击）。"
                )
            self._click_reference(*MY_HOME_ENTRY_REFERENCE_POINT, after_sleep=0.5)
            loading_state, found = self._wait_loading_or_template(
                "小屋签到",
                MY_HOME_TEMPLATE,
                name=f"my_home_early{attempt}",
            )
            if found:
                break
            if loading_state == "stuck":
                break
            if attempt < entry_clicks:
                # 补点前重新确认仍在主页；已开始转场/加载则停止补点，交给后续模板等待。
                try:
                    retry_frame = self.capture_frame()
                except (TaskDisabledException, FinishedException):
                    raise
                except Exception:
                    retry_frame = None
                if retry_frame is None or not self._frame_confirms_home(
                    retry_frame, "小屋签到重试前主页"
                ):
                    self.log_info(
                        "小屋签到：无法确认仍位于主页（截图失败或已离开主页），"
                        "停止补点，等待小屋模板。"
                    )
                    break
        self._status_set("小屋签到 loading 状态", loading_state)
        if loading_state == "stuck":
            self._status_set("小屋页面检测", "否")
            return False
        if loading_state == "loading":
            self.sleep(1.0)
        elif loading_state == "none":
            self.log_info("小屋签到：未检测到 UI_loading_black.png，继续检测 my-home.png。")

        if not found:
            found = self._wait_for_my_home_arrival(
                timeout=float(self.config.get("小屋页面等待秒数", 12.0)),
            )
        self._status_set("小屋页面检测", "是" if found else "否")
        if found:
            self.log_info("小屋签到：已进入小屋页面，返回主页。")
            self._sleep_after_recognition()
            self._click_reference(*BACK_BUTTON_REFERENCE_POINT, after_sleep=1.0)
        else:
            self.log_info("小屋签到：未确认小屋页面标题，不执行返回点击。")
            self._status_set("小屋签到返回主页结果", "未执行")
            return False

        home_ok = self._wait_for_home_confirmation("小屋签到返回主页")
        self._status_set("小屋签到返回主页结果", "通过" if home_ok else "失败")
        return home_ok

    def _wait_for_my_home_arrival(self, timeout: float) -> bool:
        vision = self._quick_vision()
        if "_wait_for_template" in self.__dict__:
            if self._wait_for_template(MY_HOME_TEMPLATE, timeout=timeout, name="my_home"):
                return True
            for attempt in range(MY_HOME_TITLE_OCR_RETRIES):
                try:
                    frame = self.capture_frame()
                except (TaskDisabledException, FinishedException):
                    raise
                except Exception:
                    frame = None
                if frame is None:
                    continue
                title = vision.ocr_text(
                    frame,
                    "小屋页面标题",
                    relative_roi=MY_HOME_TITLE_RELATIVE_ROI,
                    target_height=0,
                    ocr_scale=2.0,
                )
                if self._keyword_match_count(title, ["我的小屋"]) >= 1:
                    return True
                if attempt + 1 < MY_HOME_TITLE_OCR_RETRIES:
                    self.sleep(MY_HOME_TITLE_OCR_RETRY_INTERVAL)
            return False

        end_at = monotonic() + max(0.0, timeout)
        while monotonic() <= end_at:
            try:
                frame = self.capture_frame()
            except (TaskDisabledException, FinishedException):
                raise
            except Exception:
                frame = None
            if frame is None:
                self.sleep(0.35)
                continue
            if self._frame_confirms_home(frame, "小屋页面到达误检"):
                self.sleep(0.35)
                continue
            result = self._match(frame, MY_HOME_TEMPLATE)
            self._status_set("my_home", f"{result.score:.3f}")
            if self._passes(result, MY_HOME_TEMPLATE):
                return True
            title = vision.ocr_text(
                frame,
                "小屋页面标题",
                relative_roi=MY_HOME_TITLE_RELATIVE_ROI,
                target_height=0,
                ocr_scale=2.0,
            )
            if self._keyword_match_count(title, ["我的小屋"]) >= 1:
                return True
            self.sleep(0.35)
        return False

    def run_business_collect(self) -> bool:
        self.info_set("常客圣石结果", "未执行")
        self.info_set("常客圣石奖励", "-")
        if not self._wait_for_home_confirmation("一键收菜入口前主页确认"):
            return False

        self._click_reference(*BUSINESS_COLLECT_ENTRY_REFERENCE_POINT, after_sleep=1.0)
        found, text = self._wait_for_ocr_keywords(
            BUSINESS_COLLECT_KEYWORDS,
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

        claimed = self._finish_business_collect()
        stones_ok = claimed and self._collect_business_guest_stones()
        home_ok = (
            self.recover_daily_home() if claimed
            else self._wait_for_home_confirmation("一键收菜返回主页")
        )
        self._status_set("一键收菜领取结果", "已领取或无待领取奖励" if claimed else "未确认")
        self._status_set("一键收菜返回主页结果", "通过" if home_ok else "失败")
        if not (claimed and stones_ok and home_ok):
            self._save_flow_diagnostic("business_collect_failed")
        return claimed and stones_ok and home_ok

    def _collect_business_guest_stones(self) -> bool:
        self.info_set("常客圣石结果", "未确认")
        vision = self._quick_vision()
        frame = self.capture_frame()
        boxes = vision.ocr_boxes(frame, "餐馆入口")
        row = self._find_ocr_box(boxes, "餐馆营业额")
        if row is None:
            self.log_info("常客圣石：未识别到餐馆营业额行。")
            return False
        # 收菜后弹窗会重新排版；只点击与餐馆标题同行的前往按钮。
        actions = [
            box for box in boxes
            if normalize_ocr_text(box.name) in {"立刻前往", "立即前往"}
            and box.x > row.x
            and abs(box.y + box.height / 2 - row.y - row.height / 2)
            <= (box.height + row.height) / 2
        ]
        if len(actions) != 1:
            self.log_info("常客圣石：未能确定餐馆行的前往按钮。")
            return False
        self._click_business_bounds(frame, self._business_bounds(actions[0]), after_sleep=1.0)
        arrived, _ = self._wait_for_ocr_keywords(
            ["格鲁菲餐厅"],
            timeout=float(self.config.get("loading 消失等待秒数", 35.0)),
            minimum_matches=1,
            name="常客圣石餐厅确认",
        )
        if not arrived:
            self.log_info("常客圣石：未确认进入格鲁菲餐厅。")
            return False

        deadline = monotonic() + 12.0
        clicks = 0
        confirm_until = 0.0
        while monotonic() < deadline:
            frame = self.capture_frame()
            boxes = vision.ocr_boxes(frame, "常客圣石")
            rewards = [
                reward.group() for box in boxes
                if (reward := re.search(r"[火水风光暗]圣石[×xX*]\d+", normalize_ocr_text(box.name)))
            ]
            if clicks and rewards:
                self.info_set("常客圣石结果", "已领取")
                self.info_set("常客圣石奖励", "、".join(rewards))
                self.log_info(f"常客圣石：已领取，{'、'.join(rewards)}。")
                return True
            guest = next((box for box in boxes if normalize_ocr_text(box.name) == "常客"), None)
            if (self._find_ocr_box(boxes, "格鲁菲餐厅") is None or guest is None
                    or monotonic() < confirm_until):
                self.sleep(0.25)
                continue
            if clicks >= 2:
                self.info_set("常客圣石结果", "无待领取圣石")
                self.log_info("常客圣石：点击两次均未出现奖励提示，当前无待领取圣石。")
                return True
            self._click_business_bounds(frame, self._business_bounds(guest), after_sleep=0.8)
            clicks += 1
            # 每次等待奖励 3 秒；首次额外留 1.5 秒缓冲，期间继续读取迟到的提示。
            confirm_until = monotonic() + 3.0 + (1.5 if clicks == 1 else 0.0)
        self.log_info("常客圣石：未确认领取完成。")
        self._save_flow_diagnostic("business_guest_stones_failed")
        return False

    def _finish_business_collect(self) -> bool:
        end_at = monotonic() + BUSINESS_COLLECT_TIMEOUT
        claim_clicks = close_clicks = fallback_closes = disabled_frames = 0
        retry_at = 0.0
        claimed = closing = False
        while monotonic() < end_at:
            frame = self.capture_frame()
            boxes = list(self.ocr(
                frame=frame,
                threshold=float(self.config.get("日常 OCR 阈值", 0.2)),
                target_height=720,
                log=False,
                name="一键收菜结算",
            ))
            text = " ".join(str(box.name) for box in boxes if getattr(box, "name", ""))
            self.info_set("一键收菜 OCR", text or "-")
            reward_button = self._business_reward_button(boxes)
            menu_visible = self._keyword_match_count(text, BUSINESS_COLLECT_KEYWORDS[:3]) >= 2
            if reward_button is not None:
                claimed = claimed or claim_clicks > 0
                closing = True
                target = self._business_bounds(reward_button)
            elif menu_visible:
                cancel = self._find_ocr_box(boxes, "取消")
                claim = self._business_claim_bounds(frame, boxes, cancel)
                if claim_clicks and not claimed:
                    disabled_frames = (
                        disabled_frames + 1
                        if self._business_claim_disabled(frame, claim, cancel) else 0
                    )
                    if disabled_frames >= 2:
                        claimed = closing = True
                if claimed:
                    return True
                if not closing:
                    if monotonic() < retry_at:
                        self.sleep(0.4)
                        continue
                    if claim_clicks < 2:
                        self._click_business_bounds(frame, claim, after_sleep=0.5)
                        claim_clicks += 1
                        retry_at = monotonic() + BUSINESS_COLLECT_CLAIM_RETRY_SECONDS
                        continue
                    closing = True
                    self.log_info("一键收菜：领取结果未确认，关闭经营弹窗后报告失败。")
                    self._save_flow_diagnostic("business_collect_claim_failed")
                if cancel is not None:
                    target = self._business_bounds(cancel)
                else:
                    # 仅在经营弹窗已确认时使用本项目原关闭点，最多两次。
                    if fallback_closes >= 2:
                        self.sleep(0.4)
                        continue
                    height, width = frame.shape[:2]
                    x, y = BUSINESS_COLLECT_CLOSE_REFERENCE_POINT
                    target = (x * width / REFERENCE_WIDTH, y * height / REFERENCE_HEIGHT, 0, 0)
                    fallback_closes += 1
            else:
                disabled_frames = 0
                if self._frame_confirms_home(frame, "一键收菜返回主页"):
                    return claimed
                # 未知画面可能是奖励动画，不投递固定点以免误触其他页面。
                self.sleep(0.4)
                continue
            if close_clicks >= BUSINESS_COLLECT_MAX_CLOSE_CLICKS:
                break
            self._click_business_bounds(frame, target, after_sleep=1.0)
            close_clicks += 1
            self.info_set("一键收菜关闭次数", close_clicks)
        self.log_info("一键收菜：结算退出未在限定时间或点击次数内完成。")
        self._save_flow_diagnostic("business_collect_close_failed")
        return False

    @staticmethod
    def _business_bounds(box) -> tuple[float, float, float, float]:
        return tuple(float(getattr(box, key)) for key in ("x", "y", "width", "height"))

    def _business_claim_bounds(self, frame, boxes, cancel) -> tuple[float, float, float, float]:
        claim = self._find_ocr_box(boxes, "键获得")
        if claim is not None:
            return self._business_bounds(claim)
        height, width = frame.shape[:2]
        x, y = BUSINESS_COLLECT_CLAIM_REFERENCE_POINT
        x = x * width / REFERENCE_WIDTH
        # 领取文字在灰态可能读空；由取消框及本项目两按钮参考间距定位。
        if cancel is not None:
            gap = (
                BUSINESS_COLLECT_CLAIM_REFERENCE_POINT[0]
                - BUSINESS_COLLECT_CLOSE_REFERENCE_POINT[0]
            )
            x = float(cancel.x) + cancel.width / 2 + gap * width / REFERENCE_WIDTH
            return x - cancel.width / 2, float(cancel.y), float(cancel.width), float(cancel.height)
        return x, y * height / REFERENCE_HEIGHT, 0, 0

    def _click_business_bounds(self, frame, bounds, after_sleep: float) -> None:
        x, y, width, height = bounds
        frame_height, frame_width = frame.shape[:2]
        center_x, center_y = x + width / 2, y + height / 2
        if not (0 <= center_x < frame_width and 0 <= center_y < frame_height):
            raise ValueError("经营弹窗按钮超出当前截图范围")
        self.operate_click(
            center_x / frame_width, center_y / frame_height, after_sleep=after_sleep,
        )

    def _business_claim_disabled(self, frame, claim, cancel) -> bool:
        if cancel is None:
            return False
        samples = []
        for x, y, width, height in (claim, self._business_bounds(cancel)):
            frame_height, frame_width = frame.shape[:2]
            if not (0 <= x < x + width <= frame_width and 0 <= y < y + height <= frame_height):
                return False
            crop = frame[int(y):int(y + height), int(x):int(x + width), :3]
            if crop.size == 0:
                return False
            samples.append((
                float(np.median(crop.mean(axis=2))),
                float(np.median(crop.max(axis=2) - crop.min(axis=2))),
            ))
        claim_light, claim_colour = samples[0]
        cancel_light, _ = samples[1]
        # 同帧取消必须明亮；连续两帧确认低亮中性领取按钮，拒绝动画压暗。
        return (
            cancel_light >= HOME_DIMMED_P95_THRESHOLD_DEFAULT
            and 20 <= claim_light <= cancel_light * 0.6
            and claim_colour <= 15
        )

    def _business_reward_button(self, boxes):
        for box in boxes:
            text = normalize_ocr_text(getattr(box, "name", ""))
            if "点击" in text and "返回" in text and ("画面" in text or "即可" in text):
                return box
        text = " ".join(str(box.name) for box in boxes if getattr(box, "name", ""))
        if self._keyword_match_count(text, ["获得奖励", "奖励获得", "REWARD"]) >= 1:
            return self._find_ocr_box(boxes, "确认") or self._find_ocr_box(boxes, "确定")
        return None


class BD2BusinessCollectTestTask(BaseBD2Task):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "一键收菜与常客圣石测试"
        self.description = "从主页一键收菜，进入餐厅领取常客圣石，结束后返回主页。"
        self.icon = FluentIcon.SHOPPING_CART
        self.group_name = "测试"
        self.group_icon = FluentIcon.BOOK_SHELF
        self.visible = True

    def run(self):
        daily = self.executor.get_task_by_class(DailyTask)
        if daily is None:
            self.log_warning("未找到日常任务，无法启动收菜测试。")
            return False
        self.info_set("状态", "正在一键收菜并领取常客圣石。")
        daily.info_clear()
        try:
            success = daily.run_business_collect()
            if not success:
                daily.recover_daily_home()
        finally:
            for key, value in daily.info_snapshot().items():
                self.info_set(key, value)
        self.info_set("状态", "收菜与常客圣石测试完成。" if success else "收菜与常客圣石测试失败。")
        return success
