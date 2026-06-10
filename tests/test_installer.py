import importlib.util
import pathlib
import tempfile
import unittest
from unittest import mock


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
INSTALLER_PATH = REPO_ROOT / "bin" / "install_server.py"
SPEC = importlib.util.spec_from_file_location("install_server", INSTALLER_PATH)
install_server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(install_server)


class InstallerCopyTests(unittest.TestCase):
    def test_preserves_committed_png_assets_and_ignores_generated_images(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = pathlib.Path(temporary_dir)
            html_dir = root / "server" / "views" / "html"
            icon_dir = html_dir / "icon"
            pwa_icon_dir = root / "server" / "views" / "pwa" / "icons"
            icon_dir.mkdir(parents=True)
            pwa_icon_dir.mkdir(parents=True)

            ignore = install_server.install_copy_ignore(root)

            self.assertNotIn(
                "cloudy.png",
                ignore(str(icon_dir), ["cloudy.png"]),
            )
            self.assertNotIn(
                "weathercal-icon-192.png",
                ignore(
                    str(pwa_icon_dir),
                    ["weathercal-icon-192.png"],
                ),
            )
            self.assertIn(
                "map.png",
                ignore(str(html_dir), ["map.png", "styles.css"]),
            )
            self.assertIn(
                "calendar.html",
                ignore(str(html_dir), ["calendar.html", "styles.css"]),
            )

    @mock.patch.object(install_server, "run")
    def test_removes_only_legacy_application_logs(self, run):
        install_server.remove_legacy_application_logs(dry_run=False)

        run.assert_called_once_with(
            [
                "find",
                "/srv/inkplate",
                "-maxdepth",
                "1",
                "-type",
                "f",
                "-name",
                "eink-cal-server.log*",
                "-delete",
            ],
            sudo=True,
            dry_run=False,
        )
