"""Debug-only one-time task registrations.

Probe and diagnosis tasks are intentionally excluded from the formal app
configuration. ``main_debug.py`` installs them so local debugging keeps the
same task list it had before the split.
"""

DEBUG_ONETIME_TASKS = [
    ["src.tasks.BD2ProbeTask", "BD2ProbeTask"],
    ["src.tasks.BD2MapCollectionProbeTask", "BD2MapCollectionProbeTask"],
    ["src.tasks.BD2OneTimeTask", "BD2OneTimeTask"],
    ["src.tasks.BD2DiagnosisTask", "BD2DiagnosisTask"],
]


def install_debug_tasks(config):
    """Append debug-only task registrations that are not already present."""
    existing = {tuple(item) for item in config.get("onetime_tasks", [])}
    for registration in DEBUG_ONETIME_TASKS:
        if tuple(registration) not in existing:
            config.setdefault("onetime_tasks", []).append(registration)
    return config
