"""Django settings for creator imports, outreach, and mailing."""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")
from shared.exceptions import install_exception_hooks

install_exception_hooks(PROJECT_ROOT)
project_venv_python = (
    PROJECT_ROOT
    / ".venv"
    / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
)
AUTOMATION_PYTHON_EXECUTABLE = os.getenv(
    "AUTOMATION_PYTHON_EXECUTABLE",
    str(project_venv_python if project_venv_python.is_file() else sys.executable),
)

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "development-only-change-me")
DEBUG = os.getenv("DJANGO_DEBUG", "true").lower() == "true"
ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv(
        "DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost"
    ).split(",")
    if host.strip()
]

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "tasks.apps.TasksConfig",
    "creator_contact.apps.CreatorContactConfig",
    "mailing.apps.MailingConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

database_setting = os.getenv("DATABASE_PATH", "storage/agent.db")
database_path = Path(database_setting)
if not database_path.is_absolute():
    database_path = PROJECT_ROOT / database_path

sqlite_options: dict[str, object] = {"timeout": 20}
if os.getenv("SQLITE_ENABLE_WAL", "true").lower() == "true":
    sqlite_options["init_command"] = (
        "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;"
    )

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": database_path,
        "OPTIONS": sqlite_options,
    }
}
LOG_RETENTION_DAYS = int(os.getenv("LOG_RETENTION_DAYS", "14"))

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek/deepseek-v4-flash")
DOM_FALLBACK_MODEL = os.getenv(
    "DOM_FALLBACK_MODEL",
    "deepseek/deepseek-v4-flash",
)
DOM_FALLBACK_TIMEOUT_SECONDS = int(
    os.getenv("DOM_FALLBACK_TIMEOUT_SECONDS", "45")
)
IMPORT_RULE_MODEL = os.getenv(
    "IMPORT_RULE_MODEL",
    "deepseek/deepseek-v4-flash",
)
OPENCODE_BINARY = os.getenv("OPENCODE_BINARY", "opencode")
TASK_TIMEOUT_SECONDS = int(os.getenv("TASK_TIMEOUT_SECONDS", "1800"))
IMPORT_RULE_TIMEOUT_SECONDS = int(
    os.getenv("IMPORT_RULE_TIMEOUT_SECONDS", "120")
)
IMPORT_MAX_FILE_SIZE_BYTES = int(
    os.getenv("IMPORT_MAX_FILE_SIZE_MB", "20")
) * 1024 * 1024
IMPORT_PREVIEW_ROWS = int(os.getenv("IMPORT_PREVIEW_ROWS", "20"))
IMPORT_PREVIEW_SCAN_ROWS = int(
    os.getenv("IMPORT_PREVIEW_SCAN_ROWS", "200")
)
IMPORT_PREVIEW_TTL_SECONDS = int(
    os.getenv("IMPORT_PREVIEW_TTL_SECONDS", "1800")
)
IMPORT_PREVIEW_MAX_ENTRIES = int(
    os.getenv("IMPORT_PREVIEW_MAX_ENTRIES", "20")
)
import_temp_setting = Path(
    os.getenv("IMPORT_TEMP_DIR", "temporary/creator-imports")
)
if not import_temp_setting.is_absolute():
    import_temp_setting = PROJECT_ROOT / import_temp_setting
IMPORT_TEMP_ROOT = import_temp_setting.resolve()

# Unconfirmed spreadsheets must stay in process memory. Django must not spool
# uploads to a persistent temporary file before the user confirms the preview.
FILE_UPLOAD_HANDLERS = [
    "django.core.files.uploadhandler.MemoryFileUploadHandler",
]
FILE_UPLOAD_MAX_MEMORY_SIZE = IMPORT_MAX_FILE_SIZE_BYTES
DATA_UPLOAD_MAX_MEMORY_SIZE = IMPORT_MAX_FILE_SIZE_BYTES + (1024 * 1024)
ZINIAO_CONTACT_STORE_ID = os.getenv("ZINIAO_CONTACT_STORE_ID", "").strip()
CREATOR_CONTACT_WORKER_HOST = os.getenv(
    "CREATOR_CONTACT_WORKER_HOST",
    "127.0.0.1",
).strip()
CREATOR_CONTACT_WORKER_PORT = int(
    os.getenv("CREATOR_CONTACT_WORKER_PORT", "16852")
)
CREATOR_CONTACT_WORKER_START_TIMEOUT_SECONDS = float(
    os.getenv("CREATOR_CONTACT_WORKER_START_TIMEOUT_SECONDS", "10")
)
EMAIL_WORKER_POLL_INTERVAL_SECONDS = float(
    os.getenv("EMAIL_WORKER_POLL_INTERVAL_SECONDS", "2")
)
EMAIL_WORKER_HOST = os.getenv(
    "EMAIL_WORKER_HOST",
    "127.0.0.1",
).strip()
EMAIL_WORKER_PORT = int(os.getenv("EMAIL_WORKER_PORT", "16853"))
ziniao_browser_status_setting = Path(
    os.getenv(
        "ZINIAO_BROWSER_STATUS_PATH",
        "temporary/ziniao-browser-status.json",
    )
)
if not ziniao_browser_status_setting.is_absolute():
    ziniao_browser_status_setting = (
        PROJECT_ROOT / ziniao_browser_status_setting
    )
