import gettext
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

from scripts.compile_translations import compile_catalog, read_catalog
from src.config import config
from src.debug_profile import (
    configure_debug_profile,
    release_version,
)
from src.tasks.debug_registry import (
    DEBUG_ONETIME_TASKS,
    DEBUG_TRIGGER_TASKS,
)

ROOT = Path(__file__).resolve().parents[1]


class ProjectStructureTest(unittest.TestCase):
    def test_release_workflow_has_its_source_inputs_and_gates_publication(self):
        import re

        workflow = (ROOT / ".github/workflows/build.yml").read_text(encoding="utf-8")

        def job(name):
            return re.split(
                r"^  [\w-]+:$", workflow.split(f"  {name}:", 1)[1], maxsplit=1, flags=re.MULTILINE
            )[0]

        quality = job("quality")
        for key in (
            "GITHUB_TOKEN",
            "GH_TOKEN",
            "CNB_GH",
            "OK_GH",
            "SIGNPATH_API_TOKEN",
            "MirrorChyanUploadToken",
            "ACTIONS_RUNTIME_TOKEN",
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
        ):
            with self.subTest(variable=key):
                self.assertIn(f'{key}: ""', quality)
                self.assertIn(f'"{key}"', quality)
        prepare = job("prepare")
        for step in (
            "npm --prefix desktop/ui run build",
            "npm --prefix desktop/shell run pack:win",
            "desktop/scripts/build-shell-release.py",
            "src/desktop/shell-release.json",
            "name: desktop-release",
        ):
            self.assertIn(step, prepare)
        self.assertLess(
            prepare.index("Build Electron interface and shell"),
            prepare.index("Sync update repositories"),
        )
        package = job("package")
        self.assertIn("name: desktop-release", package)
        self.assertIn("profile: China", package)
        self.assertIn("profile: Global", package)
        self.assertIn("needs.prepare.outputs.compression", package)
        release = job("release")
        self.assertIn("name: desktop-release", release)
        self.assertIn("Copy-Item desktop-release/*.zip pyappify_dist/", release)
        expected = [
            "main.py",
            "main_debug.py",
            "pyproject.toml",
            "requirements.txt",
            "src/config.py",
            "src/tasks/DailyBatchTask.py",
            "src/tasks/DailyTask.py",
            "src/tasks/QuickHuntTask.py",
            "src/tasks/BargainLevelTask.py",
            "src/tasks/QuickSuppressionTask.py",
            "src/tasks/MapTradeTask.py",
            "src/tasks/MapCollectionTask.py",
            "src/tasks/FreeGachaTask.py",
            "src/tasks/BD2ProbeTask.py",
            "src/tasks/BD2OneTimeTask.py",
            "src/tasks/BaseBD2Task.py",
            "src/tasks/trigger/BD2TriggerTask.py",
            "src/scene/BD2Scene.py",
            "src/interaction/BD2Interaction.py",
            "main_desktop.py",
            "src/desktop/service.py",
            "assets/coco_annotations.json",
            "assets/map_trade/calendar_sources.json",
            "assets/map_trade/price_calendar.v1.json",
        ]
        for relative_path in expected:
            with self.subTest(path=relative_path):
                self.assertTrue((ROOT / relative_path).exists())


class VersionConsistencyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        cls.expected_version = cls.pyproject["project"]["version"]

    def test_source_and_debug_profiles_expose_their_version(self):
        from src.config import config, runtime_version, version

        self.assertEqual("release-tag-unset", version)
        self.assertNotEqual(self.expected_version, version)

        self.assertEqual(self.expected_version, config["version"])
        self.assertEqual(self.expected_version, runtime_version(ROOT / "pyproject.toml"))
        for tag, expected in (("v0.1.21", "0.1.22"), ("v2.0.5", "2.0.6")):
            with self.subTest(tag=tag):
                debug = dict(config)
                self.assertEqual(expected, configure_debug_profile(debug, release_tag=tag))
                self.assertEqual(expected, debug["version"])
                self.assertTrue(debug["debug"])
                self.assertEqual(f"ok-bd2 {expected} 开发版", debug["gui_title"])
                self.assertEqual(config["my_app"], debug["my_app"])
        with self.assertRaises(ValueError):
            release_version("v0.1.21-beta")


