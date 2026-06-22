import datetime as dt
import math
import os
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


ASSET_ROOT = Path(__file__).resolve().parent / "views" / "html"
BASE_WIDTH = 825
BASE_HEIGHT = 1200


class PillowCalendarRenderer:
    name = "pillow"

    def render(
        self,
        snapshot,
        map_url,
        output_path,
        width,
        height,
        options=None,
    ):
        options = options or {}
        supersample = max(1, int(options.get("supersample", 2)))
        canvas = CalendarCanvas(
            width,
            height,
            supersample=supersample,
            background=options.get("background", "white"),
        )
        canvas.render(
            snapshot.daily_summary,
            snapshot.hourly_forecasts,
            snapshot.generated_at,
            _asset_path(map_url),
        )

        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f".{output.stem}.tmp{output.suffix}")
        try:
            canvas.image.save(temporary, format="PNG", optimize=True)
            os.replace(temporary, output)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


class CalendarCanvas:
    def __init__(self, width, height, supersample=2, background="white"):
        self.width = width
        self.height = height
        self.supersample = supersample
        self.sx = width * supersample / BASE_WIDTH
        self.sy = height * supersample / BASE_HEIGHT
        self.image = Image.new(
            "L",
            (width * supersample, height * supersample),
            color=background,
        )
        self.draw = ImageDraw.Draw(self.image)
        self.rough = RoughDraw(self.draw, seed=1947)
        self.regular_path = ASSET_ROOT / "Merienda-Regular.ttf"
        self.bold_path = ASSET_ROOT / "Merienda-Bold.ttf"

    def render(self, current, hourly, generated_at, map_path):
        self._map(map_path)
        self._top(current, generated_at)
        self._forecast(hourly)
        self._footer(generated_at)
        if self.supersample > 1:
            self.image = self.image.resize(
                (self.width, self.height),
                Image.Resampling.LANCZOS,
            )

    def _top(self, current, generated_at):
        display_date = (
            generated_at.astimezone().date()
            if generated_at is not None
            else dt.datetime.now().date()
        )
        self._circle((136, 115), 99, fill=0)
        self._text(
            str(display_date.day),
            (136, 115),
            142,
            bold=True,
            fill=255,
            anchor="mm",
        )
        self._text(
            display_date.strftime("%B").upper(),
            (250, 115),
            82,
            bold=True,
            anchor="lm",
        )

        temperature = current.get("temperature") or {}
        self._circle((731, 318), 62, fill=0)
        self._text(
            "{}°".format(_measurement(temperature.get("value"))),
            (731, 318),
            58,
            bold=True,
            fill=255,
            anchor="mm",
        )
        if temperature.get("live"):
            self._live_indicator((731, 236))

        self._circle((731, 499), 62, fill=0)
        self._icon(current.get("icon"), (731, 499), 103, invert=True)
        if (current.get("alerts") or {}).get("active"):
            self._badge_icon("icon/siren.png", (672, 445), 72)

        rain = current.get("rain")
        if rain:
            self._measurement_circle(
                (94, 318),
                _measurement(rain.get("value")),
                rain.get("rate_unit") or rain.get("unit", "") + "/h",
                live=rain.get("live", False),
            )

        wind = current.get("wind")
        if wind:
            value, unit = _wind(wind)
            self._measurement_circle(
                (94, 499),
                value,
                unit,
                detail=wind.get("direction_cardinal", ""),
                live=wind.get("live", False),
            )
            if wind.get("direction") is not None:
                self._compass((153, 445), float(wind["direction"]))

    def _map(self, map_path):
        top = 240
        height = 400
        box = self._box((0, top, BASE_WIDTH, top + height))
        if map_path.is_file():
            with Image.open(map_path) as source:
                map_image = ImageOps.grayscale(source)
                target_width = box[2] - box[0]
                target_height = box[3] - box[1]
                scale = max(
                    target_width / map_image.width,
                    target_height / map_image.height,
                )
                resized = map_image.resize(
                    (
                        math.ceil(map_image.width * scale),
                        math.ceil(map_image.height * scale),
                    ),
                    Image.Resampling.LANCZOS,
                )
                left = (resized.width - target_width) // 2
                upper = (resized.height - target_height) // 2
                self.image.paste(
                    resized.crop(
                        (
                            left,
                            upper,
                            left + target_width,
                            upper + target_height,
                        )
                    ),
                    box[:2],
                )
        else:
            self.draw.rectangle(box, fill=225)
            self._text("MAP UNAVAILABLE", (BASE_WIDTH / 2, top + 200), 32)

    def _forecast(self, hourly):
        forecasts = list(hourly[:7])
        if not forecasts:
            return
        count = len(forecasts)
        column = 760 / count
        left = 32
        for index, forecast in enumerate(forecasts):
            center = left + column * (index + 0.5)
            timestamp = forecast.get("dt")
            hour = timestamp.strftime("%-I%p").lower() if timestamp else "--"
            self._text(hour, (center, 674), 35, anchor="mm")
            self._icon(forecast.get("icon"), (center, 750), 96)

        chart_left = 48
        chart_right = 790
        chart_top = 825
        chart_bottom = 1110
        temperatures = [
            float(item["temperature"]["value"]) for item in forecasts
        ]
        rain = [float(item.get("rain_probability") or 0) for item in forecasts]
        unit = forecasts[0]["temperature"].get("unit", "")
        temp_min, temp_max = ((5, 104) if "F" in unit else (-15, 40))
        step = (chart_right - chart_left) / count

        points = []
        for index, (temperature, rain_probability) in enumerate(
            zip(temperatures, rain)
        ):
            center = chart_left + step * (index + 0.5)
            bar_width = step * 0.52
            bar_top = chart_bottom - (
                rain_probability / 100 * (chart_bottom - chart_top)
            )
            self._hatched_bar(
                center - bar_width / 2,
                bar_top,
                center + bar_width / 2,
                chart_bottom,
            )
            y = chart_bottom - (
                (temperature - temp_min)
                / (temp_max - temp_min)
                * (chart_bottom - chart_top)
            )
            points.append((center, y))
            label_y = y - 28 if y > (chart_top + chart_bottom) / 2 else y + 28
            self._outlined_text(
                "{}°".format(_measurement(temperature)),
                (center, label_y),
                27,
            )
            self._text(
                "{}%".format(_measurement(rain_probability)),
                (center, 1140),
                22,
                anchor="mm",
            )

        scaled_points = [self._point(point) for point in points]
        self.rough.polyline(
            scaled_points,
            width=self._size(3),
            roughness=self._size(1.6),
            bowing=self._size(2.2),
        )
        radius = self._size(5)
        for x, y in scaled_points:
            self.rough.ellipse(
                (x - radius, y - radius, x + radius, y + radius),
                width=self._size(2),
                fill=0,
                roughness=self._size(0.8),
            )

    def _footer(self, generated_at):
        if generated_at is None:
            return
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=dt.timezone.utc)
        generated_at = generated_at.astimezone(dt.timezone.utc)
        text = "Last Refreshed: {}, {} {} {}".format(
            generated_at.strftime("%H:%M UTC"),
            generated_at.day,
            generated_at.strftime("%B"),
            generated_at.year,
        )
        self._text(text, (804, 1188), 15, anchor="rm")

    def _measurement_circle(
        self,
        center,
        value,
        unit,
        detail="",
        live=False,
    ):
        self._circle(center, 63, fill=0)
        self._text(value, (center[0], center[1] - 10), 48, fill=255, anchor="mm")
        self._text(unit, (center[0], center[1] + 31), 18, fill=255, anchor="mm")
        if detail:
            self._text(
                detail,
                (center[0], center[1] + 52),
                17,
                bold=True,
                fill=255,
                anchor="mm",
            )
        if live:
            self._live_indicator((center[0], center[1] - 76))

    def _live_indicator(self, center):
        x, y = center
        for radius in (20, 32):
            self._arc(
                (x - radius, y - radius / 2, x + radius, y + radius),
                205,
                335,
                width=4,
            )

    def _compass(self, center, direction):
        self._circle(center, 40, fill=255, outline=0, width=3)
        size = self._size(58)
        compass_path = _asset_path("icon/compass.png")
        if compass_path.is_file():
            with Image.open(compass_path) as source:
                icon = _black_alpha(source).resize(
                    (size, size),
                    Image.Resampling.LANCZOS,
                )
                icon = icon.rotate(-(direction - 45), expand=True)
                self.image.paste(
                    icon,
                    (
                        self._x(center[0]) - icon.width // 2,
                        self._y(center[1]) - icon.height // 2,
                    ),
                    icon,
                )

    def _badge_icon(self, relative_path, center, size):
        self._circle(center, size / 2, fill=255, outline=0, width=3)
        self._icon(relative_path, center, size * 0.75)

    def _icon(self, relative_path, center, size, invert=False):
        path = _asset_path(relative_path)
        if not path.is_file():
            return
        with Image.open(path) as source:
            icon = _black_alpha(source)
            if invert:
                alpha = icon.getchannel("A")
                inverted = ImageOps.invert(icon.convert("L"))
                inverted.putalpha(alpha)
                icon = inverted
            target = self._size(size)
            icon.thumbnail((target, target), Image.Resampling.LANCZOS)
            self.image.paste(
                icon,
                (
                    self._x(center[0]) - icon.width // 2,
                    self._y(center[1]) - icon.height // 2,
                ),
                icon,
            )

    def _hatched_bar(self, left, top, right, bottom):
        box = self._box((left, top, right, bottom))
        self.rough.hatched_rectangle(
            box,
            width=self._size(2),
            gap=self._size(11),
            roughness=self._size(2.4),
        )

    def _outlined_text(self, value, point, size):
        font = self._font(size)
        self.draw.text(
            self._point(point),
            value,
            font=font,
            fill=0,
            stroke_width=self._size(5),
            stroke_fill=255,
            anchor="mm",
        )

    def _text(
        self,
        value,
        point,
        size,
        bold=False,
        fill=0,
        anchor="mm",
    ):
        self.draw.text(
            self._point(point),
            str(value),
            font=self._font(size, bold=bold),
            fill=fill,
            anchor=anchor,
        )

    def _font(self, size, bold=False):
        path = self.bold_path if bold else self.regular_path
        return ImageFont.truetype(path, self._size(size))

    def _circle(self, center, radius, fill, outline=None, width=1):
        x, y = center
        self.rough.ellipse(
            self._box((x - radius, y - radius, x + radius, y + radius)),
            fill=fill,
            outline=outline,
            width=self._size(width),
            roughness=self._size(1.1),
        )

    def _line(self, values, width=1):
        x1, y1, x2, y2 = self._box(values)
        self.rough.line(
            (x1, y1),
            (x2, y2),
            width=self._size(width),
            roughness=self._size(0.8),
        )

    def _arc(self, values, start, end, width=1):
        self.draw.arc(
            self._box(values),
            start=start,
            end=end,
            fill=0,
            width=self._size(width),
        )

    def _point(self, point):
        return (self._x(point[0]), self._y(point[1]))

    def _box(self, values):
        return tuple(
            self._x(value) if index % 2 == 0 else self._y(value)
            for index, value in enumerate(values)
        )

    def _x(self, value):
        return round(value * self.sx)

    def _y(self, value):
        return round(value * self.sy)

    def _size(self, value):
        return max(1, round(value * min(self.sx, self.sy)))


