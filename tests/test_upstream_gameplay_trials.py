import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import cv2
import numpy as np

from src.tasks import run_history
from src.tasks.AffinityTalkTask import AffinityTalkTask
from src.tasks.CartridgeChallengeTask import HOME_COLLECTION_ENTRY
from src.tasks.DoomBookTask import DoomBookTask
from src.tasks.EventClaimTask import EventClaimTask
from src.tasks.EventQuickBattlePreviewTask import EventQuickBattlePreviewTask
from src.tasks.GearRefineTask import GearRefineTask
from src.tasks.TowerSweepTask import SWEEP_MAX_POINT, SWEEP_MINUS_POINT, TowerSweepTask
from src.tasks.TradeInventoryTask import TradeInventoryTask

ROOT = Path(__file__).resolve().parents[1]


class GameplayTrialTest(TestCase):
    def test_inventory_catalog_preserves_historical_item_names_and_aliases(self):
        from src.tasks.map_trade.calendar import BUNDLED_CALENDAR_HISTORY, parse_calendar_payload
        from src.tasks.map_trade.data import ITEM_ALIASES
        from src.tasks.map_trade.trader_constants import CALENDAR_DIR
        from src.utils.ocr_utils import normalize_ocr_text

        catalog = TradeInventoryTask._item_catalog()
        self.assertEqual("三文鱼芥末寿司", catalog["三文鱼芥末寿司"])
        names = list(ITEM_ALIASES.items())
        for _, filename in BUNDLED_CALENDAR_HISTORY:
            calendar = parse_calendar_payload((CALENDAR_DIR / filename).read_bytes())
            for day in calendar.days.values():
                names.extend((entry.item, entry.aliases) for entry in day)
        for name, aliases in names:
            for alias in (name, *aliases):
                self.assertEqual(name, catalog[normalize_ocr_text(alias, alnum_only=True)])

    def test_event_quick_battle_preview_only_opens_a_stable_stage(self):
        frame = np.full((720, 1280, 3), 210, dtype=np.uint8)
        button = SimpleNamespace(x=728, y=625, width=80, height=18)
        stage = [
            ("普通战斗10", .57, .24, None),
            ("角色", .43, .88, None),
            ("快速战斗", .60, .88, button),
            ("战斗1", .82, .88, None),
        ]
        changed = [("挑战战斗10", .57, .24, None), *stage[1:]]
        regular_only = [("普通战斗11", .57, .24, None), stage[1], stage[3]]
        dialog = [
            ("快速战斗", .50, .27, None), ("仅使用免费活动ap", .51, .35, None),
            ("max", .61, .56, None), ("取消", .42, .75, None), ("开始", .59, .75, None),
        ]
        for pages, success, opened in (
            ([stage, stage, dialog], True, True),
            ([stage, changed], False, False),
            ([regular_only], False, False),
            ([dialog], True, False),
        ):
            with self.subTest(pages=len(pages), success=success, opened=opened):
                task = EventQuickBattlePreviewTask.__new__(EventQuickBattlePreviewTask)
                task._read = Mock(side_effect=[(frame, labels) for labels in pages])
                task.info_set = Mock()
                task._fail = Mock(return_value=False)
                task.sleep = Mock()
                task.operate_click = Mock()

                self.assertEqual(success, task.run())
                if opened:
                    task.operate_click.assert_called_once_with(.60, .88, after_sleep=.7)
                else:
                    task.operate_click.assert_not_called()

    def test_trade_inventory_reads_visible_food_and_saves_only_stable_counts(self):
        from onnxocr.onnx_paddleocr import ONNXPaddleOcr

        frame = cv2.imread(str(ROOT / "tests/fixtures/map_trade/trade_shop/sell_page.png"))
        engine = ONNXPaddleOcr(use_angle_cls=False, use_npu=False, use_openvino=True)
        task = TradeInventoryTask.__new__(TradeInventoryTask)
        task.config = {"盘点可见出售列表": True}
        task.info_set = Mock()
        task._fail = Mock(return_value=False)
        task.sleep = Mock()
        task.capture_frame = Mock(return_value=frame)
        task.operate_click = Mock()
        boxes = []

        def read_boxes(frame, **_kwargs):
            for row in engine.ocr(frame):
                for polygon, (name, confidence) in row or ():
                    x_range, y_range = zip(*polygon)
                    x, y = min(x_range), min(y_range)
                    boxes.append(SimpleNamespace(
                        name=name, confidence=confidence, x=x, y=y,
                        width=max(x_range) - x, height=max(y_range) - y,
                    ))
            return boxes

        task.ocr = Mock(side_effect=read_boxes)
        self.assertEqual(({}, {}), task._visible_snapshot(task._item_catalog()))
        height, width = task.ocr.call_args.kwargs["frame"].shape[:2]
        title = SimpleNamespace(
            name="出售", confidence=1, x=.14*width, y=.04*height, width=20, height=10,
        )
        fewer = [box for box in boxes if box.name != "2650"]
        fewer.extend([title, SimpleNamespace(
            name="0", confidence=1, x=.275*width, y=.249*height, width=8, height=10,
        )])
        task.ocr.side_effect = [[title, *boxes], fewer]
        with TemporaryDirectory() as folder:
            path = Path(folder) / "stock.sqlite3"
            with patch("src.tasks.TradeInventoryTask.INVENTORY_PATH", path):
                self.assertTrue(task.run())
            with closing(sqlite3.connect(path)) as database:
                actual = dict(database.execute("SELECT name, quantity FROM trade_stock"))
                self.assertEqual(actual, {
                    "泰瑞丝派": 50, "冰镇甜点": 40, "火烤鱼板棒": 5501,
                    "橄榄油意面": 340, "蜂蜜黄油杏仁": 1600,
                    "地狱火紫菜包饭": 50, "街头烤鸡肉串": 503,
                })
        task.operate_click.assert_not_called()

    def test_trade_inventory_records_latest_stable_observation(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "stock.sqlite3"
            task = TradeInventoryTask.__new__(TradeInventoryTask)
            task.config = {"盘点商品名称": "香草牛排"}
            task.info_set = Mock()
            task._fail = Mock(return_value=False)
            task.sleep = Mock()
            task.operate_click = Mock()
            reads = (
                (1080, 1920, "香草牛排", "1,250", "1,250", True),
                (720, 1280, "泰瑞丝派", "950", "950", False),
                (720, 1280, "香草牛排", "5", "6", False),
                (720, 1280, "香草牛排", "0", "0", True),
            )
            with patch("src.tasks.TradeInventoryTask.INVENTORY_PATH", path), \
                 patch("src.tasks.TradeInventoryTask.time", side_effect=[3600, 3601]):
                for h, w, name, first, second, expected in reads:
                    with self.subTest(name=name, w=w, first=first, second=second):
                        frame = np.zeros((h, w, 3), dtype=np.uint8)
                        task.capture_frame = Mock(return_value=frame)
                        amounts = iter([first, second])

                        def ocr(*, frame, threshold, log):
                            if frame.shape[0] < h / 10:
                                return [SimpleNamespace(name=name)]
                            return [SimpleNamespace(name=text) for text in (
                                f"拥有 {next(amounts)}", "可购买 50", "出售",
                            )]

                        task.ocr = ocr
                        self.assertEqual(expected, task.run())
            task.operate_click.assert_not_called()
            with closing(sqlite3.connect(path)) as database:
                self.assertEqual(
                    [("1970-01-01", "香草牛排", 0, 3601)],
                    database.execute(
                        "SELECT day, name, quantity, observed_at FROM trade_stock"
                    ).fetchall(),
                )

    def test_trade_inventory_merges_scrolled_pages_and_stops_on_a_repeated_viewport(self):
        task = TradeInventoryTask.__new__(TradeInventoryTask)
        task.config = {"盘点可见出售列表": True, "出售列表扫描页数上限": 5}
        task.info_set = Mock()
        task.sleep = Mock()
        task.scroll_client = Mock()
        task._item_catalog = Mock(return_value={})
        first = ({"香草牛排": ("香草牛排", 2650)}, {"香草牛排": (.325, .198)})
        second = ({"泰瑞丝派": ("泰瑞丝派", 50)}, {"泰瑞丝派": (.496, .198)})
        task._visible_snapshot = Mock(side_effect=[first, first, second, second, second, second])
        task._save_observations = Mock()
        with patch("src.tasks.TradeInventoryTask.day_start_ts", return_value=0):
            self.assertTrue(task.run())
        self.assertEqual(
            [((.325, .198), -2), ((.496, .198), -2)],
            [wheel.args for wheel in task.scroll_client.call_args_list],
        )
        task._save_observations.assert_called_once()
        self.assertEqual(
            [("香草牛排", "香草牛排", 2650), ("泰瑞丝派", "泰瑞丝派", 50)],
            task._save_observations.call_args.args[1],
        )
        self.assertIn("未确认滚动后出现新商品", task.info_set.call_args.args[1])

    def test_affinity_checks_costume_and_stops_at_ambiguous_answers(self):
        task = AffinityTalkTask.__new__(AffinityTalkTask)
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        choices = [("第一项", .50, .43, None), ("第二项", .50, .55, None)]
        bank = (
            ({"第一项"}, {"第二项"}, {"问题一"}),
            ({"第二项"}, {"第一项"}, {"问题二"}),
        )
        task.config = {"谈心服装": "剑后", "服装列表扫描页数上限": 2}
        task._questions = Mock(return_value=("costume-id", bank))
        task.info_set = Mock()
        task.sleep = Mock()
        task.operate_click = Mock()
        task.scroll_client = Mock()
        task._finish = Mock(return_value=True)
        task._fail = Mock(return_value=False)
        heading = [("亲密度", .16, .04, None), ("今日剩余谈心次数2", .89, .04, None)]

        def detail(costume, count, daily):
            return [*heading, (costume, .56, .132, None), ("lv200", .53, .18, None),
                    ("谈心记录", .56, .24, SimpleNamespace(name=f"谈心记录 {count}/15")),
                    (f"谈心{daily}1", .78, .92, SimpleNamespace(name=f"谈心 {daily}/1"))]

        before = detail("剑后", 3, 1)
        after = detail("剑后", 4, 0)
        modal = [("要与剑后进行谈心吗", .50, .42, None), ("确认", .59, .58, None)]
        unresolved = [*choices, ("点击继续", .50, .88, None)]
        resolved = [*choices, ("问题二", .52, .21, None)]
        first_page = [*heading, ("另一服装", .35, .30, None), ("lv160", .35, .34, None)]
        second_page = [*heading, ("剑后", .70, .30, None), ("lv200", .70, .34, None)]
        task._start = Mock(return_value=(frame, first_page))
        task._read = Mock(side_effect=[(frame, labels) for labels in (
            second_page, before, before, modal, modal, unresolved, resolved, resolved,
            after, detail("其他服装", 4, 0), after, after,
        )])
        with TemporaryDirectory() as directory:
            store = run_history.RunHistoryStore(Path(directory) / "history.json")
            with patch.object(run_history, "day_start_ts", return_value=0.0), \
                 patch.object(run_history, "default_store", return_value=store), \
                 patch("src.tasks.AffinityTalkTask.reward_popup", return_value=None):
                self.assertTrue(task.run())
                self.assertTrue(task.run())
            record = store.stage("AffinityTalkTask", "谈心:0.0:costume-id")
            self.assertEqual("done", record["state"])
        self.assertEqual(12, task._read.call_count)
        task.scroll_client.assert_called_once_with((.35, .30), -2, after_sleep=.85)
        clicks = task.operate_click.call_args_list
        self.assertEqual([
            ((.70, .30), .7),
            ((.78, .92), .75),
            ((.59, .58), .75),
            ((.50, .55), .8),
        ], [(click.args, click.kwargs["after_sleep"]) for click in clicks])

    def test_affinity_fills_max_level_records_and_retains_pending_after_an_interruption(self):
        for interrupted in (False, True):
            with self.subTest(interrupted=interrupted), TemporaryDirectory() as directory:
                task = AffinityTalkTask.__new__(AffinityTalkTask)
                labels = [("谈心", .78, .92, None)]
                task.config = {"谈心服装": "剑后", "补齐Lv.20谈心记录": True}
                task._questions = Mock(return_value=("costume-id", ("bank",)))
                task._start = Mock(return_value=(None, labels))
                task._detail_page = Mock(return_value=(None, labels))
                task._detail = Mock(return_value=(20, 13, None))
                task._left_today = Mock(return_value=0)
                task.info_set = Mock()
                task._finish = Mock(return_value=True)
                task._fail = Mock(return_value=False)
                history = Path(directory) / "history.json"
                owner, step = "AffinityTalkTask", "谈心:0.0:costume-id"

                def click(*args, **kwargs):
                    record = run_history.RunHistoryStore(history).stage(owner, step)
                    self.assertEqual("pending", record["state"])
                    expected = 12 + task.operate_click.call_count
                    self.assertEqual(expected, record["evidence"]["records"])

                task.operate_click = Mock(side_effect=click)
                task._confirm_talk = Mock(side_effect=[
                    (labels, (20, 14, None), 0),
                    None if interrupted else (labels, (20, 15, None), 0),
                ])
                with patch.object(run_history, "day_start_ts", return_value=0.0), \
                     patch.object(run_history, "default_store",
                                  side_effect=lambda: run_history.RunHistoryStore(history)):
                    run_history.save_stage(
                        task, step, "done", scope="0.0", evidence={"records": 13},
                    )
                    self.assertEqual(not interrupted, task.run())
                    record = run_history.read_stage(task, step)
                    self.assertEqual("pending" if interrupted else "done", record["state"])
                    self.assertEqual(14 if interrupted else 15, record["evidence"]["records"])
                    if interrupted:
                        self.assertFalse(task.run())
                        task._start.assert_called_once()
                        task._finish.assert_not_called()
                    else:
                        task._finish.assert_called_once()
                self.assertEqual(2, task.operate_click.call_count)

    def test_affinity_auto_selection_skips_unowned_and_pending_costumes(self):
        task = AffinityTalkTask.__new__(AffinityTalkTask)
        task.config = {
            "自动选择服装": True, "补齐Lv.20谈心记录": True, "服装列表扫描页数上限": 5,
        }
        labels = [
            ("亲密度", .16, .04, None),
            ("今日剩余谈心次数12", .89, .04, None),
        ]
        for x, name, level in (
            (.16, "未拥有服装", 1), (.27, "今日已用服装", 16), (.38, "待核对服装", 20),
            (.49, "没有题库服装", 20), (.71, "可补齐服装", 20),
        ):
            labels.extend([(name, x, .30, None), (f"lv{level}0", x, .34, None)])
        labels.append(("未拥有", .17, .25, None))
        bank = {"costumes": True}
        page = (None, labels)
        second_page = (None, [*labels[:2], ("今日可用服装", .60, .30, None),
                              ("lv140", .60, .34, None)])
        task._start = Mock(return_value=page)
        task._auto_list = Mock(side_effect=[page, page, second_page])
        task._read = Mock(side_effect=[second_page, second_page])
        task.scroll_client = Mock()
        task._read_bank = Mock(return_value=bank)
        task._questions = Mock(side_effect=lambda name, _: (
            None if name == "没有题库服装" else (name, ("question",))
        ))
        task._run_costume = Mock(return_value=True)
        task._finish = Mock(return_value=True)
        with TemporaryDirectory() as directory:
            store = run_history.RunHistoryStore(Path(directory) / "history.json")
            with patch.object(run_history, "day_start_ts", return_value=0.0), \
                 patch.object(run_history, "default_store", return_value=store):
                run_history.save_stage(task, "谈心:0.0:待核对服装", "pending", scope="0.0")
                run_history.save_stage(task, "谈心:0.0:今日已用服装", "done", scope="0.0")
                self.assertTrue(task.run())
        self.assertEqual(["可补齐服装", "今日可用服装"],
                         [visit.args[0] for visit in task._run_costume.call_args_list])
        self.assertEqual(3, task._auto_list.call_count)
        self.assertEqual(2, task.scroll_client.call_count)
        self.assertEqual(2, task._read.call_count)

    @staticmethod
    def _event_wheel(spins=0, cost="1次免費", button="轉1次"):
        box = SimpleNamespace(name=button, x=1238, y=740, width=64, height=30)
        return [
            ("活動", .15, .04, None), ("轉盤活動", .631, .463, None),
            (f"累積參與{spins}次", .80, .275, None), ("鑽石", .446, .342, None),
            (button, .66, .70, box), ("轉10次", .776, .700, None),
            ("10", .798, .726, None),
        ] + ([(cost, .662, .726, None)] if cost else [])

    def test_event_wheel_only_accepts_a_visibly_free_single_spin(self):
        task = EventClaimTask.__new__(EventClaimTask)
        frame = np.full((1080, 1920, 3), 220, dtype=np.uint8)
        name = "轉盤活動"
        self.assertEqual(((.66, .70), 0), task._free_spin_control(
            frame, self._event_wheel(), name,
        ))
        self.assertEqual(((.66, .70), 0), task._free_spin_control(
            frame, self._event_wheel(cost="", button="轉1次1次免費"), name,
        ))
        self.assertIsNone(task._free_spin_control(
            frame, self._event_wheel(cost="鑽石100"), name,
        ))
        self.assertIsNone(task._free_spin_control(
            frame, self._event_wheel(button="轉10次"), name,
        ))
        self.assertIsNone(task._free_spin_control(frame, self._event_wheel(), "其他活動"))

    def test_event_free_wheel_records_pending_and_checks_count_before_finishing(self):
        frame = np.full((1080, 1920, 3), 220, dtype=np.uint8)
        for confirmed in (False, True):
            with self.subTest(confirmed=confirmed), TemporaryDirectory() as directory:
                task = EventClaimTask.__new__(EventClaimTask)
                task.info_set = Mock()
                task.sleep = Mock()
                task.operate_click = Mock()
                before = self._event_wheel(spins=4)
                after = self._event_wheel(spins=5 if confirmed else 6)
                task._read = Mock(side_effect=[
                    (frame, labels) for labels in (before, before, after, after)
                ])
                path = Path(directory) / "history.json"
                with patch.object(run_history, "day_start_ts", return_value=0.0), \
                     patch.object(run_history, "default_store",
                                  side_effect=lambda: run_history.RunHistoryStore(path)), \
                     patch("src.tasks.EventClaimTask.monotonic", side_effect=[0, 1, 2, 21]):
                    expected = "已转盘" if confirmed else "免费转盘结果待核对"
                    self.assertEqual(expected, task._claim_free_spin("轉盤活動"))
                    self.assertEqual(expected, task._claim_free_spin("轉盤活動"))
                task.operate_click.assert_called_once_with(.66, .70, after_sleep=.7)
                record = run_history.RunHistoryStore(path).stage(
                    "EventClaimTask", "免费转盘:0.0:轉盤活動",
                )
                self.assertEqual("done" if confirmed else "pending", record["state"])

    def test_event_badge_requires_a_short_red_mark_and_a_unique_card_caption(self):
        task = EventClaimTask.__new__(EventClaimTask)
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        frame[303:321, 504:522] = (0, 0, 230)
        frame[436:484, 501:528] = (0, 0, 230)
        labels = [("登录活动", .21, .34, None), ("转盘集章", .22, .47, None)]
        self.assertEqual([("登录活动", .21, .34)], task._badged_cards(frame, labels))
        self.assertEqual([], task._badged_cards(
            frame, [*labels, ("另一张卡", .215, .345, None)],
        ))

    def test_event_claim_requires_matching_card_and_skips_purchase_prompts(self):
        task = EventClaimTask.__new__(EventClaimTask)
        lit = np.full((1080, 1920, 3), 220, dtype=np.uint8)
        grey = lit.copy()
        grey[600:665, 1100:1300] = 118
        box = SimpleNamespace(name="全部领取", x=1150, y=610, width=82, height=20)
        common = [("活动", .15, .04, None), ("全部领取", .62, .57, box)]
        expected = [*common, ("登录活动", .49, .33, None)]
        for paid in (False, True):
            with self.subTest(paid=paid):
                task.sleep = Mock()
                task.operate_click = Mock()
                if paid:
                    screens = [(lit, expected), (lit, [*expected, ("付费兑换", .75, .42, None)])]
                else:
                    screens = [
                        (lit, expected), (lit, [*common, ("别的活动", .49, .33, None)]),
                        (lit, expected), (lit, expected), (grey, expected), (grey, expected),
                    ]
                task._read = Mock(side_effect=screens)
                self.assertEqual("付费页面" if paid else "已领取", task._claim_bulk("登录活动"))
                if paid:
                    task.operate_click.assert_not_called()
                else:
                    task.operate_click.assert_called_once_with(.62, .57, after_sleep=.7)

    def test_event_claims_rows_in_order_after_each_previous_icon_disappears(self):
        task = EventClaimTask.__new__(EventClaimTask)
        first, second = (.795, .42), (.795, .55)
        labels = [("活动", .15, .04, None), ("登录活动", .49, .33, None)]
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        task._read = Mock(return_value=(frame, labels))
        task.sleep = Mock()
        task.operate_click = Mock()
        task._row_points = Mock(side_effect=[
            [first, second], [first, second], [first, second], [first, second],
            [second], [second], [second], [second], [], [], [], [],
        ])
        self.assertEqual("已领取", task._claim_rows("登录活动", "逐条领取"))
        self.assertEqual([first, second], [call.args for call in task.operate_click.call_args_list])

    def test_tower_only_submits_a_single_unambiguous_sweep(self):
        task = TowerSweepTask.__new__(TowerSweepTask)
        frame = np.full((1080, 1920, 3), 220, dtype=np.uint8)
        button = SimpleNamespace(x=1060, y=806, width=60, height=25)
        labels = [("快速战斗", .5, .25, None), ("战斗", .57, .76, button)]
        self.assertEqual(
            task._configured_action(frame, [*labels, ("战斗1次奖励", .4, .32, None)]),
            (.57, .76),
        )
        self.assertIsNone(
            task._configured_action(frame, [*labels, ("战斗2次奖励", .4, .32, None)]),
        )
        self.assertIsNone(task._configured_action(
            frame, [*labels, ("战斗1次奖励", .4, .32, None), ("购买", .5, .5, None)],
        ))
        frame[:] = 120
        self.assertIsNone(
            task._configured_action(frame, [*labels, ("战斗1次奖励", .4, .32, None)]),
        )

    def test_tower_reserves_keys_and_persists_one_confirmed_sweep(self):
        frame = np.full((1080, 1920, 3), 220, dtype=np.uint8)
        quick = SimpleNamespace(x=1400, y=970, width=90, height=25)
        submit = SimpleNamespace(x=1060, y=806, width=60, height=25)
        page = [
            ("安救赎之塔", .17, .046, None), ("7", .434, .142, None),
            ("210", .932, .046, SimpleNamespace(name="2/10")),
            ("快速战斗", .75, .91, quick),
        ]
        dialog = [
            ("快速战斗", .50, .26, None), ("战斗1次奖励", .42, .32, None),
            ("战斗", .57, .75, submit),
        ]
        result = [("攻略完毕", .67, .14, None), ("快速战斗1", .58, .23, None)]
        for reserve in (1, 2):
            with self.subTest(reserve=reserve), TemporaryDirectory() as directory:
                task = TowerSweepTask.__new__(TowerSweepTask)
                task.name = task.title
                task.config = {"预留救赎钥匙": reserve}
                task.info_set = Mock()
                task.log_completion = Mock()
                task.sleep = Mock()
                task._highest_cleared = Mock(return_value=(7, (.146, .6892)))
                screens = (page, page, dialog, dialog, dialog, dialog, result)
                task._read = Mock(side_effect=[(frame, labels) for labels in screens])
                path = Path(directory) / "history.json"
                stage = "快速战斗:0.0"

                def click(x, y, **_kwargs):
                    if (x, y) == (.57, .75):
                        saved = run_history.RunHistoryStore(path).stage("TowerSweepTask", stage)
                        self.assertEqual("pending", saved["state"])
                        self.assertEqual(
                            {"level": 7, "keys_before": 2, "count": 1}, saved["evidence"],
                        )

                task.operate_click = Mock(side_effect=click)
                with patch.object(run_history, "day_start_ts", return_value=0.0), \
                     patch.object(run_history, "default_store",
                                  side_effect=lambda: run_history.RunHistoryStore(path)):
                    self.assertTrue(task.run())
                    if reserve == 1:
                        self.assertTrue(task.run())
                self.assertEqual(
                    [(.75, .91), (.57, .75)] if reserve == 1 else [],
                    [call.args for call in task.operate_click.call_args_list],
                )

    def test_tower_batch_uses_max_then_preserves_keys_and_checks_result_count(self):
        frame = np.full((1080, 1920, 3), 220, dtype=np.uint8)
        quick = SimpleNamespace(x=1400, y=970, width=90, height=25)
        submit = SimpleNamespace(x=1060, y=806, width=60, height=25)
        page = [
            ("救赎之塔", .17, .046, None), ("7", .434, .142, None),
            ("410", .932, .046, SimpleNamespace(name="4/10")),
            ("快速战斗", .75, .91, quick),
        ]

        def dialog(count):
            return [
                ("快速战斗", .50, .26, None), (f"战斗{count}次奖励", .42, .32, None),
                ("战斗", .57, .75, submit),
            ]

        result_wrong = [("攻略完毕", .67, .14, None), ("快速战斗4", .58, .23, None)]
        result_right = [("攻略完毕", .67, .14, None), ("快速战斗3", .58, .23, None)]
        task = TowerSweepTask.__new__(TowerSweepTask)
        task.name = task.title
        task.config = {"单次速刷上限": 10, "预留救赎钥匙": 0}
        task.info_set = Mock()
        task.log_completion = Mock()
        task.sleep = Mock()
        task.operate_click = Mock()
        task._highest_cleared = Mock(return_value=(7, (.146, .6892)))
        screens = [page, page, *[dialog(1)] * 2, *[dialog(4)] * 2, *[dialog(3)] * 4]
        task._read = Mock(side_effect=[
            (frame, labels) for labels in (*screens, result_wrong, result_right)
        ])
        with patch.object(run_history, "day_start_ts", return_value=0.0), \
             patch.object(run_history, "read_stage", return_value={}), \
             patch.object(run_history, "save_stage") as save:
            self.assertTrue(task.run())
        self.assertEqual(
            [(.75, .91), SWEEP_MAX_POINT, SWEEP_MINUS_POINT, (.57, .75)],
            [call.args for call in task.operate_click.call_args_list],
        )
        self.assertEqual(len(screens) + 2, task._read.call_count)
        self.assertEqual(["pending", "done"], [call.args[2] for call in save.call_args_list])
        for call in save.call_args_list:
            self.assertEqual({"level": 7, "keys_before": 4, "count": 3}, call.kwargs["evidence"])

    def test_refine_requires_a_single_button_and_an_unambiguous_cost(self):
        task = GearRefineTask.__new__(GearRefineTask)
        frame = np.full((1080, 1920, 3), 220, dtype=np.uint8)
        button = SimpleNamespace(x=1530, y=1000, width=48, height=25)
        labels = [
            ("精炼装备分数", .1, .04, None),
            ("连续精炼", .65, .93, None),
            ("精炼", .81, .93, button),
            ("80", .85, .93, None),
        ]
        self.assertEqual((.81, .93, 80), task._single_refine(frame, labels))
        self.assertIsNone(task._single_refine(frame, labels[:-1]))
        self.assertIsNone(task._single_refine(frame, [*labels, ("确认", .5, .5, None)]))
        self.assertIsNone(task._single_refine(frame, [*labels, ("90", .86, .93, None)]))
        frame[:] = 120
        self.assertIsNone(task._single_refine(frame, labels))

    def test_refine_persists_the_attempt_before_click_and_deduplicates_same_day(self):
        for confirmed in (False, True):
            with self.subTest(confirmed=confirmed), TemporaryDirectory() as directory:
                task = GearRefineTask.__new__(GearRefineTask)
                task.name = task.title
                task.config = {}
                task.info_set = Mock()
                task.log_completion = Mock()
                task.sleep = Mock()
                task._read = Mock(return_value=(None, []))
                task._single_refine = Mock(return_value=(.81, .93, 80))
                task._result_seen = Mock(return_value=confirmed)
                path = Path(directory) / "history.json"
                store = run_history.RunHistoryStore(path)
                step = "单次精炼:0.0"

                def submit(*args, **kwargs):
                    saved = run_history.RunHistoryStore(path).stage("GearRefineTask", step)
                    self.assertEqual("pending", saved["state"])
                    self.assertEqual(80, saved["evidence"]["cost"])

                task.operate_click = Mock(side_effect=submit)
                with patch.object(run_history, "default_store", return_value=store), \
                     patch.object(run_history, "day_start_ts", return_value=0.0), \
                     patch("src.tasks.GearRefineTask.monotonic", side_effect=[0, 1, 7]):
                    self.assertEqual(confirmed, task.run())
                with patch.object(run_history, "default_store",
                                  return_value=run_history.RunHistoryStore(path)), \
                     patch.object(run_history, "day_start_ts", return_value=0.0):
                    self.assertEqual(confirmed, task.run())
                task.operate_click.assert_called_once_with(.81, .93, after_sleep=.2)

    def test_refine_batch_waits_for_the_previous_toast_and_keeps_pending_on_timeout(self):
        for second_confirmed in (False, True):
            with self.subTest(second_confirmed=second_confirmed), TemporaryDirectory() as folder:
                task = GearRefineTask.__new__(GearRefineTask)
                task.name = task.title
                task.config = {"每日精炼次数上限": 2}
                task.info_set = Mock()
                task.log_completion = Mock()
                task.sleep = Mock()
                frame = np.full((1080, 1920, 3), 220, dtype=np.uint8)
                button = SimpleNamespace(x=1530, y=1000, width=48, height=25)
                ready = [
                    ("精炼装备分数", .1, .04, None), ("连续精炼", .66, .93, None),
                    ("精炼80", .81, .93, button),
                ]
                toast = [*ready, ("精炼成功", .50, .10, None)]
                task._read = Mock(side_effect=[(frame, labels) for labels in (
                    ready, ready, toast, toast, ready, ready, ready,
                    toast if second_confirmed else ready,
                )])
                history = Path(folder) / "history.json"
                store = run_history.RunHistoryStore(history)
                step = "单次精炼:0.0"

                def click(*args, **kwargs):
                    record = run_history.RunHistoryStore(history).stage("GearRefineTask", step)
                    self.assertEqual("pending", record["state"])
                    self.assertEqual(task.operate_click.call_count - 1,
                                     record["evidence"]["completed"])

                task.operate_click = Mock(side_effect=click)
                clock_ticks = [0, 1, 2, 3, 4, 5, 6, 12]
                with patch.object(run_history, "default_store", return_value=store), \
                     patch.object(run_history, "day_start_ts", return_value=0.0), \
                     patch("src.tasks.GearRefineTask.monotonic", side_effect=clock_ticks):
                    self.assertEqual(second_confirmed, task.run())
                    self.assertEqual(second_confirmed, task.run())
                self.assertEqual(2, task.operate_click.call_count)
                self.assertEqual(8, task._read.call_count)
                record = store.stage("GearRefineTask", step)
                self.assertEqual("done" if second_confirmed else "pending", record["state"])
                self.assertEqual(2 if second_confirmed else 1, record["evidence"]["completed"])

    def test_refine_rejects_price_above_the_limit_or_changed_on_recheck(self):
        for first_cost, second_cost in ((81, 81), (70, 75)):
            with self.subTest(first_cost=first_cost, second_cost=second_cost):
                task = GearRefineTask.__new__(GearRefineTask)
                task.name = task.title
                task.config = {"单次精炼材料上限": 80}
                task.info_set = Mock()
                task.sleep = Mock()
                task._read = Mock(return_value=(None, []))
                task._single_refine = Mock(side_effect=[
                    (.81, .93, first_cost), (.81, .93, second_cost),
                ])
                task.operate_click = Mock()
                with patch.object(run_history, "read_stage", return_value={}), \
                     patch.object(run_history, "save_stage") as save:
                    self.assertFalse(task.run())
                task.operate_click.assert_not_called()
                save.assert_not_called()

    def test_doom_book_starts_once_and_handles_result_then_record(self):
        task = DoomBookTask.__new__(DoomBookTask)
        frame = np.full((1080, 1920, 3), 220, dtype=np.uint8)
        button = SimpleNamespace(x=1600, y=980, width=75, height=25)
        panel = [
            ("末日之书", .18, .05, None),
            ("最高伤害", .52, .13, None),
            ("去战斗", .85, .92, button),
        ]
        other_leave = [("离开", .8, .95, None)]
        result = [("result", .5, .20, None), *other_leave]
        record = [("创造新纪录", .67, .38, None), ("最高伤害", .67, .50, None)]
        field = [("战斗分布11", .2, .09, None), ("最高伤害100", .23, .12, None)]
        task._read = Mock(side_effect=[
            (frame, labels) for labels in (panel, other_leave, result, record, field, field)
        ])
        task.info_set = Mock()
        task.sleep = Mock()
        task.operate_click = Mock()
        task._finish = Mock(return_value=True)

        self.assertTrue(task.run())
        self.assertEqual(
            [(.85, .92), (.8, .95), (.67, .38)],
            [entry.args for entry in task.operate_click.call_args_list],
        )
        task._finish.assert_called_once_with()

    def test_doom_book_waiting_on_the_original_panel_is_not_a_completion(self):
        task = DoomBookTask.__new__(DoomBookTask)
        task.name = task.title
        task._read = Mock(return_value=(None, [("末日之书", .18, .05, None)]))
        task.info_set = Mock()
        task.sleep = Mock()
        task.operate_click = Mock()
        task._finish = Mock()
        with patch("src.tasks.DoomBookTask.monotonic", side_effect=[0, 1, 2, 121]):
            self.assertFalse(task._finish_battle())
        task.operate_click.assert_not_called()
        task._finish.assert_not_called()

    def test_doom_book_uses_the_settled_card_from_home(self):
        task = DoomBookTask.__new__(DoomBookTask)
        task.name = task.title
        collection = [("游戏卡珍藏集", .17, .04, None)]
        field = [("战斗分布11", .2, .09, None), ("最高伤害100", .23, .12, None)]
        panel = [("末日之书", .18, .05, None)]
        task._read = Mock(side_effect=[
            (None, []), (None, collection), (None, collection),
            (None, collection), (None, collection), (None, field),
        ])
        task._frame_confirms_home = Mock(return_value=True)
        task._card_center = Mock(side_effect=[(.58, .45), (.58, .42), (.58, .38), (.58, .38)])
        task._open_panel = Mock(return_value=(None, panel))
        task._start_button = Mock(return_value=(.85, .92))
        task._finish_battle = Mock(return_value=True)
        task.info_set = Mock()
        task.sleep = Mock()
        task.scroll_client = Mock()
        task.operate_click = Mock()

        self.assertTrue(task.run())
        task._open_panel.assert_called_once_with(None, field)
        self.assertEqual(
            [HOME_COLLECTION_ENTRY, (.58, .38), (.85, .92)],
            [entry.args for entry in task.operate_click.call_args_list],
        )
        task.scroll_client.assert_called_once()

    def test_doom_book_stops_after_loading_an_unrecognized_cartridge(self):
        task = DoomBookTask.__new__(DoomBookTask)
        task.name = task.title
        collection = [("游戏卡珍藏集", .17, .04, None)]
        different_field = [("格鲁菲餐厅", .18, .05, None)]
        task._read = Mock(side_effect=[
            (None, collection), (None, collection), (None, different_field),
        ])
        task._card_center = Mock(return_value=(.58, .44))
        task._open_panel = Mock()
        task.info_set = Mock()
        task.sleep = Mock()
        task.operate_click = Mock()

        with patch("src.tasks.CartridgeChallengeTask.monotonic", side_effect=[0, 1, 26]):
            self.assertFalse(task.run())
        task._open_panel.assert_not_called()
        task.operate_click.assert_called_once_with(.58, .44, after_sleep=.5)
