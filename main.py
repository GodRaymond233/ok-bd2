if __name__ == "__main__":
    # 原生崩溃（如 0xc0000005）不经过 Python 异常，必须在导入 ok 之前
    # 启用 faulthandler，把各线程栈写入 logs/crash-*.log 供事后定位。
    import datetime
    import faulthandler
    from pathlib import Path

    Path("logs").mkdir(exist_ok=True)
    with open(
        Path("logs") / f"crash-{datetime.datetime.now():%Y%m%d-%H%M%S}.log",
        "a",
        encoding="utf-8",
    ) as _crash_log:
        faulthandler.enable(file=_crash_log, all_threads=True)

        # 「登记在文件缺」的损坏安装 pip 永远按 already satisfied 跳过
        # （BUG-20260905-07），进框架 import 前先校验核心依赖，缺失时强制重装。
        from src.compat.dependency_guard import ensure_core_dependencies

        ensure_core_dependencies()

        import ok

        from src.config import config

        ok_instance = ok.OK(config)
        ok_instance.start()
