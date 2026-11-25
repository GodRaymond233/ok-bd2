from __future__ import annotations

import os
import subprocess
import time
from collections import ChainMap
from dataclasses import dataclass

from ok import BaseTask
from ok.task.exceptions import FinishedException, TaskDisabledException

from src.diagnostics.runtime import observe_run, task_error
from src.icons import TaskIcon as FluentIcon
from src.tasks.ActivityDailyTask import ACTIVITY_DAILY_NAME, ActivityDailyTask
from src.tasks.BaseBD2Task import task_info_snapshot
from src.tasks.CloseGameTask import AFTER_DAILY_KEY, CloseGameTask
from src.tasks.DailyTask import DailyTask
from src.tasks.equipment_daily import EQUIPMENT_DAILY_NAME
from src.tasks.EquipmentDailyTask import EquipmentDailyTask
from src.tasks.FreeGachaTask import FreeGachaTask
from src.tasks.MapCollectionTask import MapCollectionTask
from src.tasks.MapTradeTask import MapTradeTask
from src.tasks.MissionRewardTask import MissionRewardTask
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
# 「完成日常后自动关机」的关机倒计时秒数：留出执行 shutdown /a 取消的窗口。
SHUTDOWN_COUNTDOWN_SECONDS = 60


def _schedule_system_shutdown(seconds: int) -> bool:
    """Schedule a Windows shutdown via System32 shutdown.exe.

    shutdown.exe 注册计划后立即退出，返回值即计划是否被系统接受；不等待
    倒计时。使用绝对路径防止安装目录内同名可执行文件顶替，并以
    CREATE_NO_WINDOW 避免在 GUI 进程里闪出控制台窗口。
    """
    command = [
        os.path.join(
            os.environ.get("SystemRoot", r"C:\Windows"),
            "System32",
            "shutdown.exe",
        ),
        "/s",
        "/t",
        str(int(seconds)),
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        timeout=10,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return result.returncode == 0


@dataclass(frozen=True)
class DailyBatchChild:
    config_key: str
    task_class: type[BaseTask]
    default_enabled: bool = True


DAILY_BATCH_CHILDREN = (
    DailyBatchChild("公会、小屋、酒馆", DailyTask),
    DailyBatchChild("免费抽抽乐", FreeGachaTask),
    DailyBatchChild("快速狩猎", QuickHuntTask),
    DailyBatchChild("广场女神像", SquareGoddessTask),
    DailyBatchChild("跑图", MapCollectionTask),
    DailyBatchChild("跑商", MapTradeTask),
    DailyBatchChild("自动PVP", PVPTask),
    DailyBatchChild(EQUIPMENT_DAILY_NAME, EquipmentDailyTask, default_enabled=False),
    DailyBatchChild(ACTIVITY_DAILY_NAME, ActivityDailyTask, default_enabled=False),
    DailyBatchChild("领取每日及每周奖励", MissionRewardTask),
)


class DailyBatchTask(BaseTask):
    waits_for_login = True
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
            "按顺序执行已开启的公会、小屋、酒馆、抽抽乐、快速狩猎、"
            "广场、跑图、跑商、PVP和每日活动，最后领取每日及每周奖励。"
        )
        self.icon = FluentIcon.COMPLETED
        self.group_name = "日常/周常"
        self.group_icon = FluentIcon.CALENDAR
        self.visible = True
        self._requested_run_mode = RUN_MODE_ALL
        self._start_after_login = False
        self._skip_reasons: dict[str, dict] = {}

        child_keys = [child.config_key for child in self.child_tasks]
        self.default_config.update(
            {
                "启用": True,
                "启动自动执行日常": False,
                "完成日常后自动关机": False,
                **{child.config_key: child.default_enabled for child in self.child_tasks},
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
                "完成日常后自动关机": (
                    "一键完成日常运行成功且全部已启用子任务今日均已完成"
                    "（含运行前就已完成的项目）后，60 秒倒计时自动关机；"
                    "期间在系统命令行执行 shutdown /a 可取消。"
                ),
                **{
                    key: f"是否在一键完成日常中执行{key}。"
                    for key in child_keys
                },
                "跑图": (
                    "沿用每周跑图卡片的配置；执行剩余时，今日额度已用尽或本周已完成则跳过。"
                    "技能次数每日08:00、资源周一08:00刷新（北京时间）。"
                ),
                ACTIVITY_DAILY_NAME: "优先挑战战斗，按15、10、5关查找快速战斗并使用免费活动AP。",
                "领取每日及每周奖励": "在日常流程最后领取奖励；同日再次执行也会复查新增奖励。",
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

    def skip_reasons(self) -> dict[str, dict]:
        """Why each child of the current or last run was skipped, by config key.

        ``reason`` is ``off`` (not taking part), ``stopped`` (an earlier child failed and
        could not return home), ``done`` (completed this cycle), ``settlement`` (the weekly
        PVP closure) or ``not_due`` (failure back-off; ``minutes`` until it may run). Kept in
        memory until the next run.
        """
        # dict() copies in one step, so the executor thread adding a reason mid-copy
        # cannot break the API thread's iteration.
        reasons = dict(getattr(self, "_skip_reasons", {}))
        return {key: dict(value) for key, value in reasons.items()}

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
        """子任务结束后按完成周期或失败退避时间更新 next_run。

        两种运行模式都记录；无调度策略的子任务由账本自行忽略。
        """
        if schedule_store is None:
            return
        try:
            from src.tasks.run_history import default_store

            schedule_store.delay_after_record(child_name, ok=ok, history=default_store())
        except Exception as exc:  # 调度账本失败不影响子任务结果
            self.log_error(f"一键完成日常：记录 {child_name} 的调度时间失败。", exc)

    def park_run_mode(self):
        self._requested_run_mode = self._take_run_mode(None)
        self._requested_run_mode_deadline = float("inf")

    def log_error(self, *args, **kwargs):
        task_error()
        return super().log_error(*args, **kwargs)

    @observe_run
    def run(self, run_mode: str | None = None):
        run_mode = self._take_run_mode(run_mode)
        if not bool(self.config.get("启用", True)):
            self.info_set("状态", "一键完成日常已禁用。")
            return True

        only_incomplete = run_mode == RUN_MODE_INCOMPLETE
        self._skip_reasons = {}
        from src.tasks import scheduler as task_scheduler
        from src.tasks.run_history import configuration_scope, default_store

        history = default_store()
        schedule_store = task_scheduler.default_store()

        completed: list[str] = []
        # 运行前就已完成而跳过的子任务：关机判定不能依赖运行结束后的
        # is_completed_today 重算，否则运行跨过北京时间 04:00 日界后
        # 这些记录不再算「今日」，会漏关机。
        pre_completed: list[str] = []
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
            taking_part = bool(self.config.get(child.config_key, child.default_enabled))
            if not taking_part or stop_remaining:
                skipped.append(child.config_key)
                self._skip_reasons[child.config_key] = {
                    "reason": "off" if not taking_part else "stopped", "minutes": None,
                }
                publish_outcome()
                continue

            task = self.executor.get_task_by_class(child.task_class)
            if task is None:
                failed.append(child.config_key)
                publish_outcome()
                stop_remaining = True
                self.log_error(f"一键完成日常：未找到子任务 {child.config_key}。")
                continue

            if child.task_class is PVPTask and task._skip_weekly_settlement():
                skipped.append(child.config_key)
                self._skip_reasons[child.config_key] = {"reason": "settlement", "minutes": None}
                publish_outcome()
                self.log_info(f"一键完成日常：{child.config_key} 赛季结算，本次跳过。")
                continue

            recheck = bool(getattr(task, "recheck_in_batch", False))
            if only_incomplete and not recheck and history.is_completed(
                str(task.name), scope=configuration_scope(task),
            ):
                skipped.append(child.config_key)
                pre_completed.append(child.config_key)
                self._skip_reasons[child.config_key] = {"reason": "done", "minutes": None}
                publish_outcome()
                self.log_info(f"一键完成日常：{child.config_key} 今日已完成，跳过。")
                continue

            last_run_ok = bool((history.last_run(str(task.name)) or {}).get("ok"))
            if only_incomplete and not (recheck and last_run_ok) and not schedule_store.is_due(
                str(task.name), scope=configuration_scope(task),
            ):
                remaining = schedule_store.backoff_remaining_minutes(str(task.name))
                skipped.append(child.config_key)
                self._skip_reasons[child.config_key] = {
                    "reason": "not_due", "minutes": round(remaining),
                }
                publish_outcome()
                self.log_info(
                    f"一键完成日常：{child.config_key} 调度未到期"
                    f"（约 {remaining:.0f} 分钟后可执行），跳过。"
                )
                continue

            self.info_set("当前子任务", child.config_key)
            self.log_info(f"一键完成日常：开始 {child.config_key}。")
            original_config = task.config
            child_started = time.time()
            child_result = None
            child_finished = None
            try:
                # The switches on this card are authoritative for this run. Keep
                # the child task's persisted configuration unchanged while making
                # its own top-level 启用 gate transparent to the batch runner.
                task.config = ChainMap({"启用": True, "_仅执行未完成步骤": only_incomplete},
                                       original_config or {})
                task.info_clear()
                with suppress_task_completion_notifications(task):
                    child_result = bool(task.run())
                    child_finished = time.time()
                    if task_info_snapshot(task).get("PVP 结算休战"):
                        child_result = None
                        skipped.append(child.config_key)
                        self._skip_reasons[child.config_key] = {
                            "reason": "settlement", "minutes": None,
                        }
                        publish_outcome()
                        self.log_info(f"一键完成日常：{child.config_key} 赛季结算，本次跳过。")
                    elif child_result:
                        completed.append(child.config_key)
                        publish_outcome()
                        self.log_info(f"一键完成日常：{child.config_key} 完成。")
                    else:
                        failed.append(child.config_key)
                        publish_outcome()
                        self.log_warning(f"一键完成日常：{child.config_key} 失败。")
                at_home = self._finish_child_at_home(task)
                stop_remaining = child_result is False and not at_home
            except (TaskDisabledException, FinishedException):
                raise
            except Exception as exc:
                child_result = False
                child_finished = time.time()
                failed.append(child.config_key)
                publish_outcome()
                stop_remaining = not self._finish_child_at_home(task)
                self.log_error(
                    f"一键完成日常：{child.config_key} 异常。",
                    exc,
                )
            finally:
                task.config = original_config
                if child_result is not None:
                    try:
                        history.record_task_done(
                            task,
                            finished=child_finished,
                            started=child_started,
                            ok=child_result,
                        )
                    except Exception as exc:
                        self.log_error(
                            f"一键完成日常：记录 {child.config_key} 的运行结果失败。", exc
                        )
                    self._delay_child_schedule(schedule_store, str(task.name), child_result)
                self.executor.reset_scene(check_enabled=False)

        self.info_set("当前子任务", "-")
        publish_outcome()
        if failed:
            self.info_set("状态", "一键完成日常中止。" if stop_remaining
                          else f"一键完成日常执行结束，{len(failed)} 项失败。")
            return False

        self.info_set("状态", "一键完成日常完成。")
        log_task_completion(
            self,
            f"一键完成日常完成：已执行 {len(completed)} 项，跳过 {len(skipped)} 项。",
        )
        self._maybe_shutdown_after_daily(completed, pre_completed)
        self._maybe_close_game_after_daily(completed, pre_completed)
        return True

    @staticmethod
    def _finish_child_at_home(task) -> bool:
        recover = getattr(task, "finish_gameplay_at_home", None)
        if recover is None:
            recover = getattr(task, "recover_daily_home", None)
        try:
            return bool(recover and recover())
        except (TaskDisabledException, FinishedException):
            raise
        except Exception:
            return False

    def _daily_completion_block(
        self,
        completed: list[str],
        pre_completed: list[str] | tuple[str, ...] = (),
    ) -> str | None:
        """Keep completed work from the same run if it crossed the daily reset."""
        enabled_children = [
            child
            for child in self.child_tasks
            if bool(self.config.get(child.config_key, child.default_enabled))
        ]
        if not enabled_children:
            return "没有已启用子任务"
        from src.tasks.run_history import configuration_scope, default_store

        history = default_store()
        for child in enabled_children:
            if child.config_key in completed or child.config_key in pre_completed:
                continue
            task = self.executor.get_task_by_class(child.task_class)
            name = str(getattr(task, "name", None) or child.config_key)
            if not history.is_completed(name, scope=configuration_scope(task)):
                return f"{child.config_key} 今日未完成"
        return None

    def _maybe_shutdown_after_daily(
        self,
        completed: list[str],
        pre_completed: list[str] | tuple[str, ...] = (),
    ) -> None:
        if not bool(self.config.get("完成日常后自动关机", False)):
            return
        if reason := self._daily_completion_block(completed, pre_completed):
            self.log_info(f"一键完成日常：{reason}，不执行自动关机。")
            return
        try:
            scheduled = _schedule_system_shutdown(SHUTDOWN_COUNTDOWN_SECONDS)
        except (OSError, subprocess.SubprocessError) as exc:
            self.log_error("一键完成日常：自动关机调用失败。", exc)
            return
        if not scheduled:
            self.log_error("一键完成日常：系统拒绝了自动关机计划，本次关机未执行。")
            return
        self.info_set(
            "状态", f"今日日常已全部完成，{SHUTDOWN_COUNTDOWN_SECONDS} 秒后自动关机。"
        )
        self.log_info(
            f"一键完成日常：今日日常已全部完成，{SHUTDOWN_COUNTDOWN_SECONDS} 秒后"
            "自动关机；取消请在命令行执行 shutdown /a。",
            notify=True,
        )

    def _maybe_close_game_after_daily(self, completed, pre_completed):
        task = self.executor.get_task_by_class(CloseGameTask)
        if (task is None or not task.config.get(AFTER_DAILY_KEY, False)
                or self.config.get("完成日常后自动关机", False)):
            return
        if getattr(self.executor, "onetime_task_queue", ()):
            self.log_info("一键完成日常：有任务排队，游戏继续运行。")
            return
        if reason := self._daily_completion_block(completed, pre_completed):
            self.log_info(f"一键完成日常：{reason}，游戏继续运行。")
            return
        if getattr(self.executor, "current_task", None) is self:
            self.executor.check_enabled(check_pause=False)
        try:
            task.run()
        except (ValueError, OSError) as error:
            self.log_warning(f"一键完成日常：{error}")
            return
        self.info_set("状态", "一键完成日常完成，游戏已关闭。")