ZINIAO_BROWSER_STATUS_PATH = ziniao_browser_status_setting.resolve()
ZINIAO_BROWSER_STATUS_PROBE_TIMEOUT_SECONDS = float(
    os.getenv("ZINIAO_BROWSER_STATUS_PROBE_TIMEOUT_SECONDS", "0.5")
)
# Creator collaboration email. Credentials stay in the local, ignored .env.
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = "smtp.gmail.com"
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_USE_SSL = False
EMAIL_HOST_USER = os.getenv("GMAIL_ADDRESS", "").strip()
EMAIL_HOST_PASSWORD = "".join(
    os.getenv("GMAIL_APP_PASSWORD", "").split()
)
DEFAULT_FROM_EMAIL = (
    f"Jackson | Vaelos <{EMAIL_HOST_USER}>"
    if EMAIL_HOST_USER
    else "Jackson | Vaelos"
)
EMAIL_TIMEOUT = int(os.getenv("EMAIL_TIMEOUT_SECONDS", "30"))
CREATOR_EMAIL_DAILY_LIMIT = int(
    os.getenv("CREATOR_EMAIL_DAILY_LIMIT", "100")
)
CREATOR_EMAIL_SEND_INTERVAL_MIN_SECONDS = float(
    os.getenv("CREATOR_EMAIL_SEND_INTERVAL_MIN_SECONDS", "30")
)
CREATOR_EMAIL_SEND_INTERVAL_MAX_SECONDS = float(
    os.getenv("CREATOR_EMAIL_SEND_INTERVAL_MAX_SECONDS", "60")
)
CREATOR_EMAIL_MAX_ATTEMPTS_PER_DAY = int(
    os.getenv("CREATOR_EMAIL_MAX_ATTEMPTS_PER_DAY", "3")
)
EMAIL_TEMPLATE_IMAGE_MAX_BYTES = int(
    os.getenv("EMAIL_TEMPLATE_IMAGE_MAX_MB", "5")
) * 1024 * 1024
EMAIL_TEMPLATE_TOTAL_IMAGE_MAX_BYTES = int(
    os.getenv("EMAIL_TEMPLATE_TOTAL_IMAGE_MAX_MB", "20")
) * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = max(
    DATA_UPLOAD_MAX_MEMORY_SIZE,
    EMAIL_TEMPLATE_TOTAL_IMAGE_MAX_BYTES + (1024 * 1024),
)
mailing_media_setting = Path(
    os.getenv("MAILING_MEDIA_ROOT", "storage/mailing-media")
)
if not mailing_media_setting.is_absolute():
    mailing_media_setting = PROJECT_ROOT / mailing_media_setting
MEDIA_ROOT = mailing_media_setting.resolve()
MEDIA_URL = "/media/"
creator_email_image_setting = Path(
    os.getenv(
        "CREATOR_EMAIL_IMAGE_PATH",
        "web-ui/mailing/assets/FR7A6183.png",
    )
)
if not creator_email_image_setting.is_absolute():
    creator_email_image_setting = PROJECT_ROOT / creator_email_image_setting
CREATOR_EMAIL_IMAGE_PATH = creator_email_image_setting.resolve()
SPORTS_JACKET_URL = os.getenv("SPORTS_JACKET_URL", "").strip()
WOMENS_SHORTS_URL = os.getenv("WOMENS_SHORTS_URL", "").strip()
