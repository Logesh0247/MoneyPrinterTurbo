"""Render-only authenticated reverse proxy for the Streamlit WebUI.

WEBUI_PASSWORD is supplied through Render's environment dashboard, never the
repository. The proxy and local-only Streamlit process run in the same service.
"""
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time


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
    config.write_text(f'''worker_processes 1;
pid {directory}/nginx.pid;
error_log /dev/stderr warn;
events {{ worker_connections 256; }}
http {{
  access_log off;
  client_max_body_size 200m;
  server {{
    listen 0.0.0.0:{int(raw_port)};
    location = /_render_health {{ access_log off; return 200 "ok\\n"; }}
    location / {{
      auth_basic "MoneyPrinterTurbo";
      auth_basic_user_file {password_file};
      proxy_pass http://127.0.0.1:8501;
      proxy_http_version 1.1;
      proxy_set_header Host $http_host;
      proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;
      proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
      proxy_set_header Upgrade $http_upgrade;
      proxy_set_header Connection "upgrade";
      proxy_read_timeout 86400s;
      proxy_buffering off;
    }}
  }}
}}
''', encoding="utf-8")
    streamlit = None
    nginx = None
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        streamlit = subprocess.Popen([
            sys.executable, "-m", "streamlit", "run", "webui/Main.py",
            "--server.address=127.0.0.1", "--server.port=8501",
            "--server.enableCORS=true", "--server.enableXsrfProtection=true",
            "--browser.gatherUsageStats=false", "--server.headless=true",
        ])
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
