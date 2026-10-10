import Foundation
@main struct URLTests {
    static func main() throws {
        let input = try Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1]))
        let cases = try JSONSerialization.jsonObject(with: input) as! [String: Any]
        for row in cases["accepted"] as! [[String]] { let value = try GatewayURL.connection(row[0]).absoluteString; precondition(value == row[1], row[0]) }
        for value in cases["rejected"] as! [String] { do { _ = try GatewayURL.connection(value); fatalError("Accepted " + value) } catch {} }
        let base = "https://codex.try2love.com"
        precondition(GatewayURL.same(URL(string: base + "/api/mobile/events")!, base))
        for value in ["https://codex.try2love.com.evil.example/", "http://codex.try2love.com/", "https://user@codex.try2love.com/"] { precondition(!GatewayURL.same(URL(string:value)!, base)) }
        let chat = try GatewayURL.chat(base, thread: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", host: "my server/~")
        precondition(chat.absoluteString == base + "/#aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee~my%20server%2F%7E")
        do { _ = try GatewayURL.chat(base, thread: String(repeating:"-", count:36), host:"local"); fatalError("Invalid UUID accepted") } catch {}
        for row in cases["desktopAccepted"] as! [[String]] { let target = try GatewayURL.chat(base, thread: row[0], host: row[1]); precondition(target.absoluteString == base + "/" + row[2]) }
        for row in cases["desktopRejected"] as! [[String]] { do { _ = try GatewayURL.chat(base, thread: row[0], host: row[1]); fatalError("Invalid desktop link accepted") } catch {} }
        do { _ = try GatewayURL.chat(base, thread: String(repeating:"x", count:513), host:"desktop:claude"); fatalError("Long desktop ID accepted") } catch {}
        print("Swift: 37 URL and route checks passed")
    }
}
