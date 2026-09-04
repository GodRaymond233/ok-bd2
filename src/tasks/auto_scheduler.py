"""启动后自动执行到期任务（ALAS 式调度的执行入口侧）.

对应 ALAS 主循环"启动即检查到期任务并执行最早到期者"的行为，但不引入
常驻等待循环：安装后延迟检查一次，此后每当有任务结束（task_done）且执行
器空闲时复查一次。自动启动由一键完成日常卡片上的显式开关控制（默认关闭）：

- ``启动自动执行日常``：存在今日未完成且调度到期的日常子任务时，以
  "仅执行今日未完成"模式启动一键完成日常，由批处理自身跳过已完成项。

用户手动点击任务不受影响（视为强制执行），与 ALAS 中"手动把 NextRun 改
到过去即强制立即运行"的语义一致。
"""

from __future__ import annotations

import time

# 安装后等待窗口与场景稳定，再执行首次调度检查。
INSTALL_DELAY_SECONDS = 20.0
# 首次检查若执行器忙（自动登录等 trigger 任务未结束）或暂无到期任务，按该
# 间隔有限重试；覆盖冷启动登录耗时。预算耗尽后放弃本轮启动检查，之后的
# 执行完全由 task_done 复查驱动，避免无人值守时无限拉起游戏。
STARTUP_RETRY_INTERVAL_SECONDS = 30.0
STARTUP_RETRY_BUDGET_SECONDS = 900.0
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
    if not bool(config.get("启用", True)):
        # 批处理自身被禁用时整条自动执行链路关闭。“启动自动执行日常”等
        # 开关刻意不挂在“启用”的 sub_configs 下（禁用批处理时开关仍保存
        # 旧值），若不在此处拦住，会出现
        # start -> run() 立即返回 -> task_done -> 再 start 的空转循环。
        return None

    if not _login_settled(executor):
        # 自动登录未完成时绝不启动任务：登录 trigger 是逐帧推进的状态机，
        # 周期间有约 1 秒的执行器空闲窗口，仅靠 current_task 忙检测必然
        # 漏判；批处理一旦抢先启动会顶掉登录 trigger（onetime 先于
        # trigger 出队），首个子任务在登录页上主页确认失败并进入 30 分钟
        # 退避，而触发任务不发 task_done，登录完成后没有任何复查来源。
        # 交给启动重试预算继续等待。
        return None

    if bool(config.get("启动自动执行日常", False)) and _any_batch_child_due(
        batch, executor, history, schedule_store
    ):
        from src.tasks.DailyBatchTask import RUN_MODE_INCOMPLETE

        batch.request_run_mode(RUN_MODE_INCOMPLETE)
        app.start_controller.start(batch)
        return "一键完成日常（仅执行今日未完成）"

    return None


def _login_settled(executor) -> bool:
    """Whether it is safe to auto-start a task right now.

    自动登录 trigger（AutoLoginTask）启用且尚未完成（``_finished``）时返回
    False，让调用方继续等待；其余情况（无登录任务、已停用、已完成）一律
    放行，避免停用自动登录的用户永远等不到自动执行。
    """
    try:
        from src.tasks.trigger.AutoLoginTask import AutoLoginTask
    except ImportError:  # pragma: no cover - 任务注册表缺失时的极端场景
        return True
    login_task = executor.get_task_by_class(AutoLoginTask)
    if login_task is None:
        return True
    if not bool(getattr(login_task, "_enabled", True)):
        return True
    return bool(getattr(login_task, "_finished", False))


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


def install_auto_scheduler() -> bool:
    """Install the startup check and the post-task recheck (idempotent).

    生产入口在 ``src.config`` 导入期调用本函数，此时 QApplication 尚未创建：
    信号连接不依赖应用实例（与 ``install_run_history_recorder`` 同模式），
    可以无条件执行；依赖事件循环的首次 QTimer 检查必须推迟到首个框架信号
    到达后再排布，否则定时器静默失效、启动自动执行在生产入口永不生效。
    """
    from ok.core.events import communicate
    from PySide6.QtCore import QCoreApplication, QObject, QTimer

    if getattr(install_auto_scheduler, "_installed", False):
        return False
    install_auto_scheduler._installed = True

    class _AutoRunner(QObject):
        def __init__(self):
            super().__init__()
            self._startup_scheduled = False
            self._startup_deadline = 0.0

        def schedule_startup_check(self):
            if self._startup_scheduled:
                return
            self._startup_scheduled = True
            self._startup_deadline = time.monotonic() + STARTUP_RETRY_BUDGET_SECONDS
            QTimer.singleShot(int(INSTALL_DELAY_SECONDS * 1000), self._startup_check)

        def _startup_check(self):
            # ``None`` 含义：执行器忙（自动登录 trigger 尚未结束）、游戏未
            # 就绪或当前无到期任务。前两者靠有限重试等到登录完成；后者重试
            # 只做纯配置读取，开销可忽略。启动了任务或预算耗尽即停止。
            if run_due_tasks_once() is None and time.monotonic() < self._startup_deadline:
                QTimer.singleShot(
                    int(STARTUP_RETRY_INTERVAL_SECONDS * 1000), self._startup_check
                )

        def on_first_app_signal(self, *_args):
            self.schedule_startup_check()

        def on_task_done(self, task):
            if not self._startup_scheduled:
                # 兜底：task_list_updated / starting_emulator 都未覆盖、
                # 但已有任务跑完的场景（应用一定已存在）。
                self.schedule_startup_check()
            QTimer.singleShot(int(RECHECK_DELAY_SECONDS * 1000), run_due_tasks_once)

    runner = _AutoRunner()
    communicate.task_done.connect(runner.on_task_done)
    if QCoreApplication.instance() is not None:
        runner.schedule_startup_check()
    else:
        # QApplication 由 ok 框架在导入完成后创建。任务列表刷新与模拟器
        # 启动信号都在主窗口/执行器就绪后发出，任一先到即排布首次检查。
        communicate.task_list_updated.connect(runner.on_first_app_signal)
        communicate.starting_emulator.connect(runner.on_first_app_signal)
    # Keep the receiver alive for the app's lifetime.
    install_auto_scheduler._runner = runner
    return True
