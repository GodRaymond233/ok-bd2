import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts" / "check_commit_attribution.py"


class CommitAttributionTest(unittest.TestCase):
    def run_checker(self, message: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temp_dir:
            message_path = Path(temp_dir) / "COMMIT_EDITMSG"
            message_path.write_text(message, encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(CHECKER), "--message-file", str(message_path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )

    def test_accepts_regular_commit_message(self):
        result = self.run_checker("fix(ui): keep attribution local\n")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("Commit attribution check passed.", result.stdout)

    def test_rejects_claude_coauthor_trailer(self):
        result = self.run_checker(
            "fix(ui): example\n\n"
            "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>\n"
        )

        self.assertEqual(1, result.returncode)
        self.assertIn("Prohibited commit attribution found", result.stderr)
        self.assertIn("Co-Authored-By: Claude Fable 5", result.stderr)


if __name__ == "__main__":
    unittest.main()
