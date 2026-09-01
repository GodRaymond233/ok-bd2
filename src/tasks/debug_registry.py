"""Debug-only task registrations.

Test controls, probes, and diagnosis tasks are intentionally excluded from the
formal app configuration. ``main_debug.py`` installs them for development runs.
"""

DEBUG_ONETIME_TASKS = [
    ["src.tasks.BD2ProbeTask", "BD2ProbeTask"],
    ["src.tasks.BD2MapCollectionProbeTask", "BD2MapCollectionProbeTask"],
    ["src.tasks.BD2OneTimeTask", "BD2OneTimeTask"],
    ["src.tasks.BD2DiagnosisTask", "BD2DiagnosisTask"],
    ["src.tasks.BD2InputTestTask", "BD2BackgroundMouseClickInputTestTask"],
    
    ["src.tasks..task", "BD2TestCollectTestTask"],
    ["src.tasks..mail", "BD2TestMailClaimTask"],
    ["src.tasks..quest", "BD2TestQuestRewardTask"],
    ["src.tasks..pass_claim", "BD2TestPassRewardTask"],
    ["src.tasks..activities", "BD2TestActivityRewardTask"],
    ["src.tasks..intimacy", "BD2TestIntimacyTalkTask"],
    ["src.tasks..restaurant", "BD2TestRestaurantDailyTask"],
    ["src.tasks..weekly", "BD2TestWeeklyTask"],
    ["src.tasks..redemption", "BD2TestRedemptionTowerTask"],
    ["src.tasks..event_battle", "BD2TestEventBattleTask"],
    ["src.tasks..equip_daily", "BD2TestEquipDailyTask"],
    ["src.tasks..fishing", "BD2TestFishingTask"],
    ["src.tasks..semiauto", "BD2TestSemiautoTask"],
    ["src.tasks..close_game", "BD2TestCloseGameTask"],
]

DEBUG_TRIGGER_TASKS = [
    ["src.tasks.BD2InputTestTask", "BD2ClickModeSelectorTask"],
]


def install_debug_tasks(config):
    """Install debug-only task registrations without duplicates."""
    existing = {tuple(item) for item in config.get("onetime_tasks", [])}
    for registration in DEBUG_ONETIME_TASKS:
        if tuple(registration) not in existing:
            config.setdefault("onetime_tasks", []).append(registration)
    existing_triggers = {tuple(item) for item in config.get("trigger_tasks", [])}
    for registration in reversed(DEBUG_TRIGGER_TASKS):
        if tuple(registration) not in existing_triggers:
            config.setdefault("trigger_tasks", []).insert(0, registration)
    return config
