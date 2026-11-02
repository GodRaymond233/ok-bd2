"""The core interfaces behind the desktop client's device, settings and logs pages."""

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from ok.core.events import communicate
from ok.util.GlobalConfig import create_basic_options, create_notification_options

from src.compat.starter_guard import starter_wait_status
from src.desktop.app import AUTO_CAPTURE_CONFIG, create_app
from src.desktop.settings_text import FIELD_TEXT, OMITTED_KEYS, describe_group
from src.desktop.starter_wait import STARTER_WAIT, StarterWait
from src.window_resolution import MANUAL_RESOLUTIONS, ManualResolutionError, WindowResizeResult
from tests.test_desktop_app import (
    GAME_WINDOW,
    NO_GAME_WINDOW,
    ORIGIN,
    TOKEN,
    FakeDeviceManager,
    make_executor,
)


class FakeGlobalConfig:
    """ok-script's GlobalConfig with the two groups BD2 shows, held in memory."""

    def __init__(self):
        self.options = [create_basic_options(enable_blur=True), create_notification_options()]
        self.configs = {option.name: dict(option.default_config) for option in self.options}

    def get_all_visible_configs(self):
        return [(option.name, self.configs[option.name], option) for option in self.options]

    def get_config(self, name):
        return self.configs[name]


class Window:
    def __init__(self, width, height):
        self.width = width
        self.height = height


def make_client(test, device=GAME_WINDOW, window=None, launcher_version=None):
    test.enterContext(patch("ok.core.events.communicate.task.emit"))
    manager = FakeDeviceManager(device)
    manager.hwnd_window = window
    executor = make_executor()
    executor.global_config = FakeGlobalConfig()
    headless = SimpleNamespace(
        start_controller=SimpleNamespace(start=Mock(), starting=False),
        overlay_state=lambda: {},
        tr=lambda text: text,
    )
    engine = SimpleNamespace(
        config={},
        task_executor=executor,
        device_manager=manager,
        headless_app=headless,
        start_runtime=Mock(),
        quit=Mock(),
        exit_event=threading.Event(),
    )
    app = create_app({}, token=TOKEN, origin=ORIGIN, ok_instance=engine, ui_dist=".")
    app.state.runtime._pyappify_module = SimpleNamespace(pyappify_version=launcher_version)
    test.manager, test.executor, test.headless = manager, executor, headless
    return test.enterContext(
        TestClient(app, base_url=ORIGIN, headers={"Authorization": f"Bearer {TOKEN}"}))


class SettingsTextTest(unittest.TestCase):
    def test_desktop_preferences_follow_saved_settings_and_toggle_requires_session(self):
        client = make_client(self)
        path = "/api/desktop/preferences"
        self.assertEqual(client.get(path).json(), {"closeToTray": False, "hotkey": "F9"})
        config = self.executor.global_config.configs["Basic Options"]
        config["Minimize Window to System Tray when Closing"] = True
        config["Start/Stop"] = "None"
        self.assertEqual(client.get(path).json(), {"closeToTray": True, "hotkey": "None"})
        response = client.post("/api/desktop/toggle-execution")
        self.assertEqual(response.status_code, 200, response.text)
        self.headless.start_controller.starting = True
        self.assertEqual(client.post("/api/desktop/toggle-execution").status_code, 400)
        client.headers.pop("Authorization")
        self.assertEqual(client.post("/api/desktop/toggle-execution").status_code, 403)

    def test_every_shown_setting_has_a_chinese_name(self):
        for option in (create_basic_options(enable_blur=True), create_notification_options()):
            for key in option.default_config:
                if key not in OMITTED_KEYS:
                    with self.subTest(key=key):
                        self.assertIn(key, FIELD_TEXT)

    def test_settings_route_names_fields_and_leaves_out_desktop_messengers(self):
        client = make_client(self)
        self.executor.global_config.configs["Notification"]["Discord Notification"] = True
        groups = {group["name"]: group for group in client.get("/api/settings").json()}
        self.assertEqual("基本设置", groups["Basic Options"]["label"])
        basic = {field["key"]: field for field in groups["Basic Options"]["fields"]}
        self.assertEqual("用显卡加速识别（DirectML）", basic["Use DirectML"]["label"])
        self.assertEqual(
            {"Auto": "自动", "Yes": "是", "No": "否"}, basic["Use DirectML"]["option_labels"])
        self.assertNotIn("Launch with DX11", basic)
        notify = {field["key"]: field for field in groups["Notification"]["fields"]}
        self.assertNotIn("QQ Desktop Notification (Not Reliable)", notify)
        self.assertNotIn("WeChat Desktop Notification (Not Reliable)", notify)
        # The webhook shows once its channel is on, marked as a secret.
        self.assertTrue(notify["Discord Webhook"]["secret"])
        self.assertTrue(notify["Discord Webhook"]["sub_config"])

    def test_saving_a_setting_returns_the_named_group(self):
        client = make_client(self)
        response = client.post(
            "/api/settings/Basic Options/config", json={"key": "Trigger Interval", "value": 5}
        )
        self.assertEqual(200, response.status_code, response.text)
        fields = {field["key"]: field for field in response.json()["fields"]}
        self.assertEqual(5, fields["Trigger Interval"]["value"])
        self.assertEqual("触发任务检查间隔", fields["Trigger Interval"]["label"])

    def test_unknown_fields_keep_their_key_as_the_name(self):
        group = describe_group({"name": "Other", "fields": [{"key": "X", "description": "d"}]})
        self.assertEqual(("Other", "X", "d"), (
            group["label"], group["fields"][0]["label"], group["fields"][0]["description"]))


