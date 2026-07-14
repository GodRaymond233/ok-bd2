import unittest
from unittest.mock import patch

from src.tasks.BargainLevelTask import (
    BARGAIN_CONFIRM_POINT,
    BARGAIN_ENTRY_POINT,
    BARGAIN_PROMPT,
    BUY_ALL_COLLECTION_PROMPT,
    CLOSE_SHOP_CONFIRM_POINT,
    CLOSE_SHOP_PROMPTS,
    COLLECTION_BACK_POINT,
    POST_RECOGNITION_DELAY_SECONDS,
    UPGRADE_CHECK_SECONDS,
    BargainLevelTask,
)


class BargainLevelTaskTest(unittest.TestCase):
    def test_task_metadata_and_defaults(self):
        task = object.__new__(BargainLevelTask)
        task.default_config = {}
        task.config_description = {}
        task.config_type = {}
        with patch(
            "src.tasks.BargainLevelTask.BaseBD2Task.__init__",
            return_value=None,
        ):
            BargainLevelTask.__init__(task)

        self.assertEqual("刷砍价等级", task.name)
        self.assertEqual("在第六章商人处开始", task.description)
        self.assertEqual("自动刷级", task.group_name)
        self.assertTrue(task.default_config["启用"])

    def test_reference_points_are_stored_as_ratios(self):
        self.assertEqual((192 / 1920, 905 / 1080), BARGAIN_ENTRY_POINT)
        self.assertEqual((1049 / 1920, 655 / 1080), BARGAIN_CONFIRM_POINT)
        self.assertEqual((111 / 1920, 52 / 1080), COLLECTION_BACK_POINT)
        self.assertEqual((1044 / 1920, 641 / 1080), CLOSE_SHOP_CONFIRM_POINT)

    def test_cycle_waits_for_each_ocr_gate_before_clicking(self):
        task = object.__new__(BargainLevelTask)
        task.config = {"步骤 OCR 等待秒数": 20.0}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        clicks = []
        sleeps = []
        waits = []
        responses = iter(
            [
                (True, BARGAIN_PROMPT),
                (True, BUY_ALL_COLLECTION_PROMPT),
                (True, "折扣商店结束 是否关闭折扣商店？"),
            ]
        )

        def fake_wait(keywords, timeout, minimum_matches, name):
            waits.append((keywords, timeout, minimum_matches, name))
            return next(responses)

        task._wait_for_ocr_keywords = fake_wait
        task.sleep = lambda seconds: sleeps.append(seconds)
        task.operate_click = lambda x, y: clicks.append((x, y))

        self.assertTrue(BargainLevelTask._run_cycle(task))
        self.assertEqual(
            [
                BARGAIN_ENTRY_POINT,
                BARGAIN_CONFIRM_POINT,
                COLLECTION_BACK_POINT,
                CLOSE_SHOP_CONFIRM_POINT,
            ],
            clicks,
        )
        self.assertEqual([POST_RECOGNITION_DELAY_SECONDS] * 3, sleeps)
        self.assertEqual([BARGAIN_PROMPT], waits[0][0])
        self.assertEqual([BUY_ALL_COLLECTION_PROMPT], waits[1][0])
        self.assertEqual(list(CLOSE_SHOP_PROMPTS), waits[2][0])
        self.assertEqual(1, waits[0][2])
        self.assertEqual(1, waits[1][2])
        self.assertEqual(len(CLOSE_SHOP_PROMPTS), waits[2][2])

    def test_cycle_stops_clicking_when_an_ocr_gate_times_out(self):
        task = object.__new__(BargainLevelTask)
        task.config = {"步骤 OCR 等待秒数": 20.0}
        task.info_set = lambda *_args, **_kwargs: None
        task.log_info = lambda *_args, **_kwargs: None
        clicks = []
        task.operate_click = lambda x, y: clicks.append((x, y))
        task._wait_for_ocr_keywords = lambda *_args, **_kwargs: (False, "")
        task.sleep = lambda *_args, **_kwargs: self.fail("must not sleep after failed OCR")

        self.assertFalse(BargainLevelTask._run_cycle(task))
        self.assertEqual([BARGAIN_ENTRY_POINT], clicks)

    def test_run_stops_and_reports_when_upgrade_is_detected(self):
        task = object.__new__(BargainLevelTask)
        task.config = {"启用": True}
        info = {}
        notifications = []
        task.info_set = lambda key, value: info.__setitem__(key, value)
        task.log_info = lambda message, notify=False: notifications.append((message, notify))
        cycles = []
        task._run_cycle = lambda: cycles.append(True) or True
        upgrade_checks = []

        def fake_wait(keywords, timeout, minimum_matches, name):
            upgrade_checks.append((keywords, timeout, minimum_matches, name))
            return True, "升星"

        task._wait_for_ocr_keywords = fake_wait

        self.assertTrue(BargainLevelTask.run(task))
        self.assertEqual(1, len(cycles))
        self.assertEqual(1, info["完成循环数"])
        self.assertEqual("已可以升星", info["结果"])
        self.assertEqual(UPGRADE_CHECK_SECONDS, upgrade_checks[0][1])
        self.assertTrue(notifications[-1][1])

    def test_run_starts_another_cycle_when_upgrade_is_not_detected(self):
        task = object.__new__(BargainLevelTask)
        task.config = {"启用": True}
        info = {}
        task.info_set = lambda key, value: info.__setitem__(key, value)
        task.log_info = lambda *_args, **_kwargs: None
        cycles = []
        task._run_cycle = lambda: cycles.append(True) or True
        upgrade_results = iter([(False, ""), (True, "升星")])
        task._wait_for_ocr_keywords = lambda *_args, **_kwargs: next(upgrade_results)

        self.assertTrue(BargainLevelTask.run(task))
        self.assertEqual(2, len(cycles))
        self.assertEqual(2, info["完成循环数"])
        self.assertEqual("已可以升星", info["结果"])

    def test_close_shop_gate_requires_both_prompts_and_ignores_punctuation(self):
        text = "折扣商店结束\n是否关闭折扣商店"
        self.assertEqual(
            2,
            BargainLevelTask._keyword_match_count(text, list(CLOSE_SHOP_PROMPTS)),
        )
        self.assertEqual(
            1,
            BargainLevelTask._keyword_match_count("折扣商店结束", list(CLOSE_SHOP_PROMPTS)),
        )


if __name__ == "__main__":
    unittest.main()
