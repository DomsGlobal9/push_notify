"""Push Gateway settings.

Everything secret comes from the environment. Locally, leave DATABASE_URL unset
and the gateway uses a local Postgres database called `push_gateway` (same idea
as CRM-B's USE_LOCAL_DB). On Render, DATABASE_URL comes from the managed
Postgres instance via render.yaml.
"""

import getpass
import os
import sys
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from django.templatetags.static import static
from django.urls import reverse_lazy

BASE_DIR = Path(__file__).resolve().parent.parent
RUNNING_TESTS = len(sys.argv) > 1 and sys.argv[1] == "test"


def _bool(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


DEBUG = _bool("DEBUG", False)

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY") or (
    "dev-insecure-key" if (DEBUG or RUNNING_TESTS) else None
)
if not SECRET_KEY:
    raise ImproperlyConfigured("DJANGO_SECRET_KEY is required when DEBUG is off.")

# 32 random bytes, base64. Encrypts provider credentials and tenant identity secrets.
MASTER_ENCRYPTION_KEY = os.environ.get("MASTER_ENCRYPTION_KEY") or (
    "ZGV2LW9ubHktbWFzdGVyLWtleS0zMi1ieXRlcyEhISE=" if (DEBUG or RUNNING_TESTS) else None
)
if not MASTER_ENCRYPTION_KEY:
    raise ImproperlyConfigured("MASTER_ENCRYPTION_KEY is required when DEBUG is off.")

# Bearer token for the /admin-api/ automation endpoints. Empty disables them.
ADMIN_API_TOKEN = os.environ.get("ADMIN_API_TOKEN", "")

ALLOWED_HOSTS = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "*").split(",") if h.strip()]
if os.environ.get("RENDER_EXTERNAL_HOSTNAME"):
    ALLOWED_HOSTS.append(os.environ["RENDER_EXTERNAL_HOSTNAME"])
CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()
]
if os.environ.get("RENDER_EXTERNAL_HOSTNAME"):
    CSRF_TRUSTED_ORIGINS.append(f"https://{os.environ['RENDER_EXTERNAL_HOSTNAME']}")

INSTALLED_APPS = [
    # Unfold (modern admin theme) must come before the admin itself. BasicAppConfig
    # keeps our own GatewayAdminSite (an UnfoldAdminSite) instead of replacing it.
    "unfold.apps.BasicAppConfig",
    "unfold.contrib.filters",
    "unfold.contrib.forms",
    "gateway.apps.GatewayAdminConfig",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "src.tenants_app",
    "src.devices_app",
    "src.notifications_app",
    "src.dispatch_app",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "gateway.urls"
WSGI_APPLICATION = "gateway.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

if os.environ.get("DATABASE_URL"):
    DATABASES = {
        "default": dj_database_url.parse(
            os.environ["DATABASE_URL"], conn_max_age=600, conn_health_checks=True
        )
    }
    # Supabase transaction pooler (port 6543): each transaction may run on a different
    # server connection, so Django must not hold server-side cursors across them.
    # The gateway uses no session state (no SET, LISTEN or advisory locks), so it is safe here.
    if str(DATABASES["default"].get("PORT")) == "6543":
        DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True
else:
    if not (DEBUG or RUNNING_TESTS):
        raise ImproperlyConfigured("DATABASE_URL is required when DEBUG is off.")
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("LOCAL_DB_NAME", "push_gateway"),
            "USER": os.environ.get("LOCAL_DB_USER", getpass.getuser()),
            "PASSWORD": os.environ.get("LOCAL_DB_PASSWORD", ""),
            "HOST": os.environ.get("LOCAL_DB_HOST", "localhost"),
            "PORT": os.environ.get("LOCAL_DB_PORT", "5432"),
        }
    }

# Admin users can message every user of every product: refuse weak passwords
# (applies when a password is set or changed, not to ones already stored).
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = False
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if (DEBUG or RUNNING_TESTS)
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        )
    },
}

if not (DEBUG or RUNNING_TESTS):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = _bool("SECURE_SSL_REDIRECT", True)
    SECURE_REDIRECT_EXEMPT = [r"^healthz$"]  # Render's health checker calls over plain HTTP
    SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", str(30 * 24 * 3600)))
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "src.core.authentication.ApiKeyAuthentication",
        "src.core.authentication.AdminTokenAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["src.core.permissions.TenantKey"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "EXCEPTION_HANDLER": "src.core.exceptions.api_exception_handler",
    "UNAUTHENTICATED_USER": None,
    "DEFAULT_THROTTLE_RATES": {
        "tenant_send": os.environ.get("THROTTLE_SEND", "600/min"),
        "tenant_device": os.environ.get("THROTTLE_DEVICE", "6000/min"),
    },
}

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