def _asset_path(relative_path):
    if not relative_path:
        return Path()
    path = Path(relative_path)
    return path if path.is_absolute() else ASSET_ROOT / path


def _black_alpha(source):
    image = source.convert("RGBA")
    alpha = image.getchannel("A")
    black = Image.new("L", image.size, 0)
    black.putalpha(alpha)
    return black


def _measurement(value):
    if value is None:
        return "--"
    rounded = round(float(value), 1)
    return str(int(rounded)) if rounded.is_integer() else str(rounded)


def _wind(wind):
    value = float(wind["value"])
    unit = wind.get("unit", "")
    if unit == "m/s":
        value *= 3.6
        unit = "km/h"
    elif unit == "kmh":
        unit = "km/h"
    return _measurement(value), unit


class RoughDraw:
    def __init__(self, draw, seed=0):
        self.draw = draw
        self.random = random.Random(seed)

    def line(
        self,
        start,
        end,
        fill=0,
        width=1,
        roughness=1,
        bowing=1,
        strokes=2,
    ):
        x1, y1 = start
        x2, y2 = end
        dx = x2 - x1
        dy = y2 - y1
        length = max(1, math.hypot(dx, dy))
        normal_x = -dy / length
        normal_y = dx / length
        for _ in range(strokes):
            start_jitter = self._jitter(roughness)
            end_jitter = self._jitter(roughness)
            bow = self._jitter(bowing)
            midpoint = (
                (x1 + x2) / 2 + normal_x * bow,
                (y1 + y2) / 2 + normal_y * bow,
            )
            points = [
                (
                    x1 + normal_x * start_jitter,
                    y1 + normal_y * start_jitter,
                ),
                midpoint,
                (
                    x2 + normal_x * end_jitter,
                    y2 + normal_y * end_jitter,
                ),
            ]
            self.draw.line(points, fill=fill, width=width, joint="curve")

    def polyline(self, points, width=1, roughness=1, bowing=1):
        for start, end in zip(points, points[1:]):
            self.line(
                start,
                end,
                width=width,
                roughness=roughness,
                bowing=bowing,
            )

    def ellipse(
        self,
        box,
        fill=None,
        outline=0,
        width=1,
        roughness=1,
    ):
        left, top, right, bottom = box
        center_x = (left + right) / 2
        center_y = (top + bottom) / 2
        radius_x = (right - left) / 2
        radius_y = (bottom - top) / 2
        if fill is not None:
            self.draw.ellipse(box, fill=fill)
        if outline is None:
            return
        for _ in range(2):
            points = []
            for step in range(49):
                angle = math.tau * step / 48
                jitter = self._jitter(roughness)
                points.append(
                    (
                        center_x + (radius_x + jitter) * math.cos(angle),
                        center_y + (radius_y + jitter) * math.sin(angle),
                    )
                )
            self.draw.line(points, fill=outline, width=width, joint="curve")

    def hatched_rectangle(self, box, width=1, gap=12, roughness=1):
        left, top, right, bottom = box
        self.line(
            (left, top),
            (right, top),
            width=width,
            roughness=roughness,
        )
        self.line(
            (right, top),
            (right, bottom),
            width=width,
            roughness=roughness,
        )
        self.line(
            (right, bottom),
            (left, bottom),
            width=width,
            roughness=roughness,
        )
        self.line(
            (left, bottom),
            (left, top),
            width=width,
            roughness=roughness,
        )
        mask = Image.new("L", self.draw._image.size, 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.rectangle(box, fill=255)
        hatching = Image.new("L", self.draw._image.size, 255)
        hatch_draw = ImageDraw.Draw(hatching)
        hatch = RoughDraw(
            hatch_draw,
            seed=self.random.randrange(1 << 30),
        )
        height = bottom - top
        offset = left - height - gap
        while offset < right + gap:
            start = (offset, bottom + roughness * 2)
            end = (offset + height + gap, top - roughness * 2)
            hatch.line(
                start,
                end,
                width=width,
                roughness=roughness * 1.4,
                bowing=roughness * 1.8,
                strokes=2,
            )
            if self.random.random() < 0.35:
                hatch.line(
                    (start[0] + gap * 0.35, start[1]),
                    (end[0] + gap * 0.35, end[1]),
                    width=max(1, width - 1),
                    roughness=roughness * 1.8,
                    bowing=roughness * 2.2,
                    strokes=1,
                )
            offset += gap + self._jitter(gap * 0.28)
        self.draw._image.paste(hatching, (0, 0), mask)

    def _jitter(self, amount):
        return self.random.uniform(-amount, amount)
