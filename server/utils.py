import os
import operator
import re
import numpy as np
from functools import reduce

ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}")


def get_by_path(root, items):
    """Access a nested object in root by item sequence."""
    return reduce(operator.getitem, items, root)

def get_prop_by_keys(
    config, *keys, default=None, required=True, dehumanized=False
):
    val = default
    found_vals = [get_by_path(config, keys)]

    if len(found_vals) == 0:
        if default is None and required is True:
            raise KeyError("{} not in config but is required".format(".".join(keys)))
    else:
        val = found_vals[0]

    return val


def get_prop(config, prop, default=None, required=True, dehumanized=False):
    val = default

    if prop not in config:
        if default is None and required is True:
            raise KeyError("{} not in config but is required".format(prop))
    else:
        val = config[prop]

    return val


def expand_env_vars(value):
    if isinstance(value, dict):
        return {k: expand_env_vars(v) for k, v in value.items()}

    if isinstance(value, list):
        return [expand_env_vars(v) for v in value]

    if not isinstance(value, str):
        return value

    def replace_match(match):
        var_name = match.group(1)
        default = match.group(2)
        env_value = os.environ.get(var_name)
        if env_value:
            return env_value
        if default is not None:
            return default

        raise KeyError(
            "Environment variable {} is required by config".format(var_name)
        )

    return ENV_VAR_PATTERN.sub(replace_match, value)


def even_select(n, l):
    indices = np.round(np.linspace(0, len(l) - 1, n)).astype(int)

    selection = []
    for idx in indices:
        selection.append(l[idx])
    return selection
