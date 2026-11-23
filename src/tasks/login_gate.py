"""Hand pending home-start tasks back to the executor once login releases input."""

from itertools import count

from ok.core.events import EventSignal

login_ready = EventSignal("login_ready")
_request_order = count(1)


def login_settled(executor):
    from src.tasks.trigger.AutoLoginTask import AutoLoginTask

    login = executor.get_task_by_class(AutoLoginTask)
    return login is None or not login.login_pending


def defer_until_login(task):
    if not getattr(task, "waits_for_login", False):
        return False
    with task.lock:
        if not task.enabled:
            return True
        if login_settled(task.executor):
            return False
        if park := getattr(task, "park_run_mode", None):
            park()
        task._start_after_login = next(_request_order)
        task._enabled = False
    task.info_set("状态", "等待自动登录完成，完成后自动开始。")
    return True


def release_after_login(executor):
    waiting = [task for task in executor.onetime_tasks
               if getattr(task, "_start_after_login", False)]
    released = False
    for task in sorted(waiting, key=lambda item: item._start_after_login):
        with task.lock:
            if not task._start_after_login:
                continue
            task._enabled = True
            if not executor.enqueue_onetime_task(task):
                task._enabled = False
                continue
            task._start_after_login = False
            released = True
    return released
