from __future__ import annotations

from time import monotonic

from src.tasks.map_trade.models import (
    CARD_BY_ID,
    MERCHANT_CARD_ID,
    NavigationResult,
    ScreenState,
    TemplateSpec,
)
from src.tasks.map_trade.vision import Vision, normalize_text

HOME_TEMPLATES = (
    TemplateSpec("主页", "home.png", 0.72),
    TemplateSpec("主页冰淇淋", "image/green/MainHomeIceGE.png", 0.72, green_mask=True),
    TemplateSpec("主页米饭", "image/green/MainHomeRIceGE.png", 0.72, green_mask=True),
)
SANDBOX_TEMPLATES = (
    TemplateSpec("箱庭图钉", "image/pin.png", 0.76, roi=(1080, 610, 170, 100)),
    TemplateSpec(
        "箱庭奔跑",
        "image/green/Run.png",
        0.72,
        roi=(1120, 590, 150, 125),
        green_mask=True,
    ),
)
LOADING_TEMPLATE = TemplateSpec("加载页面", "image/UI_loading_black.png", 0.70)
MERCHANT_DIALOG_TEMPLATE = TemplateSpec(
    "商人对话", "image/Mer_Dialog_TalMed.png", 0.72, roi=(930, 15, 280, 70)
)
MERCHANT_ICON_TEMPLATE = TemplateSpec(
    "商人交互", "image/green/Merchant_IcoGE.png", 0.74, green_mask=True
)
MAP_MERCHANT_TEMPLATE = TemplateSpec(
    "小地图商人", "image/Map_Merchant.png", 0.72, roi=(130, 110, 210, 190)
)
NAV_GUIDE_TEMPLATE = TemplateSpec(
    "小地图导航", "image/Nvi_SandGuideButt.png", 0.72, roi=(180, 45, 210, 110)
)
AUTO_NAV_TEMPLATE = TemplateSpec(
    "自动移动", "image/AutoNvi_ico.png", 0.72, roi=(570, 580, 140, 120)
)
HAND_TEMPLATE = TemplateSpec(
    "传送阵交互",
    "image/green/IcoHand.png",
    0.74,
    roi=(790, 470, 190, 180),
    green_mask=True,
)
NAV_TELEPORT_TEMPLATE = TemplateSpec(
    "导航图传送阵", "image/green/Nvi_TpCircleMap.png", 0.72, roi=(150, 45, 570, 620)
)
MAP_LEFT_TEMPLATE = TemplateSpec(
    "区域图左箭头", "image/green/TpMapLeft.png", 0.72, roi=(230, 270, 160, 130)
)
MAP_RIGHT_TEMPLATE = TemplateSpec(
    "区域图右箭头", "image/green/TpMapRight.png", 0.72, roi=(800, 270, 470, 140)
)
TELEPORT_MAP_TEMPLATES = (
    TemplateSpec("区域图传送阵绿幕", "image/TpCircleMapGE.png", 0.72, roi=(100, 60, 1080, 560)),
    TemplateSpec("区域图传送阵", "image/TpCircleMap.png", 0.72, roi=(100, 60, 1080, 560)),
)
OVERLAP_ARROW_TEMPLATE = TemplateSpec(
    "传送阵重叠箭头",
    "image/green/map_tcArrowGE.png",
    0.72,
    roi=(80, 50, 1160, 580),
)


