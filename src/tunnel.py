"""Ngrok tunnel — uses system ngrok CLI (reads ~/.ngrok/ngrok.yml authtoken)."""

from __future__ import annotations

import atexit
import json
import logging
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from src import config

logger = logging.getLogger(__name__)

_process: subprocess.Popen | None = None
_tunnel_started = False
NGROK_URL_FILE = config.DATA_DIR / "ngrok.url"


def _find_ngrok_bin() -> str | None:
    if config.NGROK_BIN:
        path = Path(config.NGROK_BIN)
        if path.is_file():
            return str(path)
    for candidate in (
        "/opt/homebrew/bin/ngrok",
        "/usr/local/bin/ngrok",
    ):
        if Path(candidate).is_file():
            return candidate
    return shutil.which("ngrok")


def _authtoken_from_ngrok_config() -> str:
    if config.NGROK_AUTHTOKEN:
        return config.NGROK_AUTHTOKEN

    config_paths = [
        Path.home() / "Library/Application Support/ngrok/ngrok.yml",
        Path.home() / ".ngrok2/ngrok.yml",
    ]
    for path in config_paths:
        if not path.exists():
            continue
        match = re.search(r"authtoken:\s*(\S+)", path.read_text())
        if match:
            return match.group(1)
    return ""


def _save_public_url(url: str) -> None:
    NGROK_URL_FILE.parent.mkdir(parents=True, exist_ok=True)
    dashboard = f"{url.rstrip('/')}/dashboard"
    NGROK_URL_FILE.write_text(f"{dashboard}\n")
    logger.info("Public dashboard link saved to %s", NGROK_URL_FILE)


def _fetch_https_url() -> str | None:
    for web_port in range(4040, 4055):
        api = f"http://127.0.0.1:{web_port}/api/tunnels"
        try:
            with urllib.request.urlopen(api, timeout=2) as resp:
                data = json.loads(resp.read().decode())
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError):
            continue

        tunnels = data.get("tunnels", [])
        for tunnel in tunnels:
            if tunnel.get("proto") == "https" and tunnel.get("public_url"):
                return str(tunnel["public_url"]).rstrip("/")
        for tunnel in tunnels:
            if tunnel.get("public_url"):
                return str(tunnel["public_url"]).rstrip("/")
    return None


def _start_ngrok_cli(port: int) -> str:
    global _process
    ngrok_bin = _find_ngrok_bin()
    if not ngrok_bin:
        raise RuntimeError(
            "ngrok not found. Install with: brew install ngrok/ngrok/ngrok"
        )

    cmd = [ngrok_bin, "http", str(port), "--log=stdout"]

    logger.info("Starting ngrok CLI: %s http %s", ngrok_bin, port)
    _process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    for _ in range(40):
        time.sleep(0.25)
        if _process.poll() is not None:
            err = (_process.stderr.read() if _process.stderr else "") or "unknown error"
            raise RuntimeError(f"ngrok process exited: {err.strip()}")
        url = _fetch_https_url()
        if url:
            return url

    raise RuntimeError("ngrok started but public URL was not ready in time")


def _start_pyngrok(port: int) -> str:
    from pyngrok import conf, ngrok

    token = _authtoken_from_ngrok_config()
    if not token:
        raise RuntimeError(
            "No ngrok authtoken found. Run: ngrok config add-authtoken YOUR_TOKEN"
        )

    conf.get_default().auth_token = token
    if config.NGROK_REGION:
        conf.get_default().region = config.NGROK_REGION

    tunnel = ngrok.connect(str(port), bind_tls=True)
    return tunnel.public_url.rstrip("/")


def start_ngrok(port: int | None = None) -> str:
    """Start ngrok and return the public HTTPS base URL."""
    global _tunnel_started
    port = port or config.API_PORT

    if config.NGROK_USE_CLI and _find_ngrok_bin():
        public_url = _start_ngrok_cli(port)
    else:
        public_url = _start_pyngrok(port)

    _save_public_url(public_url)

    if not _tunnel_started:
        atexit.register(stop_ngrok)
        _tunnel_started = True

    logger.info("Ngrok tunnel active: %s", public_url)
    return public_url


def stop_ngrok() -> None:
    global _process
    if _process and _process.poll() is None:
        _process.terminate()
        try:
            _process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _process.kill()
        _process = None

    try:
        from pyngrok import ngrok

        ngrok.kill()
    except Exception:
        pass


def get_saved_public_url() -> str | None:
    if not NGROK_URL_FILE.exists():
        return None
    line = NGROK_URL_FILE.read_text().strip()
    return line or None
