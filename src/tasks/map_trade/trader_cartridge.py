from __future__ import annotations

from difflib import SequenceMatcher
from time import monotonic

import numpy as np

from src.tasks.map_trade.data import (
    SHOP_CARTRIDGE_BRIGHTNESS,
    SHOP_CARTRIDGE_PAGES,
    SHOP_PURCHASE_REFERENCES,
    shop_purchase_reference,
)
from src.tasks.map_trade.models import (
    MatchResult,
    TemplateSpec,
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
    SALE_AVAILABLE_PATTERN,
    SALE_CLOSE_POINT,
    SALE_COMPLETION_INTERVAL,
    SALE_COMPLETION_STABLE_HITS,
    SALE_COMPLETION_TIMEOUT,
    SALE_CONFIRM_POINT,
    SALE_DIALOG_REGION,
    SALE_DIALOG_TIMEOUT,
    SALE_DIALOG_TITLE_REGION,
    SALE_FULL_PAGE_OCR_TARGET_HEIGHT,
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
    SHOP_CARTRIDGE_OCR_RELATIVE_ROI,
    SHOP_CARTRIDGE_OCR_ROW_LINK_RADIUS,
    SHOP_CARTRIDGE_RECOGNITION_REGION,
    SHOP_CARTRIDGE_ROW_CLUSTER_RADIUS,
    SHOP_CARTRIDGE_SCALE_RATIOS,
    SHOP_CARTRIDGE_SCROLL_POINT,
    SHOP_CARTRIDGE_SCROLL_REGION,
    SHOP_DOWN_SCROLL_INTERVAL,
    SHOP_FIRST_PAGE_MAX_UP_SCROLLS,
    SHOP_MODE_INTERVAL,
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


class ShopCartridgeNavigationMixin:
    def rebuild_favorites(self) -> bool:
        if not self._reset_shop_to_first_page():
            return False

        for page in SHOP_CARTRIDGE_PAGES:
            if page.scroll_down_from_previous:
                self._scroll_shop_cartridges(
                    scroll_amount=-1,
                    count=page.scroll_down_from_previous,
                    interval=SHOP_DOWN_SCROLL_INTERVAL,
                    after_sleep=0.5,
                )
            if not self._wait_for_shop_page(page.confirmation_shop_ids):
                labels = "、".join(
                    SHOP_PURCHASE_REFERENCES[value].label
                    for value in page.confirmation_shop_ids
                )
                self.task.log_warning(
                    f"买：向下滚动后未确认第{page.page_number}页边界卡带：{labels}。"
                )
                return False

            for shop_id in page.shop_ids:
                reference = SHOP_PURCHASE_REFERENCES[shop_id]
                if self.progress.favorite_card_complete(shop_id):
                    self.task.log_info(f"买：{reference.label}已有本次完成记录，跳过。")
                    continue
                if not self._select_purchase_cartridge(shop_id):
                    self.task.log_warning(f"买：未能选择{reference.label}。")
                    return False
                if not self._align_unfavorited_points(shop_id):
                    self.task.log_warning(f"买：{reference.label}空收藏位置核对失败。")
                    return False
                self.progress.mark_favorite_card(shop_id)
                self._status("收藏重建进度", f"{reference.label} 已完成")

        self.progress.mark_favorites_built()
        self.task.log_info("买：31张商品卡带的空收藏位置已全部核对完成。")
        return True

    def _reset_shop_to_first_page(self) -> bool:
        for attempt in range(SHOP_FIRST_PAGE_MAX_UP_SCROLLS + 1):
            frame = self.vision.capture()
            if self._cartridge_visible("S1", frame):
                self._status("商品卡带页", "第1页")
                return True
            if attempt >= SHOP_FIRST_PAGE_MAX_UP_SCROLLS:
                break
            self._scroll_shop_cartridges(
                scroll_amount=1,
                count=1,
                interval=0.0,
                after_sleep=SHOP_UP_SCROLL_RECOGNITION_INTERVAL,
            )
        self.task.log_warning("买：向上逐格滚动后仍未识别到剧情游戏卡1。")
        return False

    def _scroll_shop_cartridges(
        self,
        scroll_amount: int,
        count: int,
        interval: float,
        after_sleep: float,
    ) -> None:
        self.task.scroll_client(
            SHOP_CARTRIDGE_SCROLL_POINT,
            scroll_amount,
            count=count,
            interval=interval,
            after_sleep=after_sleep,
        )

    def _wait_for_shop_page(
        self,
        confirmation_shop_ids: tuple[str, ...],
        timeout: float = 4.0,
        interval: float = 0.25,
    ) -> bool:
        end_at = monotonic() + max(0.0, timeout)
        while monotonic() <= end_at:
            frame = self.vision.capture()
            confirmed = self._confirmed_shop_cartridge_detections(frame)
            if all(shop_id in confirmed for shop_id in confirmation_shop_ids):
                self._status("商品卡带页确认", "、".join(confirmation_shop_ids))
                return True
            self.task.sleep(interval)
        return False

    def _cartridge_spec(self, shop_id: str) -> TemplateSpec:
        reference = SHOP_PURCHASE_REFERENCES[shop_id]
        return TemplateSpec(
            name=reference.label,
            file_name=reference.cartridge_templates[0],
            threshold=SHOP_CARTRIDGE_CONFIRM_SCORE,
            relative_roi=SHOP_CARTRIDGE_RECOGNITION_REGION,
            scale_ratios=SHOP_CARTRIDGE_SCALE_RATIOS,
        )

    @staticmethod
    def _shop_cartridge_chapter_name(shop_id: str) -> str:
        label = SHOP_PURCHASE_REFERENCES[shop_id].label
        return label.split(" ", 1)[1] if " " in label else ""

    @staticmethod
    def _shop_cartridge_text_similarity(actual: str, expected: str) -> float:
        actual_normalized = normalize_text(actual)
        expected_normalized = normalize_text(expected)
        if not actual_normalized or not expected_normalized:
            return 0.0
        return SequenceMatcher(None, actual_normalized, expected_normalized).ratio()

    @staticmethod
    def _shop_cartridge_ocr_confidence(box) -> float:
        raw = getattr(box, "confidence", getattr(box, "score", 0.0))
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return 0.0
        if value > 1.0:
            value /= 100.0
        return max(0.0, min(1.0, value))

    def _shop_cartridge_ocr_rows(
        self,
        frame: np.ndarray,
    ) -> tuple[ShopCartridgeOcrRow, ...]:
        height, width = frame.shape[:2]
        scale_x = width / 1920
        scale_y = height / 1080
        texts: list[ShopCartridgeOcrText] = []
        for box in self.vision.ocr_boxes(
            frame,
            "商品卡带竞争",
            relative_roi=SHOP_CARTRIDGE_OCR_RELATIVE_ROI,
        ):
            try:
                x = float(box.x)
                y = float(box.y)
                box_width = float(box.width)
                box_height = float(box.height)
            except (AttributeError, TypeError, ValueError):
                continue
            texts.append(
                ShopCartridgeOcrText(
                    text=str(getattr(box, "name", "")),
                    confidence=self._shop_cartridge_ocr_confidence(box),
                    center=(x + box_width / 2, y + box_height / 2),
                )
            )

        rows: list[ShopCartridgeOcrRow] = []
        for category in texts:
            match = SHOP_CARTRIDGE_CATEGORY_PATTERN.search(
                normalize_text(category.text)
            )
            if match is None:
                continue
            shop_id = (
                f"{SHOP_CARTRIDGE_CATEGORY_PREFIX[match.group(1)]}"
                f"{int(match.group(2))}"
            )
            if shop_id not in SHOP_PURCHASE_REFERENCES:
                continue
            expected_name = self._shop_cartridge_chapter_name(shop_id)
            chapter_candidates = [
                value
                for value in texts
                if 8 * scale_y
                <= value.center[1] - category.center[1]
                <= 45 * scale_y
                and abs(value.center[0] - category.center[0]) <= 80 * scale_x
                and SHOP_CARTRIDGE_CATEGORY_PATTERN.search(
                    normalize_text(value.text)
                )
                is None
            ]
            chapter = max(
                chapter_candidates,
                key=lambda value: (
                    self._shop_cartridge_text_similarity(value.text, expected_name),
                    value.confidence,
                ),
                default=None,
            )
            rows.append(
                ShopCartridgeOcrRow(
                    shop_id=shop_id,
                    category=category,
                    chapter=chapter,
                    name_similarity=(
                        self._shop_cartridge_text_similarity(
                            chapter.text,
                            expected_name,
                        )
                        if chapter is not None
                        else 0.0
                    ),
                )
            )
        return tuple(sorted(rows, key=lambda value: value.category.center[1]))

    def _shop_cartridge_template_candidates(
        self,
        frame: np.ndarray,
    ) -> tuple[ShopCartridgeTemplateCandidate, ...]:
        height, width = frame.shape[:2]
        peak_radius = max(5, round(20 * min(width / 1920, height / 1080)))
        candidates: list[ShopCartridgeTemplateCandidate] = []
        for shop_id in SHOP_PURCHASE_REFERENCES:
            matches = self.vision.match_all(
                frame,
                self._cartridge_spec(shop_id),
                minimum_score=SHOP_CARTRIDGE_CANDIDATE_SCORE,
                peak_radius=peak_radius,
                max_results=30,
            )
            candidates.extend(
                ShopCartridgeTemplateCandidate(shop_id, result)
                for result in matches
            )
        return tuple(candidates)

    def _shop_cartridge_competition(
        self,
        frame: np.ndarray,
    ) -> tuple[ShopCartridgeDetection, ...]:
        height = frame.shape[0]
        cluster_radius = max(
            5,
            round(SHOP_CARTRIDGE_ROW_CLUSTER_RADIUS * height / 1080),
        )
        ocr_link_radius = max(
            5,
            round(SHOP_CARTRIDGE_OCR_ROW_LINK_RADIUS * height / 1080),
        )
        ocr_rows = self._shop_cartridge_ocr_rows(frame)
        clusters: list[list[ShopCartridgeTemplateCandidate]] = []
        for candidate in sorted(
            self._shop_cartridge_template_candidates(frame),
            key=lambda value: value.result.score,
            reverse=True,
        ):
            for cluster in clusters:
                if (
                    abs(candidate.result.center[1] - cluster[0].result.center[1])
                    <= cluster_radius
                ):
                    cluster.append(candidate)
                    break
            else:
                clusters.append([candidate])

        detections: list[ShopCartridgeDetection] = []
        for cluster in clusters:
            best_by_shop: dict[str, ShopCartridgeTemplateCandidate] = {}
            for candidate in cluster:
                current = best_by_shop.get(candidate.shop_id)
                if current is None or candidate.result.score > current.result.score:
                    best_by_shop[candidate.shop_id] = candidate
            ranked = sorted(
                best_by_shop.values(),
                key=lambda value: value.result.score,
                reverse=True,
            )
            if not ranked or ranked[0].result.score < SHOP_CARTRIDGE_CONFIRM_SCORE:
                continue
            nearest_ocr = min(
                ocr_rows,
                key=lambda value: abs(
                    value.category.center[1] - ranked[0].result.center[1]
                ),
                default=None,
            )
            if nearest_ocr is not None and (
                abs(nearest_ocr.category.center[1] - ranked[0].result.center[1])
                > ocr_link_radius
            ):
                nearest_ocr = None
            detections.append(
                ShopCartridgeDetection(
                    best=ranked[0],
                    runner_up=ranked[1] if len(ranked) > 1 else None,
                    ocr=nearest_ocr,
                )
            )
        return tuple(
            sorted(detections, key=lambda value: value.best.result.center[1])
        )

    def _shop_cartridge_detection_passes(
        self,
        detection: ShopCartridgeDetection,
    ) -> bool:
        ocr = detection.ocr
        return (
            self._cartridge_match_passes(
                detection.best.result,
                self._cartridge_spec(detection.best.shop_id),
            )
            and detection.margin >= SHOP_CARTRIDGE_MIN_MARGIN
            and ocr is not None
            and ocr.shop_id == detection.best.shop_id
            and ocr.category.confidence >= SHOP_CARTRIDGE_OCR_MIN_CONFIDENCE
            and ocr.name_similarity >= SHOP_CARTRIDGE_NAME_MIN_SIMILARITY
        )

    def _confirmed_shop_cartridge_detections(
        self,
        frame: np.ndarray,
    ) -> dict[str, ShopCartridgeDetection]:
        candidates: dict[str, list[ShopCartridgeDetection]] = {}
        for detection in self._shop_cartridge_competition(frame):
            runner = detection.runner_up.shop_id if detection.runner_up else "-"
            ocr_id = detection.ocr.shop_id if detection.ocr else "-"
            name_similarity = detection.ocr.name_similarity if detection.ocr else 0.0
            self._status(
                f"卡带竞争 {detection.best.shop_id}",
                (
                    f"match={detection.best.result.score:.3f}, runner={runner}, "
                    f"margin={detection.margin:.3f}, OCR={ocr_id}, "
                    f"name={name_similarity:.3f}"
                ),
            )
            if self._shop_cartridge_detection_passes(detection):
                candidates.setdefault(detection.best.shop_id, []).append(detection)

        confirmed: dict[str, ShopCartridgeDetection] = {}
        for shop_id, detections in candidates.items():
            if len(detections) == 1:
                confirmed[shop_id] = detections[0]
            else:
                self.task.log_warning(
                    f"买：卡带竞争结果中{shop_id}出现{len(detections)}个有效位置，拒绝猜测。"
                )
        return confirmed

    def _cartridge_visible(self, shop_id: str, frame: np.ndarray) -> bool:
        return shop_id in self._confirmed_shop_cartridge_detections(frame)

    def _cartridge_match_passes(
        self,
        result: MatchResult,
        spec: TemplateSpec,
    ) -> bool:
        # Cartridge crops are tightly calibrated local references.  Keep their
        # dedicated floor even when the task-wide threshold is configured lower,
        # otherwise a cartridge that is only partly visible at a page edge can be
        # mistaken for a complete row entry.
        return result.score >= max(spec.threshold, self.vision.threshold_for(spec))

    def _wait_for_cartridge_match(
        self,
        shop_id: str,
        timeout: float = 3.0,
        interval: float = 0.25,
    ) -> tuple[np.ndarray, TemplateSpec, MatchResult] | None:
        end_at = monotonic() + max(0.0, timeout)
        while monotonic() <= end_at:
            frame = self.vision.capture()
            detection = self._confirmed_shop_cartridge_detections(frame).get(shop_id)
            if detection is not None:
                return frame, self._cartridge_spec(shop_id), detection.best.result
            self.task.sleep(interval)
        return None

    def _select_purchase_cartridge(self, shop_id: str) -> bool:
        found = self._wait_for_cartridge_match(shop_id)
        if found is None:
            return False
        frame, spec, result = found
        self.vision.click_client(result.center, frame.shape, after_sleep=0.5)

        end_at = monotonic() + 4.0
        while monotonic() <= end_at:
            selected_frame = self.vision.capture()
            selected = self._confirmed_shop_cartridge_detections(selected_frame).get(
                shop_id
            )
            if selected is not None:
                brightness = self.vision.template_brightness_ratio(
                    selected_frame,
                    spec,
                    selected.best.result,
                    minimum_template_gray=SHOP_CARTRIDGE_BRIGHTNESS.foreground_min_gray,
                )
                self._status(f"卡带亮度 {shop_id}", f"{brightness:.3f}")
                if SHOP_CARTRIDGE_BRIGHTNESS.is_selected(brightness):
                    return True
            self.task.sleep(0.25)
        return False

    def _select_shop_cartridge_from_first_page(self, shop_id: str) -> bool:
        if shop_id not in SHOP_PURCHASE_REFERENCES:
            self.task.log_warning(f"卖：本地商品卡带表缺少 {shop_id}。")
            return False
        if not self._reset_shop_to_first_page():
            return False

        for page in SHOP_CARTRIDGE_PAGES:
            if page.scroll_down_from_previous:
                self._scroll_shop_cartridges(
                    scroll_amount=-1,
                    count=page.scroll_down_from_previous,
                    interval=SHOP_DOWN_SCROLL_INTERVAL,
                    after_sleep=0.5,
                )
            if not self._wait_for_shop_page(page.confirmation_shop_ids):
                labels = "、".join(
                    SHOP_PURCHASE_REFERENCES[value].label
                    for value in page.confirmation_shop_ids
                )
                self.task.log_warning(
                    f"卖：向下滚动后未确认第{page.page_number}页边界卡带：{labels}。"
                )
                return False
            if shop_id in page.shop_ids:
                return self._select_purchase_cartridge(shop_id)

        self.task.log_warning(f"卖：本地商品卡带分页表未覆盖 {shop_id}。")
        return False

    def _align_unfavorited_points(self, shop_id: str) -> bool:
        reference = SHOP_PURCHASE_REFERENCES[shop_id]
        for slot, point in reference.unfavorited_points:
            frame = self.vision.capture()
            if self._gray_star_present(frame, slot, point):
                self._status(f"{shop_id} 空收藏#{slot}", "已是灰星")
                continue
            self._status(f"{shop_id} 空收藏#{slot}", "点击取消收藏")
            self.task.operate_click(*point, after_sleep=STAR_POST_CLICK_DELAY)
            if not self._wait_for_gray_star(slot, point):
                self.task.log_warning(
                    f"买：{reference.label} #{slot} 点击后未确认灰星或移除提示。"
                )
                return False
        return True

    def _wait_for_gray_star(
        self,
        slot: int,
        point: tuple[float, float],
    ) -> bool:
        toast_seen = False
        for attempt in range(STAR_VERIFY_ATTEMPTS):
            frame = self.vision.capture()
            if self._gray_star_present(frame, slot, point):
                return True
            if not toast_seen:
                text = self.vision.ocr_text(frame, "取消收藏成功提示")
                normalized = normalize_text(self.vision.simplify(text))
                self._status(f"{slot} 取消收藏提示", normalized or "-")
                if STAR_ADD_TOAST_KEYWORD in normalized:
                    self.task.log_warning(
                        f"买：收藏位#{slot} 点击后出现加入收藏提示，取消收藏未生效。"
                    )
                    return False
                if STAR_REMOVE_TOAST_KEYWORD in normalized:
                    toast_seen = True
            if attempt + 1 < STAR_VERIFY_ATTEMPTS:
                self.task.sleep(STAR_VERIFY_INTERVAL)
        return toast_seen

    def _gray_star_present(
        self,
        frame: np.ndarray,
        slot: int,
        point: tuple[float, float],
    ) -> bool:
        half_x = STAR_ROI_HALF_SIZE_X / 1920
        half_y = STAR_ROI_HALF_SIZE_Y / 1080
        spec = TemplateSpec(
            name=f"灰星#{slot}",
            file_name=STAR_TEMPLATE_FILE,
            threshold=STAR_TEMPLATE_THRESHOLD,
            green_mask=True,
            relative_roi=(
                max(0.0, point[0] - half_x),
                max(0.0, point[1] - half_y),
                min(1.0, point[0] + half_x),
                min(1.0, point[1] + half_y),
            ),
            scale_ratios=SHOP_CARTRIDGE_SCALE_RATIOS,
            min_pixel_score=STAR_PIXEL_THRESHOLD,
        )
        result = self.vision.match(frame, spec)
        self._status(
            f"灰星#{slot}",
            f"match={result.score:.3f}, pixel={result.pixel_score:.3f}",
        )
        return self.vision.passes(result, spec) and not self.vision.star_is_yellow(
            frame, result
        )

    def select_shop_tab(self, shop: str) -> bool:
        try:
            reference = shop_purchase_reference(shop)
        except KeyError:
            self.task.log_warning(f"卖：价表商店没有本地商品卡带映射：{shop}。")
            return False
        return self._select_shop_cartridge_from_first_page(reference.shop_id)

