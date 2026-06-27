if __name__ == "__main__":
    import ok

    from src.config import config

    ok_instance = ok.OK(config)
    ok_instance.start()
