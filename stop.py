#!/usr/bin/env python3
"""Ask this gateway to shut down gracefully on Windows and macOS."""
import argparse
from pathlib import Path

from bridge.app.lifecycle import request_stop


def main():
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description='Stop this Codex mobile gateway')
    parser.add_argument('--config', type=Path, default=root / '.local/config.json')
    args = parser.parse_args()
    try:
        request_stop(args.config.parent)
    except RuntimeError as exc:
        parser.exit(1, str(exc) + '\n')
    print('Gateway stopped.')


if __name__ == '__main__':
    main()
