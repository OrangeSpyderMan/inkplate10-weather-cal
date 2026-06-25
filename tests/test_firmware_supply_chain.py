import pathlib
import re
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


class FirmwareSupplyChainTests(unittest.TestCase):
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
