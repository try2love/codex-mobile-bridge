import Foundation

struct MobileRelease {
    static let repository = "https://github.com/try2love/codex-mobile-bridge"
    static let api = URL(string: "https://api.github.com/repos/try2love/codex-mobile-bridge/releases?per_page=100")!
    let version: String
    let notes: String
    var page: URL { URL(string: Self.repository + "/releases/tag/v" + version)! }
    static func parts(_ value: String) -> [Int]? {
        let pattern = #"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-(alpha|beta|preview|rc)\.(0|[1-9][0-9]*))?$"#
        guard let match = try? NSRegularExpression(pattern: pattern).firstMatch(in: value, range: NSRange(value.startIndex..., in: value)) else { return nil }
        let fields = (1...5).map { index -> String? in guard let range = Range(match.range(at: index), in: value) else { return nil }; return String(value[range]) }
        guard let major = Int(fields[0] ?? ""), let minor = Int(fields[1] ?? ""), let patch = Int(fields[2] ?? ""), let number = Int(fields[4] ?? "0") else { return nil }
        return [major, minor, patch, fields[3].flatMap { ["alpha", "beta", "preview", "rc"].firstIndex(of: $0) } ?? 4, number]
    }
    static func newer(_ candidate: String, than current: String) -> Bool {
        guard let a = parts(candidate), let b = parts(current) else { return false }
        return b.lexicographicallyPrecedes(a)
    }
    static func select(_ data: Data, current: String) throws -> MobileRelease? {
        guard let rows = try JSONSerialization.jsonObject(with: data) as? [[String: Any]] else { throw URLError(.cannotParseResponse) }
        return rows.compactMap { row -> MobileRelease? in
            guard row["draft"] as? Bool == false, let tag = row["tag_name"] as? String, tag.hasPrefix("v") else { return nil }
            let version = String(tag.dropFirst())
            guard newer(version, than: current), current.contains("-") || (row["prerelease"] as? Bool == false && !version.contains("-")) else { return nil }
            let name = "Codex-Mobile-Bridge-\(version)-iOS-source.zip"
            let url = repository + "/releases/download/" + tag + "/" + name
            guard let assets = row["assets"] as? [[String: Any]], assets.contains(where: { $0["name"] as? String == name && $0["browser_download_url"] as? String == url }) else { return nil }
            return MobileRelease(version: version, notes: String((row["body"] as? String ?? "").prefix(12000)))
        }.max { newer($1.version, than: $0.version) }
    }
}
