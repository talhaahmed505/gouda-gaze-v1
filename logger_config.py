"""
Centralized logging configuration for Gouda Gaze.

Loggers:
  gouda_gaze  — general app events  → logs/app.log + logs/app-error.log
  http        — every HTTP request  → logs/http.log
  auth        — auth/admin events   → logs/auth.log
  ptz         — PTZ commands        → logs/ptz.log
  privacy     — privacy toggle      → logs/privacy.log

All files rotate at 5 MB, keeping 3 backups.

Identity injection:
  UserContextFilter appends the current user's identity to every log record
  during a Flask request, so PTZ/privacy/snapshot logs get the user field
  automatically without touching their call sites.

Format:
  YYYY-MM-DD HH:MM:SS | LEVEL    | logger | message | user | ip
  The http logger writes its own fixed format; other loggers write
  message only and rely on the filter to append user.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


# ── Constants ─────────────────────────────────────────────────────────────────

LOG_DIR        = Path("logs")
MAX_BYTES      = 5 * 1024 * 1024   # 5 MB
BACKUP_COUNT   = 3

_BASE_FMT  = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FMT  = "%Y-%m-%d %H:%M:%S"


# ── User context filter ───────────────────────────────────────────────────────

class UserContextFilter(logging.Filter):
    """
    Appends '| user@example.com' to every log record that was emitted
    during a Flask request. Falls back to '[anonymous]' when there is no
    active request or the user is not authenticated.

    Applied to all loggers except 'http' (which builds its own line).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            from flask import has_request_context, request as flask_request
            from flask_login import current_user
            if has_request_context() and current_user.is_authenticated:
                record.msg = f"{record.msg} | {current_user.email}"
            elif has_request_context():
                record.msg = f"{record.msg} | [anonymous]"
        except Exception:
            pass   # never let logging machinery crash the app
        return True


# ── Handler factory ───────────────────────────────────────────────────────────

def _rotating(filename: str, level: int, formatter: logging.Formatter) -> RotatingFileHandler:
    handler = RotatingFileHandler(
        LOG_DIR / filename,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(formatter)
    return handler


# ── Setup ─────────────────────────────────────────────────────────────────────

def setup_loggers() -> tuple:
    LOG_DIR.mkdir(exist_ok=True)

    base_fmt  = logging.Formatter(_BASE_FMT, datefmt=_DATE_FMT)
    # http uses a plain message format — the after_request hook builds the full line
    http_fmt  = logging.Formatter("%(asctime)s | %(message)s", datefmt=_DATE_FMT)

    user_filter = UserContextFilter()

    # ── App logger ────────────────────────────────────────
    app_logger = logging.getLogger("gouda_gaze")
    app_logger.setLevel(logging.DEBUG)
    app_logger.addFilter(user_filter)
    app_logger.addHandler(_rotating("app.log",       logging.DEBUG, base_fmt))
    app_logger.addHandler(_rotating("app-error.log", logging.ERROR, base_fmt))
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(base_fmt)
    app_logger.addHandler(console)

    # ── HTTP access logger ────────────────────────────────
    http_logger = logging.getLogger("http")
    http_logger.setLevel(logging.INFO)
    http_logger.addHandler(_rotating("http.log", logging.INFO, http_fmt))

    # ── Auth event logger ─────────────────────────────────
    auth_logger = logging.getLogger("auth")
    auth_logger.setLevel(logging.INFO)
    auth_logger.addHandler(_rotating("auth.log", logging.INFO, base_fmt))

    # ── PTZ logger ────────────────────────────────────────
    ptz_logger = logging.getLogger("ptz")
    ptz_logger.setLevel(logging.DEBUG)
    ptz_logger.addFilter(user_filter)
    ptz_logger.addHandler(_rotating("ptz.log", logging.DEBUG, base_fmt))

    # ── Privacy logger ────────────────────────────────────
    privacy_logger = logging.getLogger("privacy")
    privacy_logger.setLevel(logging.INFO)
    privacy_logger.addFilter(user_filter)
    privacy_logger.addHandler(_rotating("privacy.log", logging.INFO, base_fmt))

    return app_logger, http_logger, auth_logger, ptz_logger, privacy_logger


def get_loggers() -> tuple:
    """Return (app, http, auth, ptz, privacy) loggers; initialise on first call."""
    app_logger     = logging.getLogger("gouda_gaze")
    http_logger    = logging.getLogger("http")
    auth_logger    = logging.getLogger("auth")
    ptz_logger     = logging.getLogger("ptz")
    privacy_logger = logging.getLogger("privacy")

    if not app_logger.handlers:
        setup_loggers()

    return app_logger, http_logger, auth_logger, ptz_logger, privacy_logger