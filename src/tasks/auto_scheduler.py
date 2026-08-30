"""启动后自动执行到期任务（ALAS 式调度的执行入口侧）.

对应 ALAS 主循环"启动即检查到期任务并执行最早到期者"的行为，但不引入
常驻等待循环：安装后延迟检查一次，此后每当有任务结束（task_done）且执行
器空闲时复查一次。所有自动启动都由一键完成日常卡片上的两个显式开关控制
（默认关闭）：

- ``启动自动执行日常``：存在今日未完成且调度到期的日常子任务时，以
  "仅执行今日未完成"模式启动一键完成日常，由批处理自身跳过已完成项。
- ``启动自动执行每周跑图``：本周尚未完成每周跑图且调度到期时启动它。

用户手动点击任务不受影响（视为强制执行），与 ALAS 中"手动把 NextRun 改
到过去即强制立即运行"的语义一致。
"""

from __future__ import annotations

# 安装后等待窗口与场景稳定，再执行首次调度检查。
INSTALL_DELAY_SECONDS = 20.0
# 每个任务结束后等待片刻、执行器完全空闲后再复查下一批到期任务。
RECHECK_DELAY_SECONDS = 8.0


def _resolve_og(og=None):
    if og is not None:
        return og
    from ok import og

    return og


def run_due_tasks_once(og=None) -> str | None:
    """Run one scheduler pass: start the first eligible due task, if any.

    Returns a short description of what was started, or ``None``.  Only one
    task is started per pass; the post-task recheck chains further passes.
    """
    og = _resolve_og(og)
    executor = getattr(og, "executor", None)
    app = getattr(og, "app", None)
    if executor is None or app is None:
        return None
    if getattr(executor, "current_task", None) is not None:
        return None

    from src.tasks import scheduler as task_scheduler
    from src.tasks.DailyBatchTask import DailyBatchTask
    from src.tasks.run_history import default_store as default_history_store

    history = default_history_store()
    schedule_store = task_scheduler.default_store()

    batch = executor.get_task_by_class(DailyBatchTask)
    if batch is None:
        return None
    config = getattr(batch, "config", {}) or {}

    if bool(config.get("启动自动执行日常", False)) and _any_batch_child_due(
        batch, executor, history, schedule_store
    ):
        from src.tasks.DailyBatchTask import RUN_MODE_INCOMPLETE

        batch.request_run_mode(RUN_MODE_INCOMPLETE)
        app.start_controller.start(batch)
        return "一键完成日常（仅执行今日未完成）"

    if bool(config.get("启动自动执行每周跑图", False)):
        from src.tasks.MapCollectionTask import MapCollectionTask

        map_task = executor.get_task_by_class(MapCollectionTask)
        if map_task is not None and _weekly_map_due(
            map_task, history, schedule_store
        ):
            app.start_controller.start(map_task)
            return str(getattr(map_task, "name", "每周跑图"))

    return None


def _any_batch_child_due(batch, executor, history, schedule_store, now=None) -> bool:
    """Whether any enabled batch child still needs to run today and is due."""
    for child in getattr(batch, "child_tasks", ()) or ():
        if not bool(batch.config.get(child.config_key, True)):
            continue
        task = executor.get_task_by_class(child.task_class)
        if task is None:
            continue
        name = str(getattr(task, "name", child.config_key))
        if history.is_completed_today(name, now=now):
            continue
        if not schedule_store.is_due(name, now=now):
            continue
        return True
    return False


def _weekly_map_due(map_task, history, schedule_store, now=None) -> bool:
    name = str(getattr(map_task, "name", ""))
    if history.is_completed_this_week(name, now=now):
        return False
    return schedule_store.is_due(name, now=now)


def install_auto_scheduler() -> bool:
    """Install the startup check and the post-task recheck (idempotent)."""
    from ok.gui.Communicate import communicate
    from PySide6.QtCore import QCoreApplication, QObject, QTimer

    if getattr(install_auto_scheduler, "_installed", False):
        return False
    install_auto_scheduler._installed = True

    if QCoreApplication.instance() is None:
        # 无事件循环（测试/工具的导入链）：不挂定时器与信号，保持无副作用。
        return True

    class _AutoRunner(QObject):
        def on_task_done(self, task):
            QTimer.singleShot(int(RECHECK_DELAY_SECONDS * 1000), run_due_tasks_once)

    runner = _AutoRunner()
    communicate.task_done.connect(runner.on_task_done)
    QTimer.singleShot(int(INSTALL_DELAY_SECONDS * 1000), run_due_tasks_once)
    # Keep the receiver alive for the app's lifetime.
    install_auto_scheduler._runner = runner
    return True
