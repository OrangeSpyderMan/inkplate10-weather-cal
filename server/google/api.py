import time
from io import BytesIO
from pathlib import Path
import requests
from PIL import Image, ImageOps
from googlemaps import Client, timezone


class GoogleAPIService:
    def __init__(self, key):
        self.apikey = key
        self.client = Client(key)

    def get_timezone(self, location):
        tz = timezone(self.client, location)
        print(tz)
        return tz

    def get_static_map_url(self, map_id, location):
        svc = self.StaticMapService(self.apikey, map_id)
        return svc.get_url(location)

    def save_static_map(self, map_id, location, output_path):
        svc = self.StaticMapService(self.apikey, map_id)
        svc.save_image(location, output_path)

    class StaticMapService:
        DEFAULT_ZOOM = 10

        def __init__(self, apikey, map_id, cache=True):
            self.base_url = "https://maps.googleapis.com/maps/api/staticmap"
            self.apikey = apikey
            self.map_id = map_id
            self.scale = 2

            self.map_width = 600
            self.map_height = 600

            self.cache = cache

        def get_url(self, location, zoom=DEFAULT_ZOOM):
            no_cache_param = ""
            if not self.cache:
                no_cache_param = "&time={}".format(time.time())

            url = "{}?center={}&zoom={}&size={}x{}&key={}&map_id={}&scale={}&sensor=false{}".format(
                self.base_url,
                location,
                zoom,
                self.map_width,
                self.map_height,
                self.apikey,
                self.map_id,
                self.scale,
                no_cache_param,
            )

            return url

        def get_image(self, location, zoom=DEFAULT_ZOOM):
            r = requests.get(self.get_url(location, zoom), timeout=30)
            r.raise_for_status()
            img = Image.open(BytesIO(r.content))

            return img

        def save_image(self, location, output_path, zoom=DEFAULT_ZOOM):
            img = self.get_image(location, zoom)
            output = Path(output_path)
            output.parent.mkdir(parents=True, exist_ok=True)

            gray = ImageOps.grayscale(img)
            gray = ImageOps.autocontrast(gray, cutoff=1)
            palette = Image.new("P", (1, 1))
            palette_values = []
            for value in (0, 85, 170, 255):
                palette_values += [value, value, value]
            palette_values += [0] * (768 - len(palette_values))
            palette.putpalette(palette_values)
            dithered = gray.convert("RGB").quantize(
                colors=4,
                palette=palette,
                dither=Image.Dither.FLOYDSTEINBERG,
            ).convert("L")

            temp_path = output.with_suffix(output.suffix + ".tmp")
            dithered.save(temp_path, format="PNG")
            temp_path.replace(output)
