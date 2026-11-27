import inspect
import sys
import tempfile
import types
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import ok.util.shortcut as shortcut

import src.config  # noqa: F401  (installs the compat patches, as the entry points do)
from src.compat import starter_guard, startup
from src.compat.app_shortcuts import disable_framework_shortcuts
from src.compat.starter_guard import (
    StarterWaitPolicy,
    StarterWizardWatcher,
)
from src.compat.starter_launch import (
    wrap_starter_execute,
)
from src.compat.starter_update import (
    StarterObservation,
)
from src.config import config
from src.game_path import (
    calculate_pc_exe_path,
    get_launch_game_id,
    resolve_game_exe_path,
    resolve_launcher_exe_path,
)


class StartApplicationTest(unittest.TestCase):
    def guarded_startup(
        self, events, *, acquired=True, legacy=None, argv=("main.py", "--headless")
    ):
        """Patch the executor guard so tests never take a real session lock."""
        legacy_guard = SimpleNamespace(blocking_message=lambda force=False: legacy)
        holder = None if acquired else SimpleNamespace(describe=lambda: "进程 7（qt）")
        directory = self.enterContext(tempfile.TemporaryDirectory())
        patches = [
            patch.object(startup, "SCHEDULED_FAILURE_DIR", Path(directory) / "failures"),
            patch.object(sys, "argv", list(argv)),
            patch.object(startup, "open_crash_log"),
            patch.object(startup.faulthandler, "enable"),
            patch.object(startup.faulthandler, "disable"),
            patch("src.executor_guard.mark_current_process"),
            patch(
                "src.executor_guard.release_executor_lock",
                side_effect=lambda: events.append("released"),
            ),
            patch.object(
                startup,
                "_notify_headless_failure",
                side_effect=lambda arguments, message: events.append(("notice", message)),
            ),
            patch(
                "src.executor_guard.acquire_executor_lock",
                side_effect=lambda mode: events.append(("lock", mode)) or (acquired, holder),
            ),
            patch(
                "src.executor_guard.install_executor_guards",
                side_effect=lambda: events.append("guards"),
            ),
            patch("src.executor_guard.legacy_guard", return_value=legacy_guard),
            patch(
                "src.executor_guard.show_refusal_dialog",
                side_effect=lambda message: events.append(("dialog", message)),
            ),
            patch(
                "src.compat.dependency_guard.ensure_core_dependencies",
                side_effect=lambda: events.append("dependencies"),
            ),
        ]
        for item in patches:
            self.enterContext(item)

    def fake_ok(self, events):
        class FakeOk:
            def __init__(self, config):
                events.append(("ok_init", dict(config)))
                task = startup.parse_entry_arguments().task
                self.args = {
                    "task": int(task) if task and task.isdigit() else task,
                    "exit": "-e" in sys.argv,
                }

            def start(self):
                events.append("start")

            def start_mcp_server(self):
                events.append("mcp")

            def run_task(self, task, exit_after=False):
                events.append(("run_task", task, exit_after))

            def quit(self):
                events.append("quit")

        return SimpleNamespace(OK=FakeOk)

    def test_headless_start_requires_lock_before_loading_runtime(self):
        for acquired in (False, True):
            with self.subTest(acquired=acquired):
                events = []
                self.guarded_startup(events, acquired=acquired, argv=("main.py", "-t", "1", "-e"))

                def config():
                    events.append("load_config")
                    return {"debug": False}

                with patch.dict(sys.modules, {"ok": self.fake_ok(events)}):
                    if acquired:
                        startup.start_application(config)
                        self.assertEqual(
                            events[:4],
                            [("lock", "headless"), "dependencies", "load_config", "guards"],
                        )
                        self.assertEqual(
                            events[-4:], ["mcp", ("run_task", 1, True), "quit", "released"]
                        )
                        startup.open_crash_log.return_value.close.assert_called_once()
                    else:
                        with self.assertRaises(SystemExit) as failure:
                            startup.start_application(config)
                        self.assertEqual(failure.exception.code, 11)
                        self.assertEqual(events[0], ("lock", "headless"))
                        self.assertNotIn("dependencies", events)
                        self.assertNotIn("load_config", events)
                        self.assertEqual(
                            startup.scheduled_start_failures()[0]["details"]["退出码"], "11"
                        )


