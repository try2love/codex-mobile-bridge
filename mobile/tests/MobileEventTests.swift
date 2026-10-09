let first = CommandLine.arguments[1], second = CommandLine.arguments[2]
let baseline = CommandLine.arguments[3] == "baseline"
func spin(_ done: () -> Bool) {
    let deadline = Date(timeIntervalSinceNow: 3)
    while !done() && Date() < deadline { RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.005)) }
    precondition(done(), "Timed out waiting for fixture")
}
func control(_ address: String, _ query: String = "") -> [[String: Any]] {
    var result: [[String: Any]]?
    URLSession.shared.dataTask(with: URL(string: address + "/control?" + query)!) { data, _, _ in
        let json = try! JSONSerialization.jsonObject(with: data!) as! [String: Any]
        DispatchQueue.main.async { result = json["requests"] as? [[String: Any]] }
    }.resume()
    spin { result != nil }; return result!
}
func metric(_ name: String, _ row: [String: Any]) { print("\(name) request_bytes=\(row["requestBytes"]!) response_body_bytes=\(row["responseBodyBytes"]!) target=\(row["target"]!)") }
let controller = BridgeController(), notices = UNUserNotificationCenter.current()
controller.origin = first
controller.defaults.set([first], forKey: "origins")
controller.defaults.set(true, forKey: "foregroundAlerts")
controller.foreground(); spin { !controller.loading }
precondition(notices.delivered.isEmpty, "Initial foreground must suppress history")
controller.poll(); spin { !controller.loading }
let initial = control(first); metric("initial", initial[0]); metric("unchanged", initial[1])
if baseline {
    precondition(initial[0]["responseBodyBytes"] as! Int == initial[1]["responseBodyBytes"] as! Int)
    controller.background(); print("iOS baseline captured")
} else {
    precondition(initial[1]["target"] as! String == "/api/mobile/events?after=200", "Incremental poll must use cursor")
    precondition((initial[1]["responseBodyBytes"] as! Int) < 100)
    func poll(_ query: String = "") { if !query.isEmpty { _ = control(first, query) }; controller.poll(); spin { !controller.loading } }
    let key = "cursor:" + first
    poll("cursor=201"); precondition(notices.delivered.count == 1); metric("one_new", control(first)[2])
    poll(); precondition(notices.delivered.count == 1, "Repeated poll duplicated notification")
    controller.defaults.set(202, forKey: "cleared:" + first + ":fixture-stream")
    poll("cursor=202"); precondition(notices.delivered.count == 1)
    poll("cursor=203"); precondition(notices.delivered.count == 2, "Clear lost new request")
    poll("stream=reset-lower&cursor=1"); precondition(controller.defaults.integer(forKey: key) == 1 && notices.delivered.count == 2)
    poll("cursor=2"); precondition(notices.delivered.count == 3, "Lower reset starved new notifications")
    poll("stream=reset-higher&cursor=300"); precondition(controller.defaults.integer(forKey: key) == 300 && notices.delivered.count == 3)
    poll("cursor=301"); precondition(notices.delivered.count == 4)
    poll("status=401&cursor=302"); precondition(controller.defaults.integer(forKey: key) == 301, "Expired login advanced cursor")
    _ = control(first, "status=200"); controller.background(); controller.foreground(); spin { !controller.loading }
    precondition(notices.delivered.count == 4, "Re-login baseline replayed prior notifications")
    poll("cursor=303"); precondition(notices.delivered.count == 5)
    var inbox: [String: Any]?
    controller.full { result in inbox = try! result.get() }; spin { inbox != nil }
    precondition((inbox!["events"] as! [[String: Any]]).count == 200 && control(first).last!["target"] as! String == "/api/mobile/events", "Inbox must retain full historical read")
    controller.defaults.set([first, second], forKey: "origins")
    _ = control(first, "hold=1"); let before = control(first).count, secondBefore = control(second).count
    controller.poll(); spin { control(first).count == before + 1 }; controller.background()
    _ = control(first, "release=1"); RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.1))
    precondition(control(second).count == secondBefore, "Background requested subsequent computer")
    precondition(controller.defaults.integer(forKey: key) == 303, "Background callback advanced cursor")
    // Resume while the cancelled old URLSession callback is still in flight.
    controller.defaults.set([first], forKey: "origins"); _ = control(first, "hold=1")
    let previous = control(first).count; controller.foreground(); spin { control(first).count == previous + 1 }; controller.background()
    _ = control(first, "cursor=310"); controller.foreground(); spin { !controller.loading }
    _ = control(first, "release=1"); RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.1))
    precondition(controller.defaults.integer(forKey: key) == 310 && notices.delivered.count == 5, "Old batch mutated resumed baseline")
    poll("cursor=311"); precondition(notices.delivered.count == 6)
    // Cancellation before WebKit cookie callback must not start a request.
    let cookies = WKWebsiteDataStore.default().httpCookieStore
    cookies.delay = true; let delayedBefore = control(first).count
    controller.poll(); controller.background(); cookies.flush(); RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.1))
    precondition(control(first).count == delayedBefore, "Cancelled cookie lookup started a request")
    controller.foreground(); spin { !controller.loading }
    // A response sent under the old login cannot commit after account replacement.
    _ = control(first, "hold=1&cursor=312"); let replacementBefore = control(first).count; controller.poll(); spin { control(first).count == replacementBefore + 1 }
    cookies.cookies = [HTTPCookie(properties: [.name: "codex_mobile_session", .value: "replacement", .domain: "127.0.0.1", .path: "/"])!]
    _ = control(first, "release=1"); spin { !controller.loading }
    precondition(controller.defaults.integer(forKey: key) == 311 && notices.delivered.count == 6 && cookies.cookies[0].value == "replacement", "Old login committed a cursor or replaced cookie")
    controller.background(); controller.foreground(); spin { !controller.loading }
    precondition(controller.defaults.integer(forKey: key) == 312 && notices.delivered.count == 6)
    // A later login during WebKit renewal completion must also invalidate commit.
    cookies.cookies = [HTTPCookie(properties: [.name: "codex_mobile_session", .value: "fixture", .domain: "127.0.0.1", .path: "/"])!]
    controller.background(); controller.foreground(); spin { !controller.loading }
    cookies.afterSet = { cookies.cookies = [HTTPCookie(properties: [.name: "codex_mobile_session", .value: "renewal-window", .domain: "127.0.0.1", .path: "/"])!] }
    poll("cursor=313")
    precondition(controller.defaults.integer(forKey: key) == 312 && notices.delivered.count == 6, "Login during renewal completion committed old cursor")
    controller.background(); controller.foreground(); spin { !controller.loading }
    precondition(controller.defaults.integer(forKey: key) == 313 && notices.delivered.count == 6)
    // Removal during request must not restore cursor or renew its cookie.
    _ = control(first, "hold=1"); let removeBefore = control(first).count; controller.poll(); spin { control(first).count == removeBefore + 1 }
    controller.defaults.set([], forKey: "origins"); controller.defaults.removeObject(forKey: key); cookies.cookies = []
    _ = control(first, "release=1"); spin { !controller.loading }
    precondition(controller.defaults.object(forKey: key) == nil && cookies.cookies.isEmpty, "Removed connection resurrected")
    controller.background()
    print("iOS event sync: 17 scenarios passed; background subsequent-origin requests=0")
}
