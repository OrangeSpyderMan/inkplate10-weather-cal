import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file, send_from_directory

from artifacts import ArtifactStore
from output_profiles import load_exported_output_profiles


SERVER_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = SERVER_DIR / "data"
DEFAULT_PWA_DIR = SERVER_DIR / "views" / "pwa"


def create_app(
    data_dir=None,
    pwa_dir=None,
    legacy_calendar_served=None,
    output_profiles=None,
    default_output_profile=None,
):
    if output_profiles is None or default_output_profile is None:
        configured_profiles, configured_default = load_exported_output_profiles()
        output_profiles = output_profiles or configured_profiles
        default_output_profile = default_output_profile or configured_default

    app = Flask(__name__)
    app.config["ARTIFACT_STORE"] = ArtifactStore(
        data_dir or os.environ.get("INKPLATE_DATA_DIR", DEFAULT_DATA_DIR)
    )
    app.config["PWA_DIR"] = Path(pwa_dir or DEFAULT_PWA_DIR)
    app.config["LEGACY_CALENDAR_SERVED"] = legacy_calendar_served
    app.config["OUTPUT_PROFILES"] = output_profiles
    app.config["DEFAULT_OUTPUT_PROFILE"] = default_output_profile
    register_routes(app)
    return app


def register_routes(app):
    @app.get("/api/v1/health")
    def health():
        return jsonify({"status": "ok"})

    @app.get("/api/v1/ready")
    def ready():
        store = _store(app)
        snapshot_exists = store.snapshot_path.is_file()
        output_status = store.output_status(_profiles(app))
        cycle_complete = all(output_status.values())
        status = (
            "ready"
            if snapshot_exists and cycle_complete
            else "not_ready"
        )
        response = {
            "status": status,
            "snapshot": snapshot_exists,
            "outputs": output_status,
            "producer_cycle_complete": cycle_complete,
        }
        return jsonify(response), 200 if status == "ready" else 503

    @app.get("/api/v1/weather")
    def weather():
        path = _store(app).snapshot_path
        if not path.is_file():
            return jsonify({"error": "weather snapshot is not available"}), 503

        try:
            with path.open(encoding="utf-8") as snapshot_file:
                payload = json.load(snapshot_file)
                stat = os.fstat(snapshot_file.fileno())
        except (OSError, json.JSONDecodeError):
            logging.getLogger("server").exception(
                "Failed to read weather snapshot from %s", path
            )
            return jsonify({"error": "weather snapshot is not available"}), 503

        response = jsonify(payload)
        response.last_modified = datetime.fromtimestamp(
            stat.st_mtime,
            tz=timezone.utc,
        )
        response.cache_control.no_cache = True
        response.set_etag(
            f"{stat.st_mtime_ns}-{stat.st_size}",
            weak=True,
        )
        return response.make_conditional(request)

    @app.get("/outputs/<profile>/<filename>")
    def output(profile, filename):
        return _send_output(
            app,
            profile,
            filename,
            as_attachment=False,
        )

    @app.get("/calendar.png")
    def legacy_calendar_output():
        response = _send_default_output(app, as_attachment=True)
        callback = app.config.get("LEGACY_CALENDAR_SERVED")
        if callback is not None:
            callback()
        return response

    @app.get("/")
    @app.get("/app")
    @app.get("/app/")
    @app.get("/app/index.html")
    def pwa():
        return send_from_directory(app.config["PWA_DIR"], "index.html")

    @app.get("/app.css")
    def pwa_css():
        return send_from_directory(app.config["PWA_DIR"], "app.css")

    @app.get("/app.js")
    def pwa_js():
        return send_from_directory(app.config["PWA_DIR"], "app.js")

    @app.get("/manifest.webmanifest")
    def pwa_manifest():
        return send_from_directory(
            app.config["PWA_DIR"],
            "manifest.webmanifest",
            mimetype="application/manifest+json",
        )

    @app.get("/sw.js")
    def pwa_service_worker():
        return send_from_directory(
            app.config["PWA_DIR"],
            "sw.js",
            mimetype="application/javascript",
        )

    @app.get("/icons/<path:filename>")
    def pwa_icon(filename):
        return send_from_directory(app.config["PWA_DIR"] / "icons", filename)

    @app.get("/favicon.ico")
    def favicon():
        return send_from_directory(
            app.config["PWA_DIR"] / "icons",
            "weathercal-favicon.ico",
            mimetype="image/x-icon",
        )

    @app.get("/apple-touch-icon.png")
    @app.get("/apple-touch-icon-precomposed.png")
    def apple_touch_icon():
        return send_from_directory(
            app.config["PWA_DIR"] / "icons",
            "weathercal-icon-192.png",
            mimetype="image/png",
        )

    @app.get("/app/calendar.png")
    def legacy_pwa_calendar_output():
        return _send_default_output(app, as_attachment=False)


def _store(app):
    return app.config["ARTIFACT_STORE"]


def _profiles(app):
    return app.config["OUTPUT_PROFILES"]


def _send_default_output(app, as_attachment):
    profile = _profiles(app)[app.config["DEFAULT_OUTPUT_PROFILE"]]
    return _send_output(
        app,
        profile.name,
        profile.filename,
        as_attachment=as_attachment,
    )


def _send_output(app, profile_name, filename, as_attachment):
    profile = _profiles(app).get(profile_name)
    if profile is None or filename != profile.filename:
        abort(404)

    path = _store(app).output_path(profile.name, profile.filename)
    if not path.is_file():
        logging.getLogger("server").error("%s: no such file exists", path)
        abort(404)

    return send_file(
        path,
        mimetype="image/png",
        as_attachment=as_attachment,
        download_name=profile.filename if as_attachment else None,
        max_age=0,
        conditional=True,
    )


app = create_app()
