import Foundation
import CryptoKit

// File IO stays off the main thread; only the host supplies the verified binding.
enum DisplayStorage {
    static let queue = DispatchQueue(label: "bridge.display-storage")
    static func digest(_ value: String) -> String { SHA256.hash(data: Data(value.utf8)).map { String(format: "%02x", $0) }.joined() }
    static func execute(binding: String, command: [String: Any]) throws -> Any {
        let manager = FileManager.default
        var root = try manager.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true).appendingPathComponent("display-cache/" + digest(binding), isDirectory: true)
        let key = command["key"] as? String ?? "", op = command["op"] as? String ?? ""
        guard key.utf8.count <= 32768 else { throw CocoaError(.fileReadCorruptFile) }
        if op == "list" {
            return (try? manager.contentsOfDirectory(at: root, includingPropertiesForKeys: nil))?.filter { $0.pathExtension == "json" }.compactMap { url -> String? in
                guard let data = try? Data(contentsOf: url), let row = try? JSONSerialization.jsonObject(with: data) as? [String: Any], let stored = row["key"] as? String, stored.hasPrefix(key) else { return nil }; return stored
            } ?? []
        }
        let file = root.appendingPathComponent(digest(key) + ".json")
        if op == "read" {
            guard manager.fileExists(atPath: file.path) else { return NSNull() }
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
