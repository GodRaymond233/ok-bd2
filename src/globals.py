from pathlib import Path

from ok import Logger, og

logger = Logger.get_logger(__name__)


class Globals:
    """Runtime services created by ok-script after its handler and devices exist."""

    def __init__(self, exit_event):
        # Flush the bounded diagnostic evidence queue during application shutdown.
        exit_event.bind_stop(self)
        from src.diagnostics.runtime import install

        self.diagnostic_evidence = install(Path(__file__).resolve().parents[1])

        from src.game_path import seed_device_manager_launch_path
        from src.tasks.auto_scheduler import install_auto_scheduler
        from src.tasks.run_history import install_run_history_recorder

        launch_path = seed_device_manager_launch_path(og.device_manager)
        if launch_path:
            logger.info(f"seed BD2 Starter path {launch_path}")
        install_run_history_recorder(exit_event=exit_event)
        install_auto_scheduler(handler=og.handler, exit_event=exit_event)

    def stop(self):
        self.diagnostic_evidence.stop()
