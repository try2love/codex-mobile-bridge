import Foundation
import CryptoKit

final class ConnectionDiscovery: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    static func retryableStatus(_ status: Int) -> Bool { status == 404 || status == 410 || (500...599).contains(status) }
    private let lock = NSLock()
    private var stopped = false
    private var cancelled: Bool {
        get { lock.lock(); defer { lock.unlock() }; return stopped }
        set { lock.lock(); stopped = newValue; lock.unlock() }
    }
    private lazy var session: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.httpShouldSetCookies = false; config.httpCookieStorage = nil; config.urlCredentialStorage = nil; config.urlCache = nil
        config.timeoutIntervalForRequest = 2; config.timeoutIntervalForResource = 3
        return URLSession(configuration: config, delegate: self, delegateQueue: nil)
    }()
    func cancel() { cancelled = true; session.invalidateAndCancel() }
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
    static func hex<S: Sequence>(_ bytes: S) -> String where S.Element == UInt8 { bytes.map { String(format: "%02x", $0) }.joined() }
    static func hash(_ text: String) -> String { hex(SHA256.hash(data: Data(text.utf8))) }
    static func verify(_ code: String, key: String, device: String, target: String, nonce: String) -> Bool {
        guard code.range(of: "^[a-f0-9]{64}$", options: .regularExpression) != nil else { return false }
        let bytes = stride(from: 0, to: 64, by: 2).map { i in UInt8(code.dropFirst(i).prefix(2), radix: 16)! }
        return HMAC<SHA256>.isValidAuthenticationCode(bytes, authenticating: Data((device + "\n" + target + "\n" + nonce).utf8), using: SymmetricKey(data: Data(key.utf8)))
    }
    static func descriptor(_ auth: [String: Any], secret: String) -> [String: String]? {
        guard auth["authenticated"] as? Bool == true, let d = auth["connection"] as? [String: String],
              let relay = d["relay"], (try? GatewayURL.origin(relay)) == relay, relay == GatewayURL.httpOrigin(relay), relay.hasPrefix("https://"),
              d["deviceId"]?.range(of: "^[a-f0-9]{32}$", options: .regularExpression) != nil,
              d["discoveryKey"]?.range(of: "^[a-f0-9]{64}$", options: .regularExpression) != nil, !secret.isEmpty else { return nil }
        return d.merging(["binding": hash(secret)]) { _, new in new }
    }
    private func read(_ address: String, key: String? = nil) async throws -> [String: Any] {
        guard !cancelled, let url = URL(string: address) else { throw URLError(.cancelled) }
        var request = URLRequest(url: url); request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let key { request.setValue(key, forHTTPHeaderField: "X-Discovery-Key") }
        // bytes caps untrusted responses before buffering; no cookies or redirects.
        let (bytes, response) = try await session.bytes(for: request)
        guard (response as? HTTPURLResponse)?.statusCode == 200, response.expectedContentLength <= 32768 else { throw URLError(.badServerResponse) }
        var data = Data()
        for try await byte in bytes { guard !cancelled, data.count < 32768 else { throw URLError(.dataLengthExceedsMaximum) }; data.append(byte) }
        guard let value = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { throw URLError(.cannotParseResponse) }; return value
    }
    func resolve(_ old: String, descriptor: [String: String], secret: String) async -> String {
        defer { session.finishTasksAndInvalidate() }
        let key = Self.hash(secret)
        guard !secret.isEmpty, descriptor["binding"] == key, let device = descriptor["deviceId"], let relay = descriptor["relay"], let grant = descriptor["discoveryKey"],
              let routes = try? await read(relay + "/relay/discover/" + device, key: grant), routes["deviceId"] as? String == device,
              let endpoints = routes["endpoints"] as? [String] else { return old }
        for endpoint in endpoints.prefix(16) {
            guard !cancelled else { return old }
            guard let target = try? GatewayURL.origin(endpoint), target == GatewayURL.httpOrigin(target), target.hasPrefix("https://") else { continue }
            let nonce = (UUID().uuidString + UUID().uuidString).replacingOccurrences(of: "-", with: "").lowercased()
            guard let proof = try? await read(target + "/api/connection/prove?id=" + Self.hash(key) + "&challenge=" + nonce), proof["deviceId"] as? String == device, let code = proof["proof"] as? String,
                  code.range(of: "^[a-f0-9]{64}$", options: .regularExpression) != nil else { continue }
            if Self.verify(code, key: key, device: device, target: target, nonce: nonce) { return target }
        }
        return old
    }
}
