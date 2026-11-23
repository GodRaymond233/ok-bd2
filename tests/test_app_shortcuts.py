import inspect
import unittest
from unittest.mock import patch

import ok
import ok.util.shortcut as shortcut

import src.config  # noqa: F401  (installs the compat patches, as the entry points do)
from src.compat.app_shortcuts import disable_framework_shortcuts


class FrameworkShortcutTest(unittest.TestCase):
    def test_headless_start_leaves_shortcuts_to_the_launcher(self):
        disable_framework_shortcuts()
        with patch("pyappify.create_shortcut", create=True) as create:
            self.assertIsNone(shortcut.create_app_shortcut({}))
        create.assert_not_called()

    def test_ok_start_looks_the_function_up_when_it_runs(self):
        # Replacing the module attribute only works while OK.start imports it at call time.
        source = inspect.getsource(ok.OK.start)
        self.assertIn("from ok.util.shortcut import create_app_shortcut", source)
        module_source = inspect.getsource(ok)
        before_start = module_source[:module_source.index("    def start(self):")]
        self.assertNotIn("create_app_shortcut", before_start)


if __name__ == "__main__":
    unittest.main()
