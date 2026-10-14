from __future__ import annotations

from time import monotonic

import numpy as np

from src.tasks.map_trade.calendar import (
    PURCHASE_STOCK_REFRESH_HOUR,
    purchase_stock_date,
)
from src.tasks.map_trade.models import (
    ScreenState,
)
from src.tasks.map_trade.trader_constants import (
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
    BUY_TO_SELL_INTERVAL,
    BUY_TO_SELL_POST_CLICK_DELAY,
    BUY_TO_SELL_PRE_CLICK_DELAY,
    BUY_TO_SELL_SOLD_OUT_STABLE_HITS,
    BUY_TO_SELL_SOLD_OUT_TEMPLATE,
    BUY_TO_SELL_TIMEOUT,
    SELL_MODE_POINT,
    SHOP_MODE_TITLE_REGION,
)
from src.tasks.map_trade.vision import normalize_text

# 点击"购买全部收藏"后等待确认弹窗出现前的停顿。
BUY_ALL_FAVORITES_CLICK_AFTER_SLEEP = 0.3


class BuyFlowMixin:
    def run_buy(self) -> bool:
        from src.tasks import run_history

        stock_date = purchase_stock_date(self._current_market_time())
        self._status("购买库存日期", stock_date.isoformat())
        self.task.log_info(
            f"买：按{stock_date.isoformat()}库存批次执行（每日"
            f"{PURCHASE_STOCK_REFRESH_HOUR:02d}:00刷新）。"
        )
        entered = self.navigator.enter_q_sp6_buy_flow()
        if not entered.success:
            self.task.log_warning(f"买：{entered.message}")
            return False
        if entered.state != ScreenState.SHOP:
            self.task.log_warning(
                f"买：砍价后状态为{entered.state.value}，未确认商店页，停止购买。"
            )
            return False
        purchase_state = self._wait_for_buy_all_favorites_state()
        if purchase_state is None:
            return False
        if purchase_state[0] is None:
            run_history.save_stage(self.task, "购买全部收藏", "done",
                                   evidence={"stock_date": stock_date.isoformat()})
            self._buy_completed_in_current_shop = True
            return True
        if run_history.read_stage(self.task, "购买全部收藏").get("state") == "pending":
            completed = self.buy_all_favorites()
            self._buy_completed_in_current_shop = completed
            return completed
        rebuild_cycle = str(self.task.config.get("收藏重建周期", "每周"))
        every_run = rebuild_cycle == "每次"
        if rebuild_cycle == "永不":
            self.task.log_info("买：收藏重建周期设为永不，跳过收藏调整。")
        elif not self.progress.should_rebuild_favorites(every_run=every_run):
            self.task.log_info("买：本周收藏已经按本地表重建，跳过收藏调整。")
        else:
            if every_run:
                self.progress.clear_favorite_cards()
            if not self.rebuild_favorites():
                return False
        completed = self.buy_all_favorites()
        self._buy_completed_in_current_shop = completed
        return completed

    def _switch_from_completed_buy_to_sell(
        self,
        timeout: float = BUY_TO_SELL_TIMEOUT,
    ) -> bool:
        end_at = monotonic() + max(0.0, timeout)
        sold_out_hits = 0
        last_title = ""
        last_match = None
        while True:
            frame = self.vision.capture()
            title = self.vision.ocr_text(
                frame,
                "商店买卖页标题",
                relative_roi=SHOP_MODE_TITLE_REGION,
            )
            last_title = title or last_title
            normalized_title = normalize_text(self.vision.simplify(title))
            if "出售" in normalized_title:
                self._status("商店页面", "出售")
                self.task.log_info("卖：购买完成后已处于出售页，直接继续出售。")
                return True

            last_match = self.vision.match(frame, BUY_TO_SELL_SOLD_OUT_TEMPLATE)
            matched = self.vision.passes(last_match, BUY_TO_SELL_SOLD_OUT_TEMPLATE)
            sold_out_hits = sold_out_hits + 1 if matched else 0
            self._status(
                "买后售罄模板",
                (
                    f"{'命中' if matched else '未命中'} "
                    f"{sold_out_hits}/{BUY_TO_SELL_SOLD_OUT_STABLE_HITS}; "
                    f"m={last_match.score:.3f}, p={last_match.pixel_score:.3f}, "
                    f"z={last_match.zncc_score:.3f}"
                ),
            )
            if sold_out_hits >= BUY_TO_SELL_SOLD_OUT_STABLE_HITS:
                self.task.log_info(
                    "卖：售罄模板连续命中，等待0.5秒后点击出售入口。"
                )
                self.task.sleep(BUY_TO_SELL_PRE_CLICK_DELAY)
                self.task.operate_click(
                    *SELL_MODE_POINT,
                    after_sleep=BUY_TO_SELL_POST_CLICK_DELAY,
                )
                return self._ensure_sell_page()
            if monotonic() >= end_at:
                break
            self.task.sleep(BUY_TO_SELL_INTERVAL)
        score_text = (
            "-"
            if last_match is None
            else (
                f"m={last_match.score:.3f}, p={last_match.pixel_score:.3f}, "
                f"z={last_match.zncc_score:.3f}"
            )
        )
        self.task.log_warning(
            "卖：买后等待售罄模板稳定命中超时，未切换出售页，"
            f"title={last_title or '-'}, match={score_text}。"
        )
        return False

    def buy_all_favorites(self) -> bool:
        from src.tasks import run_history

        record = run_history.read_stage(self.task, "购买全部收藏")
        located = self._wait_for_buy_all_favorites_state()
        if located is None:
            self.task.log_warning("买：商店页面未稳定显示可购买按钮或售罄状态。")
            return False
        button_center, frame = located
        if button_center is None:
            run_history.save_stage(self.task, "购买全部收藏", "done",
                                   evidence={"result": "已售罄"})
            return True
        if record.get("state") == "pending":
            if self._wait_for_purchase_completion():
                current = self._wait_for_buy_all_favorites_state()
                if current is not None and current[0] is None:
                    run_history.save_stage(self.task, "购买全部收藏", "done",
                                           evidence={"result": "前次购买已核验售罄"})
                    return True
            self.task.log_warning("买：前次购买提交结果未知，售罄/完成未确认，禁止重复购买。")
            return False
        self._status(
            "一键购买全部收藏按钮点击中心",
            f"center=({button_center[0]},{button_center[1]})",
        )
        self.vision.click_client(
            button_center, frame.shape, after_sleep=BUY_ALL_FAVORITES_CLICK_AFTER_SLEEP
        )
        if not self._wait_for_purchase_confirmation():
            self.task.log_warning(
                "买：点击一键购买全部收藏后，未同时识别到确认标题和询问文字。"
            )
            return False
        self.task.log_info(
            f"买：购买确认弹窗OCR完成，等待{BUY_CONFIRM_PRE_CLICK_DELAY:.1f}秒后点击确认。"
        )
        self.task.sleep(BUY_CONFIRM_PRE_CLICK_DELAY)
        run_history.save_stage(self.task, "购买全部收藏", "pending",
                               evidence={"target": "全部收藏"})
        self.task.operate_click(
            *BUY_CONFIRM_POINT,
            after_sleep=BUY_CONFIRM_POST_CLICK_DELAY,
        )
        if not self._wait_for_purchase_completion():
            self.task.log_warning(
                "买：确认点击后未观察到售罄模板或购买完成页面，未记为购买成功。"
            )
            return False
        self.task.log_info("买：已确认购买全部收藏商品。")
        run_history.save_stage(self.task, "购买全部收藏", "done",
                               evidence={"result": "购买完成已确认"})
        return True

    def _wait_for_purchase_completion(
        self,
        timeout: float = BUY_CONFIRM_TIMEOUT,
    ) -> bool:
        """Require bounded post-confirm evidence before closing the buy phase."""

        end_at = monotonic() + max(0.0, timeout)
        sold_out_hits = 0
        success_text_hits = 0
        last_text = ""
        capture = getattr(self.vision, "capture", None)
        match_fn = getattr(self.vision, "match", None)
        passes_fn = getattr(self.vision, "passes", None)
        simplify = getattr(self.vision, "simplify", lambda value: value)
        if not callable(capture) or not callable(match_fn) or not callable(passes_fn):
            return False
        while True:
            frame = capture()
            text = ""
            ocr_text = getattr(self.vision, "ocr_text", None)
            if callable(ocr_text):
                try:
                    text = ocr_text(frame, "购买完成确认", relative_roi=SHOP_MODE_TITLE_REGION)
                except (AttributeError, TypeError, ValueError):
                    text = ""
            normalized = normalize_text(simplify(text))
            last_text = text or last_text
            if any(keyword in normalized for keyword in ("购买成功", "购买完成", "已购买")):
                success_text_hits += 1
                self._status(
                    "购买完成确认",
                    f"{success_text_hits}/{BUY_TO_SELL_SOLD_OUT_STABLE_HITS}",
                )
                if success_text_hits >= BUY_TO_SELL_SOLD_OUT_STABLE_HITS:
                    return True
            else:
                success_text_hits = 0
            match = match_fn(frame, BUY_TO_SELL_SOLD_OUT_TEMPLATE)
            passed = passes_fn(match, BUY_TO_SELL_SOLD_OUT_TEMPLATE)
            sold_out_hits = sold_out_hits + 1 if passed else 0
            self._status(
                "购买完成售罄确认",
                f"{'命中' if passed else '未命中'} "
                f"{sold_out_hits}/{BUY_TO_SELL_SOLD_OUT_STABLE_HITS}",
            )
            if sold_out_hits >= BUY_TO_SELL_SOLD_OUT_STABLE_HITS:
                return True
            if monotonic() >= end_at:
                break
            self.task.sleep(BUY_CONFIRM_INTERVAL)
        self.task.log_warning(
            f"买：购买确认后完成证据超时，OCR={last_text or '-'}。"
        )
        return False

    def _wait_for_buy_all_favorites_state(
        self,
        timeout: float = BUY_ALL_FAVORITES_TIMEOUT,
    ) -> tuple[tuple[int, int] | None, np.ndarray] | None:
        """返回按钮与当前帧；中心为 None 表示售罄，整体 None 表示超时。"""
        end_at = monotonic() + max(0.0, timeout)
        consecutive_hits = 0
        sold_out_hits = 0
        last_text = ""
        last_center: tuple[int, int] | None = None
        last_frame: np.ndarray | None = None
        expected = normalize_text(self.vision.simplify(BUY_ALL_FAVORITES_KEYWORD))
        while True:
            frame = self.vision.capture()
            boxes = self.vision.ocr_boxes(
                frame,
                "一键购买全部收藏按钮",
            )
            texts = [str(getattr(box, "name", "")) for box in boxes]
            text = " ".join(value for value in texts if value)
            self._status("一键购买全部收藏按钮 OCR", text or "-")
            last_text = text or last_text
            sold_out = self.vision.match(frame, BUY_TO_SELL_SOLD_OUT_TEMPLATE)
            sold_out_passed = self.vision.passes(sold_out, BUY_TO_SELL_SOLD_OUT_TEMPLATE)
            sold_out_hits = sold_out_hits + 1 if sold_out_passed else 0
            self._status(
                "买前售罄模板",
                (
                    f"{'命中' if sold_out_passed else '未命中'} "
                    f"{sold_out_hits}/{BUY_TO_SELL_SOLD_OUT_STABLE_HITS}; "
                    f"m={sold_out.score:.3f}, p={sold_out.pixel_score:.3f}, "
                    f"z={sold_out.zncc_score:.3f}"
                ),
            )
            if sold_out_hits >= BUY_TO_SELL_SOLD_OUT_STABLE_HITS:
                self.task.log_info("买：售罄模板连续命中，判定本日库存已购买，跳过购买。")
                return None, frame
            matched_center = next(
                (
                    center
                    for box in boxes
                    if expected
                    in normalize_text(
                        self.vision.simplify(str(getattr(box, "name", "")))
                    )
                    if (center := self._ocr_box_center(box)) is not None
                ),
                None,
            )
            # 售罄时按钮仍可能显示，任一售罄帧都必须阻止按钮点击。
            if matched_center is not None and not sold_out_passed:
                consecutive_hits += 1
                last_center = matched_center
                last_frame = frame
            else:
                consecutive_hits = 0
                last_center = None
                last_frame = None
            self._status(
                "一键购买全部收藏按钮 OCR稳定",
                f"{consecutive_hits}/{BUY_ALL_FAVORITES_STABLE_HITS}",
            )
            if (
                consecutive_hits >= BUY_ALL_FAVORITES_STABLE_HITS
                and last_center is not None
                and last_frame is not None
            ):
                return last_center, last_frame
            if monotonic() >= end_at:
                break
            self.task.sleep(BUY_ALL_FAVORITES_INTERVAL)
        self.task.log_warning(
            f"买：购买按钮或售罄状态确认超时，OCR={last_text or '-'}。"
        )
        return None

    def _wait_for_purchase_confirmation(
        self,
        timeout: float = BUY_CONFIRM_TIMEOUT,
    ) -> bool:
        end_at = monotonic() + max(0.0, timeout)
        last_text = ""
        while True:
            frame = self.vision.capture()
            text = self.vision.ocr_text(
                frame,
                "购买全部收藏确认",
                relative_roi=BUY_CONFIRM_DIALOG_REGION,
            )
            last_text = text or last_text
            normalized = normalize_text(self.vision.simplify(text))
            matched = sum(
                normalize_text(self.vision.simplify(keyword)) in normalized
                for keyword in BUY_CONFIRM_KEYWORDS
            )
            self._status(
                "购买全部收藏确认 OCR命中",
                f"{matched}/{len(BUY_CONFIRM_KEYWORDS)}",
            )
            if matched == len(BUY_CONFIRM_KEYWORDS):
                return True
            if monotonic() >= end_at:
                break
            self.task.sleep(BUY_CONFIRM_INTERVAL)
        self.task.log_warning(
            f"买：购买全部收藏确认OCR超时，OCR={last_text or '-'}。"
        )
        return False