def make_window() -> starter_guard.StarterWindow:
    return starter_guard.StarterWindow(
        pid=4242,
        exe_name="browndust2starter.exe",
        title="BrownDust II",
        width=302,
        height=193,
    )


class StarterWizardWatcherTest(unittest.TestCase):
    def test_wizard_notifications_follow_observed_progress(self):
        for state, expected in (
            ("wizard", (0, 0, 1, 1)),
            ("unknown", (0, 0, 0, 0)),
            ("updating", (0, 0, 0, 0)),
        ):
            now = [1000]
            notify = Mock()
            observation = StarterObservation(state, "解压", (0.5, 0.9), (4242, 1), 100)
            watcher = StarterWizardWatcher(
                grace_seconds=30,
                find_windows=lambda: [make_window()],
                notify=notify,
                observe=lambda _: observation,
                clock=lambda: now[0],
            )
            for elapsed, count in zip((0, 29, 31, 200), expected, strict=True):
                with self.subTest(state=state, elapsed=elapsed):
                    now[0] = 1000 + elapsed
                    watcher.poll_once()
                    self.assertEqual(count, notify.call_count)
            if state == "updating":
                for elapsed, next_observation in (
                    (201, replace(observation, bars=(0.5, 0.05))),
                    (400, replace(observation, io_bytes=100000)),
                ):
                    observation = next_observation
                    now[0] = 1000 + elapsed
                    watcher.poll_once()
                    self.assertEqual(watcher.latest.progress_at, now[0])


class StarterWaitPolicyTest(unittest.TestCase):
    def test_observations_control_launch_budget_and_recovery(self):
        # Each sequence uses one clock and one policy so loss of a previously seen
        # update and the subsequent game-launch grace exercise real state transitions.
        for sequence in (
            (
                (0, "updating", 0, 0, 0, "解压", False),
                (1800, "updating", 1800, 1800, 0, "解压", False),
                (1802, "absent", 1802, 0, 120, "", False),
                (1921, "absent", 1921, 0, 1, "", False),
                (1922, "absent", 1922, 0, 0, "", True),
            ),
            (
                (0, "unknown", 0, 0, 120, "", False),
                (119, "unknown", 119, 0, 1, "", False),
                (120, "unknown", 120, 0, 0, "", True),
            ),
            (
                (301, "updating", 301, 0, 0, "5 分钟", False),
                (302, "updating", 302, 302, 0, "解压", False),
            ),
            (
                (10, "updating", 10, 0, 0, "解压", False),
                (309, "unknown", 309, 0, 0, "无法读取", False),
                (310, "unknown", 310, 0, 0, "", True),
            ),
            (
                (0, "updating", 0, 0, 0, "解压", False),
                (16, "updating", 0, 0, 0, "无法读取", False),
                (301, "updating", 0, 0, 0, "", True),
            ),
            ((900, "wizard", 900, 0, 0, "安装向导", False), (902, "error", 902, 0, 0, "", True)),
        ):
            policy = StarterWaitPolicy(120, 0)
            for now, state, observed, progress, remaining, text, failed in sequence:
                with self.subTest(state=state, now=now):
                    obs = StarterObservation(
                        state, "解压", observed_at=observed, progress_at=progress
                    )
                    seconds, message, error = policy.evaluate(obs, now)
                    self.assertEqual(remaining, seconds)
                    self.assertIn(text, message)
                    self.assertEqual(failed, bool(error))


