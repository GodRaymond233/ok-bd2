import gettext
import unittest
from pathlib import Path

from scripts.compile_translations import compile_catalog, read_catalog

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CATALOG_ROOT = REPOSITORY_ROOT / "i18n"
ENGLISH_CATALOG = CATALOG_ROOT / "en_US" / "LC_MESSAGES" / "ok.po"
ENGLISH_MESSAGES = ENGLISH_CATALOG.with_suffix(".mo")


class TranslationCatalogTests(unittest.TestCase):
    def test_english_catalog_loads_and_translates_project_ui(self):
        translation = gettext.translation("ok", CATALOG_ROOT, languages=["en_US"])

        self.assertEqual("Complete dailies", translation.gettext("一键完成日常"))
        self.assertEqual("Quick Hunt", translation.gettext("快速狩猎"))
        self.assertEqual("Arena battle multiplier", translation.gettext("竞技场战斗倍数"))

    def test_compiled_catalog_matches_po_source(self):
        messages = read_catalog(ENGLISH_CATALOG)

        self.assertTrue(all(message for msgid, message in messages.items() if msgid))
        self.assertEqual(compile_catalog(messages), ENGLISH_MESSAGES.read_bytes())


if __name__ == "__main__":
    unittest.main()
