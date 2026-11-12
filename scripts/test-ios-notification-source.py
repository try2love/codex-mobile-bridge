#!/usr/bin/env python3
"""Check that production iOS notification attachments survive until scheduling."""
from pathlib import Path
import plistlib
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '.tmp/ios-notification-source-tests'
APP = WORK / 'SourceTests.app/Contents'
(APP / 'MacOS').mkdir(parents=True, exist_ok=True)
(APP / 'Resources').mkdir(exist_ok=True)
(APP / 'Info.plist').write_bytes(plistlib.dumps({
    'CFBundleIdentifier': 'io.github.try2love.codexbridge.notification-source-tests',
    'CFBundleExecutable': 'source-tests', 'CFBundlePackageType': 'APPL',
}))
for provider in ('codex', 'claude', 'deepseek', 'zcode', 'antigravity'):
    shutil.copyfile(ROOT / f'web/client-icons/{provider}.png', APP / f'Resources/{provider}.png')
# macOS does not accept the iOS image attachment type in this command-line
# harness. Replace only the OS data holders; exercise the production resource
# selection and file lifetime. Actual scheduling is verified on iPhone.
source = (ROOT / 'mobile/ios/BridgePreview/NotificationSource.swift').read_text().replace('import UserNotifications', '')
(WORK / 'source.swift').write_text("""import Foundation
final class UNMutableNotificationContent { var subtitle = ""; var attachments: [UNNotificationAttachment] = [] }
struct UNNotificationAttachment {
    let identifier: String; let url: URL
    init(identifier: String, url: URL) throws {
        guard FileManager.default.isReadableFile(atPath: url.path) else { throw CocoaError(.fileReadNoSuchFile) }
        self.identifier = identifier; self.url = url
    }
}
""" + source)
(WORK / 'main.swift').write_text('''import Foundation
for (provider, host, expected) in [("codex", "local", "Codex"), ("claude", "desktop:claude", "Claude"), ("deepseek", "desktop:deepseek", "DeepSeek Harness"), ("zcode", "desktop:zcode", "ZCode"), ("antigravity", "desktop:antigravity", "Antigravity"), ("unknown", "desktop:antigravity", "Antigravity"), ("unknown", "desktop:zcode", "ZCode"), ("unknown", "desktop:claude", "Claude"), ("unknown", "desktop:claude:" + String(repeating: "a", count: 32), "Claude"), ("unknown", "desktop:deepseek:" + String(repeating: "b", count: 32), "DeepSeek Harness")] {
    let content = UNMutableNotificationContent()
    NotificationSource.apply(content, provider: provider, host: host)
    precondition(content.subtitle == expected)
    precondition(content.attachments.count == 1, "Missing bundled source artwork")
    precondition(FileManager.default.isReadableFile(atPath: content.attachments[0].url.path), "Attachment was deleted before notification scheduling")
}
print("iOS source attachment file lifetime passed (OS notification types substituted)")
''')
subprocess.run(['xcrun', 'swiftc', '-module-cache-path', str(WORK / 'cache'),
                str(WORK / 'source.swift'), str(WORK / 'main.swift'),
                '-o', str(APP / 'MacOS/source-tests')], check=True)
subprocess.run([str(APP / 'MacOS/source-tests')], check=True)
