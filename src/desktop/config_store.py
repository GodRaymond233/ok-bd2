"""Validated, atomic UI writes to the existing configuration files.

Ported from ``codex/webui-refactor`` (``b8f623d``).  Writes go to the same files the
Qt window uses, so both front ends share one configuration.
"""

import json
import os
import tempfile
import threading
from pathlib import Path

from src.tasks.DailyBatchTask import DailyBatchTask
from src.tasks.quick_hunt_plan import PLAN_KEY, load_book

config_lock = threading.RLock()


def task_config_busy(task) -> bool:
    executor = task.executor
    if task.running or (task.enabled and task not in executor.trigger_tasks):
        return True
    # Batch children use temporary configs without setting their own running flags.
    batch = executor.get_task_by_class(DailyBatchTask)
    if batch is None or not any(isinstance(task, child.task_class) for child in batch.child_tasks):
        return False
    with batch.lock:
        return bool(batch.running or getattr(batch, "_start_after_login", False) or batch.enabled)


def save_config(config, changes: dict, *, allow_private=False, specifications=None) -> None:
    with config_lock:
        normalized = {}
        for key, value in changes.items():
            if key not in config or (key.startswith("_") and not allow_private):
                raise ValueError(f"未知配置项：{key}")
            original = config.get_default(key)
            if isinstance(original, float) and type(value) in (int, float):
                value = float(value)
            elif type(value) is not type(original):
                raise ValueError(f"配置类型不正确：{key}")
            if key == PLAN_KEY and value:
                load_book({PLAN_KEY: value})
            if not config.validate(key, value):
                raise ValueError(f"配置值不正确：{key}")
            specification = (specifications or {}).get(key, {})
            if isinstance(specification, dict):
                minimum, maximum = specification.get("min"), specification.get("max")
                if type(value) in (int, float) and (
                    (minimum is not None and value < minimum)
                    or (maximum is not None and value > maximum)
                ):
                    raise ValueError(f"配置值超出允许范围：{key}")
                options = specification.get("options", specification.get("options_available"))
                if options is not None:
                    values = value if isinstance(value, list) else [value]
                    if any(item not in options for item in values):
                        raise ValueError(f"请选择有效选项：{key}")
            normalized[key] = value
        candidate = dict(config)
        candidate.update(normalized)
        content = json.dumps(candidate, ensure_ascii=False, indent=4, allow_nan=False)
        path = Path(config.config_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as output:
                temporary = Path(output.name)
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
            dict.update(config, normalized)
        except OSError as error:
            raise RuntimeError("保存配置失败，请检查文件权限或磁盘空间") from error
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
