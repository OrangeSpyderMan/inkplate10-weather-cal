import copy
import datetime as dt


class WeatherSnapshot:
    def __init__(
        self,
        daily_summary,
        hourly_forecasts,
        weather_source,
        metric=True,
        generated_at=None,
    ):
        self.daily_summary = daily_summary
        self.hourly_forecasts = hourly_forecasts
        self.weather_source = weather_source
        self.metric = metric
        self.generated_at = generated_at or dt.datetime.now(dt.timezone.utc)

    def to_payload(self):
        return {
            "generated_at": self.generated_at.isoformat(),
            "source": self.weather_source,
            "units": "metric" if self.metric else "imperial",
            "current": _serializable(self.daily_summary),
            "hourly": _serializable(self.hourly_forecasts),
        }


def _serializable(value):
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            return value.isoformat()
        return value.astimezone(dt.timezone.utc).isoformat()

    if isinstance(value, dict):
        return {key: _serializable(item) for key, item in value.items()}

    if isinstance(value, list):
        return [_serializable(item) for item in value]

    return copy.deepcopy(value)
