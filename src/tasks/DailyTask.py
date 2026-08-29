from qfluentwidgets import FluentIcon

from src.tasks.BaseBD2Task import BaseBD2Task
from src.tasks.map_trade.models import TemplateSpec
from src.tasks.quick_hunt import QuickHuntConfigMixin
from src.tasks.task_vision_mixin import TaskVisionMixin
from src.utils.home_confirmation import HOME_DIMMED_P95_THRESHOLD_DEFAULT

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

# 经营管理弹窗实际文案（国服简体，BUG-20260829-011 实测转录）：
# 餐馆营业额现状/渔笼收获情况/助手工作情况/取消/一键获得。
BUSINESS_COLLECT_KEYWORDS = [
    "餐馆营业额现状",
    "渔笼收获情况",
    "助手工作情况",
    "取消",
    "一键获得",
]


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
        self.description = "执行公会签到、小屋签到和酒馆一键收菜。"
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
                '执行一键收菜': "打开经营管理弹窗并执行一键获得。",
            }
        )

    def run(self):
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
                    self.log_info(f"{name} 未满足后续触发条件，停止剩余子任务。")
            except Exception as exc:
                failed.append(name)
                stop_remaining = True
                self.log_error(f"日常子任务失败：{name}", exc)

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
        home_ok = self._wait_for_home_confirmation("公会签到返回主页")
        self._status_set("公会签到返回主页结果", "通过" if home_ok else "失败")
        return home_ok

    def run_my_home_sign_in(self) -> bool:
        if not self._wait_for_home_confirmation("小屋签到入口前主页确认"):
            return False

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

        home_ok = self._wait_for_home_confirmation("小屋签到返回主页")
        self._status_set("小屋签到返回主页结果", "通过" if home_ok else "失败")
        return home_ok

    def run_business_collect(self) -> bool:
        if not self._wait_for_home_confirmation("一键收菜入口前主页确认"):
            return False

        self._click_reference(165, 260, after_sleep=1.0)
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

        self.sleep(0.5)
        self._click_reference(1090, 814, after_sleep=2.0)
        self._click_reference(832, 814, after_sleep=1.0)
        self._click_reference(832, 814)
        home_ok = self._wait_for_home_confirmation("一键收菜返回主页")
        self._status_set("一键收菜返回主页结果", "通过" if home_ok else "失败")
        return home_ok
