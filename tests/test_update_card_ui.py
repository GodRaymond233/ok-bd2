import os
import types
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ok.ui.qt.about.UpdateCard import UpdateCard
from PySide6.QtWidgets import QApplication

from src.compat.update_card_ui import (
    MIN_CHECK_UPDATES_LAUNCHER_VERSION,
    PATCH_MARKER,
    UPDATE_CARD_STATUS_MIN_WIDTH,
    install_update_card_ui,
    launcher_supports_update_check,
    parse_launcher_version,
)

UNSUPPORTED_MESSAGE = "Update checking is not supported by this PyAppify version."
DOWNLOAD_URL = "https://github.com/GodRaymond233/ok-bd2/releases/latest"


def make_pyappify_module(pyappify_version=None):
    module = types.SimpleNamespace()
    module.pyappify_version = pyappify_version
    return module


class UpdateCardUiCompatTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        install_update_card_ui()

    def make_card(self, pyappify_version=None):
        return UpdateCard(
            "v1.2.0",
            make_pyappify_module(pyappify_version),
            download_url=DOWNLOAD_URL,
        )

    def test_parse_launcher_version(self):
        self.assertEqual((1, 1, 9), parse_launcher_version("1.1.9"))
        self.assertEqual((1, 2, 3), parse_launcher_version("v1.2.3"))
        self.assertIsNone(parse_launcher_version(None))
        self.assertIsNone(parse_launcher_version("dev"))
        self.assertIsNone(parse_launcher_version(""))

    def test_launcher_supports_update_check_threshold(self):
        self.assertIsNone(launcher_supports_update_check(None))
        self.assertIsNone(launcher_supports_update_check("dev"))
        self.assertFalse(launcher_supports_update_check("1.1.9"))
        self.assertTrue(
            launcher_supports_update_check(
                ".".join(str(part) for part in MIN_CHECK_UPDATES_LAUNCHER_VERSION)
            )
        )
        self.assertTrue(launcher_supports_update_check("1.2.3"))

    def test_install_sets_status_label_minimum_width_once(self):
        card = self.make_card("1.2.3")
        self.assertEqual(UPDATE_CARD_STATUS_MIN_WIDTH, card.status_label.minimumWidth())
        install_update_card_ui()
        self.assertTrue(getattr(UpdateCard, PATCH_MARKER, False))

    def test_check_for_updates_intercepts_old_launcher_with_guidance(self):
        card = self.make_card("1.1.9")
        card.show()
        card.check_for_updates()
        self.assertIn("启动器版本 1.1.9 过旧", card.status_label.text())
        self.assertIn("Launcher 1.1.9 is too old", card.status_label.text())
        self.assertTrue(card.download_button.isVisible())

    def test_check_for_updates_without_launcher_version_uses_upstream_path(self):
        card = self.make_card(None)
        card.show()
        card.check_for_updates()
        self.assertEqual(UNSUPPORTED_MESSAGE, card.status_label.text())

    def test_check_for_updates_new_launcher_uses_upstream_path(self):
        card = self.make_card("1.2.3")
        card.show()
        card.check_for_updates()
        self.assertEqual(UNSUPPORTED_MESSAGE, card.status_label.text())


if __name__ == "__main__":
    unittest.main()
