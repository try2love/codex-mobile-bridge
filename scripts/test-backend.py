#!/usr/bin/env python3
"""Discover backend tests; --source-only excludes native compilation/interpreting."""
import argparse
from pathlib import Path
import os
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-only', action='store_true', help='Skip the two Swift execution fixtures')
    parser.add_argument('-p', '--pattern', default='test_*.py', help='unittest filename pattern')
    args = parser.parse_args()
    os.chdir(ROOT)
    sys.path[:0] = [str(ROOT/'tests'), str(ROOT)]
    if args.source_only:
        import test_claude_native_helper
        for cls in (test_claude_native_helper.NativeConsoleSelectors, test_claude_native_helper.DesktopSessionGuards):
            cls.__unittest_skip__ = True
            cls.__unittest_skip_why__ = 'Source-only run excludes Swift compilation and interpretation'
    suite = unittest.defaultTestLoader.discover(str(ROOT/'tests'), pattern=args.pattern)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
