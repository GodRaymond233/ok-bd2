"""Hide source-level tuning parameters from desktop task configuration.

Values remain saved and available to tasks. The service omits fields marked
``hidden`` and their references in nested configuration rules.
"""

from __future__ import annotations

# "秒数" covers 等待秒数/确认秒数/间隔秒数/宽限秒数; "次数" covers 重试次数/
# 滚轮次数/最多点击次数.
HIDDEN_CONFIG_TOKENS = ("阈值", "秒数", "分钟", "像素", "命中", "次数", "测试")


def _matches(key) -> bool:
    text = str(key)
    return not text.startswith("_") and any(token in text for token in HIDDEN_CONFIG_TOKENS)


def mark_hidden_config_keys(task) -> int:
    """Inject ``hidden`` into the task's config_type for token-matching keys.

    Returns how many keys were hidden (0 — and the task untouched — when
    nothing matches).
    """
    config_type = getattr(task, "config_type", None)
    if not isinstance(config_type, dict):
        config_type = {}

    keys = set(config_type)
    config = getattr(task, "config", None)
    if hasattr(config, "keys"):
        keys |= set(config.keys())
    description = getattr(task, "config_description", None)
    if isinstance(description, dict):
        keys |= set(description)
    hidden = {key for key in keys if _matches(key)}
    if not hidden:
        return 0
    task.config_type = config_type

    for key in hidden:
        the_type = config_type.get(key)
        if isinstance(the_type, dict):
            merged = dict(the_type)
        else:
            merged = {"type": the_type} if the_type is not None else {}
        merged["hidden"] = True
        config_type[key] = merged

    for the_type in config_type.values():
        if not isinstance(the_type, dict):
            continue
        rules = the_type.get("sub_configs")
        if not isinstance(rules, dict):
            continue
        for value, rule_keys in list(rules.items()):
            if isinstance(rule_keys, str):
                rule_keys = [rule_keys]
            if not isinstance(rule_keys, list):
                continue
            filtered = [key for key in rule_keys if key not in hidden]
            if len(filtered) != len(rule_keys):
                rules[value] = filtered
    return len(hidden)
