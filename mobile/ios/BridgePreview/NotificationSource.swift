import Foundation
import UserNotifications

enum NotificationSource {
    static func apply(_ content: UNMutableNotificationContent, provider supplied: String?, host: String?) {
        let provider = ["codex", "claude", "deepseek"].contains(supplied ?? "") ? supplied! : host == "desktop:claude" ? "claude" : host == "desktop:deepseek" ? "deepseek" : "codex"
        content.subtitle = ["codex": "Codex", "claude": "Claude", "deepseek": "DeepSeek Harness"][provider]!
        // iOS retains the sending App's system icon; source artwork is an
        // attachment. It is bundled, never fetched from an arbitrary push URL.
        guard let source = Bundle.main.url(forResource: provider, withExtension: "png") else { return }
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let copy = directory.appendingPathComponent(provider + ".png")
            try FileManager.default.copyItem(at: source, to: copy)
            content.attachments = [try UNNotificationAttachment(identifier: provider, url: copy)]
        } catch { /* Keep the original notification if the attachment is unavailable. */ }
    }
}
