import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import uvicorn
from fastapi import FastAPI


@contextmanager
def serve(app: FastAPI) -> Iterator[str]:
    """Run `app` on a real Uvicorn server in a thread; yield its base URL."""
    # The test client buffers whole responses, so an endless event stream needs a real server.
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    host, port = sock.getsockname()
    server = uvicorn.Server(uvicorn.Config(app, log_config=None, lifespan="on"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("test server did not start")
        time.sleep(0.05)
    try:
        yield f"http://{host}:{port}"
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
