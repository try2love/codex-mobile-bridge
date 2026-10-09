"""Run the real macOS Console selector regression without controlling any app."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('swift'), 'Swift interpreter required')
class DesktopSessionGuards(unittest.TestCase):
    def test_pure_state_guards_without_starting_native_helper_or_reading_desktop(self):
        source = (ROOT/'bridge/integrations/native/claude-helper.swift').read_text()
        pure = source[source.index('func desktopIssue('):source.index('func currentDesktopIssue(')]
        fixture = '''import Foundation
struct SetupError: Error { let state: String; let reason: String }
''' + pure + '''
func state(_ has: Bool = true, _ console: Bool? = true, _ login: Bool? = true,
           _ locked: Bool? = false, _ front: String? = "com.anthropic.claudefordesktop", _ saver: Bool = false) -> String? {
    desktopIssue(hasSession: has, onConsole: console, loginDone: login, locked: locked, foreground: front, screenSaverWindow: saver)?.state
}
precondition(state() == nil)
precondition(state(true, true, true, nil) == nil)
precondition(state(true, true, true, false, "com.apple.ScreenSaver.Engine") == "needs-screen-saver")
precondition(state(true, true, true, nil, "com.anthropic.claudefordesktop", true) == "needs-screen-saver")
precondition(state(true, true, true, true, "com.apple.ScreenSaver.Engine", true) == "needs-unlock")
precondition(state(true, true, true, nil, "com.apple.loginwindow") == "needs-desktop")
precondition(state(false, nil, nil, nil, nil) == "needs-desktop")
precondition(state(true, false, true, false, "com.apple.ScreenSaver.Engine") == "needs-desktop")
precondition(state(true, true, false) == "needs-desktop")
precondition(state(true, nil, nil) == "needs-desktop")
precondition(state(true, true, true, nil, nil) == "needs-desktop")
precondition(isScreenSaverWindow(bundle: "com.apple.ScreenSaver.Engine", layer: 1000, onScreen: true, saverLevel: 1000))
precondition(!isScreenSaverWindow(bundle: "com.apple.ScreenSaver.Engine", layer: 0, onScreen: true, saverLevel: 1000))
precondition(!isScreenSaverWindow(bundle: "com.apple.ScreenSaver.Engine", layer: 1000, onScreen: false, saverLevel: 1000))
precondition(!isScreenSaverWindow(bundle: "com.apple.WallpaperAgent", layer: 1000, onScreen: true, saverLevel: 1000))
print("15 desktop-state fixtures passed without GUI APIs")
'''
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            folder = Path(folder); script = folder/'guards.swift'; script.write_text(fixture)
            result = subprocess.run(['swift', '-module-cache-path', str(folder/'modules'), str(script)],
                                    capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('15 desktop-state fixtures passed', result.stdout)


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('swiftc'), 'macOS Swift compiler required')
class NativeConsoleSelectors(unittest.TestCase):
    def test_observed_console_selectors_submission_and_window_guards(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            folder = Path(folder)
            helper = folder/'claude-helper'
            built = subprocess.run(['swiftc', '-module-cache-path', str(folder/'modules'),
                                    str(ROOT/'bridge/integrations/native/claude-helper.swift'), '-o', str(helper)],
                                   capture_output=True, text=True, timeout=90)
            self.assertEqual(built.returncode, 0, built.stderr)
            result = subprocess.run([str(helper), '--self-check'], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['setupState'], 'ready', result.stdout)