# ---- dashboard look (django-unfold) ----
def _admin(name):
    return reverse_lazy(f"admin:{name}")


UNFOLD = {
    "SITE_TITLE": "Push Gateway",
    "SITE_HEADER": "Push Gateway",
    "SITE_SUBHEADER": "Notifications for every app",
    "SITE_SYMBOL": "notifications_active",
    "SITE_URL": "/admin/",
    "SHOW_VIEW_ON_SITE": False,
    "SHOW_HISTORY": True,
    "BORDER_RADIUS": "8px",
    "STYLES": [lambda request: static("gateway/theme.css")],
    # Tailwind indigo.
    "COLORS": {
        "primary": {
            "50": "oklch(96.2% .018 272.314)", "100": "oklch(93% .034 272.788)",
            "200": "oklch(87% .065 274.039)", "300": "oklch(78.5% .115 274.713)",
            "400": "oklch(67.3% .182 276.935)", "500": "oklch(58.5% .233 277.117)",
            "600": "oklch(51.1% .262 276.966)", "700": "oklch(45.7% .24 277.023)",
            "800": "oklch(39.8% .195 277.366)", "900": "oklch(35.9% .144 278.697)",
            "950": "oklch(25.7% .09 281.288)",
        },
    },
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": False,
        "navigation": [
            {
                "title": "Monitor",
                "items": [
                    {"title": "Overview", "icon": "space_dashboard", "link": _admin("index")},
                    {"title": "Notifications", "icon": "send",
                     "link": _admin("notifications_app_notification_changelist")},
                    {"title": "Devices", "icon": "smartphone", "link": _admin("devices_app_device_changelist")},
                    {"title": "Queue", "icon": "pending_actions", "link": _admin("dispatch_app_job_changelist")},
                ],
            },
            {
                "title": "Setup",
                "items": [
                    {"title": "Products", "icon": "apps", "link": _admin("tenants_app_tenant_changelist")},
                    {"title": "Apps", "icon": "install_mobile", "link": _admin("tenants_app_app_changelist")},
                    {"title": "Credentials", "icon": "key", "link": _admin("tenants_app_credential_changelist")},
                ],
            },
            {
                "title": "Access",
                "items": [
                    {"title": "Admin users", "icon": "manage_accounts", "link": _admin("auth_user_changelist")},
                ],
            },
            {
                "title": "Help",
                "items": [
                    {"title": "Guide", "icon": "menu_book", "link": _admin("help")},
                    {"title": "Getting started", "icon": "rocket_launch",
                     "link": reverse_lazy("admin:help-topic", args=["getting-started"])},
                    {"title": "Add a new app", "icon": "add_circle",
                     "link": reverse_lazy("admin:help-topic", args=["add-new-app"])},
                    {"title": "Where keys come from", "icon": "vpn_key",
                     "link": reverse_lazy("admin:help-topic", args=["keys"])},
                    {"title": "Troubleshooting", "icon": "build",
                     "link": reverse_lazy("admin:help-topic", args=["troubleshooting"])},
                ],
            },
        ],
    },
}

# ---- dispatcher tuning (see src/dispatch_app/dispatcher.py) ----
PUSH = {
    "BATCH_SIZE": int(os.environ.get("PUSH_BATCH_SIZE", "500")),
    "SEND_THREADS": int(os.environ.get("PUSH_SEND_THREADS", "64")),
    "MAX_SEND_ATTEMPTS": int(os.environ.get("PUSH_MAX_SEND_ATTEMPTS", "5")),
    "STALE_JOB_MINUTES": int(os.environ.get("PUSH_STALE_JOB_MINUTES", "10")),
    "FAILURE_RETENTION_DAYS": int(os.environ.get("PUSH_FAILURE_RETENTION_DAYS", "14")),
    "NOTIFICATION_RETENTION_DAYS": int(os.environ.get("PUSH_NOTIFICATION_RETENTION_DAYS", "90")),
    "POLL_SECONDS": float(os.environ.get("PUSH_POLL_SECONDS", "1.0")),
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
    "loggers": {"httpx": {"level": "WARNING"}, "httpcore": {"level": "WARNING"}},
}