class GameWindowWaitTest(unittest.TestCase):
    def test_mcp_device_prepare_starts_cold_and_recovers_after_cancellation(self):
        from ok.core import start_controller as upstream
        from ok.core.mcp_server import LocalMcpServer

        class Controller(upstream.StartController):
            pass

        for name in ("start", "do_start", "start_device", "cancel_start"):
            setattr(Controller, name, inspect.unwrap(getattr(Controller, name)))
        module = types.SimpleNamespace(StartController=Controller, communicate=Mock())
        starter_guard._patch_start_cancellation(module)
        with patch.object(upstream, "Handler"):
            controller = Controller({"windows": {"start_exe": True}}, Mock())
        manager = Mock()
        manager.get_preferred_device.return_value = {"device": "windows", "connected": True}
        runtime = types.SimpleNamespace(task_executor=Mock(), device_manager=manager)
        app = types.SimpleNamespace(start_controller=controller)
        server = types.SimpleNamespace(runtime=runtime, runner=types.SimpleNamespace(app=app))

        def ready(**_kwargs):
            return not controller._cancel_requested.is_set()

        with patch.object(upstream.og, "device_manager", manager):
            controller._wait_until_device_ready = ready
            LocalMcpServer._prepare_device(server)
            controller._wait_until_device_ready = lambda **_: controller.cancel_start() and ready()
            with self.assertRaisesRegex(RuntimeError, "could not be started"):
                LocalMcpServer._prepare_device(server)
            controller._wait_until_device_ready = ready
            LocalMcpServer._prepare_device(server)

    def test_cancellation_stops_queued_and_waiting_starts_and_rejects_activation(self):
        from ok.core.start_controller import StartController

        for phase in ("queued", "waiting", "activation"):
            with self.subTest(phase=phase):
                # A subclass keeps ok-script's pending-task helpers (3.0.5+) while the
                # patched entry points start from the unpatched originals.
                controller_class = type(
                    "Controller",
                    (StartController,),
                    {
                        name: inspect.unwrap(getattr(StartController, name))
                        for name in ("start", "do_start", "cancel_start")
                    },
                )
                controller_class.start_device = lambda self, **_kwargs: (
                    (self.cancel_start() if phase == "waiting" else None) or True
                )
                events = Mock()
                starter_guard._patch_start_cancellation(
                    types.SimpleNamespace(
                        StartController=controller_class,
                        communicate=events,
                    )
                )
                controller = controller_class.__new__(controller_class)
                controller.starting = False
                queue = []
                controller.handler = types.SimpleNamespace(post=queue.append)
                activations = []

                def activate(*_args):
                    if not controller.start_device():
                        return False
                    self.assertFalse(controller.cancel_start())
                    activations.append(True)
                    return True

                controller._do_start = activate
                controller.start()
                if phase == "queued":
                    self.assertTrue(controller.cancel_start())
                result = queue.pop()()
                self.assertEqual(result, phase == "activation")
                self.assertEqual(activations, [True] if phase == "activation" else [])
                self.assertFalse(controller.starting)
                self.assertFalse(controller.cancel_start())
                controller.start_device = lambda **_kwargs: True
                controller.start()
                self.assertTrue(queue.pop()())
                self.assertFalse(controller.starting)

    def test_finishing_start_preserves_a_new_queued_request(self):
        from ok.core.start_controller import StartController

        original_do_start = inspect.unwrap(StartController.do_start)
        first, second = object(), object()

        def finish_first_then_queue_second(controller, task, exit_after, **kwargs):
            result = original_do_start(controller, task, exit_after, **kwargs)
            if task is first:
                controller.start(second)
            return result

        controller_class = type(
            "Controller",
            (StartController,),
            {
                "start": inspect.unwrap(StartController.start),
                "do_start": finish_first_then_queue_second,
                "cancel_start": inspect.unwrap(StartController.cancel_start),
            },
        )
        starter_guard._patch_start_cancellation(
            types.SimpleNamespace(
                StartController=controller_class,
                communicate=Mock(),
            )
        )
        controller = controller_class.__new__(controller_class)
        controller.starting = False
        queue = []
        controller.handler = types.SimpleNamespace(post=queue.append)
        controller._do_start = Mock(return_value=True)

        controller.start(first)
        self.assertTrue(queue.pop(0)())
        self.assertTrue(controller.starting)
        self.assertTrue(controller.is_task_pending(second))
        self.assertFalse(controller.start(object()))
        self.assertTrue(controller.cancel_start())
        self.assertFalse(queue.pop(0)())
        self.assertFalse(controller.starting)


