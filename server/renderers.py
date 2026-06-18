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
            generated_at=snapshot.generated_at,
        )
        page.save()


RENDERERS = {
    FirefoxCalendarRenderer.name: FirefoxCalendarRenderer,
}


def build_renderer(name):
    renderer = RENDERERS.get(name)
    if renderer is not None:
        return renderer()

    raise ValueError(
        "unsupported output renderer {!r}; supported renderers: {}".format(
            name,
            ", ".join(sorted(RENDERERS)),
        )
    )
