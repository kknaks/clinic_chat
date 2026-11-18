"""uv run python -m clinic_chat.consult [--port N] — 호스트는 127.0.0.1 고정."""

import argparse

from clinic_chat.consult.server import main

ap = argparse.ArgumentParser(prog="clinic_chat.consult")
ap.add_argument("--port", type=int, default=None)
main(port=ap.parse_args().port)
