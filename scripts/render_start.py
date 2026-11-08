"""Render-only authenticated reverse proxy for the Streamlit WebUI.

WEBUI_PASSWORD is supplied through Render's environment dashboard, never the
repository. The proxy and local-only Streamlit process run in the same service.
"""
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time


STREAMLIT_HOST = "127.0.0.1"
STREAMLIT_PORT = 8501
STREAMLIT_READY_TIMEOUT_SECONDS = 60


def nginx_config_text(listen_port, password_file, directory):
    """Build the Streamlit reverse-proxy config, including WebSocket upgrades.

    Forcing ``Connection: upgrade`` on every request makes ordinary HTTP
    pings fail with 502. Streamlit then shows “Connection failed with
    status 502” while a video is generating. Only upgrade when the browser
    actually asks for a WebSocket.
    """
    return f"""worker_processes 1;
pid {directory}/nginx.pid;
error_log /dev/stderr warn;
events {{ worker_connections 256; }}
http {{
  access_log off;
  client_max_body_size 200m;
  map $http_upgrade $connection_upgrade {{
    default upgrade;
    '' close;
  }}
  server {{
    listen 0.0.0.0:{int(listen_port)};
    location = /_render_health {{ access_log off; return 200 "ok\\n"; }}
    location / {{
      auth_basic "MoneyPrinterTurbo";
      auth_basic_user_file {password_file};
      proxy_pass http://{STREAMLIT_HOST}:{STREAMLIT_PORT};
      proxy_http_version 1.1;
      proxy_set_header Host $http_host;
      proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;
      proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
      proxy_set_header Upgrade $http_upgrade;
      proxy_set_header Connection $connection_upgrade;
      proxy_connect_timeout 60s;
      proxy_send_timeout 86400s;
      proxy_read_timeout 86400s;
      proxy_buffering off;
      proxy_cache off;
    }}
  }}
}}
"""


def streamlit_command():
    """Launch Streamlit for same-origin nginx, without file watching."""
    return [
        sys.executable, "-m", "streamlit", "run", "webui/Main.py",
        f"--server.address={STREAMLIT_HOST}", f"--server.port={STREAMLIT_PORT}",
        "--server.enableCORS=false", "--server.enableXsrfProtection=true",
        "--server.enableWebsocketCompression=false",
        "--server.fileWatcherType=none",
        "--browser.gatherUsageStats=false", "--server.headless=true",
    ]


def streamlit_environment(base=None):
    """Limit native thread pools so encoding does not starve the WebUI."""
    env = dict(os.environ if base is None else base)
    env.setdefault("OMP_NUM_THREADS", "1")
    env.setdefault("OPENBLAS_NUM_THREADS", "1")
    env.setdefault("MKL_NUM_THREADS", "1")
    env.setdefault("NUMEXPR_NUM_THREADS", "1")
    return env


def wait_for_tcp(host, port, timeout, process):
    """Return True once Streamlit accepts connections, False if it dies or stalls."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def main():
    password = os.environ.pop("WEBUI_PASSWORD", "")
    if len(password) < 20 or "\n" in password or "\r" in password:
        sys.exit("Set WEBUI_PASSWORD in the host dashboard to a strong password of at least 20 characters.")
    raw_port = os.environ.get("PORT", "10000")
    if not re.fullmatch(r"[0-9]{1,5}", raw_port) or not 1 <= int(raw_port) <= 65535 or int(raw_port) == 8501:
        sys.exit("PORT must be a valid port other than 8501.")

    directory = Path(tempfile.mkdtemp(prefix="mpt-render-"))
    # nginx worker needs to read the password hash, not the password itself.
    directory.chmod(0o755)
    password_file = directory / "htpasswd"
    try:
        subprocess.run(["htpasswd", "-i", "-c", "-B", str(password_file), "operator"],
                       input=password + "\n", text=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=True)
    except (OSError, subprocess.CalledProcessError):
        sys.exit("Unable to initialize access protection; refusing to start.")
    password = None
    password_file.chmod(0o644)
    config = directory / "nginx.conf"
    config.write_text(
        nginx_config_text(int(raw_port), password_file, directory),
        encoding="utf-8",
    )
    streamlit = None
    nginx = None
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        streamlit = subprocess.Popen(streamlit_command(), env=streamlit_environment())
        if not wait_for_tcp(
            STREAMLIT_HOST, STREAMLIT_PORT, STREAMLIT_READY_TIMEOUT_SECONDS, streamlit
        ):
            sys.exit("A web process exited; stopping the service.")
        nginx = subprocess.Popen(["nginx", "-c", str(config), "-g", "daemon off;"])
        while not stopping and streamlit.poll() is None and nginx.poll() is None:
            time.sleep(0.5)
        if not stopping:
            sys.exit("A web process exited; stopping the service.")
    finally:
        for process in (nginx, streamlit):
            if process and process.poll() is None:
                process.terminate()
        for process in (nginx, streamlit):
            if process and process.poll() is None:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    main()
