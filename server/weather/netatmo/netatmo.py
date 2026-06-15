import json
import os
import tempfile
import time

import requests

from ..service import RealtimeProvider
from ..models import CurrentConditions, Rain, Temperature, Wind


class NetatmoRealtimeService(RealtimeProvider):
    def __init__(
        self,
        client_id,
        client_secret,
        refresh_token,
        token_file,
        device_id=None,
        module_id=None,
        wind_module_id=None,
        rain_module_id=None,
        metric=True,
        baseurl="https://api.netatmo.com",
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.token_file = token_file
        self.device_id = device_id
        self.module_id = module_id
        self.wind_module_id = wind_module_id
        self.rain_module_id = rain_module_id
        self.metric = metric
        self.baseurl = baseurl

    def get_current_conditions(self):
        data = self._get_stations_data()
        device = self._get_device(data)
        primary_source = self._measurement_source(device, self.module_id)
        dashboard_data = primary_source.get("dashboard_data", {})
        conditions = CurrentConditions()

        if "Temperature" in dashboard_data:
            conditions.temperature = self._temperature(
                dashboard_data["Temperature"]
            )
        if "Humidity" in dashboard_data:
            conditions.humidity = dashboard_data["Humidity"]

        wind_data = self._module_dashboard(
            device,
            self.wind_module_id,
            primary_source,
            "WindStrength",
        )
        if wind_data:
            conditions.wind = self._wind(wind_data)

        rain_data = self._module_dashboard(
            device,
            self.rain_module_id,
            primary_source,
            "Rain",
        )
        if rain_data:
            conditions.rain = self._rain(rain_data)

        if not conditions.to_dict():
            source_id = primary_source.get("_id", "unknown")
            raise ValueError(
                f"No supported measurements found for Netatmo source {source_id}"
            )

        return conditions

    def get_current_temperature(self):
        conditions = self.get_current_conditions()
        if conditions.temperature is None:
            raise ValueError("No Temperature measurement found for Netatmo source")
        return conditions.temperature.to_dict()

    def _temperature(self, temperature_c):
        value = temperature_c if self.metric else (temperature_c * 9 / 5) + 32
        return Temperature(
            source="netatmo",
            live=True,
            unit="\N{DEGREE SIGN}C" if self.metric else "\N{DEGREE SIGN}F",
            value=round(value),
        )

    def _wind(self, data):
        factor = 1 if self.metric else 0.621371
        return Wind(
            source="netatmo",
            live=True,
            unit="kmh" if self.metric else "mph",
            value=round(data["WindStrength"] * factor, 1),
            gust=(
                round(data["GustStrength"] * factor, 1)
                if "GustStrength" in data
                else None
            ),
            direction=data.get("WindAngle"),
        )

    def _rain(self, data):
        factor = 1 if self.metric else 0.0393701
        return Rain(
            source="netatmo",
            live=True,
            unit="mm" if self.metric else "in",
            value=round(data["Rain"] * factor, 2),
            last_hour=(
                round(data["sum_rain_1"] * factor, 2)
                if "sum_rain_1" in data
                else None
            ),
            last_24_hours=(
                round(data["sum_rain_24"] * factor, 2)
                if "sum_rain_24" in data
                else None
            ),
        )

    def _get_stations_data(self):
        token = self._get_access_token()
        response = requests.get(
            f"{self.baseurl}/api/getstationsdata",
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
        )

        if response.status_code in [401, 403]:
            response.close()
            token_data = self._load_token_data()
            token = self._refresh_access_token(token_data.get("refresh_token"))
            response = requests.get(
                f"{self.baseurl}/api/getstationsdata",
                headers={"Authorization": f"Bearer {token}"},
                timeout=20,
            )

        try:
            if response.status_code != 200:
                raise ValueError(
                    "Non-200 response from Netatmo api: {}".format(
                        response.status_code
                    )
                )

            data = response.json()
            if data.get("status") and data["status"] != "ok":
                raise ValueError(
                    "Unexpected response status from Netatmo api: {}".format(
                        data["status"]
                    )
                )

            return data
        finally:
            response.close()

    def _get_device(self, data):
        body = data.get("body", {})
        devices = body.get("devices", [])
        if len(devices) == 0:
            raise ValueError("No Netatmo weather stations found in response")
        return self._find_by_id(devices, self.device_id, "station")

    def _measurement_source(self, device, module_id):
        if not module_id:
            return device
        return self._find_by_id(device.get("modules", []), module_id, "module")

    def _module_dashboard(
        self,
        device,
        module_id,
        fallback_source,
        required_key,
    ):
        source = (
            self._measurement_source(device, module_id)
            if module_id
            else fallback_source
        )
        dashboard = source.get("dashboard_data", {})
        return dashboard if required_key in dashboard else None

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

        try:
            if response.status_code != 200:
                raise ValueError(
                    "Non-200 response from Netatmo token api: {}".format(
                        response.status_code
                    )
                )

            token_data = response.json()
        finally:
            response.close()

        token_data.setdefault("refresh_token", token)
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

        directory = token_dir or "."
        fd, temporary_path = tempfile.mkstemp(
            dir=directory,
            prefix=f".{os.path.basename(self.token_file)}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(token_data, f)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary_path, self.token_file)
        except Exception:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
            raise


# Preserve imports used by existing installations and third-party code.
NetatmoCurrentTemperatureService = NetatmoRealtimeService