class GamePathTest(unittest.TestCase):
    def test_launcher_resolution_prefers_running_then_programdata_then_registered(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            registry = root / "registered"
            programdata = root / "Neowiz/Browndust2Starter"
            for parent in (registry, programdata):
                parent.mkdir(parents=True)
                (parent / "Browndust2Starter.exe").touch()
            for running, data, expected in (
                (registry / "Browndust2Starter.exe", root, registry),
                ("", root, programdata),
                ("", root / "absent", registry),
            ):
                with (
                    self.subTest(running=running, data=data),
                    patch("src.game_path.find_running_launcher_path", return_value=str(running)),
                    patch("src.game_path._registry_install_values", return_value=[str(registry)]),
                ):
                    self.assertEqual(
                        str(expected / "Browndust2Starter.exe"),
                        resolve_launcher_exe_path(env={"PROGRAMDATA": str(data)}),
                    )
        with patch("src.game_path.resolve_launcher_exe_path", return_value=""):
            self.assertEqual("", calculate_pc_exe_path(Path("D:/Games/BrownDust II.exe")))

    def test_game_id_and_path_from_registration_and_overrides(self):
        with tempfile.TemporaryDirectory() as folder:
            game = Path(folder) / "BrownDust II.exe"
            game.touch()
            records = [
                ("10000001", folder + "-missing", game.name),
                ("10000002", folder, game.name),
            ]
            for env, registered, expected in (
                ({}, [], "10000001"),
                ({}, records, "10000002"),
                ({"OK_BD2_LAUNCH_GAME_ID": "10000009"}, records, "10000009"),
            ):
                with (
                    self.subTest(expected=expected),
                    patch("src.game_path._neowiz_registered_games", return_value=registered),
                ):
                    self.assertEqual(expected, get_launch_game_id(env))
            with (
                patch("src.game_path.find_running_game_path", return_value=""),
                patch("src.game_path._registry_install_values", return_value=[]),
                patch("src.game_path._neowiz_registered_games", return_value=records),
            ):
                self.assertEqual(str(game), resolve_game_exe_path(env={}))
                self.assertEqual(str(game), resolve_game_exe_path(running_path=game, env={}))


class LauncherUpdateConfigTest(unittest.TestCase):
    def starter_uri_context(self, game_id: str = "10000001"):
        return patch("src.compat.starter_launch.get_launch_game_id", return_value=game_id)

    def test_starter_execute_wrapper_forwards_uri_to_ok_script(self):
        original_execute = Mock(return_value=True)
        wrapped_execute = wrap_starter_execute(original_execute)

        options = config["global_configs"][0]
        self.assertTrue(options.config_type["Launch with DX11"]["hidden"])
        self.assertFalse(options.validator("Launch with DX11", True)[0])
        self.assertTrue(options.validator("Launch with DX11", False)[0])
        with self.starter_uri_context():
            self.assertTrue(
                wrapped_execute(
                    r"C:\ProgramData\Neowiz\Browndust2Starter\Browndust2Starter.exe",
                    start_method="start",
                )
            )
        original_execute.assert_called_once_with(
            r"C:\ProgramData\Neowiz\Browndust2Starter\Browndust2Starter.exe",
            arguments='"browndust2:games/10000001?usn=0"',
            start_method="start",
        )


class FrameworkShortcutTest(unittest.TestCase):
    def test_headless_start_leaves_shortcuts_to_the_launcher(self):
        disable_framework_shortcuts()
        with patch("pyappify.create_shortcut", create=True) as create:
            self.assertIsNone(shortcut.create_app_shortcut({}))
        create.assert_not_called()
