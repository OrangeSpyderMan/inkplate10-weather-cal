#!/usr/bin/env python3

import argparse
import getpass
import hashlib
import http.server
import json
import socketserver
import struct
import subprocess
import tempfile
import threading
import time
from pathlib import Path

def monochrome_bmp(width=825, height=1200, phase=0):
    row_size = ((width + 31) // 32) * 4
    pixels = bytearray(row_size * height)
    for y in range(height):
        for x in range(width):
            if (x // 40 + y // 40 + phase) % 2:
                byte_index = (height - 1 - y) * row_size + x // 8
                pixels[byte_index] |= 1 << (7 - x % 8)

    pixel_offset = 14 + 40 + 8
    file_size = pixel_offset + len(pixels)
    return b"".join(
        (
            struct.pack("<2sIHHI", b"BM", file_size, 0, 0, pixel_offset),
            struct.pack(
                "<IIIHHIIIIII",
                40,
                width,
                height,
                1,
                1,
                0,
                len(pixels),
                2835,
                2835,
                2,
                2,
            ),
            b"\xff\xff\xff\x00\x00\x00\x00\x00",
            pixels,
        )
    )


READY_IMAGE = monochrome_bmp()
RECOVERY_IMAGE = monochrome_bmp(phase=1)
READY_HASH = hashlib.sha256(READY_IMAGE).hexdigest()
RECOVERY_HASH = hashlib.sha256(RECOVERY_IMAGE).hexdigest()
FAILED_HASH = "f" * 64


class FaultState:
    def __init__(self):
        self.mode = "ready"
        self.lock = threading.Lock()

    def set_mode(self, mode):
        with self.lock:
            self.mode = mode

    def snapshot(self):
        with self.lock:
            image = (
                RECOVERY_IMAGE
                if self.mode == "recovery"
                else READY_IMAGE
            )
            return self.mode, image


def fixture_response(state, path):
    mode, image = state.snapshot()
    if path == "/status":
        if mode == "status-unavailable":
            return 503, "text/plain", b"status unavailable"
        signature = {
            "image-failure": FAILED_HASH,
            "recovery": RECOVERY_HASH,
        }.get(mode, READY_HASH)
        return (
            200,
            "application/json",
            ('{"sha256":"' + signature + '"}').encode("ascii"),
        )
    if path == "/calendar.bmp":
        if mode == "image-failure":
            return 503, "text/plain", b"image unavailable"
        return 200, "image/bmp", image
    return 404, "text/plain", b"not found"


def handler_class(state):
    class FaultHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            status, content_type, payload = fixture_response(
                state,
                self.path,
            )
            self._send(status, content_type, payload)

        def _send(self, status, content_type, payload):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format_string, *args):
            return

    return FaultHandler


class ThreadingServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class SerialMonitor:
    def __init__(self, port, baud=115200):
        import serial

        deadline = time.monotonic() + 15
        while True:
            try:
                self.connection = serial.Serial(
                    port,
                    baudrate=baud,
                    timeout=0.5,
                )
                break
            except serial.SerialException:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.5)

    def close(self):
        self.connection.close()

    def wait_for(self, required, forbidden=(), timeout=90):
        deadline = time.monotonic() + timeout
        lines = []
        while time.monotonic() < deadline:
            raw = self.connection.readline()
            if not raw:
                continue
            line = raw.decode(errors="replace").strip()
            if not line:
                continue
            print(line, flush=True)
            lines.append(line)
            text = "\n".join(lines)
            if all(value in text for value in required):
                for value in forbidden:
                    if value in text:
                        raise AssertionError(
                            f"unexpected serial output {value!r}"
                        )
                return text
        raise TimeoutError(
            "timed out waiting for serial output: "
            + ", ".join(required)
        )


def write_config(path, host, port, ssid, password, retries):
    path.write_text(
        f"""display:
  rotation: 1
calendar:
  url: http://{host}:{port}/calendar.bmp
  status_url: http://{host}:{port}/status
  refresh_interval: 1
  retries: 1
  retry_interval_minutes: 1
wifi:
  ssid: {json.dumps(ssid)}
  pass: {json.dumps(password)}
  retries: {retries}
ntp:
  host: pool.ntp.org
  timezone: UTC
mqtt_logger:
  enabled: false
""",
        encoding="utf-8",
    )


