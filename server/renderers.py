class FirefoxCalendarRenderer:
    name = "firefox"

    def __init__(self, page_class):
        self.page_class = page_class

    def render(
        self,
        snapshot,
        map_url,
        output_path,
        width,
        height,
        options=None,
    ):
        page = self.page_class(
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
    "firefox": ("views.calendar", "CalendarPage"),
    "pillow": ("pillow_renderer", "PillowCalendarRenderer"),
}


def build_renderer(name):
    renderer = RENDERERS.get(name)
    if renderer is None:
        raise ValueError(
            "unsupported output renderer {!r}; supported renderers: {}".format(
                name,
                ", ".join(sorted(RENDERERS)),
            )
        )

    module_name, class_name = renderer
    try:
        module = __import__(module_name, fromlist=[class_name])
    except ModuleNotFoundError as error:
        if _missing_renderer_dependency(error, name):
            raise ValueError(
                "output renderer {!r} is unavailable in this installation; "
                "use a container flavour that includes it or install its "
                "optional dependencies".format(name)
            ) from error
        raise

    renderer_class = getattr(module, class_name)
    if name == "firefox":
        return FirefoxCalendarRenderer(renderer_class)
    return renderer_class()


def _missing_renderer_dependency(error, renderer):
    dependencies = {
        "firefox": {"airium", "selenium"},
        "pillow": {"rough"},
    }
    missing_root = (error.name or "").split(".", 1)[0]
    return missing_root in dependencies.get(renderer, set())
