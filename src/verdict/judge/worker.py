import logging
import os
import signal
import socket
import threading
from types import FrameType

from verdict.core.config import load_settings
from verdict.core.logs import configure_logging

logger = logging.getLogger(__name__)


def worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}"


def run() -> None:
    settings = load_settings()
    configure_logging(settings.log_level)
    stop = threading.Event()

    def request_stop(signum: int, _frame: FrameType | None) -> None:
        logger.info("stop requested", extra={"signal": signal.Signals(signum).name})
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    logger.info("worker idle", extra={"worker_id": worker_id()})
    stop.wait()
    logger.info("worker stopped", extra={"worker_id": worker_id()})
