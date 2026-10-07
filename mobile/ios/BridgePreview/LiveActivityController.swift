import Foundation
import ActivityKit

@available(iOS 16.2, *)
@MainActor
final class LiveActivityController {
    static let shared = LiveActivityController()
    var pushAvailable = false
    var onPushToken: ((TaskActivity, String) -> Void)?
    private var tokenTask: Task<Void, Never>?
    private var observedActivity: String?
    private var wasRunning = false
    private var revision = 0
    private var current: Activity<TaskActivity>? { Activity<TaskActivity>.activities.first }
    var enabled: Bool { current != nil }
    func start(origin: String, thread: String, host: String, phase: String, demo: Bool = false) async throws {
        revision += 1; let ticket = revision
        for item in Activity<TaskActivity>.activities { await item.end(nil, dismissalPolicy: .immediate) }
        guard ticket == revision else { return }
        guard ActivityAuthorizationInfo().areActivitiesEnabled else { throw Failure.disabled }
        wasRunning = phase == "running"
        let activity = try Activity.request(attributes: TaskActivity(origin: origin, thread: thread, host: host, demo: demo), content: ActivityContent(state: .init(phase: phase, updatedAt: Date(), language: UserDefaults.standard.string(forKey: "bridge-language")), staleDate: Date().addingTimeInterval(45)), pushType: pushAvailable && !demo ? .token : nil)
        observeTokens(activity)
    }
    private func observeTokens(_ activity: Activity<TaskActivity>) {
        guard pushAvailable, !activity.attributes.demo else { return }
        if let token = activity.pushToken { onPushToken?(activity.attributes, token.map { String(format: "%02x", $0) }.joined()) }
        guard observedActivity != activity.id else { return }
        tokenTask?.cancel(); observedActivity = activity.id
        tokenTask = Task { for await token in activity.pushTokenUpdates { self.onPushToken?(activity.attributes, token.map { String(format: "%02x", $0) }.joined()) } }
    }
    func recoverPushTokens() { if let activity = current { observeTokens(activity) } }
    func update(origin: String, thread: String, host: String, phase: String) async {
        guard let item = current, !item.attributes.demo, item.attributes.origin == origin, item.attributes.thread == thread, item.attributes.host == host else { return }
        var phase = phase
        if phase == "running" { wasRunning = true }
        if phase == "ready" && (wasRunning || item.content.state.phase == "ended") { phase = "ended" }
        await item.update(ActivityContent(state: .init(phase: phase, updatedAt: Date(), language: UserDefaults.standard.string(forKey: "bridge-language")), staleDate: Date().addingTimeInterval(45)))
    }
    func pause() async {
        guard let item = current else { return }
        if pushAvailable && !item.attributes.demo { return }
        await item.update(ActivityContent(state: item.content.state, staleDate: Date()))
    }
    func stop() async {
        tokenTask?.cancel(); tokenTask = nil; observedActivity = nil
        revision += 1
        for item in Activity<TaskActivity>.activities { await item.end(nil, dismissalPolicy: .immediate) }
        wasRunning = false
    }
    enum Failure: LocalizedError {
        case disabled
        var errorDescription: String? { "请在系统设置中允许 Bridge Preview 的实时活动。" }
    }
}