class CaptureAutoTest(unittest.TestCase):
    def test_auto_restores_the_default_that_tries_methods_in_order(self):
        client = make_client(self)
        self.manager.config["capture"] = "WGC"
        response = client.post("/api/capture-methods/select", json={"id": "auto"})
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(AUTO_CAPTURE_CONFIG, self.manager.config["capture"])
        self.assertIsNone(response.json()["capture_preference"])

    def test_auto_is_refused_while_a_task_runs(self):
        client = make_client(self)
        self.manager.config["capture"] = "WGC"
        self.executor.current_task = object()
        response = client.post("/api/capture-methods/select", json={"id": "auto"})
        self.assertEqual(400, response.status_code)
        self.assertEqual("WGC", self.manager.config["capture"])


class WindowTest(unittest.TestCase):
    def test_window_reports_the_size_and_whether_it_is_16_9(self):
        cases = {
            "1920x1080": (Window(1920, 1080), GAME_WINDOW, [1920, 1080], True),
            "1600x1000": (Window(1600, 1000), GAME_WINDOW, [1600, 1000], False),
            "no game": (None, NO_GAME_WINDOW, None, False),
        }
        for name, (window, device, size, ratio_ok) in cases.items():
            with self.subTest(name):
                client = make_client(self, device=device, window=window)
                info = client.get("/api/bd2/window").json()
                self.assertEqual(size, info["size"])
                self.assertEqual(ratio_ok, info["ratio_ok"])
                self.assertEqual([list(item) for item in MANUAL_RESOLUTIONS], info["resolutions"])
                self.assertFalse(info["resizing"])

    def test_resize_marks_the_resize_in_progress_and_reports_the_result(self):
        client = make_client(self, window=Window(1600, 900))
        seen = []

        def resize(manager, target, executor=None):
            seen.append((target, self.headless.window_resize_in_progress))
            return WindowResizeResult(*target, changed=True)

        with patch("src.desktop.app.resize_game_window", side_effect=resize):
            response = client.post("/api/bd2/window/resize", json={"width": 1920, "height": 1080})
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual({"width": 1920, "height": 1080, "changed": True}, response.json())
        self.assertEqual([((1920, 1080), True)], seen)
        self.assertFalse(self.headless.window_resize_in_progress)

    def test_resize_failures_are_refusals_and_clear_the_flag(self):
        client = make_client(self, window=Window(1600, 900))
        with patch(
            "src.desktop.app.resize_game_window",
            side_effect=ManualResolutionError("游戏窗口已最小化，请先恢复窗口。"),
        ):
            response = client.post("/api/bd2/window/resize", json={"width": 1920, "height": 1080})
        self.assertEqual(400, response.status_code)
        self.assertIn("最小化", response.json()["detail"])
        self.assertFalse(self.headless.window_resize_in_progress)

    def test_resize_is_refused_while_a_task_runs_unpaused_or_the_game_starts(self):
        cases = {
            "task runs": (lambda: setattr(self.executor, "current_task", object()), "暂停"),
            "game starting": (
                lambda: setattr(self.headless.start_controller, "starting", True), "正在启动游戏"),
        }
        for name, (arrange, message) in cases.items():
            with self.subTest(name), patch("src.desktop.app.resize_game_window") as resize:
                client = make_client(self, window=Window(1600, 900))
                arrange()
                response = client.post(
                    "/api/bd2/window/resize", json={"width": 1920, "height": 1080})
                self.assertEqual(400, response.status_code)
                self.assertIn(message, response.json()["detail"])
                resize.assert_not_called()

    def test_resize_is_allowed_while_the_task_is_paused(self):
        client = make_client(self, window=Window(1600, 900))
        self.executor.current_task = object()
        self.executor.paused = True
        with patch(
            "src.desktop.app.resize_game_window", return_value=WindowResizeResult(1920, 1080, True)
        ):
            response = client.post("/api/bd2/window/resize", json={"width": 1920, "height": 1080})
        self.assertEqual(200, response.status_code, response.text)


