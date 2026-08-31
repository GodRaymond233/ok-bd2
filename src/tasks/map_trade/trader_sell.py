from __future__ import annotations

import re
from time import monotonic
from types import SimpleNamespace

import numpy as np

from src.tasks.map_trade.calendar import (
    SALE_PRICE_REFRESH_HOUR,
    sale_price_calendar_date,
)
from src.tasks.map_trade.data import (
    ITEM_ALIASES,
)
from src.tasks.map_trade.models import (
    CalendarEntry,
    ScreenState,
)
from src.tasks.map_trade.trader_constants import (  # noqa: F401
    BUY_ALL_FAVORITES_INTERVAL,
    BUY_ALL_FAVORITES_KEYWORD,
    BUY_ALL_FAVORITES_STABLE_HITS,
    BUY_ALL_FAVORITES_TIMEOUT,
    BUY_CONFIRM_DIALOG_REGION,
    BUY_CONFIRM_INTERVAL,
    BUY_CONFIRM_KEYWORDS,
    BUY_CONFIRM_POINT,
    BUY_CONFIRM_POST_CLICK_DELAY,
    BUY_CONFIRM_PRE_CLICK_DELAY,
    BUY_CONFIRM_TIMEOUT,
    CALENDAR_DIR,
    COOK_SUBMENU_TEMPLATE,
    PROJECT_ROOT,
    SALE_120_PERCENT_MARKER_MAX_RESULTS,
    SALE_120_PERCENT_MARKER_PEAK_RADIUS,
    SALE_120_PERCENT_MARKER_TEMPLATE,
    SALE_AVAILABLE_PATTERN,
    SALE_CLOSE_POINT,
    SALE_COMPLETION_INTERVAL,
    SALE_COMPLETION_STABLE_HITS,
    SALE_COMPLETION_TIMEOUT,
    SALE_CONFIRM_POINT,
    SALE_DIALOG_REGION,
    SALE_DIALOG_TIMEOUT,
    SALE_DIALOG_TITLE_REGION,
    SALE_EMPTY_NAME_STABLE_HITS,
    SALE_FULL_PAGE_OCR_TARGET_HEIGHT,
    SALE_FULL_PAGE_OCR_TARGET_HEIGHTS,
    SALE_MARKER_MIN_MARGIN,
    SALE_MARKER_SEARCH_WIDTH,
    SALE_MARKER_VERTICAL_PADDING,
    SALE_MAX_POINT,
    SALE_MIN_POINT,
    SALE_OCR_INTERVAL,
    SALE_OWNED_PATTERN,
    SALE_PLUS_TEN_POINT,
    SALE_SLIDER_REGION,
    SALE_TOAST_ID_PATTERN,
    SELL_MODE_POINT,
    SHOP_CARTRIDGE_CANDIDATE_SCORE,
    SHOP_CARTRIDGE_CATEGORY_PATTERN,
    SHOP_CARTRIDGE_CATEGORY_PREFIX,
    SHOP_CARTRIDGE_CONFIRM_SCORE,
    SHOP_CARTRIDGE_MIN_MARGIN,
    SHOP_CARTRIDGE_NAME_MIN_SIMILARITY,
    SHOP_CARTRIDGE_OCR_MIN_CONFIDENCE,
    SHOP_CARTRIDGE_OCR_ROW_LINK_RADIUS,
    SHOP_CARTRIDGE_RECOGNITION_REGION,
    SHOP_CARTRIDGE_ROW_CLUSTER_RADIUS,
    SHOP_CARTRIDGE_SCALE_RATIOS,
    SHOP_CARTRIDGE_SCROLL_POINT,
    SHOP_CARTRIDGE_SCROLL_REGION,
    SHOP_DOWN_SCROLL_INTERVAL,
    SHOP_FIRST_PAGE_MAX_UP_SCROLLS,
    SHOP_MODE_INTERVAL,
    SHOP_MODE_SWITCH_MAX_CLICKS,
    SHOP_MODE_TIMEOUT,
    SHOP_MODE_TITLE_REGION,
    SHOP_UP_SCROLL_RECOGNITION_INTERVAL,
    STAR_ADD_TOAST_KEYWORD,
    STAR_PIXEL_THRESHOLD,
    STAR_POST_CLICK_DELAY,
    STAR_REMOVE_TOAST_KEYWORD,
    STAR_ROI_HALF_SIZE_X,
    STAR_ROI_HALF_SIZE_Y,
    STAR_TEMPLATE_FILE,
    STAR_TEMPLATE_THRESHOLD,
    STAR_VERIFY_ATTEMPTS,
    STAR_VERIFY_INTERVAL,
    SaleItemCandidate,
    ShopCartridgeDetection,
    ShopCartridgeOcrRow,
    ShopCartridgeOcrText,
    ShopCartridgeTemplateCandidate,
    split_items,
)
from src.tasks.map_trade.vision import normalize_text
from src.utils.image_utils import to_gray


