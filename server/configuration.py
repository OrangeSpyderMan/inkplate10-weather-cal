import os
import sys
from pathlib import Path

import yaml

from utils import expand_env_vars


SERVER_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_DIR_PATH = SERVER_DIR / "config" / "config.yaml"


def resolve_config_path():
    env_config_path = os.environ.get("INKPLATE_CONFIG_FILE")
    if env_config_path:
        path = Path(env_config_path)
        if path.is_file():
            return path

        print(
            f"INKPLATE_CONFIG_FILE points to a missing config file: {path}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    if DEFAULT_CONFIG_DIR_PATH.is_file():
        return DEFAULT_CONFIG_DIR_PATH

    print(
        f"No config file found. Checked {DEFAULT_CONFIG_DIR_PATH}.",
        file=sys.stderr,
    )
    raise SystemExit(1)


def load_config():
    config_path = resolve_config_path()
    with config_path.open(encoding="utf-8") as config_file:
        return config_path, expand_env_vars(yaml.safe_load(config_file))
