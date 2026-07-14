from time import monotonic

from qfluentwidgets import FluentIcon

from src.tasks.BaseBD2Task import BaseBD2Task

REFERENCE_WIDTH = 1920
REFERENCE_HEIGHT = 1080
POST_RECOGNITION_DELAY_SECONDS = 1.0
UPGRADE_CHECK_SECONDS = 1.0

# All interaction positions are ratios derived from the supplied 1920x1080 points.
BARGAIN_ENTRY_POINT = (192 / REFERENCE_WIDTH, 905 / REFERENCE_HEIGHT)
BARGAIN_CONFIRM_POINT = (1049 / REFERENCE_WIDTH, 655 / REFERENCE_HEIGHT)
COLLECTION_BACK_POINT = (111 / REFERENCE_WIDTH, 52 / REFERENCE_HEIGHT)
CLOSE_SHOP_CONFIRM_POINT = (1044 / REFERENCE_WIDTH, 641 / REFERENCE_HEIGHT)

BARGAIN_PROMPT = "使用砍价技能后，可享受商店折扣价。"
BUY_ALL_COLLECTION_PROMPT = "一键购买全部收藏"
CLOSE_SHOP_PROMPTS = ("折扣商店结束", "是否关闭折扣商店？")
UPGRADE_PROMPT = "升星"


class BargainLevelTask(BaseBD2Task):
    status_keys = [
        "启用",
        "状态",
        "当前阶段",
        "完成循环数",
        "砍价提示 OCR",
        "bargain_prompt 关键字",
        "一键购买 OCR",
        "bargain_collection 关键字",
        "关闭商店 OCR",
        "bargain_close_shop 关键字",
        "升星检测 OCR",
        "bargain_upgrade 关键字",
        "结果",
        "砍价 OCR 阈值",
        "步骤 OCR 等待秒数",
        "OCR 识别间隔秒数",
        "Log",
        "Warning",
        "Error",
    ]
    status_key_labels = {
        "bargain_prompt 关键字": "砍价提示关键字",
        "bargain_collection 关键字": "收藏页面关键字",
        "bargain_close_shop 关键字": "关闭商店关键字",
        "bargain_upgrade 关键字": "升星关键字",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "刷砍价等级"
        self.description = "在第六章商人处开始"
        self.icon = FluentIcon.SHOPPING_CART
        self.group_name = "自动刷级"
        self.group_icon = FluentIcon.SYNC
        self.visible = True
        self.default_config.update(
            {
                "启用": True,
                "砍价 OCR 阈值": 0.2,
                "步骤 OCR 等待秒数": 20.0,
                "OCR 识别间隔秒数": 0.25,
            }
        )
        self.config_description.update(
            {
                "砍价 OCR 阈值": "砍价流程中识别页面文字的最低可信度。",
                "步骤 OCR 等待秒数": "每一步等待指定页面文字出现的最长时间。",
                "OCR 识别间隔秒数": "持续识别页面文字的时间间隔。",
            }
        )
        self.config_type.update(
            {
                "砍价 OCR 阈值": {"min": 0.05, "max": 0.95, "step": 0.01},
                "步骤 OCR 等待秒数": {"min": 2.0, "max": 120.0, "step": 1.0},
                "OCR 识别间隔秒数": {"min": 0.1, "max": 2.0, "step": 0.05},
            }
        )

    def run(self):
        if not bool(self.config.get("启用", True)):
            self.info_set("状态", "刷砍价等级已禁用。")
            self.log_info("刷砍价等级已禁用。")
            return True

        completed_cycles = 0
        self.info_set("完成循环数", completed_cycles)
        self.info_set("结果", "进行中")
        self.info_set("状态", "刷砍价等级运行中。")
        self.log_info("刷砍价等级：从第六章商人处开始循环。")

        while True:
            if not self._run_cycle():
                self.info_set("状态", "页面文字确认超时，任务结束。")
                self.info_set("结果", "失败")
                return False

            completed_cycles += 1
            self.info_set("完成循环数", completed_cycles)
            self.log_info(f"刷砍价等级：已完成 {completed_cycles} 个循环。")

            self.info_set("当前阶段", "检测是否可以升星")
            can_upgrade, text = self._wait_for_ocr_keywords(
                [UPGRADE_PROMPT],
                timeout=UPGRADE_CHECK_SECONDS,
                minimum_matches=1,
                name="bargain_upgrade",
            )
            self.info_set("升星检测 OCR", text or "-")
            if can_upgrade:
                self.info_set("状态", "已可以升星。")
                self.info_set("结果", "已可以升星")
                self.log_info("刷砍价等级：检测到“升星”，已可以升星。", notify=True)
                return True

    def _run_cycle(self) -> bool:
        self.info_set("当前阶段", "打开砍价")
        self.operate_click(*BARGAIN_ENTRY_POINT)

        steps = (
            (
                "确认使用砍价技能",
                [BARGAIN_PROMPT],
                1,
                BARGAIN_CONFIRM_POINT,
                "砍价提示 OCR",
                "bargain_prompt",
            ),
            (
                "退出收藏购买页面",
                [BUY_ALL_COLLECTION_PROMPT],
                1,
                COLLECTION_BACK_POINT,
                "一键购买 OCR",
                "bargain_collection",
            ),
            (
                "确认关闭折扣商店",
                list(CLOSE_SHOP_PROMPTS),
                len(CLOSE_SHOP_PROMPTS),
                CLOSE_SHOP_CONFIRM_POINT,
                "关闭商店 OCR",
                "bargain_close_shop",
            ),
        )

        for stage, keywords, minimum_matches, click_point, status_key, ocr_name in steps:
            self.info_set("当前阶段", stage)
            found, text = self._wait_for_ocr_keywords(
                keywords,
                timeout=float(self.config.get("步骤 OCR 等待秒数", 20.0)),
                minimum_matches=minimum_matches,
                name=ocr_name,
            )
            self.info_set(status_key, text or "-")
            if not found:
                self.log_info(f"刷砍价等级：{stage}时未识别到指定文字，停止点击。")
                return False

            self.sleep(POST_RECOGNITION_DELAY_SECONDS)
            self.operate_click(*click_point)

        return True

    def _wait_for_ocr_keywords(
        self,
        keywords: list[str],
        timeout: float,
        minimum_matches: int,
        name: str,
    ) -> tuple[bool, str]:
        interval = max(0.1, float(self.config.get("OCR 识别间隔秒数", 0.25)))
        end_at = monotonic() + max(0.0, timeout)
        last_text = ""
        while monotonic() <= end_at:
            frame = self.capture_frame()
            last_text = self._ocr_text(frame, name=name)
            matches = self._keyword_match_count(last_text, keywords)
            self.info_set(f"{name} 关键字", f"{matches}/{len(keywords)}")
            if matches >= minimum_matches:
                return True, last_text
            self.sleep(interval)
        return False, last_text

    def _ocr_text(self, frame, name: str) -> str:
        try:
            boxes = self.ocr(
                frame=frame,
                threshold=float(self.config.get("砍价 OCR 阈值", 0.2)),
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
        normalized_text = BargainLevelTask._normalize_text(text)
        return sum(
            1
            for keyword in keywords
            if BargainLevelTask._normalize_text(keyword) in normalized_text
        )

    @staticmethod
    def _normalize_text(text: str) -> str:
        return "".join(character for character in str(text).lower() if character.isalnum())
