"""Stable task icon identifiers for the Electron client."""

from enum import StrEnum


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
