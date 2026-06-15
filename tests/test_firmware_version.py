import importlib.util
import pathlib
import subprocess
import tempfile
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "bin" / "generate_firmware_version.py"
SPEC = importlib.util.spec_from_file_location("generate_firmware_version", SCRIPT_PATH)
generate_firmware_version = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(generate_firmware_version)


def git(repo, *args):
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class FirmwareVersionTests(unittest.TestCase):
    def create_repo(self):
        temporary_dir = tempfile.TemporaryDirectory()
        repo = pathlib.Path(temporary_dir.name)
        git(repo, "init", "-q")
        git(repo, "config", "user.name", "Test User")
        git(repo, "config", "user.email", "test@example.com")
        return temporary_dir, repo

    def commit(self, repo, message):
        path = repo / "content.txt"
        existing = path.read_text() if path.exists() else ""
        path.write_text(existing + message + "\n")
        git(repo, "add", "content.txt")
        git(repo, "commit", "-q", "-m", message)

    def test_exact_release_tag_is_used(self):
        temporary_dir, repo = self.create_repo()
        with temporary_dir:
            self.commit(repo, "initial")
            git(repo, "tag", "-a", "v1.2.3", "-m", "v1.2.3")

            self.assertEqual(
                generate_firmware_version.detected_version(cwd=repo),
                "v1.2.3",
            )

    def test_ignores_newer_tag_that_is_not_reachable_from_head(self):
        temporary_dir, repo = self.create_repo()
        with temporary_dir:
            self.commit(repo, "released")
            git(repo, "tag", "-a", "v1.2.3", "-m", "v1.2.3")
            release_commit = git(repo, "rev-parse", "HEAD")

            git(repo, "checkout", "-q", "-b", "future")
            self.commit(repo, "future")
            git(repo, "tag", "-a", "v9.0.0", "-m", "v9.0.0")

            git(repo, "checkout", "-q", "--detach", release_commit)
            self.commit(repo, "maintenance")
            short_commit = git(repo, "rev-parse", "--short", "HEAD")

            self.assertEqual(
                generate_firmware_version.detected_version(cwd=repo),
                f"v1.2.3+g{short_commit}",
            )
