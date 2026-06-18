from dataclasses import dataclass, field
from datetime import datetime


CARDINAL_DIRECTIONS = (
    "N",
    "NNE",
    "NE",
    "ENE",
    "E",
    "ESE",
    "SE",
    "SSE",
    "S",
    "SSW",
    "SW",
    "WSW",
    "W",
    "WNW",
    "NW",
    "NNW",
)


def cardinal_direction(degrees):
    if degrees is None:
        return None
    normalized = float(degrees) % 360
    return CARDINAL_DIRECTIONS[int((normalized + 11.25) // 22.5) % 16]


@dataclass
class Temperature:
    unit: str
    value: float
    minimum: float | None = None
    maximum: float | None = None
    source: str | None = None
    live: bool = False

    def to_dict(self):
        value = {"unit": self.unit, "value": self.value}
        if self.minimum is not None:
            value["min"] = self.minimum
        if self.maximum is not None:
            value["max"] = self.maximum
        if self.source is not None:
            value["source"] = self.source
        if self.live:
            value["live"] = True
        return value

    @classmethod
    def from_dict(cls, value):
        return cls(
            unit=value["unit"],
            value=value["value"],
            minimum=value.get("min"),
            maximum=value.get("max"),
            source=value.get("source"),
            live=value.get("live", False),
        )

    def overlay(self, other):
        return Temperature(
            unit=other.unit,
            value=other.value,
            minimum=(
                other.minimum
                if other.minimum is not None
                else self.minimum
            ),
            maximum=(
                other.maximum
                if other.maximum is not None
                else self.maximum
            ),
            source=other.source,
            live=other.live,
        )


@dataclass
class Wind:
    unit: str
    value: float
    gust: float | None = None
    direction: float | None = None
    source: str | None = None
    live: bool = False

    def to_dict(self):
        value = {"unit": self.unit, "value": self.value}
        for key, item in (
            ("gust", self.gust),
            ("direction", self.direction),
            ("source", self.source),
        ):
            if item is not None:
                value[key] = item
        if self.live:
            value["live"] = True
        direction_cardinal = cardinal_direction(self.direction)
        if direction_cardinal is not None:
            value["direction_cardinal"] = direction_cardinal
        return value

    @classmethod
    def from_dict(cls, value):
        return cls(
            unit=value["unit"],
            value=value["value"],
            gust=value.get("gust"),
            direction=value.get("direction"),
            source=value.get("source"),
            live=value.get("live", False),
        )


@dataclass
class Rain:
    unit: str
    value: float
    last_hour: float | None = None
    last_24_hours: float | None = None
    source: str | None = None
    live: bool = False
    rate_unit: str | None = None
    rate_basis: str | None = None

    def to_dict(self):
        value = {"unit": self.unit, "value": self.value}
        for key, item in (
            ("last_hour", self.last_hour),
            ("last_24_hours", self.last_24_hours),
            ("source", self.source),
            ("rate_unit", self.rate_unit),
            ("rate_basis", self.rate_basis),
        ):
            if item is not None:
                value[key] = item
        if self.live:
            value["live"] = True
        return value

    @classmethod
    def from_dict(cls, value):
        return cls(
            unit=value["unit"],
            value=value["value"],
            last_hour=value.get("last_hour"),
            last_24_hours=value.get("last_24_hours"),
            source=value.get("source"),
            live=value.get("live", False),
            rate_unit=value.get("rate_unit"),
            rate_basis=value.get("rate_basis"),
        )


@dataclass
class CurrentConditions:
    icon: str | None = None
    temperature: Temperature | None = None
    wind: Wind | None = None
    rain: Rain | None = None
    humidity: float | None = None
    alerts: dict | None = None
    extra: dict = field(default_factory=dict)

    def to_dict(self):
        value = dict(self.extra)
        for key, item in (
            ("icon", self.icon),
            ("humidity", self.humidity),
            ("alerts", self.alerts),
        ):
            if item is not None:
                value[key] = item
        if self.temperature is not None:
            value["temperature"] = self.temperature.to_dict()
        if self.wind is not None:
            value["wind"] = self.wind.to_dict()
        if self.rain is not None:
            value["rain"] = self.rain.to_dict()
        return value

    @classmethod
    def from_dict(cls, value):
        known = {
            "icon",
            "temperature",
            "wind",
            "rain",
            "humidity",
            "alerts",
        }
        return cls(
            icon=value.get("icon"),
            temperature=(
                Temperature.from_dict(value["temperature"])
                if value.get("temperature") is not None
                else None
            ),
            wind=(
                Wind.from_dict(value["wind"])
                if value.get("wind") is not None
                else None
            ),
            rain=(
                Rain.from_dict(value["rain"])
                if value.get("rain") is not None
                else None
            ),
            humidity=value.get("humidity"),
            alerts=value.get("alerts"),
            extra={key: item for key, item in value.items() if key not in known},
        )

    def overlay(self, other):
        temperature = self.temperature
        if other.temperature is not None:
            temperature = (
                self.temperature.overlay(other.temperature)
                if self.temperature is not None
                else other.temperature
            )
        return CurrentConditions(
            icon=other.icon if other.icon is not None else self.icon,
            temperature=temperature,
            wind=other.wind if other.wind is not None else self.wind,
            rain=other.rain if other.rain is not None else self.rain,
            humidity=(
                other.humidity
                if other.humidity is not None
                else self.humidity
            ),
            alerts=other.alerts if other.alerts is not None else self.alerts,
            extra={**self.extra, **other.extra},
        )


@dataclass
class HourlyForecast:
    timestamp: datetime
    icon: str
    temperature: Temperature
    rain_probability: float
    wind: Wind | None = None
    humidity: float | None = None
    extra: dict = field(default_factory=dict)

    def to_dict(self):
        value = {
            **self.extra,
            "dt": self.timestamp,
            "icon": self.icon,
            "temperature": self.temperature.to_dict(),
            "rain_probability": self.rain_probability,
        }
        if self.wind is not None:
            value["wind"] = self.wind.to_dict()
        if self.humidity is not None:
            value["humidity"] = self.humidity
        return value

    @classmethod
    def from_dict(cls, value):
        known = {
            "dt",
            "icon",
            "temperature",
            "rain_probability",
            "wind",
            "humidity",
        }
        return cls(
            timestamp=value["dt"],
            icon=value["icon"],
            temperature=Temperature.from_dict(value["temperature"]),
            rain_probability=value["rain_probability"],
            wind=(
                Wind.from_dict(value["wind"])
                if value.get("wind") is not None
                else None
            ),
            humidity=value.get("humidity"),
            extra={key: item for key, item in value.items() if key not in known},
        )


@dataclass
class ForecastData:
    current: CurrentConditions
    hourly: list[HourlyForecast]

    def validate(self):
        if self.current.icon is None:
            raise ValueError("forecast current conditions require an icon")
        if self.current.temperature is None:
            raise ValueError("forecast current conditions require temperature")
        return self

    @classmethod
    def from_dicts(cls, current, hourly):
        return cls(
            current=CurrentConditions.from_dict(current),
            hourly=[HourlyForecast.from_dict(item) for item in hourly],
        )

    def current_dict(self):
        return self.current.to_dict()

    def hourly_dicts(self):
        return [forecast.to_dict() for forecast in self.hourly]
