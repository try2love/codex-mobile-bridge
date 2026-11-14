import SwiftUI
import UserNotifications

@main
struct CodexMobileApp: App {
    @UIApplicationDelegateAdaptor(DownloadAppDelegate.self) private var delegate
    @StateObject private var connection = ConnectionModel()
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            ContentView(model: connection)
                .preferredColorScheme(.light)
                .onChange(of: scenePhase) { phase in
                    if phase == .background {
                        connection.appDidEnterBackground()
                        BackgroundDownloads.shared.pauseAll()
                    }
                    if phase == .active { connection.appDidBecomeActive() }
                }
                .onChange(of: connection.showSettings) { showing in
                    if !showing { connection.recoverWebContentIfNeeded() }
                }
        }
    }
}

final class DownloadAppDelegate: NSObject, UIApplicationDelegate {
    func applicationDidEnterBackground(_ application: UIApplication) {
        BackgroundDownloads.shared.pauseAll()
    }
    func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        TaskNotifications.shared.install()
        BackgroundDownloads.shared.reconnect(); return true
    }
    func application(_ application: UIApplication, handleEventsForBackgroundURLSession identifier: String, completionHandler: @escaping () -> Void) {
        guard identifier == BackgroundDownloads.identifier else { completionHandler(); return }
        BackgroundDownloads.shared.completion = completionHandler
        BackgroundDownloads.shared.reconnect()
    }
}

struct TaskNotificationTarget: Equatable {
    let origin: String
    let id: String
    let host: String
}

@MainActor
final class TaskNotifications: NSObject, UNUserNotificationCenterDelegate {
    static let shared = TaskNotifications()
    private let center = UNUserNotificationCenter.current()
    private let defaults = UserDefaults.standard
    private let preferenceKey = "systemTaskNotificationsEnabled"
    private var requestingPermission = false
    private(set) var enabled = false
    var pendingTarget: TaskNotificationTarget?
    var onChange: (() -> Void)?

    func install() { center.delegate = self }

    private func isAllowed(_ status: UNAuthorizationStatus) -> Bool {
        status == .authorized || status == .provisional || status == .ephemeral
    }

    func refresh() async {
        let settings = await center.notificationSettings()
        enabled = (defaults.object(forKey: preferenceKey) as? Bool) != false
            && isAllowed(settings.authorizationStatus)
        onChange?()
    }

    func prepare() async {
        install()
        guard defaults.object(forKey: preferenceKey) == nil else { await refresh(); return }
        await setEnabled(true)
    }

    func setEnabled(_ value: Bool, openSettings: Bool = false) async {
        guard !requestingPermission else { return }
        defaults.set(value, forKey: preferenceKey)
        if value {
            requestingPermission = true
            defer { requestingPermission = false }
            let settings = await center.notificationSettings()
            if settings.authorizationStatus == .notDetermined {
                _ = try? await center.requestAuthorization(options: [.alert, .sound])
            } else if settings.authorizationStatus == .denied && openSettings,
                      let url = URL(string: UIApplication.openSettingsURLString) {
                _ = await UIApplication.shared.open(url)
            }
        }
        await refresh()
    }

    func post(origin: String, id: String, host: String, title: String,
              message: String, eventKey: String, sound: Bool) async -> Bool {
        let settings = await center.notificationSettings()
        guard (defaults.object(forKey: preferenceKey) as? Bool) != false,
              isAllowed(settings.authorizationStatus) else { return false }
        let protection = UIApplication.shared.beginBackgroundTask(withName: "Deliver task reminder", expirationHandler: nil)
        defer { if protection != .invalid { UIApplication.shared.endBackgroundTask(protection) } }
        return await withCheckedContinuation { continuation in
            TaskMonitorBridge.deliver(["origin": origin, "id": id, "host": host, "title": title,
                                      "message": message, "eventKey": eventKey, "sound": sound]) {
                continuation.resume(returning: $0)
            }
        }
    }

    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        var options: UNNotificationPresentationOptions = [.banner, .list]
        if notification.request.content.sound != nil { options.insert(.sound) }
        completionHandler(options)
    }

    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse, withCompletionHandler completionHandler: @escaping () -> Void) {
        guard response.actionIdentifier == UNNotificationDefaultActionIdentifier else { completionHandler(); return }
        let info = response.notification.request.content.userInfo
        guard let origin = info["origin"] as? String, origin.count <= 2048,
              let id = info["id"] as? String, UUID(uuidString: id) != nil,
              let host = info["host"] as? String, !host.isEmpty, host.count <= 512 else {
            completionHandler(); return
        }
        Task { @MainActor in
            self.pendingTarget = TaskNotificationTarget(origin: origin, id: id, host: host)
            self.onChange?()
            completionHandler()
        }
    }
}
