import Foundation
import CryptoKit

// File IO stays off the main thread; only the host supplies the verified binding.
enum DisplayStorage {
    static let queue = DispatchQueue(label: "bridge.display-storage")
    static func digest(_ value: String) -> String { SHA256.hash(data: Data(value.utf8)).map { String(format: "%02x", $0) }.joined() }
    @discardableResult
    static func forgetHistory(_ scopes: Set<String>) throws -> Int64 {
        guard !scopes.isEmpty else { return 0 }
        var released: Int64 = 0
        let manager = FileManager.default
        let root = try manager.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true).appendingPathComponent("display-cache", isDirectory: true)
        guard manager.fileExists(atPath: root.path) else { return 0 }
        for binding in try manager.contentsOfDirectory(at: root, includingPropertiesForKeys: [.isDirectoryKey]) {
            guard try binding.resourceValues(forKeys: [.isDirectoryKey]).isDirectory == true else { continue }
            for file in try manager.contentsOfDirectory(at: binding, includingPropertiesForKeys: nil) where file.pathExtension == "json" {
                let data = try Data(contentsOf: file)
                guard let row = try? JSONSerialization.jsonObject(with: data) as? [String: Any], let key = row["key"] as? String,
                      key.hasPrefix("history:"), let entry = try? JSONSerialization.jsonObject(with: Data(key.dropFirst(8).utf8)) as? [String],
                      let scope = entry.first, scopes.contains(scope) else { continue }
                try manager.removeItem(at: file)
                released += Int64(data.count)
            }
        }
        return released
    }
    static func execute(binding: String, command: [String: Any]) throws -> Any {
        let manager = FileManager.default
        var root = try manager.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true).appendingPathComponent("display-cache/" + digest(binding), isDirectory: true)
        let key = command["key"] as? String ?? "", op = command["op"] as? String ?? ""
        guard key.utf8.count <= 32768 else { throw CocoaError(.fileReadCorruptFile) }
        // Appearance and toolbar layout follow the verified computer, even after re-pairing.
        // Chat history keeps the full credential-bound namespace.
        let legacyRoot = root
        if ["appearance", "composer-preferences"].contains(key), let separator = binding.firstIndex(of: "\0") {
            root = root.deletingLastPathComponent().appendingPathComponent(digest(String(binding[..<separator]) + "\\0preferences-v1"), isDirectory: true)
        }
        if op == "list" {
            return (try? manager.contentsOfDirectory(at: root, includingPropertiesForKeys: nil))?.filter { $0.pathExtension == "json" }.compactMap { url -> String? in
                guard let data = try? Data(contentsOf: url), let row = try? JSONSerialization.jsonObject(with: data) as? [String: Any], let stored = row["key"] as? String, stored.hasPrefix(key) else { return nil }; return stored
            } ?? []
        }
        let file = root.appendingPathComponent(digest(key) + ".json")
        if op == "read" {
            if !manager.fileExists(atPath: file.path) {
                let legacy = legacyRoot.appendingPathComponent(digest(key) + ".json")
                guard root != legacyRoot, manager.fileExists(atPath: legacy.path) else { return NSNull() }
                try manager.createDirectory(at: root, withIntermediateDirectories: true)
                var attributes = URLResourceValues(); attributes.isExcludedFromBackup = true; try root.setResourceValues(attributes)
                try Data(contentsOf: legacy).write(to: file, options: .atomic)
            }
            let row = try JSONSerialization.jsonObject(with: Data(contentsOf: file)) as? [String: Any]
            return row?["value"] ?? NSNull()
        }
        if op == "remove" { if manager.fileExists(atPath: file.path) { try manager.removeItem(at: file) }; return true }
        guard op == "write", let value = command["value"] else { throw CocoaError(.fileReadCorruptFile) }
        try manager.createDirectory(at: root, withIntermediateDirectories: true)
        var attributes = URLResourceValues(); attributes.isExcludedFromBackup = true; try root.setResourceValues(attributes)
        try JSONSerialization.data(withJSONObject: ["key":key,"value":value]).write(to: file, options: .atomic)
        return true
    }
}
