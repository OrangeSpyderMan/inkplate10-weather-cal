import importlib.util
import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "bin" / "generate_firmware_version.py"
SPEC = importlib.util.spec_from_file_location("generate_firmware_version", SCRIPT_PATH)
generate_firmware_version = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(generate_firmware_version)
BUILD_VERSION_PATH = REPO_ROOT / "server" / "build_version.py"
BUILD_VERSION_SPEC = importlib.util.spec_from_file_location(
    "test_build_version",
    BUILD_VERSION_PATH,
)
build_version = importlib.util.module_from_spec(BUILD_VERSION_SPEC)
BUILD_VERSION_SPEC.loader.exec_module(build_version)


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
                build_version.detected_version(cwd=repo),
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
                build_version.detected_version(cwd=repo),
                f"v1.2.3+g{short_commit}",
            )

    def test_manifest_is_shared_with_firmware_header(self):
        temporary_dir, repo = self.create_repo()
        with temporary_dir:
            self.commit(repo, "released")
            git(repo, "tag", "-a", "v1.2.3", "-m", "v1.2.3")
            self.commit(repo, "next")
            output = repo / "firmware_version.h"

            manifest = build_version.generate_version_manifest(
                repo,
                build_date="2026-06-19T12:00:00Z",
            )
            content = (
                "#ifndef FIRMWARE_VERSION_H\n"
                "#define FIRMWARE_VERSION_H\n\n"
                f'#define FIRMWARE_VERSION "{manifest["version"]}"\n\n'
                "#endif\n"
            )
            output.write_text(content, encoding="utf-8")

            persisted = json.loads(
                (repo / ".version.json").read_text(encoding="utf-8")
            )

            self.assertEqual(persisted, manifest)
            self.assertIn(manifest["version"], output.read_text())

    @mock.patch.object(build_version.subprocess, "run")
    def test_git_explicitly_trusts_only_the_requested_checkout(self, run):
        run.return_value = mock.Mock(returncode=0, stdout="abc1234\n")
        with tempfile.TemporaryDirectory() as temporary_dir:
            checkout = pathlib.Path(temporary_dir).resolve()

            result = build_version.git(
                "rev-parse",
                "--short",
                "HEAD",
                cwd=checkout,
            )

        self.assertEqual(result, "abc1234")
        self.assertEqual(
            run.call_args.args[0][:3],
            ["git", "-c", f"safe.directory={checkout}"],
        )
        self.assertEqual(run.call_args.kwargs["cwd"], checkout)

    @mock.patch.object(build_version, "git", return_value="")
    def test_failed_git_lookup_does_not_overwrite_manifest(self, git):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = pathlib.Path(temporary_dir)
            manifest_path = root / ".version.json"
            original = '{"version": "v3.2.0+gabc1234"}\n'
            manifest_path.write_text(original, encoding="utf-8")

            with self.assertRaisesRegex(
                ValueError,
                "cannot resolve the Git revision",
            ):
                build_version.generate_version_manifest(root)

            self.assertEqual(
                manifest_path.read_text(encoding="utf-8"),
                original,
            )
