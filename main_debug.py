if __name__ == "__main__":
    import ok

    from src.config import config

    config["debug"] = True
    ok_instance = ok.OK(config)
    ok_instance.start()
