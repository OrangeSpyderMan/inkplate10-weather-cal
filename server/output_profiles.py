from dataclasses import dataclass, field
import json
import os


DEFAULT_OUTPUT_PROFILE = "inkplate10-portrait"
DEFAULT_OUTPUT_FILENAME = "calendar.png"
DEFAULT_RENDERER = "firefox"
DEFAULT_WIDTH = 825
DEFAULT_HEIGHT = 1200
OUTPUT_PROFILES_ENV = "INKPLATE_OUTPUT_PROFILES"
DEFAULT_OUTPUT_PROFILE_ENV = "INKPLATE_DEFAULT_OUTPUT_PROFILE"


@dataclass(frozen=True)
class OutputProfile:
    name: str
    renderer: str
    width: int
    height: int
    filename: str = DEFAULT_OUTPUT_FILENAME
    options: dict = field(default_factory=dict)


def load_output_profiles(config):
    outputs_config = config.get("outputs") or {}
    configured_profiles = outputs_config.get("profiles")

    if configured_profiles is None:
        image_config = config.get("image") or {}
        profiles = {
            DEFAULT_OUTPUT_PROFILE: OutputProfile(
                name=DEFAULT_OUTPUT_PROFILE,
                renderer=DEFAULT_RENDERER,
                width=int(image_config.get("width", DEFAULT_WIDTH)),
                height=int(image_config.get("height", DEFAULT_HEIGHT)),
            )
        }
    else:
        profiles = {}
        for name, profile_config in configured_profiles.items():
            profile_config = profile_config or {}
            if not profile_config.get("enabled", True):
                continue
            profiles[name] = OutputProfile(
                name=name,
                renderer=profile_config.get("renderer", DEFAULT_RENDERER),
                width=int(profile_config.get("width", DEFAULT_WIDTH)),
                height=int(profile_config.get("height", DEFAULT_HEIGHT)),
                filename=profile_config.get("filename", DEFAULT_OUTPUT_FILENAME),
                options=profile_config.get("options") or {},
            )

    default_profile = outputs_config.get("default", DEFAULT_OUTPUT_PROFILE)
    validate_output_profiles(profiles, default_profile)
    return profiles, default_profile


def validate_output_profiles(profiles, default_profile):
    if not profiles:
        raise ValueError("at least one output profile must be enabled")
    if default_profile not in profiles:
        raise ValueError(
            f"default output profile {default_profile!r} is not enabled"
        )

    for profile in profiles.values():
        if (
            not profile.name
            or "/" in profile.name
            or "\\" in profile.name
            or profile.name in {".", ".."}
        ):
            raise ValueError(f"invalid output profile name {profile.name!r}")
        if profile.width <= 0 or profile.height <= 0:
            raise ValueError(
                f"output profile {profile.name!r} dimensions must be positive"
            )
        if not profile.renderer:
            raise ValueError(
                f"output profile {profile.name!r} renderer is required"
            )
        if not isinstance(profile.options, dict):
            raise ValueError(
                f"output profile {profile.name!r} options must be a mapping"
            )
        if (
            not profile.filename
            or "/" in profile.filename
            or "\\" in profile.filename
            or profile.filename in {".", ".."}
        ):
            raise ValueError(
                f"invalid output filename {profile.filename!r}"
            )
        if not profile.filename.lower().endswith(".png"):
            raise ValueError(
                f"output profile {profile.name!r} filename must end in .png"
            )


def export_output_profiles(profiles, default_profile):
    os.environ[OUTPUT_PROFILES_ENV] = json.dumps(
        {
            name: {
                "renderer": profile.renderer,
                "width": profile.width,
                "height": profile.height,
                "filename": profile.filename,
                "options": profile.options,
            }
            for name, profile in profiles.items()
        }
    )
    os.environ[DEFAULT_OUTPUT_PROFILE_ENV] = default_profile


def load_exported_output_profiles():
    serialized = os.environ.get(OUTPUT_PROFILES_ENV)
    if not serialized:
        return load_output_profiles({})

    values = json.loads(serialized)
    profiles = {
        name: OutputProfile(name=name, **profile)
        for name, profile in values.items()
    }
    default_profile = os.environ.get(
        DEFAULT_OUTPUT_PROFILE_ENV,
        DEFAULT_OUTPUT_PROFILE,
    )
    validate_output_profiles(profiles, default_profile)
    return profiles, default_profile