def flash(port, config, extra_flags):
    subprocess.run(
        [
            "make",
            "firmware-upload",
            f"PORT={port}",
            f"CONFIG={config}",
            f"FIRMWARE_EXTRA_FLAGS={extra_flags}",
        ],
        check=True,
    )


def run_scenario(port, config, flags, required, forbidden=(), timeout=90):
    flash(port, config, flags)
    monitor = SerialMonitor(port)
    try:
        return monitor.wait_for(required, forbidden, timeout)
    finally:
        monitor.close()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run the Inkplate hardware fault/soak sequence."
    )
    parser.add_argument("--port", required=True, help="Inkplate serial port")
    parser.add_argument(
        "--host-address",
        required=True,
        help="address of this host reachable from the Inkplate Wi-Fi network",
    )
    parser.add_argument("--http-port", type=int, default=18081)
    parser.add_argument("--wifi-ssid", required=True)
    parser.add_argument(
        "--wifi-pass",
        help="Wi-Fi password; omit to prompt without exposing it in process arguments",
    )
    parser.add_argument("--cycles", type=int, default=20)
    parser.add_argument("--sleep-seconds", type=int, default=15)
    return parser.parse_args()


def main():
    args = parse_args()
    if args.cycles < 1 or args.sleep_seconds < 5:
        raise SystemExit("--cycles must be positive and --sleep-seconds at least 5")
    wifi_password = (
        args.wifi_pass
        if args.wifi_pass is not None
        else getpass.getpass("Wi-Fi password (leave empty for open network): ")
    )

    state = FaultState()
    server = ThreadingServer(
        ("0.0.0.0", args.http_port),
        handler_class(state),
    )
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    flags = f"-DINKPLATE_SOAK_SLEEP_SECONDS={args.sleep_seconds}"

    try:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            valid_config = root / "valid.yaml"
            failed_wifi_config = root / "failed-wifi.yaml"
            write_config(
                valid_config,
                args.host_address,
                args.http_port,
                args.wifi_ssid,
                wifi_password,
                2,
            )
            write_config(
                failed_wifi_config,
                args.host_address,
                args.http_port,
                "inkplate-soak-missing-network",
                "",
                0,
            )

            print("SCENARIO low battery", flush=True)
            run_scenario(
                args.port,
                valid_config,
                flags + " -DINKPLATE_SOAK_BATTERY_VOLTAGE=3.0F",
                ("battery critical; skipping network refresh", "Sleeping..."),
                ("connecting to WiFi",),
            )

            print("SCENARIO failed Wi-Fi", flush=True)
            run_scenario(
                args.port,
                failed_wifi_config,
                flags,
                ("wifi connect timeout", "Sleeping..."),
            )

            print("SCENARIO network soak", flush=True)
            state.set_mode("ready")
            flash(args.port, valid_config, flags)
            monitor = SerialMonitor(args.port)
            try:
                monitor.wait_for(("REFRESH - status=ready", "Sleeping..."))
                monitor.wait_for(("REFRESH - status=unchanged", "Sleeping..."))

                state.set_mode("status-unavailable")
                monitor.wait_for(
                    (
                        "calendar status unavailable; falling back to image refresh",
                        "REFRESH - status=ready",
                        "Sleeping...",
                    )
                )

                state.set_mode("image-failure")
                monitor.wait_for(
                    (
                        "image display error after 2 attempts",
                        "REFRESH - status=failed retaining=previous",
                        "Sleeping...",
                    )
                )

                state.set_mode("recovery")
                for cycle in range(args.cycles):
                    monitor.wait_for(
                        ("REFRESH - status=ready", "Sleeping...")
                        if cycle == 0
                        else ("REFRESH - status=unchanged", "Sleeping..."),
                        timeout=max(90, args.sleep_seconds * 4),
                    )
                    print(
                        f"SOAK cycle {cycle + 1}/{args.cycles} passed",
                        flush=True,
                    )
            finally:
                monitor.close()
    finally:
        server.shutdown()
        server.server_close()

    print(f"Hardware soak passed; fixture image sha256={READY_HASH}")


if __name__ == "__main__":
    main()
