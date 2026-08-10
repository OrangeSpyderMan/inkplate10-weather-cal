import os
import pathlib
import re
import stat
import subprocess
import tempfile
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


class FirmwareSupplyChainTests(unittest.TestCase):
    def run_host_dependency_check(self, python_script):
        with tempfile.TemporaryDirectory() as temp_dir:
            bin_dir = pathlib.Path(temp_dir)
            python = bin_dir / "python3"
            python.write_text(python_script, encoding="utf-8")
            python.chmod(python.stat().st_mode | stat.S_IXUSR)
            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
            return subprocess.run(
                [
                    "make",
                    "--no-print-directory",
                    "firmware-ensure-host-deps",
                ],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

    def test_world_checks_host_dependencies_before_toolchain_setup(self):
        makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        world_recipe = re.search(
            r"^firmware-world:\n((?:\t.*\n)+)",
            makefile,
            re.MULTILINE,
        ).group(1)

        self.assertLess(
            world_recipe.index("firmware-ensure-host-deps"),
            world_recipe.index("firmware-ensure-cli"),
        )
        self.assertLess(
            world_recipe.index("firmware-ensure-host-deps"),
            world_recipe.index("firmware-ensure-setup"),
        )

    def test_host_dependency_check_fails_with_actionable_pyserial_error(self):
        result = self.run_host_dependency_check(
            "#!/bin/sh\n"
            "echo \"No module named 'serial'\" >&2\n"
            "exit 1\n"
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "Missing or incompatible Python module: pyserial",
            result.stderr,
        )
        self.assertIn(
            "python3 -m pip install pyserial",
            result.stderr,
        )

    def test_host_dependency_check_accepts_python_with_pyserial(self):
        result = self.run_host_dependency_check("#!/bin/sh\nexit 0\n")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "Firmware host dependencies are available",
            result.stdout,
        )

    def test_board_index_and_libraries_are_version_pinned(self):
        makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

        self.assertRegex(
            makefile,
            r"FIRMWARE_BOARD_INDEX_COMMIT \?= [0-9a-f]{40}",
        )
        self.assertNotIn(
            "raw/master/package_Dasduino_Boards_index.json",
            makefile,
        )
        libraries = re.search(
            r"^FIRMWARE_LIBRARIES \?= (.+)$",
            makefile,
            re.MULTILINE,
        ).group(1).split()
        self.assertTrue(libraries)
        self.assertTrue(all(re.fullmatch(r"[^@\s]+@[^@\s]+", item) for item in libraries))
        self.assertIn('$$2 == version', makefile)

    def test_arduino_cli_archive_is_checksum_verified(self):
        installer = (
            REPO_ROOT / "bin/install_arduino_cli.sh"
        ).read_text(encoding="utf-8")

        self.assertIn('VERSION="${ARDUINO_CLI_VERSION:-1.3.1}"', installer)
        self.assertIn(
            "ARDUINO_CLI_SHA256 (required when overriding VERSION)",
            installer,
        )
        self.assertIn("actual_sha256=$(sha256sum", installer)
        self.assertIn("actual_sha256=$(shasum -a 256", installer)
        self.assertIn(
            'if [ "$actual_sha256" != "$archive_sha256" ]',
            installer,
        )
        self.assertGreaterEqual(
            len(re.findall(r'archive_sha256="[0-9a-f]{64}"', installer)),
            6,
        )

    def test_firmware_workflow_actions_are_sha_pinned(self):
        workflow = (
            REPO_ROOT / ".github/workflows/firmware.yml"
        ).read_text(encoding="utf-8")

        action_lines = [
            line.strip()
            for line in workflow.splitlines()
            if "uses:" in line
        ]
        self.assertTrue(action_lines)
        for line in action_lines:
            self.assertRegex(line, r"uses: [^@\s]+@[0-9a-f]{40} # v\d+")


if __name__ == "__main__":
    unittest.main()
