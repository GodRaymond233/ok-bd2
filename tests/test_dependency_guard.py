"""BUG-20260905-07：核心依赖预检与自愈（dependency_guard）单测。"""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from src.compat import dependency_guard as guard


class FindMissingModulesTest(unittest.TestCase):
    def test_reports_absent_specs(self):
        with patch(
            "importlib.util.find_spec",
            lambda name: None if name == "win32con" else object(),
        ):
            self.assertEqual(guard.find_missing_modules(), ["win32con"])

    def test_treats_import_error_as_missing(self):
        def raise_error(name):
            raise ImportError("broken parent package")

        with patch("importlib.util.find_spec", raise_error):
            self.assertEqual(
                sorted(guard.find_missing_modules()),
                sorted(guard.CORE_MODULE_PACKAGES),
            )


class ParseLockedVersionsTest(unittest.TestCase):
    def test_skips_comments_and_keeps_markers(self):
        text = (
            "#\n"
            "# via ok-bd2\n"
            "certifi==2026.7.22 ; sys_platform == 'win32'\n"
            "    # via requests\n"
            "pywin32==311 ; sys_platform == 'win32'\n"
            "ok-script==2.0.6 ; sys_platform == 'win32'\n"
            "PySide6==6.9.1 ; sys_platform == 'win32'\n"
        )
        self.assertEqual(
            guard.parse_locked_versions(text),
            {
                "certifi": "2026.7.22",
                "pywin32": "311",
                "ok-script": "2.0.6",
                "pyside6": "6.9.1",
            },
        )


class MissingPackagesTest(unittest.TestCase):
    def test_deduplicates_modules_to_dist_packages(self):
        self.assertEqual(
            guard._missing_packages(["win32con", "win32api", "cv2"]),
            {
                "pywin32": ["win32con", "win32api"],
                "opencv-python": ["cv2"],
            },
        )


class BuildRepairCommandTest(unittest.TestCase):
    def test_pins_version_and_mirror(self):
        self.assertEqual(
            guard.build_repair_command("pywin32", "311", "https://mirror/simple"),
            [
                guard.sys.executable,
                "-m",
                "pip",
                "install",
                "--force-reinstall",
                "--no-deps",
                "pywin32==311",
                "--index-url",
                "https://mirror/simple",
            ],
        )

    def test_without_mirror_has_no_index_url(self):
        self.assertNotIn(
            "--index-url", guard.build_repair_command("pywin32", "311", None)
        )


class ForceReinstallTest(unittest.TestCase):
    def test_falls_back_to_next_mirror(self):
        calls = []

        def fake_run(command, **_kwargs):
            calls.append(command)
            if len(calls) == 1:
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="boom")
            return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

        with patch.object(guard.subprocess, "run", fake_run):
            self.assertTrue(guard.force_reinstall("pywin32", "311"))
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][-2:], ["--index-url", guard.PIP_INDEX_FALLBACK[0]])
        self.assertNotIn("--index-url", calls[1])

    def test_returns_false_when_all_mirrors_fail(self):
        def fake_run(command, **_kwargs):
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="boom")

        with patch.object(guard.subprocess, "run", fake_run):
            self.assertFalse(guard.force_reinstall("pywin32", "311"))


class EnsureCoreDependenciesTest(unittest.TestCase):
    def run_ensure(self, missing, locked_versions=None, repair_result=True):
        """missing 为序列时依次作为各次 find_missing_modules 的返回。"""
        context = {"repair_calls": 0, "notify": []}

        def fake_notify(text, *, error=False):
            context["notify"].append((text, error))

        def fake_repair(package, version):
            context["repair_calls"] += 1
            context.setdefault("repaired", []).append((package, version))
            return repair_result

        missing_sequence = missing if isinstance(missing, list) and (
            not missing or isinstance(missing[0], list)
        ) else [missing]

        with (
            patch.object(guard, "find_missing_modules", side_effect=missing_sequence),
            patch.object(guard, "read_locked_versions", lambda: locked_versions or {}),
            patch.object(guard, "force_reinstall", fake_repair),
            patch.object(guard, "_notify", fake_notify),
        ):
            exit_code = None
            try:
                guard.ensure_core_dependencies()
            except SystemExit as exit_error:
                exit_code = exit_error.code
        return exit_code, context

    def test_returns_silently_when_all_present(self):
        exit_code, context = self.run_ensure([[]])
        self.assertIsNone(exit_code)
        self.assertEqual(context["repair_calls"], 0)

    def test_repairs_and_exits_zero_when_fixed(self):
        exit_code, context = self.run_ensure(
            [["win32con"], []], locked_versions={"pywin32": "311"}
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(context["repaired"], [("pywin32", "311")])
        self.assertFalse(context["notify"][0][1])

    def test_exits_one_without_locked_versions(self):
        exit_code, context = self.run_ensure([["win32con"], ["win32con"]])
        self.assertEqual(exit_code, 1)
        self.assertEqual(context["repair_calls"], 0)
        self.assertTrue(context["notify"][0][1])

    def test_exits_one_when_repair_fails(self):
        exit_code, context = self.run_ensure(
            [["win32con"], ["win32con"]],
            locked_versions={"pywin32": "311"},
            repair_result=False,
        )
        self.assertEqual(exit_code, 1)
        self.assertEqual(context["repair_calls"], 1)
        self.assertTrue(context["notify"][0][1])


if __name__ == "__main__":
    unittest.main()
