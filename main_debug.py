if __name__ == "__main__":
    from src.compat.startup import start_application

    def load_config():
        from src.config import config

        return config

    def configure_debug(config):
        import os
        from pathlib import Path

        from src.debug_profile import configure_debug_profile
        from src.tasks.debug_registry import install_debug_tasks
        from src.tasks.map_trade.progress import configure_development_ledger

        configure_development_ledger(
            os.environ.get("OK_BD2_DEV_LEDGER_DIR")
            or str(Path(__file__).resolve().parent / ".local-dev" / "state" / "event-collection")
        )
        configure_debug_profile(config)
        install_debug_tasks(config)

    start_application(load_config, configure_debug)
