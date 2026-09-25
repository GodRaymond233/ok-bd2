if __name__ == "__main__":
    from src.compat.startup import start_application

    def load_config():
        from src.config import config

        return config

    start_application(load_config)
