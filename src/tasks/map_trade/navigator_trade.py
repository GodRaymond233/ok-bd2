from __future__ import annotations

from time import monotonic

from src.tasks.map_trade.models import (
    MERCHANT_CARD_ID,
    MapPageMode,
    MatchResult,
    NavigationResult,
    ScreenState,
)
from src.tasks.map_trade.navigator_constants import (  # noqa: F401
    AREA_MAP_BACK_TEMPLATE,
    AREA_MAP_CHANGE_INTERVAL,
    AREA_MAP_CHANGE_TIMEOUT,
    AREA_MAP_CLICK_SETTLE_SECONDS,
    AREA_MAP_OPEN_REFERENCE_POINT,
    AREA_MAP_OPEN_RELATIVE_POINT,
    AREA_MAP_REFERENCE_SIZE,
    AREA_MAP_SCAN_LIMIT,
    AREA_MAP_TELEPORT_BRIGHT_MAXIMUM_SPREAD,
    AREA_MAP_TELEPORT_BRIGHT_MINIMUM_GRAY,
    AREA_MAP_TELEPORT_BRIGHT_NEUTRAL_RATIO,
    AREA_MAP_TELEPORT_BRIGHT_RADIUS_RATIO,
    AREA_MAP_TELEPORT_CLUSTER_RADIUS,
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
    FIRST_CARD_CONFIRM_REGION,
    FIRST_CARD_INSERT_REGION,
    FIRST_CARD_SKIP_TEMPLATE,
    HAND_TEMPLATE,
    LOADING_TEMPLATE,
    MERCHANT_AUTO_NAV_POLL_INTERVAL,
    MERCHANT_AUTO_NAV_START_TIMEOUT,
    MERCHANT_AUTO_NAV_TEMPLATE,
    MERCHANT_AUTO_NAV_TIMEOUT,
    MERCHANT_CLICK_LOCATION_FAILURE_MESSAGE,
    MERCHANT_CLICK_LOCATION_TEMPLATE,
    MERCHANT_DIALOG_CONFIRM_TIMEOUT,
    MERCHANT_NAV_CONFIRM_OCR_ROI,
    MERCHANT_NAV_GUIDE_TEMPLATE,
    MERCHANT_NAV_GUIDE_TIMEOUT,
    MERCHANT_NAV_LANDMARK_TIMEOUT,
    MERCHANT_NAV_MENU_OCR_INTERVAL,
    MERCHANT_NAV_MENU_OCR_ROI,
    MERCHANT_NAV_MENU_OCR_TIMEOUT,
    OVERLAP_ARROW_TEMPLATE,
    PROBE_QUICK_SWITCH_SCROLL_AMOUNT,
    PROBE_QUICK_SWITCH_SCROLL_COUNT,
    PROBE_QUICK_SWITCH_SCROLL_INTERVAL_SECONDS,
    PROBE_QUICK_SWITCH_SCROLL_POINT,
    PROBE_QUICK_SWITCH_SCROLL_SETTLE_SECONDS,
    PROBE_QUICK_SWITCH_SCROLL_STEPS,
    PROBE_STORY_BADGE_CONFIRM_SECONDS,
    Q_SP6_BARGAIN_CLICK_DELAY,
    Q_SP6_BARGAIN_OCR_TIMEOUT,
    Q_SP6_BARGAIN_RECHECK_DELAY,
    Q_SP6_SHOP_PAGE_KEYWORDS,
    Q_SP6_SHOP_PAGE_OCR_INTERVAL,
    Q_SP6_SHOP_PRIORITY_TIMEOUT,
    Q_SP6_STORY_NUMBER,
    QUICK_SWITCH_CARTRIDGE_REGION,
    QUICK_SWITCH_PAGE_KEYWORDS,
    QUICK_SWITCH_SCROLL_FOCUS_POINT,
    QUICK_SWITCH_SCROLL_INTERVAL,
    QUICK_SWITCH_SCROLL_POINT,
    QUICK_SWITCH_SCROLL_RESET_AMOUNT,
    QUICK_SWITCH_SCROLL_RESET_COUNT,
    QUICK_SWITCH_SCROLL_SCAN_STEPS,
    QUICK_SWITCH_SCROLL_SETTLE_SECONDS,
    QUICK_SWITCH_SCROLL_UP_AMOUNT,
    QUICK_SWITCH_SCROLL_UP_COUNT,
    QUICK_SWITCH_TEMPLATE,
    RETURN_HOME_TIMEOUT,
    SANDBOX_CONFIRM_ACTION_TEMPLATES,
    SANDBOX_INTERACTION_PROBE_INTERVAL,
    SANDBOX_INTERACTION_PROBE_TIMEOUT,
    SANDBOX_MAP_SETTLE_SECONDS,
    SANDBOX_MAP_TELEPORT_TEMPLATE,
    SANDBOX_MAP_TELEPORT_TIMEOUT,
    SANDBOX_NAVIGATION_CONFIRM_TIMEOUT,
    SANDBOX_NAVIGATION_MAP_TIMEOUT,
    SANDBOX_NAVIGATION_OCR_INTERVAL,
    SANDBOX_NAVIGATION_OPEN_SETTLE_SECONDS,
    SANDBOX_NAVIGATION_OPEN_TEMPLATES,
    SANDBOX_NAVIGATION_OPEN_TIMEOUT,
    SANDBOX_NAVIGATION_PAGE_KEYWORDS,
    SANDBOX_NAVIGATION_PIN_TEMPLATE,
    SANDBOX_NAVIGATION_RUN_TEMPLATE,
    SANDBOX_NAVIGATION_TELEPORT_SETTLE_SECONDS,
    SANDBOX_NAVIGATION_WALK_TIMEOUT,
    SANDBOX_SKILL_GROUP_PIXEL_SCORE,
    SANDBOX_SKILL_GROUP_SCALE_RATIOS,
    SANDBOX_SKILL_GROUP_SEARCH_ROI,
    SANDBOX_SKILL_GROUP_SWITCH_SETTLE_SECONDS,
    SANDBOX_SKILL_GROUP_TEMPLATE_SCORE,
    SANDBOX_SKILL_GROUP_ZNCC_SCORE,
    SANDBOX_SKILL_SELECTED_YELLOW_MIN_RATIO,
    SANDBOX_SKILL_SLOT_1_CENTER_ROI,
    SANDBOX_SKILL_SLOT_1_REFERENCE_CENTER,
    SANDBOX_SKILL_SLOT_1_RELATIVE_POINT,
    SANDBOX_SKILL_SLOT_1_SELECTED_TEMPLATE,
    SANDBOX_SKILL_SLOT_1_UNSELECTED_TEMPLATE,
    SANDBOX_SKILL_SLOT_2_CENTER_ROI,
    SANDBOX_SKILL_SLOT_2_REFERENCE_CENTER,
    SANDBOX_SKILL_SLOT_2_SELECTED_TEMPLATE,
    SANDBOX_SKILL_SLOT_2_UNSELECTED_TEMPLATE,
    SANDBOX_SKILL_STATE_TEMPLATES,
    SANDBOX_SKILL_UNSELECTED_YELLOW_MAX_RATIO,
    SANDBOX_TELEPORT_SKILL_FAILURE_GROUPS,
    SANDBOX_TELEPORT_SKILL_POLL_INTERVAL,
    SANDBOX_TELEPORT_SKILL_REFERENCE_CENTER,
    SANDBOX_TELEPORT_SKILL_RELATIVE_POINT,
    SANDBOX_TELEPORT_SKILL_TEMPLATE,
    SANDBOX_TELEPORT_SKILL_TIMEOUT,
    SANDBOX_TEMPLATES,
    SHOP_CLOSE_CLICK_INTERVAL,
    SHOP_CLOSE_CLICK_RETRIES,
    SHOP_ENTRY_CLICK_INTERVAL,
    SHOP_ENTRY_CLICK_RETRIES,
    SHOP_PAGE_OCR_KEYWORDS,
    STORY_BADGE_CANDIDATE_PIXEL_SCORE,
    STORY_BADGE_CANDIDATE_SCORE,
    STORY_BADGE_CANDIDATE_ZNCC_SCORE,
    STORY_BADGE_CENTER_REGION,
    STORY_BADGE_CLUSTER_RADIUS,
    STORY_BADGE_MIN_MARGIN,
    STORY_BADGE_OCR_BINARY_THRESHOLD,
    STORY_BADGE_OCR_HORIZONTAL_BORDER,
    STORY_BADGE_OCR_INNER_HEIGHT,
    STORY_BADGE_OCR_INNER_RADIUS_RATIO,
    STORY_BADGE_OCR_MIN_CONFIDENCE,
    STORY_BADGE_OCR_VERTICAL_BORDER,
    STORY_BADGE_PIXEL_SCORE,
    STORY_BADGE_SPECS,
    STORY_BADGE_TEMPLATE_SCORE,
    STORY_CATEGORY_HIGHLIGHT_MIN_RATIO,
    STORY_CATEGORY_HIGHLIGHT_REGION,
    STORY_CATEGORY_POINT,
    STORY_SANDBOX_STABLE_HITS,
    STORY_SANDBOX_SWITCH_WINDOW,
    STORY_SANDBOX_SWITCH_WINDOW_HITS,
    TELEPORT_GENERATION_OCR_INTERVAL,
    TELEPORT_GENERATION_OCR_KEYWORDS,
    TELEPORT_GENERATION_OCR_TIMEOUT,
    TELEPORT_INTERACTION_CLICK_DELAY,
    TELEPORT_INTERACTION_POLL_INTERVAL,
    TELEPORT_INTERACTION_TIMEOUT,
    TELEPORT_MAP_BACKWARD_TEMPLATE,
    TELEPORT_MAP_FIRST_PAGE_LIMIT,
    TELEPORT_MAP_FORWARD_TEMPLATE,
    TELEPORT_MAP_OPEN_TIMEOUT,
    TELEPORT_MAP_RETURN_REFERENCE_POINT,
    TELEPORT_MAP_RETURN_RELATIVE_POINT,
    TELEPORT_MAP_SKILL_TEMPLATE,
    TELEPORT_MAP_TELEPORT_CIRCLE_TEMPLATE,
    TELEPORT_MAP_TELEPORT_CIRCLE_TEMPLATES,
    TELEPORT_MAP_TITLE_OCR_REFERENCE_ROI,
    TELEPORT_MAP_TITLE_OCR_RELATIVE_ROI,
    TELEPORT_MAP_TRAVEL_SETTLE_SECONDS,
    TRADE_MERCHANT_CONTEXT_TEMPLATE,
    AreaMapContext,
    LocatedStoryCard,
    ProbedStoryCard,
    SandboxConfirmation,
    StoryBadgeCandidate,
    StoryBadgeDetection,
    _sandbox_skill_template,
)
from src.tasks.map_trade.trader_constants import (
    BUY_TO_SELL_SOLD_OUT_STABLE_HITS,
    BUY_TO_SELL_SOLD_OUT_TEMPLATE,
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
        if not self._click_shop_entry_with_retries():
            return NavigationResult(
                False,
                self.classify_trade(),
                "未识别到商店/进入商店入口，停止进入商店",
            )
        state = self.wait_trade_state({ScreenState.SHOP}, 12)
        if state == ScreenState.SHOP:
            return NavigationResult(True, state)
        return NavigationResult(
            False,
            state,
            "已点击商店入口，但商店页OCR未确认",
        )

    def _click_shop_entry_with_retries(self) -> bool:
        """Click the OCR-confirmed shop entry with bounded retries, never a blind point."""

        for attempt in range(1, SHOP_ENTRY_CLICK_RETRIES + 1):
            if self.vision.click_ocr(
                [r"商店", r"进入商店"],
                roi=(60, 400, 1040, 260),
                name=f"商店入口({attempt})",
            ):
                return True
            if attempt < SHOP_ENTRY_CLICK_RETRIES:
                self.task.sleep(SHOP_ENTRY_CLICK_INTERVAL)
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

    def reach_merchant_shop(self) -> NavigationResult:
        state = self.classify_trade()
        if state == ScreenState.SHOP:
            return NavigationResult(True, state)
        if state not in {ScreenState.SANDBOX, ScreenState.MERCHANT_DIALOG}:
            entered = self.select_trade_card(MERCHANT_CARD_ID)
            if not entered.success:
                return entered

        if self.classify_trade() == ScreenState.MERCHANT_DIALOG:
            return self._bargain_and_enter_shop()
        if not self._click_merchant_interaction(timeout=2.0, after_sleep=1.2):
            return NavigationResult(
                False,
                ScreenState.SANDBOX,
                MERCHANT_CLICK_LOCATION_FAILURE_MESSAGE,
            )
        if (
            self.wait_trade_state(
                {ScreenState.MERCHANT_DIALOG},
                MERCHANT_DIALOG_CONFIRM_TIMEOUT,
            )
            != ScreenState.MERCHANT_DIALOG
        ):
            return NavigationResult(False, self.classify_trade(), "未进入商人对话")
        return self._bargain_and_enter_shop()

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

