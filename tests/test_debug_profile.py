import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.debug_profile import (
    DebugGlobals,
    configure_debug_profile,
    fetch_latest_release_tag,
    next_development_version,
    release_version,
    resolve_latest_release_tag,
)
from src.globals import Globals


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class DebugProfileTest(unittest.TestCase):
    def test_release_version_and_next_patch_are_strict(self):
        self.assertEqual((0, 1, 21), release_version("v0.1.21"))
        self.assertEqual("0.1.22", next_development_version("v0.1.21"))
        with self.assertRaises(ValueError):
            release_version("v0.1.21-beta")

    def test_fetch_latest_release_tag_uses_github_release_payload(self):
        requests = []

        def opener(request, timeout):
            requests.append((request, timeout))
            return _Response({"tag_name": "v0.1.21"})

        self.assertEqual(
            "v0.1.21",
            fetch_latest_release_tag(timeout=2.0, opener=opener),
        )
        self.assertEqual(2.0, requests[0][1])
        self.assertEqual(
            "application/vnd.github+json",
            requests[0][0].get_header("Accept"),
        )

    def test_resolve_latest_release_uses_local_tag_only_when_remote_fails(self):
        with (
            patch("src.debug_profile.fetch_latest_release_tag", side_effect=OSError),
            patch("src.debug_profile.latest_local_release_tag", return_value="v0.1.20"),
        ):
            self.assertEqual("v0.1.20", resolve_latest_release_tag())

    def test_debug_profile_uses_next_release_and_exact_title(self):
        config = {
            "debug": False,
            "version": "0.1.1",
            "gui_title": "ok-bd2",
            "my_app": ["src.globals", "Globals"],
        }

        self.assertEqual(
            "0.1.22",
            configure_debug_profile(config, release_tag="v0.1.21"),
        )
        self.assertTrue(config["debug"])
        self.assertEqual("0.1.22", config["version"])
        self.assertEqual("ok-bd2 0.1.22 开发版", config["debug_window_title"])
        self.assertEqual(["src.debug_profile", "DebugGlobals"], config["my_app"])

    def test_debug_globals_overrides_ok_script_debug_suffix_title(self):
        debug_globals = DebugGlobals.__new__(DebugGlobals)
        titles = []
        main_window = SimpleNamespace(setWindowTitle=titles.append)

        with (
            patch.object(Globals, "on_show_main_window"),
            patch(
                "ok.og.config",
                {"debug_window_title": "ok-bd2 0.1.22 开发版"},
            ),
        ):
            DebugGlobals.on_show_main_window(debug_globals, main_window)

        self.assertEqual(["ok-bd2 0.1.22 开发版"], titles)


if __name__ == "__main__":
    unittest.main()
