import pathlib
import sys
import unittest


SERVER_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

from redaction import exception_text, redact_sensitive


class RedactionTests(unittest.TestCase):
    def test_redacts_query_json_and_bearer_credentials(self):
        value = (
            "GET https://example.test/?appid=one&key=two "
            '{"client_secret":"three"} Authorization: Bearer four'
        )

        redacted = redact_sensitive(value)

        for secret in ("one", "two", "three", "four"):
            self.assertNotIn(secret, redacted)
        self.assertEqual(redacted.count("<redacted>"), 4)

    def test_redacts_explicit_secret_without_a_field_name(self):
        redacted = exception_text(
            RuntimeError("provider rejected opaque-secret"),
            secrets=("opaque-secret",),
        )

        self.assertEqual(
            redacted,
            "RuntimeError: provider rejected <redacted>",
        )


if __name__ == "__main__":
    unittest.main()
