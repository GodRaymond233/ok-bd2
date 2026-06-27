import threading
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from typing import Callable

from ok import Logger
from PySide6.QtCore import QObject

logger = Logger.get_logger(__name__)


class Globals(QObject):
    def __init__(self, exit_event):
        super().__init__()
        self._thread_pool_executor_max_workers = 0
        self.thread_pool_executor: ThreadPoolExecutor | None = None
        self.thread_pool_exit_event = Event()
        self._periodic_tasks = {}
        self._periodic_tasks_lock = threading.Lock()
        exit_event.bind_stop(self)

    def stop(self):
        self.shutdown_thread_pool_executor()

    def on_show_main_window(self, main_window):
        from ok import og

        from src.game_path import seed_device_manager_game_path
        from src.ui.live_screenshot import install_live_screenshot

        game_path = seed_device_manager_game_path(og.device_manager)
        if game_path:
            logger.info(f"seed BD2 game path {game_path}")
        install_live_screenshot(main_window.start_tab)

    def get_thread_pool_executor(self, max_workers: int = 6) -> ThreadPoolExecutor:
        if (
            self.thread_pool_executor is not None
            and max_workers > self._thread_pool_executor_max_workers
        ):
            logger.info(
                "thread pool max_workers not enough, reset max_workers "
                f"{self._thread_pool_executor_max_workers} -> {max_workers}"
            )
            self.shutdown_thread_pool_executor()

        if self.thread_pool_executor is None:
            logger.info(f"create thread pool executor, max_workers: {max_workers}")
            self.thread_pool_exit_event = Event()
            self.thread_pool_executor = ThreadPoolExecutor(max_workers=max_workers)
            self._thread_pool_executor_max_workers = max_workers

        return self.thread_pool_executor

    def shutdown_thread_pool_executor(self) -> None:
        if self.thread_pool_executor is None:
            return

        logger.info("Shutting down thread pool executor...")
        with self._periodic_tasks_lock:
            for record in self._periodic_tasks.values():
                record["stop_event"].set()
            self._periodic_tasks.clear()
        self.thread_pool_exit_event.set()
        self.thread_pool_executor.shutdown(wait=False, cancel_futures=True)
        self.thread_pool_executor = None
        self._thread_pool_executor_max_workers = 0

    def submit_periodic_task(self, delay: float, task: Callable, *args, **kwargs):
        executor = self.get_thread_pool_executor()
        exit_event = self.thread_pool_exit_event
        task_key = self._get_periodic_task_key(task)
        task_name = self._get_periodic_task_name(task)
        task_stop_event = Event()

        with self._periodic_tasks_lock:
            old_record = self._periodic_tasks.get(task_key)
            if old_record is not None:
                logger.debug(f"Stopping previous periodic task {task_name}.")
                old_record["stop_event"].set()
                old_future = old_record.get("future")
                if old_future is not None:
                    old_future.cancel()

            self._periodic_tasks[task_key] = {
                "stop_event": task_stop_event,
                "future": None,
            }

        def loop_wrapper():
            logger.debug(f"Periodic task {task_name} started.")
            try:
                while not exit_event.is_set() and not task_stop_event.is_set():
                    should_stop = False
                    try:
                        if task(*args, **kwargs) is False:
                            should_stop = True
                    except Exception as exc:
                        logger.error(f"Error in periodic task {task_name}: {exc}")

                    if should_stop:
                        break
                    if task_stop_event.wait(timeout=delay) or exit_event.is_set():
                        break
            finally:
                with self._periodic_tasks_lock:
                    current_record = self._periodic_tasks.get(task_key)
                    if (
                        current_record is not None
                        and current_record["stop_event"] is task_stop_event
                    ):
                        del self._periodic_tasks[task_key]

        future = executor.submit(loop_wrapper)
        with self._periodic_tasks_lock:
            current_record = self._periodic_tasks.get(task_key)
            if current_record is not None and current_record["stop_event"] is task_stop_event:
                current_record["future"] = future
        return future

    def _get_periodic_task_key(self, task: Callable):
        bound_self = getattr(task, "__self__", None)
        func = getattr(task, "__func__", task)
        func_name = getattr(func, "__name__", repr(task))

        if bound_self is not None:
            cls = bound_self.__class__
            return ("bound_method", cls.__module__, cls.__qualname__, func_name)

        return (
            "callable",
            getattr(func, "__module__", None),
            getattr(func, "__qualname__", func_name),
            None,
        )

    def _get_periodic_task_name(self, task: Callable) -> str:
        bound_self = getattr(task, "__self__", None)
        func = getattr(task, "__func__", task)
        func_name = getattr(func, "__name__", repr(task))
        if bound_self is None:
            return func_name
        return f"{bound_self.__class__.__name__}.{func_name}"
