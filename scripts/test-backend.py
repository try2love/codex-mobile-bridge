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
    parser.add_argument('--source-only', action='store_true', help='Skip Swift and Windows native execution fixtures')
    parser.add_argument('-p', '--pattern', default='test_*.py', help='unittest filename pattern')
    args = parser.parse_args()
    os.chdir(ROOT)
    sys.path[:0] = [str(ROOT/'tests'), str(ROOT)]
    if args.source_only:
        import test_claude_native_helper
        import test_codex_native_quit
        import test_windows_force_exit_native
        import test_windows_preview_launcher
        for cls in (test_claude_native_helper.NativeConsoleSelectors,
                    test_claude_native_helper.DesktopSessionGuards,
                    test_claude_native_helper.WindowsNativeWindowSelectors,
                    test_codex_native_quit.CodexNativeQuit,
                    test_windows_force_exit_native.WindowsForceExitNativeTests,
                    test_windows_preview_launcher.PreviewLauncherTest):
            cls.__unittest_skip__ = True
            cls.__unittest_skip_why__ = 'Source-only run excludes native compilation and execution'
    suite = unittest.defaultTestLoader.discover(str(ROOT/'tests'), pattern=args.pattern)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
