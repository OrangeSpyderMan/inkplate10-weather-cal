from views.calendar import CalendarPage


class FirefoxCalendarRenderer:
    name = "firefox"

    def render(
        self,
        snapshot,
        map_url,
        output_path,
        width,
        height,
        options=None,
    ):
        page = CalendarPage(
            width,
            height,
            output_path=output_path,
        )
        page.template(
            map_url=map_url,
            daily_summary=snapshot.daily_summary,
            hourly_forecasts=snapshot.hourly_forecasts,
        )
        page.save()


def build_renderer(name):
    if name == FirefoxCalendarRenderer.name:
        return FirefoxCalendarRenderer()

    raise ValueError(f"unsupported output renderer {name!r}")
