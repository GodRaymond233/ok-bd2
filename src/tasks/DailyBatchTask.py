from __future__ import annotations

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

        child_keys = [child.config_key for child in self.child_tasks]
        self.default_config.update(
            {
                "启用": True,
                **{key: True for key in child_keys},
            }
        )
        self.config_description.update(
            {
                "启用": "是否允许一键完成日常按顺序执行已开启的子任务。",
                **{
                    key: f"是否在一键完成日常中执行{key}。"
                    for key in child_keys
                },
            }
        )
        self.config_type.update(
            {
                "启用": {
                    "sub_configs": {
                        True: child_keys,
                    }
                }
            }
        )

    def request_run_mode(self, run_mode: str) -> None:
        """Select the next executor-driven run without persisting UI config."""
        self._requested_run_mode = self._validate_run_mode(run_mode)

    @staticmethod
    def _validate_run_mode(run_mode: str) -> str:
        if run_mode not in _VALID_RUN_MODES:
            raise ValueError(f"unsupported daily batch run mode: {run_mode}")
        return run_mode

    def _take_run_mode(self, explicit_run_mode: str | None) -> str:
        requested = getattr(self, "_requested_run_mode", RUN_MODE_ALL)
        self._requested_run_mode = RUN_MODE_ALL
        return self._validate_run_mode(explicit_run_mode or requested)

    def run(self, run_mode: str | None = None):
        run_mode = self._take_run_mode(run_mode)
        if not bool(self.config.get("启用", True)):
            self.info_set("状态", "一键完成日常已禁用。")
            return True

        only_incomplete = run_mode == RUN_MODE_INCOMPLETE
        history = None
        if only_incomplete:
            from src.tasks.run_history import default_store

            history = default_store()

        completed: list[str] = []
        failed: list[str] = []
        skipped: list[str] = []
        stop_remaining = False
        self.info_set("状态", "一键完成日常启动。")
        self.info_set(
            "运行模式",
            "仅执行今日未完成" if only_incomplete else "全部已启用子任务",
        )

        for child in self.child_tasks:
            if not bool(self.config.get(child.config_key, True)) or stop_remaining:
                skipped.append(child.config_key)
                continue

            task = self.executor.get_task_by_class(child.task_class)
            if task is None:
                failed.append(child.config_key)
                stop_remaining = True
                self.log_error(f"一键完成日常：未找到子任务 {child.config_key}。")
                continue

            if history is not None and history.is_completed_today(str(task.name)):
                skipped.append(child.config_key)
                self.log_info(f"一键完成日常：{child.config_key} 今日已完成，跳过。")
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
                        self.log_info(f"一键完成日常：{child.config_key} 完成。")
                    else:
                        failed.append(child.config_key)
                        stop_remaining = True
                        self.log_warning(
                            f"一键完成日常：{child.config_key} 失败，停止后续子任务。"
                        )
            except Exception as exc:
                failed.append(child.config_key)
                stop_remaining = True
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