CHECKER = ROOT / "scripts" / "check_commit_attribution.py"


class CommitAttributionTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repo = Path(directory.name)
        self.git_env = {**os.environ, "GIT_CONFIG_COUNT": "0"}
        self.git("init", "--quiet")
        self.git("config", "user.name", "Test Author")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "core.hooksPath", str(self.repo / "no-hooks"))
        self.git("config", "commit.gpgSign", "false")
        self.git("commit", "--quiet", "--allow-empty", "-m", "Initial commit")

    def git(self, *args):
        subprocess.run(
            ["git", *args], cwd=self.repo, env=self.git_env, capture_output=True, check=True
        )

    def run_checker(self, message: str) -> subprocess.CompletedProcess[str]:
        message_path = self.repo / "COMMIT_EDITMSG"
        message_path.write_text(message, encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(CHECKER), "--message-file", str(message_path)],
            cwd=self.repo,
            env=self.git_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def test_commit_attribution_checks_pending_message_and_history(self):
        clean = self.run_checker("fix(ui): keep attribution local")
        self.assertEqual(0, clean.returncode, clean.stderr)
        self.assertIn("Commit attribution check passed.", clean.stdout)
        message = "fix(ui): example\n\nCo-Authored-By: Claude Fable 5 <noreply@anthropic.com>\n"

        pending = self.run_checker(message)
        self.git("commit", "--quiet", "--allow-empty", "-m", message)
        history = self.run_checker("Regular follow-up")
        for result in (pending, history):
            self.assertEqual(1, result.returncode)
            self.assertIn("Prohibited commit attribution found", result.stderr)
            self.assertIn("Co-Authored-By: Claude Fable 5", result.stderr)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


CATALOG_ROOT = REPOSITORY_ROOT / "i18n"


ENGLISH_CATALOG = CATALOG_ROOT / "en_US" / "LC_MESSAGES" / "ok.po"


ENGLISH_MESSAGES = ENGLISH_CATALOG.with_suffix(".mo")


class TranslationCatalogTests(unittest.TestCase):
    def test_english_catalog_loads_and_translates_project_ui(self):
        messages = read_catalog(ENGLISH_CATALOG)
        self.assertTrue(all(value for msgid, value in messages.items() if msgid))
        self.assertEqual(compile_catalog(messages), ENGLISH_MESSAGES.read_bytes())
        translation = gettext.translation("ok", CATALOG_ROOT, languages=["en_US"])

        self.assertEqual("Complete dailies", translation.gettext("一键完成日常"))
        self.assertEqual("Quick Hunt", translation.gettext("快速狩猎"))
        self.assertEqual("Arena battle multiplier", translation.gettext("竞技场战斗倍数"))


class DebugRegistryTest(unittest.TestCase):
    def test_formal_and_debug_tasks_have_unique_runtime_names(self):
        from importlib import import_module
        from unittest.mock import Mock

        registrations = [
            *config["onetime_tasks"], *config["trigger_tasks"],
            *DEBUG_ONETIME_TASKS, *DEBUG_TRIGGER_TASKS,
        ]
        executor, app = Mock(), Mock()
        names = {}
        for module, class_name in registrations:
            task = getattr(import_module(module), class_name)(executor=executor, app=app)
            self.assertNotIn(task.name, names, f"Duplicate task name on {class_name}")
            names[task.name] = class_name

    def test_probe_tasks_are_not_registered_in_formal_config(self):
        registered = {tuple(item) for item in config["onetime_tasks"]}
        for registration in DEBUG_ONETIME_TASKS:
            with self.subTest(registration=registration):
                self.assertNotIn(tuple(registration), registered)
        registered_triggers = {tuple(item) for item in config["trigger_tasks"]}
        for registration in DEBUG_TRIGGER_TASKS:
            with self.subTest(registration=registration):
                self.assertNotIn(tuple(registration), registered_triggers)