class GamePathTest(unittest.TestCase):
    def test_game_route_resolves_game_and_starter_paths_separately(self):
        game_path = r"D:\BD2\BrownDust II.exe"
        starter_path = r"D:\Neowiz\Browndust2Starter.exe"
        cases = (
            (starter_path, starter_path),
            (game_path, starter_path),
            ("", starter_path),
            (game_path, ""),
        )
        for recorded_path, detected_starter in cases:
            with self.subTest(recorded=recorded_path, starter=detected_starter):
                client = make_client(self)
                self.manager.config["pc_full_path"] = recorded_path
                with (
                    patch("src.desktop.app.resolve_game_exe_path", return_value=game_path) as game,
                    patch(
                        "src.desktop.app.resolve_launcher_exe_path", return_value=detected_starter
                    ) as launcher,
                ):
                    response = client.get("/api/bd2/game")
                self.assertEqual(200, response.status_code, response.text)
                self.assertEqual({"game": game_path, "launcher": detected_starter}, response.json())
                game.assert_called_once_with(running_path=recorded_path)
                launcher.assert_called_once_with(running_path=recorded_path)


class TestNotificationTest(unittest.TestCase):
    def post(self, client, channel):
        return client.post("/api/bd2/notify/test", json={"channel": channel})

    def test_sends_one_message_through_the_chosen_channel_only(self):
        client = make_client(self)
        notify = self.executor.global_config.configs["Notification"]
        notify["Telegram Bot Token"] = " 123:abc "
        notify["Telegram Chat ID"] = "42"
        notify["Discord Webhook"] = "https://discord.example/hook"
        with (
            patch("ok.notification.providers.TelegramBotProvider.send", return_value=True) as tg,
            patch("ok.notification.providers.DiscordProvider.send") as discord,
        ):
            response = self.post(client, "telegram")
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(("123:abc", "42"), tg.call_args.args[:2])
        discord.assert_not_called()

    def test_names_missing_settings_and_reports_failures(self):
        client = make_client(self)
        response = self.post(client, "telegram")
        self.assertEqual(400, response.status_code)
        self.assertEqual("请先填写：Bot Token、Chat ID", response.json()["detail"])
        self.assertEqual(400, self.post(client, "qq-desktop").status_code)
        self.executor.global_config.configs["Notification"]["Discord Webhook"] = "https://x"
        with patch("ok.notification.providers.DiscordProvider.send", return_value=False):
            response = self.post(client, "discord")
        self.assertEqual(400, response.status_code)
        self.assertIn("发送失败", response.json()["detail"])
        self.assertEqual(400, self.post(client, "system").status_code)
        self.headless.notification_manager = SimpleNamespace(notify_system=Mock(return_value=True))
        self.assertEqual(200, self.post(client, "system").status_code)
        self.headless.notification_manager.notify_system.assert_called_once()
        self.headless.notification_manager.notify_system.return_value = False
        self.assertEqual(400, self.post(client, "system").status_code)


class StarterWaitTest(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.wait = StarterWait(clock=lambda: self.now)
        self.wait.install()
        self.addCleanup(self.wait.uninstall)
        self.events = []
        listener = communicate.any.connect(lambda event: self.events.append(event.name))
        self.addCleanup(communicate.any.disconnect, listener)

    def test_reports_the_wait_until_the_start_ends(self):
        cancel = threading.Event()
        communicate.starting_emulator.emit(False, None, 110)
        starter_wait_status.emit("", cancel)
        self.now += 12
        starter_wait_status.emit("游戏正在更新，已等待 12 秒", cancel)
        self.assertEqual(
            {"message": "游戏正在更新，已等待 12 秒", "seconds_left": 110, "waited": 12},
            self.wait.snapshot(),
        )
        self.assertIn("starter_wait", self.events)
        communicate.starting_emulator.emit(True, None, 0)
        self.assertIsNone(self.wait.snapshot())
        self.assertFalse(self.wait.cancel())

    def test_cancel_sets_the_wait_s_own_event(self):
        cancel = threading.Event()
        starter_wait_status.emit("", cancel)
        self.assertTrue(self.wait.cancel())
        self.assertTrue(cancel.is_set())
        self.assertIsNone(self.wait.snapshot())
        self.assertFalse(self.wait.cancel())

    def test_capture_ui_and_cancel_route_use_the_process_wait(self):
        client = make_client(self)
        self.addCleanup(communicate.starting_emulator.emit, True, None, 0)
        self.assertIsNone(client.get("/api/ui/capture").json()["starter_wait"])
        cancel = threading.Event()
        starter_wait_status.emit("", cancel)
        self.assertIsNotNone(STARTER_WAIT.snapshot())
        self.assertIsNotNone(client.get("/api/ui/capture").json()["starter_wait"])
        self.assertEqual({"cancelled": True}, client.post("/api/bd2/starter/cancel").json())
        self.assertTrue(cancel.is_set())
        self.assertEqual({"cancelled": False}, client.post("/api/bd2/starter/cancel").json())


class LauncherInfoTest(unittest.TestCase):
    def test_launcher_versions(self):
        cases = {"1.2.3": False, "1.1.0": True, None: None}
        for version, outdated in cases.items():
            with self.subTest(version=version):
                client = make_client(self, launcher_version=version)
                info = client.get("/api/desktop/info").json()
                self.assertEqual({"version": version, "outdated": outdated}, info["launcher"])


if __name__ == "__main__":
    unittest.main()
