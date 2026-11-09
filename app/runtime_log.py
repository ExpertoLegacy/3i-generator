"""A small local log of startup, shutdown, and crashes. It never records anything a student wrote."""
from __future__ import annotations

import faulthandler
import logging
import sys
import threading
from logging.handlers import RotatingFileHandler

from app.session import data_dir
from app.version import VERSION

log = logging.getLogger("rrg")
_fault_file = None


def start() -> None:
    global _fault_file
    handler = RotatingFileHandler(data_dir() / "app.log", maxBytes=256_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False
    _fault_file = open(data_dir() / "crash.log", "a", encoding="utf-8")  # noqa: SIM115 - stays open for native crashes
    faulthandler.enable(_fault_file)
    sys.excepthook = lambda kind, value, trace: log.critical("unhandled %s", kind.__name__, exc_info=(kind, value, trace))
    threading.excepthook = lambda args: log.critical("thread %s failed: %s", args.thread.name if args.thread else "?", args.exc_type.__name__, exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
    log.info("start version=%s frozen=%s args=%s", VERSION, getattr(sys, "frozen", False), sys.argv[1:])
