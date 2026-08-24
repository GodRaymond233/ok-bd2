"""Aggregate installer for the quest-style UI (mockup V2, 2026-08-18).

Called once from ``src/config.py`` right after Codex's responsive patch, before
the framework builds the main window, so every chained class method is in
place before the first tab is created.
"""

from __future__ import annotations


def install_quest_ui() -> None:
    from src.tasks.run_history import install_run_history_recorder
    from src.ui.expand_timing import install_expand_timing
    from src.ui.fluent_motion import install_fluent_tab_entrance
    from src.ui.hide_config_rows import install_hide_config_rows
    from src.ui.quest_banner import install_quest_tab_chrome
    from src.ui.quest_cards import install_quest_cards
    from src.ui.run_panel import install_run_panel

    install_run_history_recorder()
    install_quest_cards()
    install_run_panel()
    install_quest_tab_chrome()
    install_fluent_tab_entrance()
    install_hide_config_rows()
    install_expand_timing()
