from __future__ import annotations

from time import monotonic

from src.tasks.map_trade.models import (
    MapPageMode,
    MatchResult,
    NavigationResult,
    ScreenState,
)
from src.tasks.map_trade.navigator_constants import (
    BARGAIN_CONFIRM_POINT,
    BARGAIN_POINT,
    BARGAIN_SHOP_CONFIRM_POPUP_KEYWORD,
    BARGAIN_SHOP_CONFIRM_STABLE_HITS,
    CHAPTER_HOME_POINT,
    CHAPTER_HOME_TEMPLATES,
    DISCOUNT_SHOP_CLOSE_CONTROL_REFERENCE_POINT,
    DISCOUNT_SHOP_CLOSE_CONTROL_TEMPLATES,
    DISCOUNT_SHOP_CLOSE_DIALOG_REGION,
    DISCOUNT_SHOP_CLOSE_KEYWORDS,
    DISCOUNT_SHOP_CLOSE_POINT,
    DISCOUNT_SHOP_CLOSE_TIMEOUT,
    MERCHANT_AUTO_NAV_POLL_INTERVAL,
    MERCHANT_AUTO_NAV_START_TIMEOUT,
    MERCHANT_AUTO_NAV_TEMPLATE,
    MERCHANT_AUTO_NAV_TIMEOUT,
    MERCHANT_CLICK_LOCATION_FAILURE_MESSAGE,
    MERCHANT_CLICK_LOCATION_TEMPLATE,
    MERCHANT_NAV_CONFIRM_OCR_ROI,
    MERCHANT_NAV_GUIDE_TEMPLATE,
    MERCHANT_NAV_GUIDE_TIMEOUT,
    MERCHANT_NAV_LANDMARK_TIMEOUT,
    MERCHANT_NAV_MENU_OCR_INTERVAL,
    MERCHANT_NAV_MENU_OCR_ROI,
    MERCHANT_NAV_MENU_OCR_TIMEOUT,
    Q_SP6_BARGAIN_CLICK_DELAY,
    Q_SP6_BARGAIN_OCR_TIMEOUT,
    Q_SP6_BARGAIN_RECHECK_DELAY,
    Q_SP6_SHOP_PAGE_KEYWORDS,
    Q_SP6_SHOP_PAGE_OCR_INTERVAL,
    Q_SP6_SHOP_PRIORITY_TIMEOUT,
    Q_SP6_STORY_NUMBER,
    RETURN_HOME_TIMEOUT,
    SHOP_CLOSE_CLICK_INTERVAL,
    SHOP_CLOSE_CLICK_RETRIES,
)
from src.tasks.map_trade.trader_constants import (
    BUY_TO_SELL_SOLD_OUT_STABLE_HITS,
    BUY_TO_SELL_SOLD_OUT_TEMPLATE,
    SALE_AVAILABLE_PATTERN,
    SALE_CLOSE_POINT,
    SALE_DIALOG_REGION,
    SALE_DIALOG_TIMEOUT,
    SALE_OCR_INTERVAL,
    SALE_OWNED_PATTERN,
)
from src.tasks.map_trade.vision import normalize_text


