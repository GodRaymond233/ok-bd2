from concurrent.futures import Future
from threading import Thread
from time import monotonic
from types import SimpleNamespace

from ok import BaseScene, Logger
from ok.feature.FeatureSet import resize_image

from src.tasks.map_trade.vision import Vision
from src.utils.home_confirmation import read_home_signals

logger = Logger.get_logger(__name__)


class BD2Scene(BaseScene):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._logged_in = False
        self._in_combat = None
        self._next_home_sample = 0.0
        self._home_observation = None
        self._pending_home_sample = None
        self._home_revision = 0

    @property
    def at_home(self):
        sample = self._home_observation
        return sample[1] if sample and monotonic() - sample[0] < 5.0 else None

    def record_home(self, confirmed, sampled_at=None):
        self._home_revision += 1
        sampled_at = monotonic() if sampled_at is None else sampled_at
        self._home_observation = sampled_at, confirmed
        self._next_home_sample = sampled_at + 2.0

    def observe_home(self, executor, frame=None):
        """Publish fresh observations on the executor thread, with one sample in flight."""
        try:
            self._poll_home(executor, frame, monotonic())
        except Exception as error:
            self.record_home(None)
            logger.debug(f"首页监测暂不可用: {error}")

    def _poll_home(self, executor, frame, now):
        pending = self._pending_home_sample
        if pending and not pending[0].done():
            return
        if not pending and now < self._next_home_sample:
            return
        from src.tasks.trigger.AutoLoginTask import AutoLoginTask

        login = next((t for t in executor.trigger_tasks if isinstance(t, AutoLoginTask)), None)
        if login is None or executor.exit_event.is_set():
            return
        capture = executor.method
        current = executor.current_task
        context = (self._home_revision, login, login._game_window(), login.enabled, capture,
                   current, getattr(current, "start_time", None), executor._get_wake_version())
        if pending:
            future, sampled_context, started = pending
            self._pending_home_sample = None
            if context == sampled_context and now - started < 5.0:
                confirmed, sampled_frame, details = future.result()
                self.record_home(confirmed, started)
                for key, value in details.items():
                    login.info_set(key, value)
                if confirmed is False and current is None and login.enabled and login._finished:
                    login.check_login_screen(sampled_frame)
                return
        if now < self._next_home_sample or capture is None or "default" not in executor._ocr_lib:
            return
        if current is None and login.login_pending:
            return
        self._next_home_sample = now + 2.0
        future = Future()
        config = dict(login.config)
        threshold = login._home_p95_threshold()

        def sample(image):
            details = {}
            confirmed = None
            try:
                if image is None and executor.can_capture():
                    image = capture.get_frame(busy_wait=0)
                if image is not None:
                    backend = login.ocr_fun("home_observer")

                    def read_boxes(*, frame, target_height, threshold, **_):
                        crop, scale = resize_image(frame, frame.shape[0], target_height)
                        boxes, _all_boxes = backend(None, crop, None, scale, threshold,
                                                    "home_observer")
                        return boxes

                    reader = SimpleNamespace(config=config, ocr=read_boxes,
                                             info_set=details.__setitem__)
                    confirmed = read_home_signals(
                        image, Vision(reader), threshold, "首页监测", reader.info_set,
                    )[0]
                    if any(key.endswith("OCR错误") for key in details):
                        confirmed = None
            except Exception as error:
                logger.debug(f"首页监测失败: {error}")
            finally:
                future.set_result((confirmed, image, details))

        Thread(target=sample, args=(frame.copy() if frame is not None else None,),
               name="BD2HomeObserver", daemon=True).start()
        self._pending_home_sample = future, context, now

    def reset(self):
        self._in_combat = None

    def logged_in(self) -> bool:
        return self._logged_in

    def set_logged_in(self, value: bool = True) -> None:
        self._logged_in = value

    def in_combat(self):
        return self._in_combat

    def set_in_combat(self) -> bool:
        self._in_combat = True
        return True

    def set_not_in_combat(self) -> bool:
        self._in_combat = False
        return False
