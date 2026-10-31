import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

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
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.dir = directory.name
        self.path = os.path.join(self.dir, "task_run_history.json")
        self.store = RunHistoryStore(self.path)

    def test_stage_survives_restart_and_failed_write_keeps_pending_evidence(self):
        self.store.save_stage("狩猎", "MIN", "pending", "today", {"cost": 6})
        with open(self.path, encoding="utf-8") as stream:
            original = stream.read()
        with patch("src.tasks.run_history.write_json_file", side_effect=OSError("disk full")):
            with self.assertRaises(RuntimeError):
                self.store.save_stage("狩猎", "MIN", "done", "today")
        self.assertEqual("pending", self.store.stage("狩猎", "MIN")["state"])
        self.assertEqual("pending", RunHistoryStore(self.path).stage("狩猎", "MIN")["state"])
        with open(self.path, encoding="utf-8") as stream:
            self.assertEqual(original, stream.read())

    def test_corrupt_history_cannot_authorize_spending_or_be_overwritten(self):
        with open(self.path, "w", encoding="utf-8") as stream:
            stream.write("{broken")
        broken = RunHistoryStore(self.path)
        with self.assertRaises(RuntimeError):
            broken.stage("料理", "牛排")
        with self.assertRaises(RuntimeError):
            broken.save_stage("料理", "牛排", "pending", "today")
        broken.record_task_done(_TaskStub("日常", {"状态": "失败"}))
        with open(self.path, encoding="utf-8") as stream:
            self.assertEqual("{broken", stream.read())

    def test_record_and_reload_roundtrip(self):
        task = _TaskStub("快速狩猎", info={"状态": "快速狩猎完成。"}, start_time=1000.0)
        self.store.record_task_done(task, finished=1090.0)

        record = self.store.last_run("快速狩猎")
        self.assertTrue(record["ok"])
        self.assertEqual(record["duration"], 90.0)

        reloaded = RunHistoryStore(self.path)
        self.assertEqual(reloaded.last_run("快速狩猎"), record)

    def test_concurrent_records_keep_disk_and_memory_consistent(self):
        first_saving = threading.Event()
        second_at_write_boundary = threading.Event()
        release_first = threading.Event()
        writer_id = threading.local()
        record_lock = threading.RLock()

        class ObservedLock:
            def __enter__(self):
                if getattr(writer_id, "name", None) == "second":
                    second_at_write_boundary.set()
                record_lock.acquire()

            def __exit__(self, *_args):
                record_lock.release()

        self.store._lock = ObservedLock()

        def save(path, data):
            snapshot = json.dumps(data)
            if writer_id.name == "first":
                first_saving.set()
                if not release_first.wait(5):
                    raise TimeoutError("first writer was not released")
            with open(path, "w", encoding="utf-8") as stream:
                stream.write(snapshot)
            if writer_id.name == "second":
                second_at_write_boundary.set()

        def record(name):
            writer_id.name = name
            self.store.record_task_done(_TaskStub(name), finished=2000, ok=True)

        with patch("src.tasks.run_history.write_json_file", side_effect=save):
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(record, "first")
                try:
                    self.assertTrue(first_saving.wait(5))
                    second = executor.submit(record, "second")
                    self.assertTrue(second_at_write_boundary.wait(5))
                finally:
                    release_first.set()
                first.result(timeout=5)
                second.result(timeout=5)
        reloaded = RunHistoryStore(self.path)
        for name in ("first", "second"):
            self.assertEqual(self.store.last_run(name), reloaded.last_run(name))

    def test_error_info_marks_run_failed(self):
        task = _TaskStub("每日跑商", info={"状态": "跑商完成。", "Error": "boom"})
        self.store.record_task_done(task, finished=2000.0)
        self.assertFalse(self.store.last_run("每日跑商")["ok"])

    def test_aborted_status_marks_run_failed(self):
        task = _TaskStub("一键完成日常", info={"状态": "一键完成日常中止。"})
        self.store.record_task_done(task, finished=2000.0)
        self.assertFalse(self.store.last_run("一键完成日常")["ok"])

    def test_neutral_status_with_failure_count_marks_run_failed(self):
        task = _TaskStub(
            "公会、小屋、酒馆",
            info={"状态": "公会、小屋、酒馆结束。", "完成": "[]", "失败": "['公会签到']"},
        )
        self.store.record_task_done(task, finished=2000.0)
        self.assertFalse(self.store.last_run("公会、小屋、酒馆")["ok"])

    def test_neutral_status_with_failed_phase_names_marks_run_failed(self):
        # MapTradeTask/MapCollectionTask report "…部分流程未完成。" with the
        # failed phase names in the 失败 key.
        task = _TaskStub(
            "每日跑商",
            info={"状态": "跑商部分流程未完成。", "完成": "-", "失败": "跑商买入", "跳过": "-"},
        )
        self.store.record_task_done(task, finished=2000.0)
        self.assertFalse(self.store.last_run("每日跑商")["ok"])

    def test_result_key_failure_marks_run_failed(self):
        # BargainLevelTask writes the verdict into 结果 instead of 状态.
        task = _TaskStub(
            "刷砍价等级",
            info={"状态": "页面文字确认超时，任务结束。", "结果": "失败"},
        )
        self.store.record_task_done(task, finished=2000.0)
        self.assertFalse(self.store.last_run("刷砍价等级")["ok"])

    def test_empty_info_snapshot_is_not_success(self):
        task = _TaskStub("空快照任务", info={})
        self.store.record_task_done(task, finished=2000.0)
        self.assertFalse(self.store.last_run("空快照任务")["ok"])

    def test_safety_stop_statuses_remain_due_for_retry(self):
        for name, status in (
            ("刷压制等级", "刷压制等级已停止。"),
            ("结束游戏", "未开启“确认关闭游戏”，安全停止。"),
            ("自动钓鱼", "缺少抛竿位置参考点，安全停止。"),
            ("地图探针", "未执行：测试卡带起止范围无效"),
        ):
            with self.subTest(name=name):
                self.store.record_task_done(
                    _TaskStub(name, info={"状态": status}),
                    finished=2000.0,
                )
                self.assertFalse(self.store.last_run(name)["ok"])

    def test_placeholder_failure_values_still_count_as_success(self):
        task = _TaskStub(
            "每日跑商",
            info={"状态": "跑商完成。", "完成": "跑商买入", "失败": "-", "跳过": "-"},
        )
        self.store.record_task_done(task, finished=2000.0)
        self.assertTrue(self.store.last_run("每日跑商")["ok"])

        task = _TaskStub(
            "公会、小屋、酒馆",
            info={"状态": "公会、小屋、酒馆结束。", "完成": "3", "失败": "0", "跳过": "0"},
        )
        self.store.record_task_done(task, finished=2100.0)
        self.assertTrue(self.store.last_run("公会、小屋、酒馆")["ok"])

    def test_pvp_and_goddess_failed_runs_remain_due_for_retry(self):
        from src.tasks.PVPTask import PVPTask
        from src.tasks.scheduler import TaskScheduleStore
        from src.tasks.SquareGoddessTask import SquareGoddessTask

        finished = _beijing_ts(2026, 9, 12, 12)
        for task_class, methods in (
            (PVPTask, ("_ensure_pvp_hub", "_start_auto_battle", "_wait_result_and_leave")),
            (SquareGoddessTask, (
                "_enter_square_from_home", "_pray_at_goddess", "_return_home_from_square",
            )),
        ):
            for failed_method in methods:
                with self.subTest(task=task_class.__name__, failed=failed_method):
                    info = {}
                    task = SimpleNamespace(
                        config={}, info=info, start_time=finished - 10,
                        name="镜中之战" if task_class is PVPTask else "广场女神像",
                        info_set=lambda key, value: info.__setitem__(key, value),
                        _status_set=lambda key, value: info.__setitem__(key, value),
                        log_info=lambda *args, **kwargs: None,
                        _target_multiplier=lambda: 4,
                    )
                    for method in methods:
                        value = False if method == failed_method else True
                        if method == "_start_auto_battle":
                            value = "failed" if method == failed_method else "started"
                        setattr(task, method, lambda *args, value=value: value)
                    self.assertFalse(task_class.run(task))
                    self.store.record_task_done(task, finished=finished)
                    record = self.store.last_run(task.name)
                    self.assertFalse(record["ok"])
                    self.assertFalse(self.store.is_completed_today(task.name, now=finished))
                    schedule = TaskScheduleStore(os.path.join(self.dir, "schedule.json"))
                    retry_at = schedule.delay_after_run(task.name, ok=record["ok"], now=finished)
                    self.assertGreater(retry_at, finished)
                    self.assertLess(retry_at, _beijing_ts(2026, 9, 13, 4))

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
        # The last attempt remains failed, while the earlier completed objective survives.
        self.store.record_task_done(
            _TaskStub("广场女神像", info={"Error": "x"}), finished=_beijing_ts(2026, 8, 18, 10, 0)
        )
        self.assertTrue(
            self.store.is_completed_today("广场女神像", now=_beijing_ts(2026, 8, 18, 11, 0))
        )
        self.assertFalse(self.store.last_run("广场女神像")["ok"])
        self.assertTrue(RunHistoryStore(self.path).is_completed_today(
            "广场女神像", now=_beijing_ts(2026, 8, 18, 11)))

    def test_changed_task_configuration_cannot_reuse_previous_completion(self):
        from src.tasks.run_history import configuration_scope
        task = _TaskStub("日常", {"状态": "完成"})
        task.config = {"执行公会签到": True, "执行小屋签到": False}
        stamp = _beijing_ts(2026, 10, 15, 9)
        self.store.record_task_done(task, finished=stamp, ok=True)
        self.assertTrue(self.store.is_completed_today(
            task.name, now=stamp, scope=configuration_scope(task)))
        task.config["执行小屋签到"] = True
        self.assertFalse(self.store.is_completed_today(
            task.name, now=stamp, scope=configuration_scope(task)))

    def test_is_completed_this_week(self):
        task = _TaskStub("每周跑图", info={"状态": "每周跑图完成。"})
        self.store.record_task_done(task, finished=_beijing_ts(2026, 8, 17, 8, 0))
        self.assertFalse(
            self.store.is_completed_this_week("每周跑图", now=_beijing_ts(2026, 8, 19, 12))
        )
        task.info["跑图完成范围"] = "今日"
        task.info["跑图周期"] = "2026-08-18"
        self.store.record_task_done(task, finished=_beijing_ts(2026, 8, 18, 23))
        reloaded = RunHistoryStore(self.path)
        self.assertTrue(reloaded.is_completed_today(
            "每周跑图", now=_beijing_ts(2026, 8, 19, 7, 59),
        ))
        self.assertFalse(reloaded.is_completed_today("每周跑图", now=_beijing_ts(2026, 8, 19, 8)))
        self.assertFalse(reloaded.is_completed_this_week(
            "每周跑图", now=_beijing_ts(2026, 8, 19, 7),
        ))
        task.info["跑图完成范围"] = "本周"
        task.info["跑图周期"] = "2026-08-17"
        self.store.record_task_done(task, finished=_beijing_ts(2026, 8, 19, 12))
        self.assertTrue(
            self.store.is_completed_this_week("每周跑图", now=_beijing_ts(2026, 8, 24, 7, 59))
        )
        self.assertFalse(
            self.store.is_completed_this_week("每周跑图", now=_beijing_ts(2026, 8, 24, 8))
        )
        self.store.record_task_done(task, finished=_beijing_ts(2026, 8, 24, 8, 1))
        self.assertFalse(
            self.store.is_completed_this_week("每周跑图", now=_beijing_ts(2026, 8, 24, 8, 2))
        )

    def test_collection_recorder_schedules_daily_weekly_and_crossed_refresh(self):
        from src.tasks import scheduler
        from src.tasks.run_history import install_run_history_recorder

        install_run_history_recorder()
        schedule = scheduler.TaskScheduleStore(os.path.join(self.dir, "schedule.json"))
        for period, cycle, day, expected in (
            ("今日", "2026-08-19", 19, _beijing_ts(2026, 8, 20, 8)),
            ("本周", "2026-08-17", 19, _beijing_ts(2026, 8, 24, 8)),
            ("本周", "2026-08-17", 24, _beijing_ts(2026, 8, 24, 12)),
        ):
            with self.subTest(period=period, day=day):
                now = _beijing_ts(2026, 8, day, 12)
                task = _TaskStub("每周跑图", {
                    "状态": "跑图完成。", "跑图完成范围": period, "跑图周期": cycle,
                })
                with patch("src.tasks.run_history.default_store", return_value=self.store), patch(
                    "src.tasks.scheduler.default_store", return_value=schedule,
                ), patch("time.time", return_value=now):
                    install_run_history_recorder._recorder.on_task_done(task)
                self.assertEqual(expected, schedule.next_run(task.name))

    def test_batch_completion_keeps_each_child_actual_finish_time(self):
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
        child_finish = _beijing_ts(2026, 8, 18, 3, 59)
        self.store.record_task_done(
            executor.get_task_by_class(daily_cls),
            finished=child_finish,
            started=child_finish - 30,
            ok=True,
        )
        self.store.record_task_done(
            executor.get_task_by_class(pvp_cls),
            finished=child_finish + 120,
            started=child_finish + 30,
            ok=False,
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
        self.assertEqual(child_finish, self.store.last_run("公会、小屋、酒馆")["finished"])
        self.assertEqual(30, self.store.last_run("公会、小屋、酒馆")["duration"])
        self.assertFalse(self.store.is_completed_today(
            "公会、小屋、酒馆", now=_beijing_ts(2026, 8, 18, 9, 30)
        ))
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
