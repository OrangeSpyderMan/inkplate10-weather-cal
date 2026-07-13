import re


INSTANCE_ID_PATTERN = re.compile(r"^[a-z0-9]{6}$")
LEGACY_CLIENT_IDS = {
    "diagnostics": "inkplate-diagnostics-server",
    "weather": "inkplate-weather-server",
}


def mqtt_client_id(role, instance_id=None):
    if role not in LEGACY_CLIENT_IDS:
        raise ValueError(f"unknown MQTT client role: {role}")
    if instance_id is None or str(instance_id).strip() == "":
        return LEGACY_CLIENT_IDS[role]

    value = str(instance_id).strip()
    if not INSTANCE_ID_PATTERN.fullmatch(value):
        raise ValueError(
            "MQTT instance ID must contain six lowercase base-36 characters"
        )
    prefix = "inkplate-diag" if role == "diagnostics" else "inkplate-weather"
    return f"{prefix}.{value}"
