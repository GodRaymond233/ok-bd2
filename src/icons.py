"""Task metadata stays usable without loading a desktop toolkit.

Qt mode keeps the Fluent icons the existing window renders.  Service mode only
needs stable icon identifiers for the desktop client, so Qt is never imported.
"""

from enum import StrEnum

from src.ui_mode import is_qt_mode

if is_qt_mode():
    from qfluentwidgets import FluentIcon as TaskIcon
else:

    class TaskIcon(StrEnum):
        ACCEPT = "accept"
        BOOK_SHELF = "book-shelf"
        CALENDAR = "calendar"
        CAR = "car"
        CERTIFICATE = "certificate"
        CHECKBOX = "checkbox"
        COMPLETED = "completed"
        DEVELOPER_TOOLS = "developer-tools"
        EDUCATION = "education"
        FLAG = "flag"
        GAME = "game"
        GLOBE = "globe"
        HOME = "home"
        LIBRARY = "library"
        MAIL = "mail"
        PEOPLE = "people"
        POWER_BUTTON = "power-button"
        SEARCH = "search"
        SETTING = "setting"
        SHOPPING_CART = "shopping-cart"
        SPEED_HIGH = "speed-high"
        SYNC = "sync"
        TILES = "tiles"


__all__ = ["TaskIcon"]
