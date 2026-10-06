"""Timing events for the workbook cleaner, also written to a rotating file.

The service and pipeline already log ``workbook_clean_timing`` /
``cre_pipeline_timing`` to the normal loggers (the server terminal). This
module writes the same events, plus per-file GCS I/O timings, as one JSON
object per line to ``<repo>/logs/workbook_cleaner_timing.log`` so they can be
read after a benchmark (IMPL-20261002-03). ``/logs`` and ``*.log`` are already
in the repo's .gitignore.

Environment:
    WORKBOOK_CLEANER_TIMING_LOG  file path to use instead, or "off" to disable.

Never raises: if the directory cannot be created (e.g. a read-only image),
the events are dropped and a single warning is logged.
"""
from __future__ import annotations

import contextvars
import datetime as _dt
import json
import logging
import os
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Optional

LOGGER_NAME = "workbook_cleaner.timing_file"
DEFAULT_PATH = Path(__file__).resolve().parents[3] / "logs" / "workbook_cleaner_timing.log"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 5

# Request id shared by every event of one clean (set by the service).
request_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "workbook_clean_request_id", default=None,
)

_lock = threading.Lock()
_configured = False


def log_path() -> Optional[Path]:
    raw = (os.environ.get("WORKBOOK_CLEANER_TIMING_LOG") or "").strip()
    if raw.lower() in {"off", "0", "false", "none", "disabled"}:
        return None
    return Path(raw).expanduser() if raw else DEFAULT_PATH


def _logger() -> logging.Logger:
    global _configured
    logger = logging.getLogger(LOGGER_NAME)
    if _configured:
        return logger
    with _lock:
        if _configured:
            return logger
        logger.propagate = False  # file only; the terminal already has these events
        logger.setLevel(logging.INFO)
        path = log_path()
        if path is None:
            logger.addHandler(logging.NullHandler())
        else:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                handler = RotatingFileHandler(
                    path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8",
                    delay=True,
                )
                handler.setFormatter(logging.Formatter("%(message)s"))
                logger.addHandler(handler)
            except Exception as exc:  # read-only FS etc.: drop the events
                logger.addHandler(logging.NullHandler())
                logging.getLogger(__name__).warning(
                    "workbook_cleaner_timing_log_unavailable: %s: %s", type(exc).__name__, exc,
                )
        _configured = True
    return logger


def reset_for_tests() -> None:
    """Drop handlers so the next event re-reads WORKBOOK_CLEANER_TIMING_LOG."""
    global _configured
    with _lock:
        logger = logging.getLogger(LOGGER_NAME)
        for h in list(logger.handlers):
            logger.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass
        _configured = False


def log_timing(event: str, request_id: Optional[str] = None, **fields: Any) -> None:
    """Write one JSON line: {"ts", "event", "request_id", "thread", **fields}."""
    try:
        record = {
            "ts": _dt.datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "event": event,
            "request_id": request_id or request_id_var.get(),
            "pid": os.getpid(),
            "thread": threading.current_thread().name,
            **fields,
        }
        _logger().info(json.dumps(record, default=str, sort_keys=False))
    except Exception:  # timing must never break a request
        pass
