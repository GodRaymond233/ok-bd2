import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.desktop.tools import DesktopTools


class FakeRunner:
    def __init__(self):
        self.calls = []

    def run_task(self, file_path):
        self.calls.append(("run_task", file_path))

    def run_code(self, source):
        self.calls.append(("run_code", source))

    def reload_tasks(self, task_ids=None):
        self.calls.append(("reload_tasks", task_ids))


class FakeMcpServer:
    def __init__(self, ok, config):
        self.runner = FakeRunner()

    def start(self):
        pass


class McpRunGuardTest(unittest.TestCase):
    def test_every_runner_entry_checks_the_client_gates(self):
        runtime = SimpleNamespace(
            ok=SimpleNamespace(config={"mcp": {"enabled": True}}, _mcp_server=None),
            executor=SimpleNamespace(basic_options={}),
        )
        tools = DesktopTools(runtime)
        tools.guard = Mock(side_effect=ValueError("任务运行中"))
        with patch("ok.core.mcp_server.LocalMcpServer", FakeMcpServer):
            tools.start_mcp()
        runner = runtime.ok._mcp_server.runner
        # run_code arrived with ok-script 3.0.10 and runs a snippet as a task.
        for name, argument in (("run_task", "task.py"), ("run_code", "self.sleep(1)"),
                               ("reload_tasks", None)):
            with self.subTest(name=name), self.assertRaises(ValueError):
                getattr(runner, name)(argument)
        self.assertEqual([], runner.calls)
        tools.guard.side_effect = None
        runner.run_code("1 + 1")
        self.assertEqual([("run_code", "1 + 1")], runner.calls)


if __name__ == "__main__":
    unittest.main()
