from pillow_renderer import PillowCalendarRenderer


RENDERERS = {"pillow": PillowCalendarRenderer}


def build_renderer(name):
    if name == "firefox":
        raise ValueError(
            "output renderer 'firefox' was removed in v4; "
            "use 'pillow' or remain on a v3.x release"
        )

    renderer_class = RENDERERS.get(name)
    if renderer_class is None:
        raise ValueError(
            "unsupported output renderer {!r}; supported renderers: {}".format(
                name,
                ", ".join(sorted(RENDERERS)),
            )
        )

    return renderer_class()
