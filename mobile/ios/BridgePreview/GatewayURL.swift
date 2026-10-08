import Foundation

enum GatewayURL {
    static func origin(_ input: String) throws -> String {
        guard let c = URLComponents(string: input.trimmingCharacters(in: .whitespacesAndNewlines)),
              let host = c.host, !host.isEmpty, let scheme = c.scheme?.lowercased(), c.user == nil, c.password == nil,
              c.query == nil, c.path.isEmpty || c.path == "/", c.port == nil || (1...65535).contains(c.port!) else { throw InvalidURL() }
        guard scheme == "https" || (scheme == "http" && local(host)) else { throw InvalidURL() }
        var clean = URLComponents(); clean.scheme = scheme; clean.host = host.lowercased(); clean.port = c.port
        guard let value = clean.string else { throw InvalidURL() }; return value
    }
    static func local(_ host: String) -> Bool {
        if host.lowercased() == "localhost" || host == "::1" || host == "[::1]" { return true }
        let parts = host.split(separator: ".").map(String.init)
        let n = parts.compactMap(Int.init)
        guard n.count == 4, zip(parts,n).allSatisfy({ $0.0 == String($0.1) && (0...255).contains($0.1) }) else { return false }
        return n[0] == 10 || n[0] == 127 || (n[0] == 192 && n[1] == 168) || (n[0] == 172 && (16...31).contains(n[1]))
    }
    static func connection(_ input: String) throws -> URL {
        let base = try origin(input), fragment = URLComponents(string: input.trimmingCharacters(in: .whitespacesAndNewlines))?.fragment
        if let fragment, fragment.range(of: "^pair=[A-Za-z0-9_-]{43}$", options: .regularExpression) == nil { throw InvalidURL() }
        return URL(string: base + "/" + (fragment.map { "#" + $0 } ?? ""))!
    }
    static func same(_ url: URL, _ origin: String) -> Bool {
        guard var c = URLComponents(url: url, resolvingAgainstBaseURL: false), c.user == nil, c.password == nil else { return false }
        c.path = ""; c.query = nil; c.fragment = nil
        return c.string == origin
    }
    static func chat(_ origin: String, thread: String, host: String) throws -> URL {
        let desktop = host == "desktop:claude" || host == "desktop:deepseek"
        guard !thread.isEmpty, thread.utf16.count <= 512, host.utf16.count <= 256,
              !(thread + host).unicodeScalars.contains(where: { $0.value < 32 || $0.value == 127 }),
              desktop || (!host.hasPrefix("desktop:") && UUID(uuidString: thread) != nil) else { throw InvalidURL() }
        // Encode each field once; tilde remains the fragment delimiter only.
        let identifier = desktop ? thread.addingPercentEncoding(withAllowedCharacters: CharacterSet(charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._"))! : thread
        return URL(string: origin + "/#" + identifier + "~" + (host.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? "local"))!
    }
    struct InvalidURL: LocalizedError { var errorDescription: String? { "请填写完整的 HTTPS 网关地址，或局域网 HTTP 地址，不含路径、账号和参数。" } }
}
