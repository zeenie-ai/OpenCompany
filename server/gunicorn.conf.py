"""Gunicorn configuration for production deployment.

Reads settings from environment variables (same as config.py).
No hardcoded values - everything from .env file.

Usage:
    gunicorn main:app -c gunicorn.conf.py
"""

import os
import multiprocessing

# Load from environment (same vars used by config.py)
host = os.getenv("HOST", "0.0.0.0")
port = os.getenv("PORT", "3010")
workers_env = os.getenv("WORKERS", "0")  # 0 = auto-calculate
log_level = os.getenv("LOG_LEVEL", "INFO").lower()
debug = os.getenv("DEBUG", "false").lower() == "true"

# Bind - use HOST and PORT from .env
bind = f"{host}:{port}"

# Workers - from WORKERS env or auto-calculate
# WORKERS=0 means auto (2 * cpu + 1), WORKERS=N means use N
workers_count = int(workers_env)
workers = workers_count if workers_count > 0 else (multiprocessing.cpu_count() * 2 + 1)
worker_class = "uvicorn.workers.UvicornWorker"

# Timeouts - configurable via env
timeout = int(os.getenv("GUNICORN_TIMEOUT", "120"))
graceful_timeout = int(os.getenv("GUNICORN_GRACEFUL_TIMEOUT", "30"))
keepalive = int(os.getenv("GUNICORN_KEEPALIVE", "5"))

# Restart workers periodically to prevent memory leaks
max_requests = int(os.getenv("GUNICORN_MAX_REQUESTS", "10000"))
max_requests_jitter = int(os.getenv("GUNICORN_MAX_REQUESTS_JITTER", "1000"))

# Logging - use LOG_LEVEL from .env
accesslog = "-" if not debug else None
errorlog = "-"
loglevel = log_level

# Process naming
proc_name = "opencompany-backend"

# Preload app for faster worker startup (disable in debug for reload)
preload_app = not debug
