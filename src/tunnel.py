"""Ngrok tunnel management."""

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
_api_ready = False
NGROK_URL_FILE = config.DATA_DIR / "ngrok.url"


def set_api_ready(ready: bool = True) -> None:
    """Call once the FastAPI server is listening (before starting ngrok)."""
    global _api_ready
    _api_ready = ready


def _static_endpoint_url() -> str | None:
    """Normalized https URL for the configured static ngrok domain."""
    raw = config.NGROK_STATIC_DOMAIN
    if not raw:
        return None
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw.rstrip("/")
    return f"https://{raw.rstrip('/')}"


def _ngrok_http_cmd(ngrok_bin: str, port: int) -> list[str]:
    cmd = [
        ngrok_bin,
        "http",
        str(port),
        "--log=stdout",
        "--web-addr",
        f"127.0.0.1:{config.NGROK_WEB_PORT}",
    ]
    static_url = _static_endpoint_url()
    if static_url:
        cmd.extend(["--url", static_url])
    return cmd


def _find_ngrok_bin() -> str | None:
    if config.NGROK_BIN:
        path = Path(config.NGROK_BIN)
        if path.is_file():
            return str(path)
    for candidate in (
        "/opt/homebrew/bin/ngrok",
        "/usr/local/bin/ngrok",
        "/snap/bin/ngrok",
    ):
        if Path(candidate).is_file():
            return candidate
    return shutil.which("ngrok")


def _authtoken_from_ngrok_config() -> str:
    if config.NGROK_AUTHTOKEN:
        return config.NGROK_AUTHTOKEN

    config_paths = [
        Path.home() / "Library/Application Support/ngrok/ngrok.yml",
        Path.home() / ".config/ngrok/ngrok.yml",
        Path.home() / ".ngrok/ngrok.yml",
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
    ports = [config.NGROK_WEB_PORT]
    ports.extend(port for port in range(4040, 4055) if port != config.NGROK_WEB_PORT)
    for web_port in ports:
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
            "ngrok not found. Install ngrok, or set NGROK_BIN to the ngrok executable."
        )

    cmd = _ngrok_http_cmd(ngrok_bin, port)

    logger.info(
        "Starting ngrok CLI: %s (static=%s)",
        " ".join(cmd[:4]),
        _static_endpoint_url() or "random",
    )
    log_path = config.DATA_DIR.parent / "logs" / "ngrok.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, "a", encoding="utf-8")
    log_file.write(f"\n--- ngrok start {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
    log_file.flush()

    _process = subprocess.Popen(
        cmd,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )

    for _ in range(40):
        time.sleep(0.25)
        if _process.poll() is not None:
            tail = ""
            if log_path.exists():
                tail = log_path.read_text(encoding="utf-8")[-2000:]
            raise RuntimeError(
                f"ngrok process exited early. Log tail:\n{tail.strip() or 'unknown error'}"
            )
        url = _fetch_https_url()
        if url:
            return url

    static = _static_endpoint_url()
    if static:
        raise RuntimeError(
            f"ngrok started but static URL {static} was not ready. "
            "Check the domain at https://dashboard.ngrok.com/domains"
        )
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

    static = _static_endpoint_url()
    if static:
        hostname = static.replace("https://", "").replace("http://", "")
        tunnel = ngrok.connect(str(port), hostname=hostname, bind_tls=True)
    else:
        tunnel = ngrok.connect(str(port), bind_tls=True)
    return tunnel.public_url.rstrip("/")


def _expected_base_url() -> str | None:
    static = _static_endpoint_url()
    if static:
        return static.rstrip("/")
    saved = get_saved_public_url()
    if saved:
        return saved.replace("/dashboard", "").rstrip("/")
    return None


def is_tunnel_live_local() -> bool:
    """True when ngrok process is up, local API reports a tunnel, and app responds."""
    if not is_ngrok_process_running():
        return False
    tunnel_url = _fetch_https_url()
    if not tunnel_url:
        return False
    expected = _expected_base_url()
    if expected and tunnel_url.rstrip("/") != expected.rstrip("/"):
        return False
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{config.API_PORT}/api/health", timeout=3
        ) as resp:
            return resp.status == 200
    except Exception:
        return False


def start_ngrok(port: int | None = None) -> str:
    """Start ngrok and return the public HTTPS base URL."""
    global _tunnel_started
    port = port or config.API_PORT

    existing = _expected_base_url()
    if existing and is_tunnel_live_local():
        logger.info("Ngrok tunnel already healthy: %s", existing)
        _save_public_url(existing)
        return existing

    stop_ngrok()

    if config.NGROK_USE_CLI and _find_ngrok_bin():
        public_url = _start_ngrok_cli(port)
    else:
        public_url = _start_pyngrok(port)

    _save_public_url(public_url)

    if not _tunnel_started:
        atexit.register(stop_ngrok)
        _tunnel_started = True

    expected = _static_endpoint_url()
    if expected and public_url.rstrip("/") != expected.rstrip("/"):
        logger.warning(
            "Tunnel URL %s does not match NGROK_STATIC_DOMAIN %s — update .env",
            public_url,
            expected,
        )

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
        subprocess.run(
            ["pkill", "-f", f"ngrok http {config.API_PORT}"],
            capture_output=True,
            text=True,
        )
    except Exception:
        pass

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


def is_ngrok_process_running() -> bool:
    global _process
    if _process is not None and _process.poll() is None:
        return True
    # Fallback: any ngrok http child for our port
    try:
        import subprocess

        out = subprocess.run(
            ["pgrep", "-f", f"ngrok http {config.API_PORT}"],
            capture_output=True,
            text=True,
        )
        return out.returncode == 0
    except Exception:
        return False


def verify_public_url(base_url: str) -> bool:
    """Check tunnel end-to-end; prefer curl (LibreSSL urllib often fails on ngrok)."""
    url = f"{base_url.rstrip('/')}/api/health"
    curl_bin = shutil.which("curl") or "/usr/bin/curl"
    try:
        out = subprocess.run(
            [
                curl_bin,
                "-sf",
                "-o",
                "/dev/null",
                "-w",
                "%{http_code}",
                "-H",
                "ngrok-skip-browser-warning: 1",
                "--max-time",
                "15",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if out.stdout.strip() == "200":
            return True
    except Exception:
        pass
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "AltCoinTrading/health",
                "ngrok-skip-browser-warning": "1",
            },
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            return resp.status == 200
    except Exception:
        return False


def ensure_ngrok_running() -> str | None:
    """Restart ngrok if enabled but tunnel is missing or unreachable."""
    if not config.NGROK_ENABLED or not _api_ready:
        return _expected_base_url()

    existing = _expected_base_url()
    if existing and is_tunnel_live_local():
        return existing

    logger.warning("Ngrok tunnel down or stale — restarting")
    stop_ngrok()
    try:
        from src.health import get_health

        public_url = start_ngrok(config.API_PORT)
        get_health().mark_ngrok(public_url)
        return public_url
    except Exception:
        logger.exception("Ngrok restart failed")
        return None
