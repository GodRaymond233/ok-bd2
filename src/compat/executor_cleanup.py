"""Task cleanup for the pinned ok-script 2.0.22 runtime.

Backported from ok-script cdcdd60a4b5d87dd32fae4564a72de04e9e87434;
keeping the 2.x Qt runtime requires retaining its executor loop here.
"""

import time

from ok.core.events import communicate
from ok.core.notifications import alert_info
from ok.task.exceptions import (
    CaptureException,
    FinishedException,
    HotkeyConfigException,
    TaskDisabledException,
)
from ok.task.TaskExecutor import TaskExecutor
from ok.util.logger import Logger
from ok.util.process import prevent_sleeping

logger = Logger.get_logger(__name__)


def _execute(self):
    logger.info("start execute")
    while not self.exit_event.is_set():
        wake_version = self._get_wake_version()
        job = self._pop_job()
        if job is not None:
            self._run_job(job)
            continue
        if self.paused or self._exclusive_owner is not None:
            self._wait_for_activity(1, wake_version)
            continue
        task, cycled, is_trigger_task = self.next_task()
        if not task:
            self._wait_for_activity(self.next_trigger_delay(), wake_version)
            continue
        if cycled:
            self.reset_scene()
        elif time.time() - self._last_frame_time > 0.2:
            self.reset_scene()
        returned = False
        try:
            task.start_time = time.time()
            task.running = True
            self.current_task = task
            if not is_trigger_task:
                communicate.task.emit(task)
            self._prepare_task_for_run(is_trigger_task)
            if cycled or self._frame is None:
                if self.next_frame(time_out=4) is None and is_trigger_task:
                    logger.info("no frame available, skip remaining trigger tasks")
                    self.trigger_task_index = len(self.trigger_tasks) - 1
                    continue
            if is_trigger_task:
                if task.run():
                    self.trigger_task_index = -1
                    self.reset_scene()
                    continue
            else:
                prevent_sleeping(True)
                try:
                    logger.debug(f"start running onetime_task {task.name}")
                    task.run()
                    returned = True
                    logger.debug(f"end running onetime_task {task.name}")
                finally:
                    prevent_sleeping(False)
                task.disable()
                communicate.task_done.emit(task)
                if task.exit_after_task or task.config.get("Exit After Task"):
                    message = "Successfully Executed Task, Exiting Game and App!"
                    logger.info(message)
                    alert_info(message)
                    time.sleep(5)
                    self.device_manager.stop_hwnd()
                    time.sleep(5)
                    communicate.quit.emit()
        except TaskDisabledException:
            logger.info(f"TaskDisabledException, continue {task}")
            if not returned:
                _record_interrupted(task, is_trigger_task, "已中止")
            self._finish_task(task, is_trigger_task)
            communicate.notification.emit("Stopped", task.name, False, False, None, None, None)
        except FinishedException:
            logger.info("FinishedException, breaking")
            if not returned:
                _record_interrupted(task, is_trigger_task, "已中止")
            break
        except Exception as error:
            if isinstance(error, CaptureException):
                communicate.capture_error.emit()
            name = task.name
            task.running = False
            task.disable()
            params = None
            if isinstance(error, HotkeyConfigException):
                message = "{key} is invalid, please check the hotkey config!"
                params = {"key": error.key}
            else:
                message = str(error)
            communicate.notification.emit(message, name, True, True, None, params, None)
            task.info_set(task._app.tr("Error"), message)
            if not returned:
                _record_interrupted(task, is_trigger_task, "失败")
            logger.error(f"{name} exception stopped", error)
            if self._frame is not None:
                communicate.screenshot.emit(self._frame, name, True, None)
            self.current_task = None
            communicate.task.emit(None)
        finally:
            self._finish_task(task, is_trigger_task)
    self.destroy()


def _record_interrupted(task, is_trigger_task, status):
    if is_trigger_task:
        return
    try:
        # task_done only reports normal returns; keep that framework contract unchanged.
        from src.tasks.run_history import default_store

        default_store().record_task_done(task, ok=False, status=status)
    except Exception as error:
        logger.error(f"record interrupted task failed: {error}")


def _finish_task(self, task, is_trigger_task):
    if self.current_task is None and not task.running:
        return
    task.running = False
    self.current_task = None
    if not is_trigger_task:
        communicate.task.emit(task)


def enable_executor_cleanup() -> None:
    TaskExecutor.execute = _execute
    TaskExecutor._finish_task = _finish_task