class TradeNavigationMixin:
    def enter_q_sp6_buy_flow(self) -> NavigationResult:
        """Run the buy entry flow and stop after the discounted shop page is confirmed."""

        self._status("导航状态", "优先识别MerchantClickLocation.png")
        shop_opened = self._enter_q_sp6_shop(
            Q_SP6_SHOP_PRIORITY_TIMEOUT,
            log_timeout=False,
        )
        if not shop_opened:
            self._status("导航状态", "确认主页")
            story_menu = self._open_story_quick_switcher()
            if not story_menu.success:
                return story_menu
            self._status("导航状态", "识别剧情游戏卡6角标")
            badge_match = self._wait_for_story_badge(Q_SP6_STORY_NUMBER)
            if badge_match is None:
                return NavigationResult(False, self.classify(), "未唯一确认剧情游戏卡6角标")
            badge_frame, badge = badge_match
            self._status(
                "剧情游戏卡6角标点击中心",
                (
                    f"center=({badge.best.result.center[0]},"
                    f"{badge.best.result.center[1]}), "
                    f"match={badge.best.result.score:.3f}, "
                    f"pixel={badge.best.result.pixel_score:.3f}, "
                    f"zncc={badge.best.result.zncc_score:.3f}, "
                    f"margin={badge.margin:.3f}, "
                    f"ocr={badge.ocr_number if badge.ocr_number is not None else '-'}"
                ),
            )
            self.vision.click_client(
                badge.best.result.center,
                badge_frame.shape,
                after_sleep=0.0,
            )

            self._status("导航状态", "持续识别MerchantClickLocation.png")
            shop_opened = self._enter_q_sp6_shop(
                self._loading_timeout(),
                log_timeout=True,
            )
            if not shop_opened:
                shop_opened = self._auto_navigate_to_merchant_shop()
            if not shop_opened:
                return NavigationResult(
                    False,
                    self.classify(),
                    MERCHANT_CLICK_LOCATION_FAILURE_MESSAGE,
                )

        self.task.sleep(Q_SP6_BARGAIN_RECHECK_DELAY)
        self._status("导航状态", "确认砍价入口")
        if not self._wait_for_ocr_keywords(
            ("砍价",),
            Q_SP6_BARGAIN_OCR_TIMEOUT,
            "砍价入口",
        ):
            return NavigationResult(False, self.classify(), "商店页面未识别到砍价入口")
        self.task.sleep(Q_SP6_BARGAIN_CLICK_DELAY)
        self.task.operate_click(*BARGAIN_POINT, after_sleep=0.0)

        bargain_tip = "使用砍价技能后可享受商店折扣价"
        if not self._wait_for_ocr_keywords((bargain_tip,), 10.0, "砍价说明"):
            return NavigationResult(False, self.classify(), "未识别到砍价技能折扣说明")
        self.task.operate_click(*BARGAIN_CONFIRM_POINT, after_sleep=0.0)
        if not self._wait_for_bargain_shop_confirmation():
            return NavigationResult(
                False,
                self.classify(),
                "砍价确认后未通过OCR确认商店页面",
            )
        return NavigationResult(True, ScreenState.SHOP, "已通过OCR确认商店页面")

    def _enter_q_sp6_shop(
        self,
        timeout: float,
        *,
        log_timeout: bool,
        interval: float = 0.5,
    ) -> bool:
        if not self._click_merchant_interaction(
            timeout,
            after_sleep=0.0,
            interval=interval,
        ):
            if log_timeout:
                self.task.log_warning(
                    f"跑商：{MERCHANT_CLICK_LOCATION_FAILURE_MESSAGE}。"
                )
            return False
        self._status("商店进入", "成功，已点击MerchantClickLocation中心")
        return True

    def _click_merchant_interaction(
        self,
        timeout: float,
        *,
        after_sleep: float,
        interval: float = 0.25,
    ) -> bool:
        """Find the calibrated merchant location crop and click its center."""

        end_at = monotonic() + max(0.0, timeout)
        last = MatchResult(-1.0, (0, 0), (0, 0))
        while monotonic() <= end_at:
            frame = self.vision.capture()
            last = self.vision.match(frame, MERCHANT_CLICK_LOCATION_TEMPLATE)
            passed = self.vision.passes(last, MERCHANT_CLICK_LOCATION_TEMPLATE)
            self._status(
                MERCHANT_CLICK_LOCATION_TEMPLATE.name,
                (
                    f"{'pass' if passed else 'miss'}; match={last.score:.3f}; "
                    f"pixel={last.pixel_score:.3f}; zncc={last.zncc_score:.3f}"
                ),
            )
            if passed:
                click_point = last.center
                self._status(
                    "商人交互点击位置",
                    f"center=({click_point[0]},{click_point[1]})",
                )
                self.vision.click_client(
                    click_point,
                    frame.shape,
                    after_sleep=after_sleep,
                )
                return True
            self.task.sleep(interval)
        self.task.log_warning(
            f"跑商：{MERCHANT_CLICK_LOCATION_FAILURE_MESSAGE}，"
            f"最后匹配={last.score:.3f}/{last.pixel_score:.3f}/{last.zncc_score:.3f}。"
        )
        return False

    def _auto_navigate_to_merchant_shop(self) -> bool:
        """地标不在视野时，经小地图导航菜单自动移动到商店，再重新识别地标。"""

        self._status("导航状态", "商人地标不可见，尝试小地图导航到商店")
        if not self.vision.click_template(
            MERCHANT_NAV_GUIDE_TEMPLATE,
            timeout=MERCHANT_NAV_GUIDE_TIMEOUT,
            after_sleep=0.8,
        ):
            self.task.log_warning("跑商：未识别到小地图导航按钮，无法自动寻路到商店。")
            return False
        if not self._click_merchant_nav_destination():
            return False
        if not self._wait_merchant_auto_navigation():
            return False
        return self._click_merchant_interaction(
            MERCHANT_NAV_LANDMARK_TIMEOUT,
            after_sleep=1.2,
        )

    def _click_merchant_nav_destination(self) -> bool:
        """在导航菜单中依次 OCR 点击"商店"目的地与"确认"按钮。"""

        end_at = monotonic() + MERCHANT_NAV_MENU_OCR_TIMEOUT
        while monotonic() <= end_at:
            if self.vision.click_ocr(
                [r"商店"],
                roi=MERCHANT_NAV_MENU_OCR_ROI,
                after_sleep=0.8,
                name="商店导航",
            ):
                break
            self.task.sleep(MERCHANT_NAV_MENU_OCR_INTERVAL)
        else:
            self.task.log_warning("跑商：小地图导航菜单未识别到商店目的地。")
            return False

        end_at = monotonic() + MERCHANT_NAV_MENU_OCR_TIMEOUT
        while monotonic() <= end_at:
            if self.vision.click_ocr(
                [r"确认"],
                roi=MERCHANT_NAV_CONFIRM_OCR_ROI,
                after_sleep=0.8,
                name="商店导航确认",
            ):
                return True
            self.task.sleep(MERCHANT_NAV_MENU_OCR_INTERVAL)
        self.task.log_warning("跑商：小地图导航确认按钮未出现。")
        return False

    def _wait_merchant_auto_navigation(self) -> bool:
        """等待自动移动开始并结束；地标提前进入视野时立即放行。"""

        started_at = monotonic()
        end_at = started_at + MERCHANT_AUTO_NAV_TIMEOUT
        seen = False
        while monotonic() <= end_at:
            frame = self.vision.capture()
            landmark = self.vision.match(frame, MERCHANT_CLICK_LOCATION_TEMPLATE)
            if self.vision.passes(landmark, MERCHANT_CLICK_LOCATION_TEMPLATE):
                self._status(
                    "自动移动到商店",
                    f"地标已进入视野 center={landmark.center}",
                )
                return True
            active = self.vision.passes(
                self.vision.match(frame, MERCHANT_AUTO_NAV_TEMPLATE),
                MERCHANT_AUTO_NAV_TEMPLATE,
            )
            if active:
                seen = True
            elif seen:
                self._status("自动移动到商店", "自动移动已结束")
                return True
            if not seen and monotonic() - started_at >= MERCHANT_AUTO_NAV_START_TIMEOUT:
                self.task.log_warning("跑商：确认导航后未观察到自动移动。")
                return False
            self.task.sleep(MERCHANT_AUTO_NAV_POLL_INTERVAL)
        self.task.log_warning("跑商：自动移动到商店超时。")
        return False

    def wait_for_q_sp6_sandbox(
        self,
        timeout: float,
        *,
        interval: float = 0.25,
    ) -> bool:
        """Strictly confirm the trade sandbox and the Q_sp6 merchant location.

        The merchant-location template remains scoped to the explicit trade
        flow.  It is never added to the shared story or PVP classifiers.
        """

        self._status("导航状态", "等待第六章箱庭确认")
        end_at = monotonic() + max(0.0, timeout)
        last_state = ScreenState.UNKNOWN
        last = MatchResult(-1.0, (0, 0), (0, 0))
        while True:
            frame = self.vision.capture()
            last_state = self._classify_trade_frame(frame)
            last = self.vision.match(frame, MERCHANT_CLICK_LOCATION_TEMPLATE)
            merchant_passed = self.vision.passes(
                last,
                MERCHANT_CLICK_LOCATION_TEMPLATE,
            )
            self._status(
                "Q_sp6商人前确认",
                (
                    f"state={last_state.value}; "
                    f"merchant={'pass' if merchant_passed else 'miss'}; "
                    f"match={last.score:.3f}; pixel={last.pixel_score:.3f}; "
                    f"zncc={last.zncc_score:.3f}"
                ),
            )
            if last_state == ScreenState.SANDBOX and merchant_passed:
                return True
            if monotonic() >= end_at:
                break
            self.task.sleep(interval)
        self.task.log_warning(
            "跑商：未同时确认 Q_sp6 箱庭与商人位置，"
            f"state={last_state.value}, "
            f"merchant={last.score:.3f}/{last.pixel_score:.3f}/{last.zncc_score:.3f}。"
        )
        return False

    def select_trade_card(self, card_id: str) -> NavigationResult:
        """Enter a trade card using trade-only sandbox evidence."""

        located = self._locate_story_card(card_id)
        if isinstance(located, NavigationResult):
            return located
        badge = located.badge
        result = badge.best.result
        self._status(
            f"剧情游戏卡{located.card.number}角标点击中心",
            (
                f"center=({result.center[0]},{result.center[1]}), "
                f"match={result.score:.3f}, pixel={result.pixel_score:.3f}, "
                f"zncc={result.zncc_score:.3f}, margin={badge.margin:.3f}, "
                f"ocr={badge.ocr_number if badge.ocr_number is not None else '-'}"
            ),
        )
        self.vision.click_client(result.center, located.frame.shape, after_sleep=1.0)
        if self.wait_for_q_sp6_sandbox(self._loading_timeout()):
            return NavigationResult(True, ScreenState.SANDBOX, card_id)
        return NavigationResult(
            False,
            self.classify_trade(),
            f"剧情游戏卡{located.card.number}跑商箱庭确认超时",
        )

    def _wait_for_bargain_shop_confirmation(self, timeout: float | None = None) -> bool:
        """Wait until the discounted shop page is confirmed by stable OCR.

        The bargain popup itself still exposes the shop keywords, so a frame only
        counts when the shop keywords are present and the popup-specific marker is
        absent on consecutive captures. When the daily stock is already sold out
        the purchase-button keyword never appears; the stable sold-out template
        then confirms the page under the same popup exclusion.
        """

        timeout = self._loading_timeout() if timeout is None else float(timeout)
        end_at = monotonic() + max(0.0, timeout)
        consecutive_hits = 0
        sold_out_hits = 0
        last_text = ""
        popup_marker = normalize_text(self.vision.simplify(BARGAIN_SHOP_CONFIRM_POPUP_KEYWORD))
        while True:
            frame = self.vision.capture()
            matched, text = self._ocr_keywords_in_frame(
                frame,
                Q_SP6_SHOP_PAGE_KEYWORDS,
                "砍价后商店页面",
            )
            last_text = text or last_text
            normalized = normalize_text(self.vision.simplify(text))
            popup_present = popup_marker in normalized
            if matched and not popup_present:
                consecutive_hits += 1
                sold_out_hits = 0
            else:
                consecutive_hits = 0
                sold_out = self.vision.match(frame, BUY_TO_SELL_SOLD_OUT_TEMPLATE)
                sold_out_passed = not popup_present and self.vision.passes(
                    sold_out,
                    BUY_TO_SELL_SOLD_OUT_TEMPLATE,
                )
                sold_out_hits = sold_out_hits + 1 if sold_out_passed else 0
                self._status(
                    "砍价后商店页面 售罄模板",
                    (
                        f"{'命中' if sold_out_passed else '未命中'} "
                        f"{sold_out_hits}/{BUY_TO_SELL_SOLD_OUT_STABLE_HITS}; "
                        f"popup={'yes' if popup_present else 'no'}; "
                        f"m={sold_out.score:.3f}, p={sold_out.pixel_score:.3f}, "
                        f"z={sold_out.zncc_score:.3f}"
                    ),
                )
            self._status(
                "砍价后商店页面 OCR稳定",
                f"{consecutive_hits}/{BARGAIN_SHOP_CONFIRM_STABLE_HITS}",
            )
            if consecutive_hits >= BARGAIN_SHOP_CONFIRM_STABLE_HITS:
                return True
            if sold_out_hits >= BUY_TO_SELL_SOLD_OUT_STABLE_HITS:
                self.task.log_info(
                    "跑商：未显示一键购买全部收藏，售罄模板已稳定命中，确认当天已购买完。"
                )
                return True
            if monotonic() >= end_at:
                break
            self.task.sleep(Q_SP6_SHOP_PAGE_OCR_INTERVAL)
        self.task.log_warning(f"跑商：砍价确认后未通过OCR确认商店页面，OCR={last_text or '-'}。")
        return False

    def _click_shop_close_control(self, after_sleep: float = 0.0) -> None:
        """Click the discount shop close control, template-first with a calibrated fallback."""

        for attempt in range(1, SHOP_CLOSE_CLICK_RETRIES + 1):
            frame = self.vision.capture()
            for spec in DISCOUNT_SHOP_CLOSE_CONTROL_TEMPLATES:
                result = self.vision.match(frame, spec)
                passed = self.vision.passes(result, spec)
                self._status(
                    spec.name,
                    (
                        f"{'pass' if passed else 'miss'}; "
                        f"match={result.score:.3f}; pixel={result.pixel_score:.3f}; "
                        f"zncc={result.zncc_score:.3f}"
                    ),
                )
                if passed:
                    self._status(
                        "折扣商店关闭按钮",
                        f"center=({result.center[0]},{result.center[1]})",
                    )
                    self.vision.click_client(
                        result.center,
                        frame.shape,
                        after_sleep=after_sleep,
                    )
                    return
            if attempt < SHOP_CLOSE_CLICK_RETRIES:
                self.task.sleep(SHOP_CLOSE_CLICK_INTERVAL)
        self._status("折扣商店关闭按钮", "模板未命中，回退到标定相对点(82,36)")
        self.vision.click_reference(
            *DISCOUNT_SHOP_CLOSE_CONTROL_REFERENCE_POINT,
            after_sleep=after_sleep,
        )

    def _click_chapter_home_button(self, after_sleep: float = 0.0) -> None:
        """Click the chapter home button, template-first with a calibrated fallback."""

        for attempt in range(1, SHOP_CLOSE_CLICK_RETRIES + 1):
            frame = self.vision.capture()
            for spec in CHAPTER_HOME_TEMPLATES:
                result = self.vision.match(frame, spec)
                passed = self.vision.passes(result, spec)
                self._status(
                    spec.name,
                    (
                        f"{'pass' if passed else 'miss'}; "
                        f"match={result.score:.3f}; pixel={result.pixel_score:.3f}; "
                        f"zncc={result.zncc_score:.3f}"
                    ),
                )
                if passed:
                    self._status(
                        "箱庭主页按钮",
                        f"center=({result.center[0]},{result.center[1]})",
                    )
                    self.vision.click_client(
                        result.center,
                        frame.shape,
                        after_sleep=after_sleep,
                    )
                    return
            if attempt < SHOP_CLOSE_CLICK_RETRIES:
                self.task.sleep(SHOP_CLOSE_CLICK_INTERVAL)
        self._status("箱庭主页按钮", "模板未命中，回退到标定相对点(1797,63)")
        self.task.operate_click(*CHAPTER_HOME_POINT, after_sleep=after_sleep)

    def return_home(self) -> NavigationResult:
        state = self.classify()
        if state == ScreenState.HOME:
            return NavigationResult(True, state)
        if state == ScreenState.SHOP:
            return self._return_home_from_discount_shop()
        if state in {ScreenState.AREA_MAP, ScreenState.SANDBOX_MAP}:
            expected_modes = (
                {
                    MapPageMode.DIRECT_TELEPORT,
                    MapPageMode.GENERATE_TELEPORT,
                }
                if state == ScreenState.AREA_MAP
                else {MapPageMode.SANDBOX_LARGE_MAP}
            )
            closed = self._close_confirmed_map_page(
                expected_modes,
                timeout=self._loading_timeout(),
            )
            if not closed.success:
                return NavigationResult(
                    False,
                    closed.state,
                    f"返回主页前关闭地图页面失败：{closed.message}",
                    map_page_mode=closed.map_page_mode,
                )
            state = ScreenState.SANDBOX
        if state == ScreenState.LOADING:
            state = self.wait_state(
                {ScreenState.HOME, ScreenState.SANDBOX},
                self._loading_timeout(),
            )
            if state == ScreenState.HOME:
                return NavigationResult(True, state)
        if state != ScreenState.SANDBOX:
            return NavigationResult(
                False,
                state,
                "当前页面没有已确认的安全返回路径，未执行点击",
            )

        self._click_chapter_home_button()
        if self._wait_for_cartridge_home(
            timeout=RETURN_HOME_TIMEOUT,
            allow_return_announcement_cleanup=True,
        ):
            return NavigationResult(True, ScreenState.HOME, "已从箱庭返回主页")
        return NavigationResult(
            False,
            self.classify(),
            "点击一次箱庭主页按钮后未在10秒内确认主页",
        )

    def _return_home_from_discount_shop(self) -> NavigationResult:
        if not self._close_sale_dialog_before_return():
            return NavigationResult(False, ScreenState.SHOP, "返回前未确认出售弹窗已关闭")
        self._click_shop_close_control()
        if not self._wait_for_ocr_keywords(
            DISCOUNT_SHOP_CLOSE_KEYWORDS,
            DISCOUNT_SHOP_CLOSE_TIMEOUT,
            "折扣商店关闭确认",
            interval=0.25,
            relative_roi=DISCOUNT_SHOP_CLOSE_DIALOG_REGION,
        ):
            return NavigationResult(
                False,
                self.classify(),
                "点击返回后未识别到折扣商店关闭确认",
            )

        self.task.operate_click(*DISCOUNT_SHOP_CLOSE_POINT, after_sleep=0.8)
        self._click_shop_close_control(after_sleep=0.8)
        self._click_chapter_home_button()
        if self._wait_for_cartridge_home(
            timeout=RETURN_HOME_TIMEOUT,
            allow_return_announcement_cleanup=True,
        ):
            return NavigationResult(True, ScreenState.HOME, "已关闭折扣商店并返回主页")
        return NavigationResult(False, self.classify(), "关闭折扣商店后未在10秒内返回主页")

    def _close_sale_dialog_before_return(self) -> bool:
        def read_dialog() -> str:
            return normalize_text(self.vision.ocr_text(
                self.vision.capture(), "返回前出售弹窗", relative_roi=SALE_DIALOG_REGION,
            ))

        text = read_dialog()
        if "拥有" not in text and "可购买" not in text:
            return True
        if not (
            SALE_OWNED_PATTERN.search(text)
            and SALE_AVAILABLE_PATTERN.search(text)
            and "出售" in text
        ):
            return False
        self.task.operate_click(*SALE_CLOSE_POINT, after_sleep=0.5)
        end_at = monotonic() + SALE_DIALOG_TIMEOUT
        stable_hits = 0
        while True:
            text = read_dialog()
            closed = bool(text) and "拥有" not in text and "可购买" not in text
            stable_hits = stable_hits + 1 if closed else 0
            if stable_hits >= 2:
                return True
            if monotonic() >= end_at:
                return False
            self.task.sleep(SALE_OCR_INTERVAL)