class SellFlowMixin:
    def run_sell(self) -> bool:
        entries = self._resolve_sale_entries()
        if entries is None:
            self._buy_completed_in_current_shop = False
            return False
        if not entries:
            self._buy_completed_in_current_shop = False
            self._status("未出售商品", "无（当前价表没有可出售商品）")
            self.task.log_info("卖：当前价表没有可出售商品，跳过进入出售页面。")
            return True

        if getattr(self, "_buy_completed_in_current_shop", False):
            self.task.log_info("卖：买卖同时执行，继续使用当前商店并等待购买结果。")
            if not self._switch_from_completed_buy_to_sell():
                return False
        else:
            entered = self.navigator.enter_q_sp6_buy_flow()
            if not entered.success:
                self.task.log_warning(f"卖：{entered.message}")
                return False
            if entered.state != ScreenState.SHOP:
                self.task.log_warning(
                    f"卖：进入商店后状态为{entered.state.value}，未确认商店页，停止出售。"
                )
                return False
            if not self._ensure_sell_page():
                return False
        self._buy_completed_in_current_shop = False
        self._sale_entries_override = entries
        try:
            return self.sell_max_price_items()
        finally:
            self._sale_entries_override = None

    def _ensure_sell_page(
        self,
        timeout: float = SHOP_MODE_TIMEOUT,
        *,
        allow_switch: bool = True,
    ) -> bool:
        end_at = monotonic() + max(0.0, timeout)
        switch_clicks = 0
        last_text = ""
        while True:
            frame = self.vision.capture()
            text = self.vision.ocr_text(
                frame,
                "商店买卖页标题",
                relative_roi=SHOP_MODE_TITLE_REGION,
            )
            last_text = text or last_text
            normalized = normalize_text(self.vision.simplify(text))
            if "出售" in normalized:
                self._status("商店页面", "出售")
                return True
            if (
                "购买" in normalized
                and allow_switch
                and switch_clicks < SHOP_MODE_SWITCH_MAX_CLICKS
            ):
                switch_clicks += 1
                self._status(
                    "商店页面",
                    f"购买→出售 {switch_clicks}/{SHOP_MODE_SWITCH_MAX_CLICKS}",
                )
                self.task.operate_click(*SELL_MODE_POINT, after_sleep=0.5)
                continue
            if monotonic() >= end_at:
                break
            self.task.sleep(SHOP_MODE_INTERVAL)
        self.task.log_warning(
            f"卖：未能通过标题区域确认已切换到出售页面，OCR={last_text or '-'}。"
        )
        return False

    def sell_max_price_items(
        self,
        entries: list[CalendarEntry] | None = None,
    ) -> bool:
        if entries is None:
            entries = getattr(self, "_sale_entries_override", None)
            if entries is None:
                entries = self._resolve_sale_entries()
        if entries is None:
            return False
        if not entries:
            self._status("未出售商品", "无（当前价表没有可出售商品）")
            self.task.log_info("卖：当前价表没有可出售商品，跳过出售。")
            return True
        return self._sell_resolved_entries(entries)

    def _resolve_sale_entries(self) -> list[CalendarEntry] | None:
        try:
            market_now = self._current_market_time()
            calendar_date = sale_price_calendar_date(market_now)
            calendar = self.calendar_client.load(
                use_bundled=bool(
                    self.task.config.get("使用程序默认价表", True)
                ),
                use_online=bool(self.task.config.get("使用在线价表", True)),
                manual_text=str(self.task.config.get("自定义最高价表", "")),
            )
            self._status("价表来源", calendar.source)
            self._status("出售价表日期", calendar_date.isoformat())
            self.task.log_info(
                f"卖：当前北京时间{market_now.strftime('%Y-%m-%d %H:%M:%S')}，"
                f"按{calendar_date.isoformat()}最高价表执行（每日"
                f"{SALE_PRICE_REFRESH_HOUR:02d}:00刷新）。"
            )
            entries = list(calendar.entries_for(calendar_date.day))
        except Exception as exc:
            self.task.log_warning(f"价表加载失败，为避免误卖已停止出售：{exc}")
            return None

        sellable = []
        for entry in entries:
            if not entry.sell:
                self.task.log_info(f"卖：{entry.item}标记为不出售，跳过。")
                continue
            sellable.append(entry)

        use_whitelist = bool(self.task.config.get("使用出售白名单", True))
        self._status("出售白名单", "开启" if use_whitelist else "关闭")
        if use_whitelist:
            whitelist = self._sale_whitelist()
            entries = [entry for entry in sellable if self._entry_allowed(entry, whitelist)]
        else:
            entries = sellable
            self.task.log_info("卖：出售白名单已关闭，执行价表中全部允许出售的商品。")

        use_blacklist = bool(self.task.config.get("使用出售黑名单", False))
        self._status("出售黑名单", "开启" if use_blacklist else "关闭")
        if use_blacklist:
            blacklist = self._sale_blacklist()
            allowed_entries = []
            for entry in entries:
                if self._entry_allowed(entry, blacklist):
                    self.task.log_info(f"卖：{entry.item}命中出售黑名单，跳过。")
                    continue
                allowed_entries.append(entry)
            entries = allowed_entries
        if not entries:
            if use_blacklist:
                self.task.log_info("跑商：筛选后没有可出售的最高价物品。")
            elif use_whitelist:
                self.task.log_info("跑商：今天没有白名单内的最高价物品。")
            else:
                self.task.log_info("跑商：今天没有允许出售的最高价物品。")
            return []

        return entries

    def _sell_resolved_entries(self, entries: list[CalendarEntry]) -> bool:

        failed = []
        unavailable: list[str] = []
        not_sold_details: list[str] = []
        selected_shop = ""
        for entry in entries:
            if entry.shop != selected_shop:
                if not self.select_shop_tab(entry.shop):
                    failed.append(entry)
                    not_sold_details.append(f"{entry.item}（商店卡带选择失败）")
                    self._status("未出售商品", "、".join(not_sold_details))
                    selected_shop = ""
                    continue
                selected_shop = entry.shop
            if not self._sell_selected_entry(entry):
                if getattr(self, "_last_sale_unavailable", False):
                    detail = f"{entry.item}（{self._last_sale_reason}）"
                    unavailable.append(detail)
                    not_sold_details.append(detail)
                    self._status("未出售商品", "、".join(not_sold_details))
                    continue
                failed.append(entry)
                not_sold_details.append(f"{entry.item}（出售执行失败）")
                self._status("未出售商品", "、".join(not_sold_details))
                self.task.log_warning(
                    f"卖：{entry.item}执行过程中断，停止继续处理后续价表商品。"
                )
                break
        if unavailable:
            self.task.log_warning("未出售商品：" + "、".join(unavailable))
        if not not_sold_details:
            self._status("未出售商品", "无")
        if failed:
            self.task.log_warning("最高价出售失败：" + "、".join(entry.item for entry in failed))
        return not failed

    def sell_entry(self, entry: CalendarEntry) -> bool:
        if not self.select_shop_tab(entry.shop):
            return False
        return self._sell_selected_entry(entry)

    def _sell_selected_entry(self, entry: CalendarEntry) -> bool:
        self._last_sale_unavailable = False
        self._last_sale_reason = ""
        self._last_sale_page_empty = False
        sold_count = 0
        previous_owned: int | None = None
        while True:
            located = self._wait_sale_item_candidates(entry)
            if located is None:
                if sold_count and getattr(self, "_last_sale_page_empty", False):
                    self._last_sale_unavailable = False
                    self._last_sale_reason = ""
                    self._status(
                        "出售完成确认",
                        f"{entry.item}:已出售{sold_count}组，商品名连续消失",
                    )
                    self.task.log_info(
                        f"卖：{entry.item}商品名连续{SALE_EMPTY_NAME_STABLE_HITS}次未识别到，"
                        f"当前商店页已无剩余可出售组，共出售{sold_count}组。"
                    )
                    return True
                if sold_count:
                    self.task.log_warning(
                        f"卖：{entry.item}出售后仍可见商品名但120%标志未确认"
                        f"（{getattr(self, '_last_sale_reason', '') or '未知原因'}），"
                        "不能判定当前页已售完。"
                    )
                elif not getattr(self, "_last_sale_unavailable", False):
                    self.task.log_warning(
                        f"卖：{entry.item}商品名与左侧120%局部定位失败："
                        f"{getattr(self, '_last_sale_reason', '') or '未知原因'}"
                    )
                return False

            candidates, frame = located
            candidate = candidates[0]
            outcome = self._sell_one_candidate(
                entry,
                candidate,
                frame,
                previous_owned=previous_owned,
            )
            if outcome is None:
                return False
            owned, sold = outcome
            if not sold:
                return True
            sold_count += 1
            previous_owned = owned

    def _sell_one_candidate(
        self,
        entry: CalendarEntry,
        candidate: SaleItemCandidate,
        frame: np.ndarray,
        *,
        previous_owned: int | None,
    ) -> tuple[int, bool] | None:
        """Sell one currently visible card and verify that the page advanced."""

        before_signature = self._sale_name_signature(entry, frame)
        before_toast_id = self._sale_toast_id(frame)
        known_toast_id = getattr(self, "_last_sale_toast_id", None)
        if known_toast_id is not None:
            before_toast_id = max(before_toast_id or 0, known_toast_id)
        self.vision.click_client(candidate.center, frame.shape, after_sleep=0.5)
        if not self._wait_sale_dialog_item(entry):
            self.task.log_warning(f"卖：{entry.item}出售弹窗商品标题未确认。")
            return None
        owned = self._wait_owned_quantity()
        if owned is None:
            self.task.log_warning(f"卖：{entry.item}出售弹窗未识别到拥有数量。")
            return None
        available = self._wait_available_quantity()
        if available is None:
            self.task.log_warning(f"卖：{entry.item}出售弹窗未识别到可购买数量。")
            return None
        if previous_owned is not None and owned >= previous_owned:
            self.task.log_warning(
                f"卖：{entry.item}出售后拥有数量未下降（当前{owned}，上次{previous_owned}），停止以避免重复出售。"
            )
            return None

        self._status("出售弹窗库存", f"{entry.item}:{owned}")
        self._status("出售当前组上限", f"{entry.item}:{available}")
        self.task.log_info(
            f"卖：{entry.item}出售弹窗记录库存{owned}个，当前组可购买{available}个。"
        )
        if entry.reserve and owned <= entry.reserve:
            self.task.log_info(
                f"卖：{entry.item}弹窗库存{owned}不超过保留量{entry.reserve}，关闭弹窗并跳过。"
            )
            self.task.operate_click(*SALE_CLOSE_POINT, after_sleep=0.5)
            return owned, False
        if not self._choose_sale_quantity(entry, owned):
            return None
        expected_selected = None
        if entry.reserve <= 0:
            expected_selected = (
                1
                if bool(self.task.config.get("出售保险", False))
                else available
            )
        if not self._wait_selected_sale_quantity(expected_selected):
            self.task.log_warning(
                f"卖：{entry.item}出售数量未确认（期望{expected_selected or '正数'}，"
                f"当前组上限{available}），停止确认。"
            )
            return None
        self.task.operate_click(*SALE_CONFIRM_POINT, after_sleep=0.5)
        self.task.log_info(
            f"卖：{entry.item}已点击出售，本组最大可出售{available}个，等待交易完成。"
        )
        if not self._wait_sale_completion(
            entry,
            frame,
            before_signature,
            before_toast_id=before_toast_id,
        ):
            return None
        return owned, True

    def _wait_sale_item_candidates(
        self,
        entry: CalendarEntry,
        timeout: float = 8.0,
        interval: float = 0.5,
    ) -> tuple[list[SaleItemCandidate], np.ndarray] | None:
        """Wait for all OCR-confirmed sale cards for one calendar entry."""

        end_at = monotonic() + max(0.0, timeout)
        last_reason = ""
        empty_name_hits = 0
        while True:
            frame = self.vision.capture()
            candidates = self._locate_sale_items(entry, frame)
            if candidates:
                self._last_sale_page_empty = False
                return candidates, frame
            last_reason = str(getattr(self, "_last_sale_reason", "") or "")
            if getattr(self, "_last_sale_name_seen", False):
                empty_name_hits = 0
            elif getattr(self, "_last_sale_ocr_output", False):
                empty_name_hits += 1
            else:
                empty_name_hits = 0
            if empty_name_hits >= SALE_EMPTY_NAME_STABLE_HITS:
                self._last_sale_page_empty = True
                self._last_sale_reason = last_reason
                return None
            if monotonic() >= end_at:
                break
            self.task.sleep(interval)
        self._last_sale_page_empty = empty_name_hits >= SALE_EMPTY_NAME_STABLE_HITS
        self._last_sale_reason = last_reason
        return None

    @staticmethod
    def _sale_name_matches(
        normalized: str,
        normalized_names: tuple[str, ...],
    ) -> bool:
        if not normalized:
            return False
        return any(
            name and (name in normalized or normalized in name)
            for name in normalized_names
        )

    @classmethod
    def _sale_marker_search_roi(
        cls,
        name_box: object,
        frame_shape: tuple[int, ...],
    ) -> tuple[int, int, int, int] | None:
        geometry = cls._ocr_box_geometry(name_box)
        if geometry is None:
            return None
        x, y, width, height = geometry
        frame_height, frame_width = frame_shape[:2]
        scale_x = frame_width / 1920.0
        scale_y = frame_height / 1080.0
        search_width = round(SALE_MARKER_SEARCH_WIDTH * scale_x)
        padding = round(SALE_MARKER_VERTICAL_PADDING * scale_y)
        left = max(0, round(x) - search_width)
        top = max(0, round(y) - padding)
        right = min(frame_width, round(x))
        bottom = min(frame_height, round(y + height) + padding)
        if right <= left or bottom <= top:
            return None
        return left, top, right - left, bottom - top

    @staticmethod
    def _sale_roi_overlap_ratio(
        first: tuple[int, int, int, int],
        second: tuple[int, int, int, int],
    ) -> float:
        first_left, first_top, first_width, first_height = first
        second_left, second_top, second_width, second_height = second
        intersection_width = max(
            0,
            min(first_left + first_width, second_left + second_width)
            - max(first_left, second_left),
        )
        intersection_height = max(
            0,
            min(first_top + first_height, second_top + second_height)
            - max(first_top, second_top),
        )
        smaller_area = min(
            first_width * first_height,
            second_width * second_height,
        )
        if smaller_area <= 0:
            return 0.0
        return (intersection_width * intersection_height) / smaller_area

    def _sale_template_percent_boxes(
        self,
        frame: np.ndarray,
        search_roi: tuple[int, int, int, int],
        gray_frame: np.ndarray,
    ) -> list[object]:
        """Return ↑120% markers only from one item-name-driven ROI."""

        matches = self.vision.match_all(
            frame,
            SALE_120_PERCENT_MARKER_TEMPLATE,
            minimum_score=self.vision.threshold_for(SALE_120_PERCENT_MARKER_TEMPLATE),
            peak_radius=SALE_120_PERCENT_MARKER_PEAK_RADIUS,
            max_results=SALE_120_PERCENT_MARKER_MAX_RESULTS,
            search_roi=search_roi,
            gray_frame=gray_frame,
        )
        boxes = [
            SimpleNamespace(
                name="↑120%",
                confidence=result.score,
                x=result.position[0],
                y=result.position[1],
                width=result.size[0],
                height=result.size[1],
            )
            for result in matches
        ]
        boxes.sort(key=lambda box: float(box.confidence), reverse=True)
        return boxes

    def _locate_sale_items(
        self,
        entry: CalendarEntry,
        frame: np.ndarray,
    ) -> list[SaleItemCandidate]:
        """Return sale cards confirmed by OCR names and marker templates.

        OCR is used only to find candidate item names. The left-side ↑120%
        marker must come from the real rendered template and is paired
        one-to-one with that name. This avoids treating a corrupted numeric OCR
        string such as ``4120%`` as a premium marker.
        """

        names = (entry.item, *entry.aliases, *ITEM_ALIASES.get(entry.item, ()))
        normalized_names = tuple(self._normal(value) for value in names if value)
        saw_item_name = False
        saw_ocr_output = False
        specific_local_failure = False
        last_reason = ""
        gray_frame: np.ndarray | None = None
        searched_roi_results: list[
            tuple[tuple[int, int, int, int], list[object]]
        ] = []
        roi_searches = 0
        reused_rois = 0
        marker_hits = 0
        self._last_sale_name_seen = False
        self._last_sale_ocr_output = False

        for target_height in SALE_FULL_PAGE_OCR_TARGET_HEIGHTS:
            name_boxes: list[object] = []
            ocr_boxes = self.vision.ocr_boxes(
                frame,
                "出售商品列表",
                target_height=target_height,
            )
            saw_ocr_output = saw_ocr_output or bool(ocr_boxes)
            for box in ocr_boxes:
                text = str(getattr(box, "name", ""))
                normalized = self._normal(text)
                if self._sale_name_matches(normalized, normalized_names):
                    name_boxes.append(box)

            if not name_boxes:
                if not saw_item_name:
                    last_reason = "全画面OCR未识别到商品名"
                continue
            saw_item_name = True

            name_boxes = self._deduplicate_ocr_boxes(name_boxes)
            if not name_boxes:
                last_reason = "全画面OCR商品名框几何无效"
                specific_local_failure = True
                continue

            ordered_names = sorted(
                name_boxes,
                key=lambda box: (
                    self._ocr_box_center(box) or (10**9, 10**9)
                )[1:],
            )
            candidates: list[SaleItemCandidate] = []
            for name_box in ordered_names:
                center = self._ocr_box_center(name_box)
                if center is None:
                    continue
                search_roi = self._sale_marker_search_roi(name_box, frame.shape)
                if search_roi is None:
                    last_reason = "商品名或局部模板框几何无效"
                    specific_local_failure = True
                    continue

                cached_boxes = next(
                    (
                        boxes
                        for searched_roi, boxes in searched_roi_results
                        if self._sale_roi_overlap_ratio(search_roi, searched_roi) >= 0.90
                    ),
                    None,
                )
                if cached_boxes is None:
                    if gray_frame is None:
                        gray_frame = to_gray(frame)
                    percent_boxes = self._deduplicate_ocr_boxes(
                        self._sale_template_percent_boxes(
                            frame,
                            search_roi,
                            gray_frame,
                        )
                    )
                    searched_roi_results.append((search_roi, percent_boxes))
                    roi_searches += 1
                    marker_hits += len(percent_boxes)
                else:
                    roi_left, roi_top, roi_width, roi_height = search_roi
                    roi_right = roi_left + roi_width
                    roi_bottom = roi_top + roi_height
                    percent_boxes = [
                        box
                        for box in cached_boxes
                        if roi_left <= box.x
                        and roi_top <= box.y
                        and box.x + box.width <= roi_right
                        and box.y + box.height <= roi_bottom
                    ]
                    reused_rois += 1

                if not percent_boxes:
                    if not specific_local_failure:
                        last_reason = "商品名左侧局部ROI未命中↑120%模板"
                    continue
                if len(percent_boxes) > 1 and (
                    percent_boxes[0].confidence - percent_boxes[1].confidence
                    < SALE_MARKER_MIN_MARGIN
                ):
                    last_reason = "商品名左侧局部ROI模板候选不唯一"
                    specific_local_failure = True
                    continue

                percent_box = percent_boxes[0]
                _, name_y, _, name_height = self._ocr_box_geometry(name_box)
                _, marker_y, _, marker_height = self._ocr_box_geometry(percent_box)
                vertical_overlap = min(
                    name_y + name_height,
                    marker_y + marker_height,
                ) - max(name_y, marker_y)
                # At non-16:9 sizes independent axis scaling can leave a marker
                # inside the padded ROI without overlapping the item-name row.
                if vertical_overlap <= 0:
                    last_reason = "局部↑120%模板与商品名框几何关系非法"
                    specific_local_failure = True
                    continue
                candidates.append(
                    SaleItemCandidate(
                        center=center,
                        name_box=name_box,
                        percent_box=percent_box,
                    )
                )

            if candidates:
                self._last_sale_name_seen = True
                self._last_sale_ocr_output = saw_ocr_output
                self._last_sale_page_empty = False
                self._last_sale_unavailable = False
                self._last_sale_reason = ""
                candidates.sort(
                    key=lambda candidate: (candidate.center[1], candidate.center[0])
                )
                self._status(
                    "出售120%局部模板",
                    f"{entry.item}搜索{roi_searches}个ROI，复用{reused_rois}个，"
                    f"命中{marker_hits}个模板。",
                )
                self._status(
                    "出售商品定位",
                    f"{entry.item}候选{len(candidates)}组："
                    + "、".join(str(candidate.center) for candidate in candidates),
                )
                return candidates

        self._status(
            "出售120%局部模板",
            f"{entry.item}搜索{roi_searches}个ROI，复用{reused_rois}个，"
            f"命中{marker_hits}个模板。",
        )
        self._last_sale_name_seen = saw_item_name
        self._last_sale_ocr_output = saw_ocr_output
        self._last_sale_page_empty = False
        if not saw_ocr_output:
            self._last_sale_unavailable = False
            self._last_sale_reason = "全画面OCR未返回任何文本"
        else:
            # OCR returned page text, but every height missed this item name: the
            # caller may treat the entry as unavailable. Once the name is seen,
            # any local template or geometry miss remains a recognition failure.
            self._last_sale_unavailable = not saw_item_name
            self._last_sale_reason = last_reason or "全画面OCR未形成可用商品候选"
        return []

    def _sale_name_signature(
        self,
        entry: CalendarEntry,
        frame: np.ndarray,
    ) -> tuple[tuple[str, int, int, int, int], ...]:
        """Return a page signature independent of 120% OCR success.

        The card list can reflow after a sale.  Including all matching name
        boxes lets completion detection notice that a card disappeared even
        when OCR temporarily misses one or more 120% markers.
        """

        names = (entry.item, *entry.aliases, *ITEM_ALIASES.get(entry.item, ()))
        normalized_names = tuple(self._normal(value) for value in names if value)
        signature = []
        matching_boxes = []
        for box in self.vision.ocr_boxes(
            frame,
            "出售商品列表",
            target_height=SALE_FULL_PAGE_OCR_TARGET_HEIGHT,
        ):
            text = str(getattr(box, "name", ""))
            normalized = self._normal(text)
            if not self._sale_name_matches(normalized, normalized_names):
                continue
            matching_boxes.append(box)
        for box in self._deduplicate_ocr_boxes(matching_boxes):
            normalized = self._normal(str(getattr(box, "name", "")))
            geometry = self._ocr_box_geometry(box)
            if geometry is None:
                continue
            x, y, box_width, box_height = geometry
            signature.append(
                (
                    normalized,
                    round(x),
                    round(y),
                    round(box_width),
                    round(box_height),
                )
            )
        return tuple(sorted(signature, key=lambda value: value[1:]))

    def _wait_sale_completion(
        self,
        entry: CalendarEntry,
        before_frame: np.ndarray,
        before_signature: tuple[tuple[str, int, int, int, int], ...],
        timeout: float = SALE_COMPLETION_TIMEOUT,
        *,
        before_toast_id: int | None = None,
    ) -> bool:
        """Wait until the sale dialog closes and the page advances.

        A transaction toast is preferred.  If it is missed, two consecutive
        post-sale OCR signatures that differ from the pre-sale page are an
        equivalent reflow confirmation.  A still-open sale dialog never
        counts as completion.
        """

        if before_toast_id is None:
            before_toast_id = self._sale_toast_id(before_frame)
        end_at = monotonic() + max(0.0, timeout)
        changed_signature: tuple[tuple[str, int, int, int, int], ...] | None = None
        stable_hits = 0
        last_text = ""
        while True:
            frame = self.vision.capture()
            dialog_text = self.vision.ocr_text(
                frame,
                "出售弹窗完成确认",
                relative_roi=SALE_DIALOG_REGION,
            )
            normalized_dialog = self._normal(dialog_text)
            last_text = dialog_text or last_text
            if "拥有" not in normalized_dialog and "可购买" not in normalized_dialog:
                full_text = self.vision.ocr_text(frame, "出售交易完成")
                normalized_full = self._normal(full_text)
                last_text = full_text or last_text
                toast_match = SALE_TOAST_ID_PATTERN.search(normalized_full)
                if toast_match is not None:
                    toast_id = self._quantity_from_text(toast_match.group(1))
                    if before_toast_id is None or (
                        toast_id is not None and toast_id > before_toast_id
                    ):
                        self._last_sale_toast_id = toast_id
                        self._status(
                            "出售完成确认",
                            f"{entry.item}:交易提示{toast_id or '-'}",
                        )
                        return True
                current_signature = self._sale_name_signature(entry, frame)
                if current_signature != before_signature:
                    if current_signature == changed_signature:
                        stable_hits += 1
                    else:
                        changed_signature = current_signature
                        stable_hits = 1
                    if stable_hits >= SALE_COMPLETION_STABLE_HITS:
                        self._status("出售完成确认", f"{entry.item}:页面重排")
                        return True
            if monotonic() >= end_at:
                break
            self.task.sleep(SALE_COMPLETION_INTERVAL)
        self.task.log_warning(
            f"卖：{entry.item}点击出售后未确认交易完成，OCR={last_text or '-'}。"
        )
        return False

    def _sale_toast_id(self, frame: np.ndarray) -> int | None:
        """Read the currently visible transaction toast sequence number."""

        try:
            text = self.vision.ocr_text(frame, "出售交易完成")
        except AttributeError:
            return None
        normalized = self._normal(text)
        matched = SALE_TOAST_ID_PATTERN.search(normalized)
        if matched is None:
            return None
        return self._quantity_from_text(matched.group(1))

    @staticmethod
    def _ocr_box_geometry(box) -> tuple[float, float, float, float] | None:
        x = getattr(box, "x", None)
        y = getattr(box, "y", None)
        width = getattr(box, "width", None)
        height = getattr(box, "height", None)
        if any(value is None for value in (x, y, width, height)):
            raw_box = getattr(box, "box", None)
            if raw_box is not None and len(raw_box) >= 4:
                x, y, width, height = raw_box[:4]
        if any(value is None for value in (x, y, width, height)):
            return None
        return float(x), float(y), float(width), float(height)

    @classmethod
    def _ocr_box_center(cls, box) -> tuple[int, int] | None:
        geometry = cls._ocr_box_geometry(box)
        if geometry is None:
            return None
        x, y, width, height = geometry
        return round(x + width / 2), round(y + height / 2)

    @classmethod
    def _ocr_box_contains(cls, box, point: tuple[int, int]) -> bool:
        geometry = cls._ocr_box_geometry(box)
        if geometry is None:
            return False
        x, y, width, height = geometry
        px, py = point
        return x <= px < x + width and y <= py < y + height

    @classmethod
    def _deduplicate_ocr_boxes(cls, boxes: list[object]) -> list[object]:
        unique = []
        seen: set[tuple[int, int, int, int]] = set()
        for box in boxes:
            geometry = cls._ocr_box_geometry(box)
            if geometry is None:
                continue
            x, y, width, height = geometry
            if width <= 0 or height <= 0:
                continue
            key = round(x), round(y), round(width), round(height)
            if key in seen:
                continue
            seen.add(key)
            unique.append(box)
        return unique

    def _wait_sale_dialog_item(
        self,
        entry: CalendarEntry,
        timeout: float = SALE_DIALOG_TIMEOUT,
    ) -> bool:
        names = (entry.item, *entry.aliases, *ITEM_ALIASES.get(entry.item, ()))
        normalized_names = tuple(self._normal(value) for value in names if value)
        end_at = monotonic() + max(0.0, timeout)
        while True:
            text = self.vision.ocr_text(
                self.vision.capture(),
                "出售弹窗商品标题",
                relative_roi=SALE_DIALOG_TITLE_REGION,
            )
            normalized = self._normal(text)
            if self._sale_name_matches(normalized, normalized_names):
                return True
            if monotonic() >= end_at:
                return False
            self.task.sleep(SALE_OCR_INTERVAL)

    def _wait_owned_quantity(self, timeout: float = SALE_DIALOG_TIMEOUT) -> int | None:
        end_at = monotonic() + max(0.0, timeout)
        while True:
            text = self.vision.ocr_text(
                self.vision.capture(),
                "出售弹窗库存",
                relative_roi=SALE_DIALOG_REGION,
            )
            normalized = self.vision.simplify(text)
            matched = SALE_OWNED_PATTERN.search(normalized)
            if matched is not None:
                quantity = self._quantity_from_text(matched.group(1))
                if quantity is not None:
                    return quantity
            if monotonic() >= end_at:
                return None
            self.task.sleep(SALE_OCR_INTERVAL)

    def _wait_available_quantity(
        self,
        timeout: float = SALE_DIALOG_TIMEOUT,
    ) -> int | None:
        end_at = monotonic() + max(0.0, timeout)
        while True:
            text = self.vision.ocr_text(
                self.vision.capture(),
                "出售弹窗可购买数量",
                relative_roi=SALE_DIALOG_REGION,
            )
            normalized = self.vision.simplify(text)
            matched = SALE_AVAILABLE_PATTERN.search(normalized)
            if matched is not None:
                quantity = self._quantity_from_text(matched.group(1))
                if quantity is not None and quantity > 0:
                    return quantity
            if monotonic() >= end_at:
                return None
            self.task.sleep(SALE_OCR_INTERVAL)

    def _wait_selected_sale_quantity(
        self,
        expected: int | None,
        timeout: float = SALE_DIALOG_TIMEOUT,
    ) -> bool:
        end_at = monotonic() + max(0.0, timeout)
        while True:
            text = self.vision.ocr_text(
                self.vision.capture(),
                "出售弹窗已选数量",
                relative_roi=SALE_DIALOG_REGION,
            )
            selected = self._selected_quantity_from_text(text)
            if selected is not None and selected > 0:
                if expected is None or selected == expected:
                    self._status("出售已选数量", str(selected))
                    return True
            if monotonic() >= end_at:
                return False
            self.task.sleep(SALE_OCR_INTERVAL)

    @staticmethod
    def _selected_quantity_from_text(text: str) -> int | None:
        normalized = str(text).replace("，", ",")
        values = []
        for matched in re.finditer(r"([0-9][0-9,.]*)\s*个", normalized):
            prefix = normalized[max(0, matched.start() - 4) : matched.start()]
            if "拥有" in prefix or "可购买" in prefix:
                continue
            quantity = SellFlowMixin._quantity_from_text(matched.group(1))
            if quantity is not None:
                values.append(quantity)
        return max(values, default=None)

    @staticmethod
    def _quantity_from_text(text: str) -> int | None:
        values = []
        for matched in re.findall(r"[0-9][0-9,，.]*", str(text)):
            digits = re.sub(r"\D", "", matched)
            if digits:
                values.append(int(digits))
        return max(values, default=None)

    def _choose_sale_quantity(self, entry: CalendarEntry, owned: int) -> bool:
        if entry.reserve > 0:
            slider_point = self._sale_slider_point(owned, entry.reserve)
            if slider_point is None:
                return False
            amount = owned - entry.reserve
            self.task.operate_click(*slider_point, after_sleep=0.5)
            self._status("出售保留量", f"{entry.item}:约{entry.reserve}")
            self.task.log_info(
                f"卖：{entry.item}拥有{owned}个，滑条选择出售约{amount}个，"
                f"目标保留约{entry.reserve}个。"
            )
            return True
        if bool(self.task.config.get("出售保险", False)):
            self.task.operate_click(*SALE_MIN_POINT, after_sleep=0.5)
        else:
            self.task.operate_click(*SALE_MAX_POINT, after_sleep=0.5)
        return True

    @staticmethod
    def _sale_slider_point(
        owned: int, reserve: int
    ) -> tuple[float, float] | None:
        """Map the desired sale amount onto the one-to-all sale slider."""

        if owned <= reserve or owned <= 0:
            return None
        amount = owned - reserve
        ratio = 0.0 if owned == 1 else (amount - 1) / (owned - 1)
        ratio = max(0.0, min(1.0, ratio))
        left, top, right, bottom = SALE_SLIDER_REGION
        return left + ((right - left) * ratio), (top + bottom) / 2

    @staticmethod
    def _box_values(box) -> tuple[float, float, float, float] | None:
        values = tuple(getattr(box, key, None) for key in ("x", "y", "width", "height"))
        if any(value is None for value in values):
            raw_box = getattr(box, "box", None)
            if raw_box is not None and len(raw_box) >= 4:
                values = tuple(raw_box[:4])
        if any(value is None for value in values):
            return None
        return tuple(float(value) for value in values)
