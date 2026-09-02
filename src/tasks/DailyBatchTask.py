from __future__ import annotations

import time
from collections import ChainMap
from dataclasses import dataclass

from ok import BaseTask
from qfluentwidgets import FluentIcon

from src.tasks.DailyTask import DailyTask
from src.tasks.FreeGachaTask import FreeGachaTask
from src.tasks.MapTradeTask import MapTradeTask
from src.tasks.PVPTask import PVPTask
from src.tasks.QuickHuntTask import QuickHuntTask
from src.tasks.SquareGoddessTask import SquareGoddessTask
from src.tasks.task_notifications import (
    log_task_completion,
    suppress_task_completion_notifications,
)

RUN_MODE_ALL = "all"
RUN_MODE_INCOMPLETE = "incomplete"
_VALID_RUN_MODES = frozenset({RUN_MODE_ALL, RUN_MODE_INCOMPLETE})
# 启动自动执行注入的 run_mode 有效期：覆盖冷启动拉起游戏到设备就绪的
# 常规耗时；超时未消费即作废，防止启动失败后的残留模式被手动点击继承。
REQUESTED_RUN_MODE_VALIDITY_SECONDS = 600.0


@dataclass(frozen=True)
class DailyBatchChild:
    config_key: str
    task_class: type[BaseTask]


DAILY_BATCH_CHILDREN = (
    DailyBatchChild("公会、小屋、酒馆", DailyTask),
    DailyBatchChild("快速狩猎", QuickHuntTask),
    DailyBatchChild("免费抽抽乐", FreeGachaTask),
    DailyBatchChild("广场女神像", SquareGoddessTask),
    DailyBatchChild("自动PVP", PVPTask),
    DailyBatchChild("跑商", MapTradeTask),
)


