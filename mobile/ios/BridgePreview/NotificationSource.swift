import Foundation
import UserNotifications

enum NotificationSource {
    static func apply(_ content: UNMutableNotificationContent, provider supplied: String?, host: String?) {
        let family = host?.range(of: "^desktop:(claude|deepseek)(:[0-9a-f]{32})?$", options: .regularExpression) != nil ? host!.split(separator: ":")[1].description : host == "desktop:zcode" ? "zcode" : "codex"
        let provider = ["codex", "claude", "deepseek", "zcode"].contains(supplied ?? "") ? supplied! : family
        content.subtitle = ["codex": "Codex", "claude": "Claude", "deepseek": "DeepSeek Harness", "zcode": "ZCode"][provider]!
        // iOS retains the sending App's system icon; source artwork is an
        // attachment. It is bundled, never fetched from an arbitrary push URL.
        guard let source = Bundle.main.url(forResource: provider, withExtension: "png") else { return }
        do {
            // Scheduling (or extension completion) validates the file later.
            // Bundle resources are copied by iOS; a deleted temporary copy
            // invalidates the entire notification before it can be delivered.
            content.attachments = [try UNNotificationAttachment(identifier: provider, url: source)]
        } catch { /* Keep the original notification if the attachment is unavailable. */ }
    }
}
