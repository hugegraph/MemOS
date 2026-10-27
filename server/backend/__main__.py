"""Run with python -m server.backend from the Dashboard repository root."""

import argparse

import uvicorn
from pydantic import ValidationError

from .app import create_app
from .settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the independent MemOS Dashboard backend")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: loopback)")
    parser.add_argument("--port", type=int, default=5281, help="Bind port (default: 5281)")
    args = parser.parse_args()
    try:
        settings = Settings.from_env()
    except ValidationError:
        parser.error("Invalid backend configuration; check DASHBOARD_UPSTREAM_URL and timeout")
    # Invitation URLs contain a capability token; never persist raw request URLs.
    uvicorn.run(create_app(settings), host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
