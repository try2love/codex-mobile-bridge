#!/usr/bin/env python3
"""Exercise production iOS foreground polling and source choices with queued callbacks."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '.tmp/ios-notification-source-tests'
WORK.mkdir(parents=True, exist_ok=True)
source = (ROOT / 'mobile/ios/BridgePreview/App.swift').read_text()
methods = source[source.index('    private func cancelSync()'):source.index('    private func sourceSwitch(')].replace('private func', 'func')
methods += source[source.index('    private func channelRecord('):source.index('    private var lastChannelProbe')]
methods += source[source.index('    private func preferenceKey('):source.index('    private func addresses(')]
swift = r'''
import Foundation
final class MemoryDefaults {
 var values: [String: Any] = [:]
 func dictionary(forKey key: String) -> [String: Any]? { values[key] as? [String: Any] }
 func bool(forKey key: String) -> Bool { values[key] as? Bool ?? false }
 func integer(forKey key: String) -> Int { values[key] as? Int ?? 0 }
 func string(forKey key: String) -> String? { values[key] as? String }
 func stringArray(forKey key: String) -> [String]? { values[key] as? [String] }
 func set(_ value: Any, forKey key: String) { values[key] = value }
}
final class UIApplication { enum State { case active, background }; static let shared = UIApplication(); var applicationState = State.active }
final class FakeView { var arrangedSubviews: [FakeView] = []; func removeFromSuperview() {} }
final class FakeTask { var cancelled = false; func cancel() { cancelled = true } }
enum MobileStrings { static func text(_ text: String) -> String { text } }
final class UNMutableNotificationContent { var title = "", body = ""; enum Sound { case `default` }; var sound: Sound?; var userInfo: [String: Any] = [:] }
struct UNNotificationRequest { let identifier: String; let content: UNMutableNotificationContent; let trigger: String? }
final class UNUserNotificationCenter { static let instance = UNUserNotificationCenter(); static func current() -> UNUserNotificationCenter { instance }; var requests: [UNNotificationRequest] = []; func add(_ request: UNNotificationRequest) { requests.append(request) } }
enum NotificationSource { static func apply(_ content: UNMutableNotificationContent, provider: String?, host: String?) {} }
final class Controller {
 let defaults = MemoryDefaults()
 var saved: [String] { defaults.stringArray(forKey: "origins") ?? [] }
 var pollGeneration = 0, pollsStarted = 0
 var polling = Set<String>(), foregroundBaselines = Set<String>()
 var pollTasks: [String: FakeTask] = [:], computerNotifications: [String: FakeView] = [:]
 var computerSnapshots: [String: [String: Any]] = [:]
 struct Pending { let address: String; let complete: (Result<[String: Any], Error>) -> Void }
 var pending: [Pending] = []
 func registerNativePush() {}
 func updateComputerNotifications(_ value: [String: Any]?, address: String) { if let value { computerSnapshots[address] = value } }
 func fetch(address: String, after: Int, poll: Int, completion: @escaping (Result<[String: Any], Error>) -> Void) { pending.append(Pending(address: address, complete: completion)); pollTasks[address] = FakeTask(); pollsStarted += 1 }
 METHODS
 func finish(_ cursor: Int) { let callbacks = pending; pending.removeAll(); for item in callbacks { item.complete(.success(["enabled": true, "streamId": "test", "cursor": cursor, "events": [["id": "event-" + String(cursor), "sequence": cursor, "title": "Synthetic", "body": "fixture"]]])) } }
}
func check(_ value: Bool, _ message: String) { precondition(value, message) }
let c = Controller(), center = UNUserNotificationCenter.current()
let addresses = ["http://computer:8787", "https://computer.example", "http://computer:8788", "https://test.example"]
c.defaults.set(addresses, forKey: "origins"); c.defaults.set(true, forKey: "foregroundAlerts")
c.sync(); c.finish(200); check(center.requests.isEmpty, "Initial baseline does not replay history")
c.sync(); c.finish(201); check(center.requests.count == 4, "Four saved addresses default to enabled")
for address in addresses.dropFirst() { c.setNotificationSource(address, enabled: false) }
c.finish(202); let before = center.requests.count
c.sync(); c.finish(203)
check(center.requests.count == before + 1 && center.requests.last!.content.userInfo["origin"] as? String == addresses[0], "Only selected source alerts")
check(c.defaults.integer(forKey: "cursor:" + addresses[1]) == 203 && c.computerSnapshots[addresses[1]]?["cursor"] as? Int == 203, "Muted source still updates cursor and inbox")
let restored = Controller(); restored.defaults.values = c.defaults.values; check(!restored.notificationSourceEnabled(addresses[1]), "Stored choice restores in another controller")
c.sync(); let old = c.pending; c.pending.removeAll(); let tasks = Array(c.pollTasks.values)
c.setNotificationSource(addresses[1], enabled: true); c.setNotificationSource(addresses[1], enabled: false); c.setNotificationSource(addresses[1], enabled: true)
check(tasks.allSatisfy { $0.cancelled }, "Changing source retires pending polling tasks")
let count = center.requests.count
for pending in old { pending.complete(.success(["enabled": true, "streamId": "test", "cursor": 204, "events": [["id": "late", "sequence": 204]]])) }
check(center.requests.count == count && c.defaults.integer(forKey: "cursor:" + addresses[1]) == 203, "Late callbacks cannot deliver or commit cursor")
c.finish(205); check(center.requests.filter { $0.content.userInfo["origin"] as? String == addresses[1] }.count == 1, "Re-enabled source baselines instead of replaying muted backlog")
c.sync(); c.finish(206); check(center.requests.last!.content.userInfo["origin"] as? String == addresses[1], "New events after baseline resume alerts")
let generation = c.pollGeneration; c.setNotificationSource("https://unsaved.example", enabled: false); check(c.pollGeneration == generation, "Unsaved addresses cannot alter subscriptions")
let otherPhone = Controller(); check(otherPhone.notificationSourceEnabled(addresses[1]), "Choices remain local to this phone")
UIApplication.shared.applicationState = .background; let requests = c.pollsStarted; c.sync(); check(c.pollsStarted == requests, "No accidental iOS background polling")
print("PASS iOS four-source selection, persistent choices, inbox continuity, late response rejection, baseline on re-enable and foreground-only boundary")
'''.replace('METHODS', methods)
(WORK / 'Sources.swift').write_text(swift)
subprocess.run(['swiftc', '-module-cache-path', str(WORK / 'module-cache'), str(WORK / 'Sources.swift'), '-o', str(WORK / 'sources')], check=True)
subprocess.run([str(WORK / 'sources')], check=True)
