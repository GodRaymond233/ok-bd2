import os
import tempfile
import unittest
from datetime import datetime

from src.tasks.run_history import (
    BEIJING_TZ,
    RunHistoryStore,
    contains_joined_name,
    day_start_ts,
    week_start_ts,
)


def _beijing_ts(year, month, day, hour, minute=0) -> float:
    return datetime(year, month, day, hour, minute, tzinfo=BEIJING_TZ).timestamp()


class _ExecutorStub:
    def __init__(self, tasks_by_class):
        self._tasks_by_class = tasks_by_class

    def get_task_by_class(self, cls):
        return self._tasks_by_class.get(cls)


class _ChildTaskStub:
    def __init__(self, name):
        self.name = name


class _ChildSpec:
    def __init__(self, config_key, task_class):
        self.config_key = config_key
        self.task_class = task_class


class _TaskStub:
    def __init__(self, name, info=None, start_time=1000.0, child_tasks=None, executor=None):
        self.name = name
        self.info = info or {}
        self.start_time = start_time
        if child_tasks is not None:
            self.child_tasks = child_tasks
        self.executor = executor


class DayBoundaryTest(unittest.TestCase):
    def test_before_4am_belongs_to_previous_day(self):
        anchor = day_start_ts(_beijing_ts(2026, 8, 18, 3, 59))
        self.assertEqual(anchor, _beijing_ts(2026, 8, 17, 4, 0))

    def test_after_4am_belongs_to_today(self):
        anchor = day_start_ts(_beijing_ts(2026, 8, 18, 4, 1))
        self.assertEqual(anchor, _beijing_ts(2026, 8, 18, 4, 0))

    def test_week_starts_monday_4am(self):
        # Wednesday noon -> this Monday 04:00.
        anchor = week_start_ts(_beijing_ts(2026, 8, 19, 12, 0))
        self.assertEqual(anchor, _beijing_ts(2026, 8, 17, 4, 0))

    def test_monday_before_4am_belongs_to_previous_week(self):
        anchor = week_start_ts(_beijing_ts(2026, 8, 17, 3, 59))
        self.assertEqual(anchor, _beijing_ts(2026, 8, 10, 4, 0))


class JoinedNameTest(unittest.TestCase):
    def test_element_containing_separator_matches_whole(self):
        joined = "公会、小屋、酒馆、快速狩猎"
        self.assertTrue(contains_joined_name(joined, "公会、小屋、酒馆"))
        self.assertTrue(contains_joined_name(joined, "快速狩猎"))

    def test_non_member_is_rejected(self):
        joined = "公会、小屋、酒馆、快速狩猎"
        self.assertFalse(contains_joined_name(joined, "跑商"))
        self.assertFalse(contains_joined_name(joined, ""))
        self.assertFalse(contains_joined_name("-", "跑商"))
        self.assertFalse(contains_joined_name(None, "跑商"))


class RunHistoryStoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "task_run_history.json")
        self.store = RunHistoryStore(self.path)

    def test_record_and_reload_roundtrip(self):
        task = _TaskStub("快速狩猎", info={"状态": "快速狩猎完成。"}, start_time=1000.0)
        self.store.record_task_done(task, finished=1090.0)

        record = self.store.last_run("快速狩猎")
        self.assertTrue(record["ok"])
        self.assertEqual(record["duration"], 90.0)

        reloaded = RunHistoryStore(self.path)
        self.assertEqual(reloaded.last_run("快速狩猎"), record)

    def test_error_info_marks_run_failed(self):
        task = _TaskStub("每日跑商", info={"状态": "跑商完成。", "Error": "boom"})
        self.store.record_task_done(task, finished=2000.0)
        self.assertFalse(self.store.last_run("每日跑商")["ok"])

    def test_aborted_status_marks_run_failed(self):
        task = _TaskStub("一键完成日常", info={"状态": "一键完成日常中止。"})
        self.store.record_task_done(task, finished=2000.0)
        self.assertFalse(self.store.last_run("一键完成日常")["ok"])

    def test_is_completed_today_respects_4am_boundary(self):
        task = _TaskStub("广场女神像", info={"状态": "ok"})
        self.store.record_task_done(task, finished=_beijing_ts(2026, 8, 18, 9, 0))

        self.assertTrue(
            self.store.is_completed_today("广场女神像", now=_beijing_ts(2026, 8, 18, 23, 0))
        )
        # After the next 04:00 refresh it no longer counts.
        self.assertFalse(
            self.store.is_completed_today("广场女神像", now=_beijing_ts(2026, 8, 19, 5, 0))
        )
        # A failed run never counts.
        self.store.record_task_done(
            _TaskStub("广场女神像", info={"Error": "x"}), finished=_beijing_ts(2026, 8, 18, 10, 0)
        )
        self.assertFalse(
            self.store.is_completed_today("广场女神像", now=_beijing_ts(2026, 8, 18, 11, 0))
        )

    def test_is_completed_this_week(self):
        task = _TaskStub("每周跑图", info={})
        self.store.record_task_done(task, finished=_beijing_ts(2026, 8, 17, 8, 0))
        self.assertTrue(
            self.store.is_completed_this_week("每周跑图", now=_beijing_ts(2026, 8, 19, 12, 0))
        )
        self.assertFalse(
            self.store.is_completed_this_week("每周跑图", now=_beijing_ts(2026, 8, 24, 4, 1))
        )

    def test_batch_run_fans_out_to_child_display_names(self):
        daily_cls = type("DailyTask", (), {})
        pvp_cls = type("PVPTask", (), {})
        trade_cls = type("MapTradeTask", (), {})
        children = [
            _ChildSpec("公会、小屋、酒馆", daily_cls),
            _ChildSpec("自动PVP", pvp_cls),
            _ChildSpec("跑商", trade_cls),
        ]
        executor = _ExecutorStub(
            {
                daily_cls: _ChildTaskStub("公会、小屋、酒馆"),
                pvp_cls: _ChildTaskStub("镜中之战"),
                trade_cls: _ChildTaskStub("每日跑商"),
            }
        )
        info = {
            "状态": "一键完成日常中止。",
            "完成": "公会、小屋、酒馆",
            "失败": "自动PVP",
            "跳过": "跑商",
            "Error": "x",
        }
        batch = _TaskStub("一键完成日常", info=info, child_tasks=children, executor=executor)
        self.store.record_task_done(batch, finished=_beijing_ts(2026, 8, 18, 9, 30))

        self.assertTrue(self.store.last_run("公会、小屋、酒馆")["ok"])
        # Config keys are resolved to the child tasks' display names.
        self.assertIsNotNone(self.store.last_run("镜中之战"))
        self.assertFalse(self.store.last_run("镜中之战")["ok"])
        # Skipped children get no record, so they stay "not done".
        self.assertIsNone(self.store.last_run("每日跑商"))
        self.assertFalse(
            self.store.is_completed_today("每日跑商", now=_beijing_ts(2026, 8, 18, 12, 0))
        )

    def test_corrupt_file_loads_empty(self):
        with open(self.path, "w", encoding="utf-8") as file:
            file.write("{not json")
        store = RunHistoryStore(self.path)
        self.assertIsNone(store.last_run("快速狩猎"))

    def test_unknown_version_loads_empty(self):
        with open(self.path, "w", encoding="utf-8") as file:
            file.write('{"version": 999, "tasks": {"a": {"finished": 1}}}')
        store = RunHistoryStore(self.path)
        self.assertIsNone(store.last_run("a"))


if __name__ == "__main__":
    unittest.main()
