import os

workers = int(os.environ.get("WEB_CONCURRENCY", "2"))
threads = int(os.environ.get("GUNICORN_THREADS", "4"))
worker_class = "gthread"
bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"

# The API only writes a row and enqueues; nothing here should take long.
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "30"))
graceful_timeout = 20
max_requests = 2000
max_requests_jitter = 200
forwarded_allow_ips = "*"
accesslog = "-"
errorlog = "-"
