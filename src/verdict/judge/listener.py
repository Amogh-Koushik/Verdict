import logging
import time

import psycopg
from sqlalchemy.engine import make_url

from verdict.core.events import QUEUE_CHANNEL

logger = logging.getLogger(__name__)


class QueueListener:
    """Waits for NOTIFY on the queue channel; the wait's timeout doubles as the poll interval."""

    def __init__(self, database_url: str) -> None:
        # psycopg wants a plain postgresql:// URL, without SQLAlchemy's +psycopg driver suffix.
        url = make_url(database_url).set(drivername="postgresql")
        self._conninfo = url.render_as_string(hide_password=False)
        self._connection: psycopg.Connection[tuple[object, ...]] | None = None

    def wait(self, timeout_seconds: float) -> None:
        """Return on the first notification, or after `timeout_seconds` without one."""
        try:
            connection = self._connect()
            for _ in connection.notifies(timeout=timeout_seconds, stop_after=1):
                pass
        except psycopg.Error:
            # Notifications sent while reconnecting are lost; the next poll finds that work.
            logger.warning("queue listener lost its connection", exc_info=True)
            self.close()
            time.sleep(timeout_seconds)

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def _connect(self) -> psycopg.Connection[tuple[object, ...]]:
        if self._connection is None or self._connection.closed:
            self._connection = psycopg.connect(self._conninfo, autocommit=True, connect_timeout=3)
            self._connection.execute(f"LISTEN {QUEUE_CHANNEL}")
        return self._connection
