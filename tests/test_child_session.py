import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from ok.child_session import protocol
from ok.device import DeviceManager as device_module

from src.child_session.agent import BD2Agent, private_json
from src.child_session.compat import BD2AgentClient, BD2ChildSessionCapture
from src.child_session.controller import ChildSessionController
from src.child_session.input import MouseInput
from src.child_session.interaction import BD2ChildSessionInteraction, window_context
from src.config import config
from src.interaction.BD2Interaction import InputTargetChanged
from src.tasks.BaseBD2Task import BaseBD2Task
from tests.helpers.desktop import make_client

INFO = {"session": 3, "pid": 22, "hwnd": 44, "width": 1280, "height": 720, "exists": True}


class ChildSessionTest(unittest.TestCase):
    def interaction(self):
        window = SimpleNamespace(is_child_session=True, width=1280, height=720,
                                 info=Mock(return_value=dict(INFO)))
        capture = SimpleNamespace(hwnd_window=window, client=Mock(),
                                  input_context=window_context(INFO))
        capture.client.call.return_value = {"ok": True}
        return BD2ChildSessionInteraction(capture, window)

    def test_click_drag_and_scroll_use_only_child_agent_and_reject_changed_targets(self):
        interaction = self.interaction()
        task = BaseBD2Task.__new__(BaseBD2Task)
        task._executor = SimpleNamespace(interaction=interaction, method=interaction.capture)
        task.sleep = Mock()
        task.capture_frame = Mock(return_value=np.zeros((720, 1280, 3), np.uint8))
        with (
            patch("win32api.SetCursorPos") as cursor,
            patch("win32api.mouse_event") as host_mouse,
        ):
            interaction.click(10, 20)
            task.drag_client((10, 20), (30, 40), 0.1)
            task.scroll_client((0.5, 0.5), -4)
            calls = interaction.capture.client.call.call_args_list
            self.assertEqual(["click", "swipe", "scroll"],
                             [call.kwargs["operation"] for call in calls])
            for call in calls:
                self.assertEqual("bd2_mouse", call.args[0])
                self.assertEqual(list(window_context(INFO)), call.kwargs["context"])
            interaction.capture.hwnd_window.info.return_value = dict(INFO, pid=23)
            with self.assertRaises(InputTargetChanged):
                interaction.click(10, 20)
            self.assertEqual(3, interaction.capture.client.call.call_count)
            # A preview may have captured the new window while a task kept an older target.
            interaction.capture.input_context = window_context(dict(INFO, pid=23))
            task._frame_input_context = (0, window_context(INFO))
            with self.assertRaises(InputTargetChanged):
                task.operate(lambda: interaction.click(10, 20))
            task._executor.interaction = SimpleNamespace()
            with self.assertRaises(RuntimeError):
                task.drag_client((10, 20), (30, 40))
            cursor.assert_not_called()
            host_mouse.assert_not_called()

    def test_capture_attaches_only_the_context_of_the_returned_frame(self):
        client = Mock()
        window = SimpleNamespace(is_child_session=True)
        capture = BD2ChildSessionCapture(client, window, threading.Event())
        frame = np.zeros((720, 1280, 3), np.uint8)
        reply = {"w": 1280, "h": 720, "context": list(window_context(INFO))}
        client.request.return_value = reply, frame.tobytes()
        self.assertEqual(frame.shape, capture.do_get_frame().shape)
        self.assertEqual(window_context(INFO), capture.input_context)
        client.request.return_value = dict(reply, context=[3, 22, 44, 1920, 1080]), frame.tobytes()
        self.assertIsNone(capture.do_get_frame())
        self.assertIsNone(capture.input_context)
        with self.assertRaises(RuntimeError):
            capture.get_abs_cords(10, 20)

    def test_agent_limits_commands_and_does_not_dispatch_stale_coordinates(self):
        agent = BD2Agent.__new__(BD2Agent)
        agent._command_lock = threading.Lock()
        agent.window_info = Mock(return_value=dict(INFO))
        agent.input = Mock()
        agent._launch = {
            "path": "C:\\Games\\Browndust2Starter.exe", "args": "uri", "cwd": "C:\\Games"
        }
        for command in (
            "configure", "restart", "close_windows", "send_key", "key_down", "input_text"
        ):
            with self.subTest(command=command), self.assertRaises(ValueError):
                agent.handle(command, {})
        with patch("os.startfile") as launch:
            with self.assertRaises(ValueError):
                agent.handle("launch", {"path": "C:\\Windows\\System32\\cmd.exe"})
            launch.assert_not_called()
        reply, _ = agent.handle("bd2_mouse", {
            "context": [3, 99, 44, 1280, 720], "operation": "click", "x": 10, "y": 20,
        })
        self.assertTrue(reply["target_changed"])
        agent.input.click.assert_not_called()
        agent.handle("bd2_mouse", {"context": list(window_context(INFO)), "operation": "click",
                                    "x": 10, "y": 20})
        agent.input.click.assert_called_once_with(44, x=10, y=20)

    def test_mouse_activation_failure_never_presses_a_button(self):
        mouse = MouseInput.__new__(MouseInput)
        mouse.mouse = Mock()
        mouse.buttons = SimpleNamespace(left="left")
        with (
            patch("win32gui.GetClientRect", return_value=(0, 0, 1280, 720)),
            patch("win32gui.ClientToScreen", return_value=(10, 20)),
            patch("win32gui.GetForegroundWindow", return_value=1),
            patch("win32gui.SetForegroundWindow"),
        ):
            with self.assertRaises(RuntimeError):
                mouse.click(44, 10, 20)
        mouse.mouse.press.assert_not_called()

    def test_client_does_not_repeat_input_when_the_reply_is_lost(self):
        client = BD2AgentClient(".")
        client._sock, client._reader = Mock(), Mock()
        with (
            patch.object(client, "_connect"),
            patch.object(protocol, "send_message") as send,
            patch.object(protocol, "read_message", side_effect=ConnectionError),
        ):
            with self.assertRaises(protocol.AgentError):
                client.call("bd2_mouse", operation="click", x=10, y=20)
        send.assert_called_once()
        self.assertIsNone(client._sock)

    def test_request_requires_authentication_and_matching_protocol(self):
        agent = BD2Agent.__new__(BD2Agent)
        agent.exit_event = threading.Event()
        agent._connections = threading.BoundedSemaphore(4)
        agent.token = "example-test-token"
        agent.handle = Mock(return_value=({"ok": True}, None))
        for token, version in (("wrong", 1), (agent.token, -1)):
            with self.subTest(token=token, version=version):
                client, server = socket.socketpair()
                worker = threading.Thread(target=agent.serve_connection, args=(server,))
                worker.start()
                with client:
                    client.settimeout(2)
                    protocol.send_message(client, {
                        "cmd": "hello", "token": token, "version": version
                    })
                    self.assertEqual(b"", client.recv(1))
                worker.join(2)
                self.assertFalse(worker.is_alive())
        agent.handle.assert_not_called()

    def test_lifecycle_keeps_game_on_disconnect_and_requires_explicit_logoff(self):
        manager = SimpleNamespace(config={"capture": "ChildSession"}, set_capture=Mock())
        controller = ChildSessionController(manager)
        self.addCleanup(controller.close)
        controller.client = Mock()
        controller.viewer = Mock()
        controller.viewer.poll.return_value = None
        with (
            patch("src.child_session.controller.host.remove_autostart"),
            patch("src.child_session.controller.win_api.child_session_id", return_value=3),
            patch("src.child_session.controller.win_api.logoff", return_value=True) as logoff,
            patch.object(controller, "state", return_value={}),
        ):
            controller.close()
            logoff.assert_not_called()
            controller.client.try_call.assert_not_called()
            controller.logoff()
            logoff.assert_called_once_with(3, wait=False)
            manager.set_capture.assert_called_once_with("windows")

    def test_agent_state_file_is_private_to_the_current_user(self):
        import win32security

        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "agent.json")
            private_json(path, {"token": "test-token"})
            self.assertEqual({"token": "test-token"}, json.loads(Path(path).read_text()))
            descriptor = win32security.GetFileSecurity(
                path, win32security.DACL_SECURITY_INFORMATION
            )
            acl = descriptor.GetSecurityDescriptorDacl()
            self.assertEqual(1, acl.GetAceCount())

    def test_child_capture_and_input_stay_paired(self):
        self.assertTrue(config["child_session"])
        manager = device_module.DeviceManager.__new__(device_module.DeviceManager)
        manager.config = {"capture": "ChildSession", "interaction": "ChildSession"}
        with self.assertRaises(ValueError):
            manager.set_interaction("BD2Interaction")
        manager.config["capture"] = "windows"
        with self.assertRaises(ValueError):
            manager.set_interaction("ChildSession")

    def test_child_starter_keeps_uri_and_accepts_remote_window_identity(self):
        from ok.core import start_controller

        from src.compat import starter_guard
        from src.compat.starter_launch import starter_launch_uri
        from src.compat.starter_update import StarterObservation

        window = SimpleNamespace(is_child_session=True, hwnd=0, exists=True,
                                 width=1920, height=1080, capture_target_signature=(44, 1920, 1080),
                                 do_update_window_size=Mock())
        manager = Mock(hwnd_window=window)
        manager.get_exe_path.return_value = "C:\\Games\\Browndust2Starter.exe"
        manager.uses_child_session.return_value = True
        controller = SimpleNamespace()
        controller.config = {"windows": {}}
        controller.start_timeout = 10
        controller.exit_event = threading.Event()
        controller._cancel_requested = threading.Event()
        controller.STARTED_WINDOW_MIN_SIZE = (1280, 720)
        controller.STARTED_WINDOW_STABLE_SECONDS = 1
        controller.STARTED_WINDOW_POLL_INTERVAL = 0
        remote = Mock()
        remote.__enter__ = Mock(return_value=remote)
        remote.__exit__ = Mock(return_value=False)
        remote.latest = StarterObservation()
        module = SimpleNamespace(og=SimpleNamespace(device_manager=manager), communicate=Mock())
        with (
            patch.object(start_controller.og, "device_manager", manager),
            patch.object(start_controller.og, "global_config") as global_config,
            patch("src.child_session.compat.RemoteStarterWatcher", return_value=remote),
            patch.object(starter_guard, "StarterWizardWatcher") as host_watcher,
            patch.object(starter_guard, "monotonic", side_effect=[0, 0, 2]),
        ):
            global_config.get_config.return_value = {}
            args = start_controller.StartController._launch_arguments(
                controller, {"device": "windows"}
            )
            self.assertEqual(f'"{starter_launch_uri()}"', args)
            self.assertTrue(starter_guard._wait_for_game_window(controller, module))
            host_watcher.assert_not_called()

    def test_desktop_rejects_logoff_without_confirmation_and_while_a_task_runs(self):
        client = make_client(self)
        runtime = client.app.state.runtime
        runtime.child_session = Mock()
        url = "/api/bd2/child-session/logoff"
        self.assertEqual(400, client.post(url, json={}).status_code)
        runtime.child_session.logoff.assert_not_called()
        self.executor.current_task = object()
        self.assertEqual(400, client.post(url, json={"confirm": True}).status_code)
        runtime.child_session.logoff.assert_not_called()