class Navigator:
    def __init__(self, task, vision: Vision) -> None:
        self.task = task
        self.vision = vision

    def classify(self, frame=None) -> ScreenState:
        frame = self.vision.capture() if frame is None else frame
        for spec in HOME_TEMPLATES:
            if self.vision.match(frame, spec).score >= self.vision.threshold_for(spec):
                return ScreenState.HOME
        if self.vision.match(frame, MERCHANT_DIALOG_TEMPLATE).score >= self.vision.threshold_for(
            MERCHANT_DIALOG_TEMPLATE
        ):
            return ScreenState.MERCHANT_DIALOG
        if self.vision.match(frame, LOADING_TEMPLATE).score >= self.vision.threshold_for(
            LOADING_TEMPLATE
        ):
            return ScreenState.LOADING
        for spec in SANDBOX_TEMPLATES:
            if self.vision.match(frame, spec).score >= self.vision.threshold_for(spec):
                return ScreenState.SANDBOX

        text = normalize_text(self.vision.simplify(self.vision.ocr_text(frame, "界面分类")))
        if "购买" in text and "出售" in text:
            return ScreenState.SHOP
        if "移动魔法阵" in text:
            return ScreenState.AREA_MAP
        if "游戏卡珍藏" in text or "剧情游戏卡" in text:
            return ScreenState.CARD_MENU
        if "所需材料" in text and "料理" in text:
            return ScreenState.COOKING
        return ScreenState.UNKNOWN

    def wait_state(self, wanted: set[ScreenState], timeout: float) -> ScreenState:
        end_at = monotonic() + max(0.0, timeout)
        last = ScreenState.UNKNOWN
        while monotonic() <= end_at:
            last = self.classify()
            self._status("导航状态", last.value)
            if last in wanted:
                return last
            self.task.sleep(0.5)
        return last

    def _loading_timeout(self) -> float:
        return max(10.0, float(self.task.config.get("加载页面等待秒数", 45.0)))

    def ensure_card_menu(self) -> NavigationResult:
        for _attempt in range(3):
            state = self.classify()
            if state == ScreenState.CARD_MENU:
                return NavigationResult(True, state)
            if state == ScreenState.LOADING:
                self.wait_state(
                    {ScreenState.HOME, ScreenState.SANDBOX, ScreenState.CARD_MENU},
                    self._loading_timeout(),
                )
                continue
            if state == ScreenState.HOME:
                self.vision.click_reference(1129, 653, after_sleep=1.2)
            elif state == ScreenState.SANDBOX:
                self.vision.click_reference(1203, 664, after_sleep=1.0)
            elif state in {
                ScreenState.AREA_MAP,
                ScreenState.MERCHANT_DIALOG,
                ScreenState.SHOP,
                ScreenState.COOKING,
                ScreenState.UNKNOWN,
            }:
                self.vision.click_reference(82, 36, after_sleep=0.8)
            state = self.wait_state(
                {ScreenState.CARD_MENU, ScreenState.HOME, ScreenState.SANDBOX}, 8
            )
            if state == ScreenState.CARD_MENU:
                return NavigationResult(True, state)
        return NavigationResult(False, self.classify(), "无法恢复到卡带列表")

    def select_card(self, card_id: str) -> NavigationResult:
        card = CARD_BY_ID.get(card_id)
        if card is None:
            return NavigationResult(False, ScreenState.UNKNOWN, f"未知卡带：{card_id}")
        menu = self.ensure_card_menu()
        if not menu.success:
            return menu
        self.vision.click_ocr(
            [r"剧情游戏卡"], roi=(20, 500, 1220, 100), after_sleep=0.8, name="剧情卡带"
        )

        spec = TemplateSpec(
            f"卡带{card_id}",
            card.template,
            threshold=0.72,
            roi=(0, 540, 1280, 180),
        )
        # Reset the carousel to its visual left edge.  Identity matching below,
        # rather than the resulting slot, remains the source of truth.
        for _ in range(4):
            self.vision.drag_reference((250, 635), (1110, 635), duration=0.45, after_sleep=0.15)
        for swipe_index in range(7):
            frame = self.vision.capture()
            match = self.vision.match(frame, spec)
            self._status("目标卡带", f"{card_id}:{match.score:.3f}")
            if match.score >= self.vision.threshold_for(spec):
                self.vision.click_client(match.center, frame.shape, after_sleep=1.0)
                state = self.wait_state({ScreenState.SANDBOX, ScreenState.LOADING}, 8)
                if state == ScreenState.LOADING:
                    state = self.wait_state({ScreenState.SANDBOX}, self._loading_timeout())
                if state == ScreenState.SANDBOX:
                    return NavigationResult(True, state, card_id)
            if swipe_index < 6:
                self.vision.drag_reference((1100, 635), (250, 635), duration=0.55, after_sleep=0.35)
        return NavigationResult(False, self.classify(), f"未找到卡带 {card_id}")

    def ensure_sandbox(self, card_id: str | None = None) -> NavigationResult:
        if self.classify() == ScreenState.SANDBOX and card_id is None:
            return NavigationResult(True, ScreenState.SANDBOX)
        if card_id is not None:
            return self.select_card(card_id)
        menu = self.ensure_card_menu()
        return menu

    def reach_merchant_shop(self) -> NavigationResult:
        state = self.classify()
        if state == ScreenState.SHOP:
            return NavigationResult(True, state)
        if state not in {ScreenState.SANDBOX, ScreenState.MERCHANT_DIALOG}:
            entered = self.select_card(MERCHANT_CARD_ID)
            if not entered.success:
                return entered

        if self.classify() == ScreenState.MERCHANT_DIALOG:
            return self._bargain_and_enter_shop()
        if self.vision.click_template(MERCHANT_ICON_TEMPLATE, timeout=2.0, after_sleep=1.2):
            if self.wait_state({ScreenState.MERCHANT_DIALOG}, 8) == ScreenState.MERCHANT_DIALOG:
                return self._bargain_and_enter_shop()

        if self.vision.wait_template(MAP_MERCHANT_TEMPLATE, 3) is None:
            return NavigationResult(False, self.classify(), "小地图未找到商人")
        if not self.vision.click_template(NAV_GUIDE_TEMPLATE, timeout=3, after_sleep=0.8):
            return NavigationResult(False, self.classify(), "未找到小地图导航按钮")
        if not self.vision.click_ocr([r"商店"], roi=(220, 40, 360, 340), name="商店导航"):
            return NavigationResult(False, self.classify(), "导航菜单未识别到商店")
        self.task.sleep(0.8)
        self.vision.click_ocr([r"确认"], roi=(620, 350, 230, 240), name="商店导航确认")
        self._wait_auto_navigation(timeout=90)
        if not self.vision.click_template(MERCHANT_ICON_TEMPLATE, timeout=10, after_sleep=1.2):
            return NavigationResult(False, self.classify(), "到达商店后未找到商人交互图标")
        if self.wait_state({ScreenState.MERCHANT_DIALOG}, 10) != ScreenState.MERCHANT_DIALOG:
            return NavigationResult(False, self.classify(), "未进入商人对话")
        return self._bargain_and_enter_shop()

    def _bargain_and_enter_shop(self) -> NavigationResult:
        bargained = self.vision.click_ocr([r"砍价"], roi=(80, 520, 1000, 150), name="砍价")
        if bargained:
            self.task.sleep(0.5)
            if not self.vision.click_ocr(
                [r"砍价", r"确认"], roi=(500, 300, 380, 280), name="砍价确认"
            ):
                self.task.log_warning("跑商：砍价确认未出现，可能缺少砍价药，继续进入商店。")
        else:
            self.task.log_warning("跑商：未找到砍价选项，尝试直接进入商店。")
        self.task.sleep(0.7)
        if not self.vision.click_ocr(
            [r"商店", r"进入商店"], roi=(60, 400, 1040, 260), name="商店入口"
        ):
            # Only used after the merchant dialogue template was positively identified.
            self.vision.click_reference(130, 447, after_sleep=0.5)
        state = self.wait_state({ScreenState.SHOP}, 12)
        if state == ScreenState.SHOP:
            return NavigationResult(True, state)
        # Dialogue animation occasionally needs one harmless central click.
        self.vision.click_reference(640, 620, after_sleep=0.6)
        state = self.wait_state({ScreenState.SHOP}, 6)
        return NavigationResult(state == ScreenState.SHOP, state, "商店进入超时")

    def _wait_auto_navigation(self, timeout: float) -> None:
        end_at = monotonic() + timeout
        seen = False
        while monotonic() <= end_at:
            frame = self.vision.capture()
            active = self.vision.match(frame, AUTO_NAV_TEMPLATE).score >= self.vision.threshold_for(
                AUTO_NAV_TEMPLATE
            )
            seen = seen or active
            if seen and not active:
                return
            if not seen and self.vision.match(
                frame, MERCHANT_ICON_TEMPLATE
            ).score >= self.vision.threshold_for(MERCHANT_ICON_TEMPLATE):
                return
            self.task.sleep(0.5)

    def ensure_area_map(self) -> NavigationResult:
        state = self.classify()
        if state == ScreenState.AREA_MAP:
            return NavigationResult(True, state)
        if state != ScreenState.SANDBOX:
            return NavigationResult(False, state, "不在箱庭，无法打开传送地图")
        if self.vision.click_template(HAND_TEMPLATE, timeout=2.0, after_sleep=0.8):
            if self.wait_state({ScreenState.AREA_MAP}, 8) == ScreenState.AREA_MAP:
                return NavigationResult(True, ScreenState.AREA_MAP)

        self.vision.click_reference(148, 119, after_sleep=0.8)
        if self.vision.click_template(NAV_TELEPORT_TEMPLATE, timeout=5, after_sleep=0.7):
            self.vision.click_ocr([r"确认", r"生成"], roi=(550, 300, 320, 260), name="传送阵导航")
            self._wait_auto_navigation(90)
            if self.vision.click_template(HAND_TEMPLATE, timeout=10, after_sleep=0.8):
                if self.wait_state({ScreenState.AREA_MAP}, 8) == ScreenState.AREA_MAP:
                    return NavigationResult(True, ScreenState.AREA_MAP)
        return NavigationResult(False, self.classify(), "无法打开传送地图")

    def enter_collection_submap(self, submap_index: int) -> NavigationResult:
        area = self.ensure_area_map()
        if not area.success:
            return area
        for _ in range(5):
            if not self.vision.click_template(MAP_LEFT_TEMPLATE, timeout=0.7, after_sleep=0.25):
                break
        for _ in range(submap_index + 1):
            if not self.vision.click_template(MAP_RIGHT_TEMPLATE, timeout=2.0, after_sleep=0.35):
                return NavigationResult(False, self.classify(), "区域图无法向右切换")

        clicked = any(
            self.vision.click_template(spec, timeout=1.5, after_sleep=0.5)
            for spec in TELEPORT_MAP_TEMPLATES
        )
        if not clicked and self.vision.click_template(
            OVERLAP_ARROW_TEMPLATE, timeout=1.0, after_sleep=0.4
        ):
            clicked = any(
                self.vision.click_template(spec, timeout=1.5, after_sleep=0.5)
                for spec in TELEPORT_MAP_TEMPLATES
            )
        if not clicked:
            return NavigationResult(False, self.classify(), "区域图未找到目标传送阵")
        self.vision.click_ocr([r"确认", r"生成"], roi=(540, 300, 350, 280), name="传送确认")
        state = self.wait_state({ScreenState.SANDBOX, ScreenState.LOADING}, 8)
        if state == ScreenState.LOADING:
            state = self.wait_state({ScreenState.SANDBOX}, self._loading_timeout())
        return NavigationResult(state == ScreenState.SANDBOX, state, "传送小图超时")

    def return_home(self) -> NavigationResult:
        for _ in range(6):
            state = self.classify()
            if state == ScreenState.HOME:
                return NavigationResult(True, state)
            if state == ScreenState.LOADING:
                self.wait_state(
                    {ScreenState.HOME, ScreenState.SANDBOX, ScreenState.CARD_MENU},
                    self._loading_timeout(),
                )
                continue
            self.vision.click_reference(82, 36, after_sleep=0.8)
        state = self.classify()
        return NavigationResult(state == ScreenState.HOME, state, "返回章节主页失败")

    def _status(self, key: str, value) -> None:
        try:
            self.task.info_set(key, value)
        except AttributeError:
            pass
