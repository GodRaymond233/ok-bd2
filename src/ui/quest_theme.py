"""Design tokens for the quest-style task UI (mockup V2, 2026-08-18).

One-to-one translation of ``.local-dev/experiments/ui-redesign-20260818/mockup.html``
into Qt: a light and a dark palette plus small QSS builders shared by the task
cards, the daily board banner, the run panel and the navigation section labels.
The framework keeps managing the global accent color; these tokens only style
the widgets introduced by the quest UI so the two never fight.
"""

from __future__ import annotations

from qfluentwidgets import isDarkTheme

BODY_FONT = '"MiSans", "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif'
MONO_FONT = (
    '"JetBrains Mono", "Cascadia Mono", "Cascadia Code", Consolas,'
    ' "Microsoft YaHei UI", monospace'
)

# Preferred global UI font stack, best first; absent families are skipped so
# machines without MiSans degrade to the stock Windows UI fonts.
APP_FONT_FAMILIES = ("MiSans", "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI")


def apply_app_font() -> None:
    """Set the app-wide font to the preferred stack (keeps the default size)."""
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return
    available = set(QFontDatabase.families())
    families = [name for name in APP_FONT_FAMILIES if name in available]
    if not families:
        return
    font = app.font()
    font.setFamilies(families)
    app.setFont(font)

_LIGHT = {
    "bg": "#F3F3F3",
    "bg_panel": "#FAFAFA",
    "card": "#FFFFFF",
    "inset": "#F7F7F7",
    "line": "#E5E5E5",
    "line_strong": "#D1D1D1",
    "ink": "#1B1B1B",
    "ink_dim": "#5C5C5C",
    "ink_faint": "#8A8A8A",
    "accent": "#0F6CBD",
    "accent_hi": "#115EA3",
    "accent_deep": "#0B5A9E",
    "accent_soft": "rgba(15,108,189,0.10)",
    "ok": "#107C10",
    "ok_soft": "rgba(16,124,16,0.08)",
    "info": "#0E7386",
    "info_soft": "rgba(14,115,134,0.08)",
    "warn": "#C50F1F",
    "warn_soft": "rgba(197,15,31,0.07)",
    "warn_ink": "#B26A00",
    "beta": "#5C2E91",
    "beta_soft": "rgba(92,46,145,0.08)",
    "seal_idle": "#B0ADA8",
    "seal_off": "#D0CDC7",
    "seg_skip": "#C8C6C4",
}

_DARK = {
    "bg": "#202020",
    "bg_panel": "#272727",
    "card": "#2B2B2B",
    "inset": "#1C1C1C",
    "line": "#3A3A3A",
    "line_strong": "#4A4A4A",
    "ink": "#F0F0F0",
    "ink_dim": "#B8B8B8",
    "ink_faint": "#7F7F7F",
    "accent": "#479EF5",
    "accent_hi": "#62ABF5",
    "accent_deep": "#2B7CD3",
    "accent_soft": "rgba(71,158,245,0.14)",
    "ok": "#54B054",
    "ok_soft": "rgba(84,176,84,0.12)",
    "info": "#58B7C7",
    "info_soft": "rgba(88,183,199,0.12)",
    "warn": "#F1707B",
    "warn_soft": "rgba(241,112,123,0.10)",
    "warn_ink": "#F0B23E",
    "beta": "#B39DDB",
    "beta_soft": "rgba(179,157,219,0.12)",
    "seal_idle": "#6B6B6B",
    "seal_off": "#4A4A4A",
    "seg_skip": "#5A5A5A",
}


def palette(dark: bool | None = None) -> dict[str, str]:
    """Return the active token palette (follows qfluentwidgets' theme)."""
    if dark is None:
        dark = isDarkTheme()
    return dict(_DARK if dark else _LIGHT)


def chip_qss(color: str, soft: str) -> str:
    """Badge chip style: tinted text, soft background, thin colored border."""
    return (
        f"color: {color};"
        f" background-color: {soft};"
        f" border: 1px solid {rgba(color, 0.38)};"
        " border-radius: 5px;"
        " padding: 1px 7px;"
        " font-size: 11px;"
        " font-weight: 700;"
    )


def rgba(hex_color: str, alpha: float) -> str:
    """'#RRGGBB' + alpha → 'rgba(r,g,b,a)' for QSS."""
    r, g, b = _hex_rgb(hex_color)
    return f"rgba({r},{g},{b},{alpha:.2f})"


def mix(base: str, tint: str, t: float) -> str:
    """Opaque blend of ``tint`` into ``base`` ('#RRGGBB').

    Qt QSS stacks no background layers, so the mockup's translucent accent
    wash over a card is baked as an opaque gradient stop instead.
    """
    r1, g1, b1 = _hex_rgb(base)
    r2, g2, b2 = _hex_rgb(tint)
    return "#{:02X}{:02X}{:02X}".format(
        round(r1 + (r2 - r1) * t),
        round(g1 + (g2 - g1) * t),
        round(b1 + (b2 - b1) * t),
    )


def seal_qss(color: str, ring_soft: str | None = None) -> str:
    """Round status dot; ``ring_soft`` adds the running-state halo."""
    halo = f" border: 3px solid {ring_soft};" if ring_soft else " border: none;"
    return f"background-color: {color};{halo} border-radius: 4px;"


def _hex_rgb(color: str) -> tuple[int, int, int]:
    value = color.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def on_theme_changed(callback, owner) -> None:
    """Re-run ``callback`` on theme changes until ``owner`` is destroyed."""
    from qfluentwidgets import qconfig

    def _update(*_args):
        callback()

    def _disconnect(*_args):
        try:
            qconfig.themeChanged.disconnect(_update)
        except (RuntimeError, TypeError):
            pass

    qconfig.themeChanged.connect(_update)
    owner.destroyed.connect(_disconnect)
