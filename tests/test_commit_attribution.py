import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
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
        subprocess.run(["git", *args], cwd=self.repo, env=self.git_env,
                       capture_output=True, check=True)

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

    def test_accepts_regular_commit_message(self):
        result = self.run_checker("fix(ui): keep attribution local\n")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("Commit attribution check passed.", result.stdout)

    def test_rejects_claude_coauthor_trailer(self):
        message = (
            "fix(ui): example\n\n"
            "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>\n"
        )

        pending = self.run_checker(message)
        self.git("commit", "--quiet", "--allow-empty", "-m", message)
        history = self.run_checker("Regular follow-up")
        for result in (pending, history):
            self.assertEqual(1, result.returncode)
            self.assertIn("Prohibited commit attribution found", result.stderr)
            self.assertIn("Co-Authored-By: Claude Fable 5", result.stderr)


if __name__ == "__main__":
    unittest.main()
