import re


REDACTED = "<redacted>"
SENSITIVE_NAMES = (
    "access_token",
    "api_key",
    "apikey",
    "appid",
    "client_secret",
    "key",
    "password",
    "refresh_token",
    "secret",
    "token",
)
SENSITIVE_NAME_PATTERN = "|".join(
    re.escape(name) for name in SENSITIVE_NAMES
)
ASSIGNMENT_PATTERN = re.compile(
    rf"(?i)(\b(?:{SENSITIVE_NAME_PATTERN})\b"
    rf"(?:%3[dD]|=|[\"']?\s*:\s*[\"']?))"
    rf"([^&,\s;\"']+)"
)
BEARER_PATTERN = re.compile(r"(?i)(\bBearer\s+)[^\s,;\"']+")


def redact_sensitive(value, secrets=()):
    text = str(value)
    for secret in sorted(
        {str(secret) for secret in secrets if secret},
        key=len,
        reverse=True,
    ):
        text = text.replace(secret, REDACTED)
    text = ASSIGNMENT_PATTERN.sub(rf"\1{REDACTED}", text)
    return BEARER_PATTERN.sub(rf"\1{REDACTED}", text)


def exception_text(exc, secrets=()):
    return f"{type(exc).__name__}: {redact_sensitive(exc, secrets)}"
