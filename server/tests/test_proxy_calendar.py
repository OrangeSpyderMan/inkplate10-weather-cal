import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from producer_config import ProducerConfig
from weather.providers import build_forecast_provider
from web import create_app


class CalendarProxyTests(unittest.TestCase):
    def config(self):
        return {"weather": {"service": "weatherproxy", "url": "http://proxy:8090", "location": "landry"},
                "location": "Landry, FR", "google": {"apikey": "map", "staticmaps_mapid": "map-id"}}

    def test_proxy_config_needs_no_upstream_weather_key(self):
        settings = ProducerConfig.from_config(self.config())
        self.assertEqual(settings.weather_api_key, "")
        adapter = build_forecast_provider(settings.weather_service, apikey=settings.weather_api_key,
                                         location=settings.location, proxy_config=settings.weather_proxy_config)
        self.assertEqual(adapter.location, "landry")
        self.assertEqual(adapter.url, "http://proxy:8090")

    def test_proxy_mode_rejects_double_overlay(self):
        config = self.config()
        config["current_conditions"] = {"source": "netatmo"}
        with self.assertRaisesRegex(ValueError, "second current-conditions overlay"):
            ProducerConfig.from_config(config)

    def test_browser_forwarding_uses_private_token_and_conditional_headers(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(os.environ, {
                "WEATHER_PROXY_URL": "http://proxy:8090", "WEATHER_PROXY_TOKEN": "private-token"}):
            app = create_app(data_dir=temporary)
            response = mock.Mock(status_code=200, headers={"ETag": '"revision"'})
            response.content = json.dumps({"locations": [{"id": "landry"}]}).encode()
            with mock.patch("web.requests.get", return_value=response) as get:
                result = app.test_client().get("/api/v1/locations", headers={"If-None-Match": '"previous"'})
            self.assertEqual(result.json, {"locations": [{"id": "landry"}]})
            self.assertEqual(result.headers["ETag"], '"revision"')
            self.assertEqual(get.call_args.kwargs["headers"], {
                "Authorization": "Bearer private-token", "If-None-Match": '"previous"'})
            self.assertNotIn("private-token", result.get_data(as_text=True))
            response.close.assert_called_once()



if __name__ == "__main__":
    unittest.main()
