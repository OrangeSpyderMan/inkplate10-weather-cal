import json
import os
import time

import requests


class NetatmoCurrentTemperatureService:
    def __init__(
        self,
        client_id,
        client_secret,
        refresh_token,
        token_file,
        device_id=None,
        module_id=None,
        metric=True,
        baseurl="https://api.netatmo.com",
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.token_file = token_file
        self.device_id = device_id
        self.module_id = module_id
        self.metric = metric
        self.baseurl = baseurl

    def get_current_temperature(self):
        data = self._get_stations_data()
        temperature_c = self._extract_temperature(data)
        value = temperature_c if self.metric else (temperature_c * 9 / 5) + 32

        return {
            "source": "netatmo",
            "live": True,
            "unit": "\N{DEGREE SIGN}C" if self.metric else "\N{DEGREE SIGN}F",
            "value": round(value),
        }

    def _get_stations_data(self):
        token = self._get_access_token()
        response = requests.get(
            f"{self.baseurl}/api/getstationsdata",
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
        )

        if response.status_code in [401, 403]:
            token = self._refresh_access_token()
            response = requests.get(
                f"{self.baseurl}/api/getstationsdata",
                headers={"Authorization": f"Bearer {token}"},
                timeout=20,
            )

        if response.status_code != 200:
            raise ValueError(
                "Non-200 response from Netatmo api: {}".format(response.status_code)
            )

        data = response.json()
        if data.get("status") and data["status"] != "ok":
            raise ValueError(
                "Unexpected response status from Netatmo api: {}".format(
                    data["status"]
                )
            )

        return data

    def _extract_temperature(self, data):
        body = data.get("body", {})
        devices = body.get("devices", [])
        if len(devices) == 0:
            raise ValueError("No Netatmo weather stations found in response")

        device = self._find_by_id(devices, self.device_id, "station")
        measurement_source = device

        if self.module_id:
            modules = device.get("modules", [])
            measurement_source = self._find_by_id(modules, self.module_id, "module")

        dashboard_data = measurement_source.get("dashboard_data", {})
        if "Temperature" not in dashboard_data:
            source_id = measurement_source.get("_id", "unknown")
            raise ValueError(
                "No Temperature measurement found for Netatmo source {}".format(
                    source_id
                )
            )

        return dashboard_data["Temperature"]

    def _find_by_id(self, items, item_id, item_name):
        if item_id is None:
            return items[0]

        for item in items:
            if item.get("_id") == item_id:
                return item

        raise ValueError("No Netatmo {} found with id {}".format(item_name, item_id))

    def _get_access_token(self):
        token_data = self._load_token_data()
        if (
            token_data.get("access_token")
            and token_data.get("expires_at", 0) > time.time() + 60
        ):
            return token_data["access_token"]

        return self._refresh_access_token(token_data.get("refresh_token"))

    def _refresh_access_token(self, refresh_token=None):
        token = refresh_token or self.refresh_token
        response = requests.post(
            f"{self.baseurl}/oauth2/token",
            data={
                "grant_type": "refresh_token",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": token,
            },
            timeout=20,
        )

        if response.status_code != 200:
            raise ValueError(
                "Non-200 response from Netatmo token api: {}".format(
                    response.status_code
                )
            )

        token_data = response.json()
        token_data["expires_at"] = time.time() + token_data.get("expires_in", 0)
        self._save_token_data(token_data)
        return token_data["access_token"]

    def _load_token_data(self):
        if not self.token_file or not os.path.exists(self.token_file):
            return {"refresh_token": self.refresh_token}

        with open(self.token_file) as f:
            return json.load(f)

    def _save_token_data(self, token_data):
        if not self.token_file:
            return

        token_dir = os.path.dirname(self.token_file)
        if token_dir:
            os.makedirs(token_dir, exist_ok=True)

        with open(self.token_file, "w") as f:
            json.dump(token_data, f)
