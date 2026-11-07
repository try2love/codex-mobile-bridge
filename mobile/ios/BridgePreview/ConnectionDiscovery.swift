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
        config.timeoutIntervalForRequest = 6; config.timeoutIntervalForResource = 10
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
    fileprivate func read(_ address: String, key: String? = nil, cookie: String? = nil, csrf: String? = nil, body: [String:Any]? = nil) async throws -> [String: Any] {
        guard !cancelled, let url = URL(string: address) else { throw URLError(.cancelled) }
        var request = URLRequest(url: url); request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let key { request.setValue(key, forHTTPHeaderField: "X-Discovery-Key") }
        if let cookie {
            request.setValue(cookie, forHTTPHeaderField:"Cookie")
            request.setValue("BridgeMobile/0.1-iOS",forHTTPHeaderField:"User-Agent")
            if let body {
                request.httpMethod="POST"; request.httpBody=try JSONSerialization.data(withJSONObject:body)
                request.setValue("application/json",forHTTPHeaderField:"Content-Type")
                request.setValue(csrf,forHTTPHeaderField:"X-CSRF-Token"); request.setValue(GatewayURL.httpOrigin(address),forHTTPHeaderField:"Origin")
            }
        }
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

private actor ChannelProbeSlots {
    static let shared=ChannelProbeSlots()
    private var available=4
    private var waiting=[CheckedContinuation<Void,Never>]()
    func acquire() async {
        if available>0 {available-=1;return}
        await withCheckedContinuation { waiting.append($0) }
    }
    func release() {
        if waiting.isEmpty {available+=1} else {waiting.removeFirst().resume()}
    }
}

enum GatewayChannels {
    static func vault(_ device: String) -> String { "https://" + device + ".route.invalid" }
    static func token(_ root: String, anchor: String, target: String) -> String {
        if target == anchor { return root }
        let code = HMAC<SHA256>.authenticationCode(for: Data(("gateway-route-v1\n" + target).utf8), using: SymmetricKey(data: Data(root.utf8)))
        return "cmbroute1_" + ConnectionDiscovery.hex(code)
    }
    static func endpoints(_ descriptor: [String: Any]) -> [String] {
        (descriptor["endpoints"] as? [String] ?? []).filter { (try? GatewayURL.origin($0)) == $0 && GatewayURL.httpOrigin($0) == $0 }
    }
    static func proves(_ device: String, target: String, token: String) async -> Bool {
        let probe = ConnectionDiscovery(), key = ConnectionDiscovery.hash(token)
        defer { probe.cancel() }
        let nonce = (UUID().uuidString + UUID().uuidString).replacingOccurrences(of: "-", with: "").lowercased()
        guard let response = try? await probe.read(target + "/api/connection/prove?device=" + device + "&id=" + ConnectionDiscovery.hash(key) + "&challenge=" + nonce),
              response["deviceId"] as? String == device, let code = response["proof"] as? String else { return false }
        return ConnectionDiscovery.verify(code, key: key, device: device, target: target, nonce: nonce)
    }
    static func probe(_ descriptor:[String:Any], root:String, targets:[String], sample:@escaping @MainActor (String,Int?) -> Void) async {
        guard let id=descriptor["deviceId"] as? String, let anchor=descriptor["anchor"] as? String else { return }
        let limit=min(8,max(1,(descriptor["race"] as? [String:Any])?["parallel"] as? Int ?? 4))
        await withTaskGroup(of:(String,Int?).self) { group in
            var iterator=targets.makeIterator()
            func launch(_ url:String) {
                group.addTask {
                    await ChannelProbeSlots.shared.acquire()
                    let start=ProcessInfo.processInfo.systemUptime
                    let ok=await proves(id,target:url,token:token(root,anchor:anchor,target:url))
                    await ChannelProbeSlots.shared.release()
                    return (url,ok ? Int((ProcessInfo.processInfo.systemUptime-start)*1000) : nil)
                }
            }
            for _ in 0..<limit { if let next=iterator.next(){launch(next)} }
            for await (url, ms) in group {
                if Task.isCancelled {group.cancelAll();return}
                await sample(url,ms)
                if let next=iterator.next(){launch(next)}
            }
        }
    }
    static func sign(_ key:String,_ text:String)->String {
        ConnectionDiscovery.hex(HMAC<SHA256>.authenticationCode(for:Data(text.utf8),using:SymmetricKey(data:Data(key.utf8))))
    }
    static func refresh(_ old:[String:Any], reached:String,name:String,root:String) async throws -> [String:Any] {
        guard let id=old["deviceId"] as? String,let anchor=old["anchor"] as? String else {throw URLError(.badServerResponse)}
        let client=ConnectionDiscovery(),cookie=name+"="+token(root,anchor:anchor,target:reached)
        defer{client.cancel()}
        let auth=try await client.read(reached+"/api/auth",cookie:cookie)
        guard auth["trustedDevice"] as? Bool == true,var next=auth["mobileRoutes"] as? [String:Any],next["deviceId"] as? String == id else {throw URLError(.userAuthenticationRequired)}
        let allowed=Set(endpoints(next)),missing=(next["unbound"] as? [String] ?? []).filter{allowed.contains($0)}
        let verifier=ConnectionDiscovery.hash(next["renewal"] as? String == "anchor" ? "cmbroute1_"+sign(root,"gateway-route-v1\n"+anchor) : sign(root,"gateway-route-renew-v1"))
        var claims=[String:[String:String]]()
        for (i,url) in missing.enumerated() {
            let digest=ConnectionDiscovery.hash("cmbroute1_"+sign(root,"gateway-route-v1\n"+url))
            claims[url]=["digest":digest,"proof":sign(verifier,["route-grant-v1",id,url,digest].joined(separator:"\n"))]
            if claims.count==32 || i+1==missing.count {
                _=try await client.read(reached+"/api/connection/routes",cookie:cookie,csrf:auth["csrf"] as? String,body:["deviceId":id,"anchor":anchor,"bindings":claims])
                claims.removeAll()
            }
        }
        next["anchor"]=anchor;return next
    }
}
