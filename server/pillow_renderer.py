import datetime as dt
import math
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps
from rough import Options
from rough.generator import RoughGenerator


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
        self.rough = RoughPillowDraw(self.draw, seed=1947)
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
            self._live_indicator((731, 242))

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
        self.draw.line(
            self._box((24, chart_bottom, 801, chart_bottom)),
            fill=160,
            width=max(1, self._size(1)),
        )

        points = []
        for index, (temperature, rain_probability) in enumerate(
            zip(temperatures, rain)
        ):
            center = chart_left + step * (index + 0.5)
            bar_width = step * 0.72
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
            roughness=1,
            bowing=0.1,
            max_randomness_offset=self._size(2),
        )
        radius = self._size(5)
        for x, y in scaled_points:
            self.rough.ellipse(
                (x - radius, y - radius, x + radius, y + radius),
                width=self._size(2),
                fill=None,
                outline=0,
                roughness=self._size(1.2),
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
        if bottom - top < 0.5:
            scaled_left, _, scaled_right, scaled_bottom = box
            for start, end in (
                ((scaled_left, scaled_bottom), (scaled_right, scaled_bottom)),
                ((scaled_right, scaled_bottom), (scaled_left, scaled_bottom)),
            ):
                self.rough.line(
                    start,
                    end,
                    width=self._size(1),
                    roughness=4,
                    bowing=0.2,
                    max_randomness_offset=self._size(2),
                )
            return
        self.rough.hatched_rectangle(
            box,
            border_width=self._size(3),
            hatch_width=self._size(1),
            gap=self._size(18),
            roughness=4,
            bowing=0.2,
            max_randomness_offset=self._size(2),
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
            roughness=0.8,
            max_randomness_offset=self._size(2),
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


class RoughPillowDraw:
    """Render rough-py drawing operations onto a Pillow canvas."""
    def __init__(self, draw, seed=0):
        self.draw = draw
        self.generator = RoughGenerator()
        self.seed = seed

    def line(
        self,
        start,
        end,
        fill=0,
        width=1,
        roughness=1,
        bowing=1,
        strokes=2,
        max_randomness_offset=2,
    ):
        options = Options(
            stroke=self._color(fill),
            strokeWidth=width,
            roughness=roughness,
            bowing=bowing,
            maxRandomnessOffset=max_randomness_offset,
            disableMultiStroke=strokes == 1,
            seed=self._next_seed(),
        )
        self._draw(
            self.generator.line(
                start[0],
                start[1],
                end[0],
                end[1],
                options,
            )
        )

    def polyline(
        self,
        points,
        width=1,
        roughness=1,
        bowing=1,
        max_randomness_offset=2,
    ):
        self._draw(
            self.generator.linearPath(
                points,
                Options(
                    stroke="black",
                    strokeWidth=width,
                    roughness=roughness,
                    bowing=bowing,
                    maxRandomnessOffset=max_randomness_offset,
                    seed=self._next_seed(),
                ),
            )
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
        self._draw(
            self.generator.ellipse(
                (left + right) / 2,
                (top + bottom) / 2,
                right - left,
                bottom - top,
                Options(
                    stroke=(
                        self._color(outline)
                        if outline is not None
                        else "none"
                    ),
                    strokeWidth=width,
                    fill=self._color(fill) if fill is not None else None,
                    fillStyle="solid",
                    roughness=1,
                    maxRandomnessOffset=roughness,
                    seed=self._next_seed(),
                ),
            )
        )

    def hatched_rectangle(
        self,
        box,
        border_width=1,
        hatch_width=1,
        gap=12,
        roughness=1,
        bowing=1,
        max_randomness_offset=2,
    ):
        """Draw a rough-py zigzag fill and Chart.js-style separate borders."""
        left, top, right, bottom = box
        inset = border_width / 2
        left += inset
        top += inset
        right -= inset
        bottom -= inset
        if right <= left or bottom <= top:
            return
        self._draw(
            self.generator.rectangle(
                left,
                top,
                right - left,
                bottom - top,
                Options(
                    stroke="none",
                    fill="black",
                    fillStyle="zigzag",
                    fillWeight=hatch_width,
                    hachureAngle=45,
                    hachureGap=gap,
                    roughness=roughness,
                    bowing=bowing,
                    maxRandomnessOffset=max_randomness_offset,
                    seed=self._next_seed(),
                ),
            )
        )

        edges = (
            ((left, top), (right, top)),
            ((right, top), (right, bottom)),
            ((right, bottom), (left, bottom)),
            ((left, bottom), (left, top)),
        )
        for start, end in edges:
            self.line(
                start,
                end,
                width=border_width,
                roughness=roughness,
                bowing=bowing,
                max_randomness_offset=max_randomness_offset,
            )

    def _draw(self, drawable):
        for operation_set in drawable.sets:
            paths = self._paths(operation_set.ops)
            if operation_set.type == "fillPath":
                for path in paths:
                    if len(path) >= 3:
                        self.draw.polygon(
                            path,
                            fill=self._gray(drawable.options.fill),
                        )
                continue

            if operation_set.type == "fillSketch":
                color = self._gray(drawable.options.fill)
                width = max(1, round(drawable.options.fillWeight))
            else:
                color = self._gray(drawable.options.stroke)
                width = max(1, round(drawable.options.strokeWidth))
            for path in paths:
                if len(path) >= 2:
                    self.draw.line(
                        path,
                        fill=color,
                        width=width,
                        joint="curve",
                    )

    def _paths(self, operations):
        paths = []
        current = []
        cursor = None
        for operation in operations:
            data = operation.data
            if operation.op == "move":
                if current:
                    paths.append(current)
                cursor = (data[0], data[1])
                current = [cursor]
            elif operation.op == "lineTo":
                cursor = (data[0], data[1])
                current.append(cursor)
            elif operation.op == "bcurveTo" and cursor is not None:
                control_1 = (data[0], data[1])
                control_2 = (data[2], data[3])
                end = (data[4], data[5])
                current.extend(
                    self._sample_cubic(
                        cursor,
                        control_1,
                        control_2,
                        end,
                    )
                )
                cursor = end
        if current:
            paths.append(current)
        return paths

    @staticmethod
    def _sample_cubic(start, control_1, control_2, end):
        points = []
        for step in range(1, 17):
            progress = step / 16
            inverse = 1 - progress
            points.append(
                (
                    inverse**3 * start[0]
                    + 3 * inverse**2 * progress * control_1[0]
                    + 3 * inverse * progress**2 * control_2[0]
                    + progress**3 * end[0],
                    inverse**3 * start[1]
                    + 3 * inverse**2 * progress * control_1[1]
                    + 3 * inverse * progress**2 * control_2[1]
                    + progress**3 * end[1],
                )
            )
        return points

    def _next_seed(self):
        self.seed += 1
        return self.seed

    @staticmethod
    def _color(value):
        return "white" if value in (255, "white") else "black"

    @staticmethod
    def _gray(value):
        return 255 if value in (255, "white", "#fff", "#ffffff") else 0
