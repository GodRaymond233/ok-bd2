if __name__ == "__main__":
    from src.compat.startup import start_application

    def load_config():
        from src.config import config

        return config

    def configure_debug(config):
        from src.debug_profile import configure_debug_profile
        from src.tasks.debug_registry import install_debug_tasks

        configure_debug_profile(config)
        install_debug_tasks(config)

    start_application(load_config, configure_debug)
