import pathlib
import sys
import unittest


SERVER_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

from output_profiles import DEFAULT_OUTPUT_PROFILE, load_output_profiles


class OutputProfileTests(unittest.TestCase):
    def test_uses_legacy_image_dimensions_when_outputs_are_not_configured(self):
        profiles, default_profile = load_output_profiles(
            {"image": {"width": 600, "height": 448}}
        )

        self.assertEqual(default_profile, DEFAULT_OUTPUT_PROFILE)
        self.assertEqual(profiles[default_profile].width, 600)
        self.assertEqual(profiles[default_profile].height, 448)
        self.assertEqual(profiles[default_profile].renderer, "firefox")

    def test_loads_multiple_enabled_renderer_profiles(self):
        profiles, default_profile = load_output_profiles(
            {
                "outputs": {
                    "default": "inkplate10-portrait",
                    "profiles": {
                        "inkplate10-portrait": {
                            "renderer": "firefox",
                            "width": 825,
                            "height": 1200,
                        },
                        "inkplate6-landscape": {
                            "renderer": "pillow",
                            "width": 800,
                            "height": 600,
                            "filename": "weather.png",
                            "options": {"layout": "compact"},
                        },
                        "disabled": {"enabled": False},
                    },
                }
            }
        )

        self.assertEqual(default_profile, "inkplate10-portrait")
        self.assertEqual(
            list(profiles),
            ["inkplate10-portrait", "inkplate6-landscape"],
        )
        self.assertEqual(profiles["inkplate6-landscape"].renderer, "pillow")
        self.assertEqual(profiles["inkplate6-landscape"].filename, "weather.png")
        self.assertEqual(
            profiles["inkplate6-landscape"].options,
            {"layout": "compact"},
        )

    def test_rejects_disabled_default_profile(self):
        with self.assertRaisesRegex(ValueError, "is not enabled"):
            load_output_profiles(
                {
                    "outputs": {
                        "default": "disabled",
                        "profiles": {
                            "disabled": {"enabled": False},
                            "enabled": {},
                        },
                    }
                }
            )
