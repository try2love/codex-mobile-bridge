import UserNotifications

final class NotificationService: UNNotificationServiceExtension {
    private var handler: ((UNNotificationContent) -> Void)?
    private var content: UNNotificationContent?
    override func didReceive(_ request: UNNotificationRequest, withContentHandler contentHandler: @escaping (UNNotificationContent) -> Void) {
        handler = contentHandler; content = request.content
        if let updated = request.content.mutableCopy() as? UNMutableNotificationContent {
            NotificationSource.apply(updated, provider: updated.userInfo["provider"] as? String, host: updated.userInfo["host"] as? String)
            content = updated
        }
        finish()
    }
    private func finish() { if let handler, let content { self.handler = nil; handler(content) } }
    override func serviceExtensionTimeWillExpire() { finish() }
}
