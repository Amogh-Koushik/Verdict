import argparse

import uvicorn

from verdict.core.config import load_settings
from verdict.core.logs import configure_logging


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m verdict.web")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()

    # Configure logging here as well as in create_app so the supervisor process logs JSON too.
    configure_logging(load_settings().log_level)
    uvicorn.run(
        "verdict.web.main:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        workers=args.workers,
        log_config=None,
    )


if __name__ == "__main__":
    main()