class DailyBatchTask(BaseTask):
    """Run the selected home-to-home daily tasks in a fixed safe order."""

    status_keys = [
        "启用",
        "状态",
        "运行模式",
        "当前子任务",
        "完成",
        "失败",
        "跳过",
        "Log",
        "Warning",
        "Error",
    ]
    child_tasks = DAILY_BATCH_CHILDREN

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "一键完成日常"
        self.description = (
            "按顺序执行已开启的公会、小屋、酒馆、快速狩猎、抽抽乐、"
            "广场、PVP和跑商。"
        )
        self.icon = FluentIcon.COMPLETED
        self.group_name = "日常/周常"
        self.group_icon = FluentIcon.CALENDAR
        self.visible = True
        self._requested_run_mode = RUN_MODE_ALL
        self._start_after_login = False

        child_keys = [child.config_key for child in self.child_tasks]
        self.default_config.update(
            {
                "启用": True,
                "启动自动执行日常": False,
                "启动自动执行每周跑图": False,
                **{key: True for key in child_keys},
            }
        )
        self.config_description.update(
            {
                "启用": "是否允许一键完成日常按顺序执行已开启的子任务。",
                "启动自动执行日常": (
                    "应用启动后，当存在今日未完成且调度到期的日常子任务时，"
                    "自动以「仅执行今日未完成」模式运行一键完成日常；"
                    "已完成的子任务仍会被跳过。"
                ),
                "启动自动执行每周跑图": (
                    "应用启动后，当本周（周一 04:00 起）尚未完成每周跑图"
                    "且其调度到期时，自动执行每周跑图；仅在调试模式生效，"
                    "自动执行时会按需拉起游戏客户端。"
                ),
                **{
                    key: f"是否在一键完成日常中执行{key}。"
                    for key in child_keys
                },
            }
        )
        config_type_updates = {
            "启用": {
                "sub_configs": {
                    True: child_keys,
                }
            }
        }
        if not bool(getattr(self._app, "debug", False)):
            # 每周跑图在正式前端保持隐藏（v0.1.21 产品决策）；其自动执行
            # 开关同样只在调试模式展示，防止正式用户重新启用隐藏任务。
            config_type_updates["启动自动执行每周跑图"] = {"hidden": True}
        self.config_type.update(config_type_updates)

    def request_run_mode(self, run_mode: str) -> None:
        """Select the next executor-driven run without persisting UI config.

        请求带有效期（覆盖启动器拉起游戏到设备就绪的常规耗时）：启动失败
        （do_start 异步失败、设备无法就绪）时 run() 不会执行、请求无法被
        消费，过期后自动作废，避免残留的「仅执行今日未完成」被之后的
        手动点击静默继承。
        """
        self._requested_run_mode = self._validate_run_mode(run_mode)
        self._requested_run_mode_deadline = (
            time.monotonic() + REQUESTED_RUN_MODE_VALIDITY_SECONDS
        )

    @staticmethod
    def _validate_run_mode(run_mode: str) -> str:
        if run_mode not in _VALID_RUN_MODES:
            raise ValueError(f"unsupported daily batch run mode: {run_mode}")
        return run_mode

    def _take_run_mode(self, explicit_run_mode: str | None) -> str:
        requested = getattr(self, "_requested_run_mode", RUN_MODE_ALL)
        self._requested_run_mode = RUN_MODE_ALL
        if (
            requested != RUN_MODE_ALL
            and time.monotonic()
            > getattr(self, "_requested_run_mode_deadline", 0.0)
        ):
            requested = RUN_MODE_ALL
        return self._validate_run_mode(explicit_run_mode or requested)

    def _delay_child_schedule(self, schedule_store, child_name: str, ok: bool) -> None:
        """ALAS 式 task_delay：子任务结束后按策略推迟 next_run 并落盘。

        两种运行模式都记录；无调度策略的子任务由账本自行忽略。
        """
        if schedule_store is None:
            return
        try:
            schedule_store.delay_after_run(child_name, ok=ok)
        except Exception as exc:  # 调度账本失败不影响子任务结果
            self.log_error(f"一键完成日常：记录 {child_name} 的调度时间失败。", exc)

    def _auto_login_pending(self) -> bool:
        """自动登录启用且未完成时为 True（与 auto_scheduler 的启动门控同语义）。

        执行器中 onetime 出队优先于登录 trigger，且 onetime 运行期间 trigger
        不执行：手动点开始且游戏冷启动时立即跑子任务，只会在登录页/公告页上
        把主页确认烧超时并中止整批，登录完成后也没有任何机制重新拉起批次。
        """
        try:
            from src.tasks.trigger.AutoLoginTask import AutoLoginTask
        except ImportError:  # pragma: no cover - 任务注册表缺失时的极端场景
            return False
        login_task = self.executor.get_task_by_class(AutoLoginTask)
        if login_task is None:
            return False
        if not bool(getattr(login_task, "_enabled", True)):
            return False
        return not bool(getattr(login_task, "_finished", False))

    @classmethod
    def release_after_login(cls, executor) -> bool:
        """自动登录落定后放行被门控挂起的批次；返回是否有批次被放行。"""
        batch = executor.get_task_by_class(cls)
        if batch is None or not getattr(batch, "_start_after_login", False):
            return False
        batch._start_after_login = False
        batch._enabled = True
        if not executor.enqueue_onetime_task(batch):
            return False
        batch.log_info("一键完成日常：自动登录已完成，开始执行。")
        return True

    def run(self, run_mode: str | None = None):
        if self._auto_login_pending():
            # 门控期间不消费注入的 run_mode，放行后的正式运行再取。
            self._start_after_login = True
            self.info_set("状态", "等待自动登录完成，完成后自动开始。")
            self.log_info("一键完成日常：自动登录未完成，待登录完成后自动开始。")
            return True
        run_mode = self._take_run_mode(run_mode)
        if not bool(self.config.get("启用", True)):
            self.info_set("状态", "一键完成日常已禁用。")
            return True

        only_incomplete = run_mode == RUN_MODE_INCOMPLETE
        from src.tasks import scheduler as task_scheduler
        from src.tasks.run_history import default_store

        history = default_store() if only_incomplete else None
        schedule_store = task_scheduler.default_store()

        completed: list[str] = []
        failed: list[str] = []
        skipped: list[str] = []
        stop_remaining = False

        def publish_outcome() -> None:
            # Live per-child progress feeds the run panel's segments and
            # cells during the run; the same keys are written once more with
            # the final values after the loop.
            self.info_set("完成", "、".join(completed) or "-")
            self.info_set("失败", "、".join(failed) or "-")
            self.info_set("跳过", "、".join(skipped) or "-")

        self.info_set("状态", "一键完成日常启动。")
        self.info_set(
            "运行模式",
            "仅执行今日未完成" if only_incomplete else "全部已启用子任务",
        )

        for child in self.child_tasks:
            if not bool(self.config.get(child.config_key, True)) or stop_remaining:
                skipped.append(child.config_key)
                publish_outcome()
                continue

            task = self.executor.get_task_by_class(child.task_class)
            if task is None:
                failed.append(child.config_key)
                publish_outcome()
                stop_remaining = True
                self.log_error(f"一键完成日常：未找到子任务 {child.config_key}。")
                continue

            if history is not None and history.is_completed_today(str(task.name)):
                skipped.append(child.config_key)
                publish_outcome()
                self.log_info(f"一键完成日常：{child.config_key} 今日已完成，跳过。")
                continue

            if only_incomplete and not schedule_store.is_due(str(task.name)):
                remaining = schedule_store.backoff_remaining_minutes(str(task.name))
                skipped.append(child.config_key)
                publish_outcome()
                self.log_info(
                    f"一键完成日常：{child.config_key} 调度未到期"
                    f"（约 {remaining:.0f} 分钟后可执行），跳过。"
                )
                continue

            self.info_set("当前子任务", child.config_key)
            self.log_info(f"一键完成日常：开始 {child.config_key}。")
            original_config = task.config
            try:
                # The switches on this card are authoritative for this run. Keep
                # the child task's persisted configuration unchanged while making
                # its own top-level 启用 gate transparent to the batch runner.
                task.config = ChainMap({"启用": True}, original_config or {})
                task.info_clear()
                with suppress_task_completion_notifications(task):
                    if bool(task.run()):
                        completed.append(child.config_key)
                        publish_outcome()
                        self.log_info(f"一键完成日常：{child.config_key} 完成。")
                        self._delay_child_schedule(schedule_store, str(task.name), True)
                    else:
                        failed.append(child.config_key)
                        publish_outcome()
                        stop_remaining = True
                        self._delay_child_schedule(schedule_store, str(task.name), False)
                        self.log_warning(
                            f"一键完成日常：{child.config_key} 失败，停止后续子任务。"
                        )
            except Exception as exc:
                failed.append(child.config_key)
                publish_outcome()
                stop_remaining = True
                self._delay_child_schedule(schedule_store, str(task.name), False)
                self.log_error(
                    f"一键完成日常：{child.config_key} 异常，停止后续子任务。",
                    exc,
                )
            finally:
                task.config = original_config
                self.executor.reset_scene(check_enabled=False)

        self.info_set("当前子任务", "-")
        self.info_set("完成", "、".join(completed) or "-")
        self.info_set("失败", "、".join(failed) or "-")
        self.info_set("跳过", "、".join(skipped) or "-")
        if failed:
            self.info_set("状态", "一键完成日常中止。")
            return False

        self.info_set("状态", "一键完成日常完成。")
        log_task_completion(
            self,
            f"一键完成日常完成：已执行 {len(completed)} 项，跳过 {len(skipped)} 项。",
        )
        return True
