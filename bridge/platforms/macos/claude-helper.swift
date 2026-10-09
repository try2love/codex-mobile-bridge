// Native, visible Claude Console bootstrap. No CDP, clipboard or trust changes.
import AppKit
import ApplicationServices
import Foundation

struct SetupError: Error { let state: String; let reason: String }
func fail(_ state: String, _ reason: String) throws -> Never { throw SetupError(state: state, reason: reason) }
func desktopIssue(hasSession: Bool, onConsole: Bool?, loginDone: Bool?, locked: Bool?, foreground: String?, screenSaverWindow: Bool) -> SetupError? {
    if locked == true {
        return SetupError(state: "needs-unlock", reason: "Mac 已锁屏，Claude 建立连接需要可交互桌面；解锁后会自动继续连接")
    }
    if hasSession && onConsole == true && loginDone == true && (foreground == "com.apple.ScreenSaver.Engine" || screenSaverWindow) {
        return SetupError(state: "needs-screen-saver", reason: "Mac 正在显示屏幕保护程序，Claude 建立连接需要可交互桌面；退出屏保后会自动继续连接")
    }
    if !hasSession || onConsole != true || loginDone != true || foreground == nil || foreground == "com.apple.loginwindow" {
        return SetupError(state: "needs-desktop", reason: "当前 Mac 会话不可交互，Claude 接入已暂停；返回当前用户桌面后会自动继续连接")
    }
    return nil
}
func isScreenSaverWindow(bundle: String?, layer: Int, onScreen: Bool, saverLevel: Int) -> Bool {
    // A background wallpaper service or a normal Screen Saver preview is not
    // evidence that a screen saver covers the desktop.
    bundle == "com.apple.ScreenSaver.Engine" && onScreen && layer >= saverLevel
}
func currentDesktopIssue(scanWindows: Bool = true) -> SetupError? {
    let session = CGSessionCopyCurrentDictionary() as? [String: Any]
    let foreground = NSWorkspace.shared.frontmostApplication?.bundleIdentifier
    func issue(_ screenSaverWindow: Bool) -> SetupError? {
        desktopIssue(hasSession: session != nil, onConsole: session?[kCGSessionOnConsoleKey as String] as? Bool,
                     loginDone: session?[kCGSessionLoginDoneKey as String] as? Bool,
                     // This extra key is not part of Apple's documented session
                     // contract. Only an explicit true establishes locked; an
                     // absent key never turns screen saver into "locked".
                     locked: session?["CGSSessionScreenIsLocked"] as? Bool,
                     foreground: foreground, screenSaverWindow: screenSaverWindow)
    }
    if let blocked = issue(false) { return blocked }
    // Window dictionaries are relatively expensive. Check them at entry and
    // before activation/HID submission, not for each Unicode input chunk.
    if scanWindows, let windows = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] {
        let saverLevel = Int(CGWindowLevelForKey(.screenSaverWindow))
        for window in windows {
            guard let layer = window[kCGWindowLayer as String] as? Int, layer >= saverLevel,
                  let pid = window[kCGWindowOwnerPID as String] as? Int32 else { continue }
            if isScreenSaverWindow(bundle: NSRunningApplication(processIdentifier: pid)?.bundleIdentifier,
                                   layer: layer, onScreen: window[kCGWindowIsOnscreen as String] as? Bool == true, saverLevel: saverLevel) {
                return issue(true)
            }
        }
    }
    return nil
}
func requireDesktop(scanWindows: Bool = true) throws {
    if let issue = currentDesktopIssue(scanWindows: scanWindows) { throw issue }
}
func output(_ state: String, _ reason: String) {
    let value: [String: Any] = ["setupState": state, "reason": reason, "helperPath": CommandLine.arguments[0]]
    let data = try! JSONSerialization.data(withJSONObject: value)
    print(String(data: data, encoding: .utf8)!)
}
func attribute(_ element: AXUIElement, _ name: String) -> CFTypeRef? {
    var value: CFTypeRef?
    return AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success ? value : nil
}
func text(_ element: AXUIElement, _ name: String) -> String { attribute(element, name) as? String ?? "" }
func child(_ element: AXUIElement, _ name: String) -> AXUIElement? {
    guard let value = attribute(element, name), CFGetTypeID(value) == AXUIElementGetTypeID() else { return nil }
    return (value as! AXUIElement)
}
func children(_ element: AXUIElement) -> [AXUIElement] { attribute(element, kAXChildrenAttribute) as? [AXUIElement] ?? [] }
func find(_ root: AXUIElement, matching: (AXUIElement) -> Bool) -> AXUIElement? {
    var queue = [root], index = 0
    while index < queue.count && index < 3500 {
        let item = queue[index]; index += 1
        if matching(item) { return item }
        if queue.count < 3500 { queue.append(contentsOf: children(item)) }
    }
    return nil
}
func promptName(_ name: String) -> Bool {
    ["console-prompt", "Console prompt", "控制台提示", "控制台提示符"].contains(name)
}
func isPrompt(_ element: AXUIElement) -> Bool {
    promptName(text(element, kAXIdentifierAttribute)) || promptName(text(element, kAXDescriptionAttribute)) || promptName(text(element, kAXTitleAttribute))
}
func consoleTab(role: String, identifier: String, description: String, title: String) -> Bool {
    ["AXRadioButton", "AXButton", "AXTab"].contains(role) &&
        [identifier, description, title].contains(where: { ["Console", "控制台"].contains($0) })
}
func isConsoleTab(_ element: AXUIElement) -> Bool {
    consoleTab(role: text(element, kAXRoleAttribute), identifier: text(element, kAXIdentifierAttribute),
               description: text(element, kAXDescriptionAttribute), title: text(element, kAXTitleAttribute))
}
func submissionAccepted(_ value: String?) -> Bool {
    value?.trimmingCharacters(in: .newlines).isEmpty == true
}
func devToolsWindow(role: String, title: String, owner: pid_t, expectedOwner: pid_t) -> Bool {
    role == "AXWindow" && owner == expectedOwner &&
        ["Developer Tools", "DevTools", "开发者工具"].contains(where: { title == $0 || title.hasPrefix($0 + " - ") })
}
func contents(_ element: AXUIElement) -> String { text(element, kAXValueAttribute).trimmingCharacters(in: .newlines) }
func focusedWithin<Element>(_ expected: Element, focused: Element?, parent: (Element) -> Element?, equal: (Element, Element) -> Bool) -> Bool {
    var cursor = focused
    for _ in 0..<5 {
        guard let current = cursor else { return false }
        if equal(current, expected) { return true }
        cursor = parent(current)
    }
    return false
}

final class Connector {
    let app: NSRunningApplication
    let root: AXUIElement
    let cancel: URL?
    init(pid: pid_t, executable: String, cancelPath: String?) throws {
        guard let found = NSRunningApplication(processIdentifier: pid),
              found.bundleIdentifier == "com.anthropic.claudefordesktop",
              found.executableURL?.resolvingSymlinksInPath().path == URL(fileURLWithPath: executable).resolvingSymlinksInPath().path else {
            try fail("needs-attention", "目标不是已发现的 Claude Desktop")
        }
        app = found; root = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(root, 1)
        cancel = cancelPath.map { URL(fileURLWithPath: $0) }
    }
    func check(_ prompt: AXUIElement? = nil, foreground: Bool = true) throws {
        if let cancel = cancel, FileManager.default.fileExists(atPath: cancel.path) { try fail("cancelled", "已取消 Claude 连接") }
        try requireDesktop(scanWindows: false)
        if CGEventSource.keyState(.combinedSessionState, key: 53) { try fail("cancelled", "已取消 Claude 连接") }
        guard !app.isTerminated, !foreground || NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier else {
            try fail("needs-attention", "焦点已离开 Claude，自动连接已停止，请重试")
        }
        if let prompt = prompt {
            guard let window = child(prompt, kAXWindowAttribute) else { try fail("needs-attention", "无法确认 Claude Console 输入焦点") }
            try checkWindow(window)
            guard focusedWithin(prompt, focused: child(root, kAXFocusedUIElementAttribute),
                                parent: { child($0, kAXParentAttribute) }, equal: { CFEqual($0, $1) }) else {
                try fail("needs-attention", "Claude Console 输入焦点已改变，自动连接已停止")
            }
        }
    }
    func checkWindow(_ window: AXUIElement) throws {
        try check()
        guard let focused = child(root, kAXFocusedWindowAttribute), CFEqual(window, focused) else {
            try fail("needs-attention", "Claude 窗口已改变，自动连接已停止，请重试")
        }
    }
    func activate() throws {
        try requireDesktop()
        try check(foreground: false)
        app.activate(options: [.activateAllWindows])
        Thread.sleep(forTimeInterval: 0.3); try check()
    }
    func key(_ code: CGKeyCode, flags: CGEventFlags = [], prompt: AXUIElement? = nil) throws {
        for down in [true, false] {
            try check(prompt)
            guard let event = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: down) else { try fail("needs-attention", "无法创建键盘事件") }
            event.flags = flags; event.postToPid(app.processIdentifier)
        }
    }
    func developerMode() throws {
        try activate()
        guard let menu = child(root, kAXMenuBarAttribute), let help = find(menu, matching: { ["Help", "帮助"].contains(text($0, kAXTitleAttribute)) }) else {
            try fail("needs-developer-mode", "请在 Claude 的帮助菜单中启用开发者模式，然后重试连接")
        }
        try check(); AXUIElementPerformAction(help, kAXPressAction as CFString)
        Thread.sleep(forTimeInterval: 0.2)
        let names = ["Enable Developer Mode…", "Enable Developer Mode...", "启用开发者模式…", "启用开发者模式..."]
        guard let item = find(menu, matching: { names.contains(text($0, kAXTitleAttribute)) }) else {
            try fail("needs-developer-mode", "请在 Claude 的帮助菜单中启用开发者模式，然后重试连接")
        }
        try check(); AXUIElementPerformAction(item, kAXPressAction as CFString)
        // The native confirmation is left to the user. Never click Enable.
        output("needs-developer-mode", "请确认 Claude 的开发者模式提示，完成后会继续连接")
    }
    func console() throws -> AXUIElement {
        try activate()
        guard let initial = child(root, kAXFocusedWindowAttribute) else { try fail("needs-attention", "无法确认 Claude Console 输入焦点") }
        let previous = attribute(root, kAXWindowsAttribute) as? [AXUIElement] ?? []
        if let prompt = find(initial, matching: isPrompt) { return prompt }
        // macOS Chromium exposes the tab name as AXDescription, with no title.
        // When DevTools is already open on Elements, select its Console directly.
        if let tab = find(initial, matching: isConsoleTab) {
            try checkWindow(initial); AXUIElementPerformAction(tab, kAXPressAction as CFString)
        } else {
            try checkWindow(initial)
            try key(34, flags: [.maskCommand, .maskAlternate])
        }
        let deadline = Date().addingTimeInterval(12)
        while Date() < deadline {
            Thread.sleep(forTimeInterval: 0.25); try check()
            guard let window = child(root, kAXFocusedWindowAttribute) else { try fail("needs-attention", "无法确认 Claude Console 输入焦点") }
            if !CFEqual(window, initial) {
                var owner: pid_t = 0
                guard !previous.contains(where: { CFEqual($0, window) }), AXUIElementGetPid(window, &owner) == .success,
                      devToolsWindow(role: text(window, kAXRoleAttribute), title: text(window, kAXTitleAttribute), owner: owner, expectedOwner: app.processIdentifier) else {
                    try fail("needs-attention", "Claude 窗口已改变，自动连接已停止，请重试")
                }
            }
            if let prompt = find(window, matching: isPrompt) { return prompt }
            if let tab = find(window, matching: isConsoleTab) {
                try checkWindow(window); AXUIElementPerformAction(tab, kAXPressAction as CFString)
            }
        }
        try fail("failed", "未找到 Claude Console，请确认开发者工具窗口可用后重试")
    }
    func verify(_ prompt: AXUIElement, _ expected: String) throws {
        for _ in 0..<40 {
            Thread.sleep(forTimeInterval: 0.025); try check(prompt)
            if contents(prompt) == expected { return }
        }
        try fail("needs-attention", "Console 内容未完整到达，未执行连接脚本，请重试")
    }
    func submit(_ prompt: AXUIElement) throws {
        try check(prompt)
        guard let down = CGEvent(keyboardEventSource: nil, virtualKey: 36, keyDown: true),
              let up = CGEvent(keyboardEventSource: nil, virtualKey: 36, keyDown: false) else {
            try fail("failed", "无法创建 Console 提交事件，连接脚本已保留")
        }
        // Chromium accepted the source but did not submit Return posted only
        // to its PID. Enter the HID stream once, guarded by the actual prompt.
        down.flags = []; up.flags = []
        down.setIntegerValueField(.keyboardEventAutorepeat, value: 0)
        try requireDesktop()
        try check(prompt)
        down.post(tap: .cghidEventTap)
        Thread.sleep(forTimeInterval: 0.03)
        up.post(tap: .cghidEventTap)
        try check(prompt)
        let deadline = Date().addingTimeInterval(3)
        while Date() < deadline {
            try check(prompt)
            // An unreadable AXValue is not an empty editor or proof of submit.
            if submissionAccepted(attribute(prompt, kAXValueAttribute) as? String) { return }
            Thread.sleep(forTimeInterval: 0.05)
        }
        try fail("failed", "未确认 Console 已提交，已停止自动操作；请检查 Console 和连接状态后重试")
    }
    func inject(_ path: String) throws {
        let source = try String(contentsOfFile: path, encoding: .utf8)
        guard source.hasPrefix("/* codex bridge connector */"), source.utf8.count < 300000,
              !source.contains("\n"), !source.contains("\r") else { try fail("needs-attention", "连接脚本格式无效") }
        let prompt = try console()
        guard let window = child(prompt, kAXWindowAttribute) else { try fail("needs-attention", "无法确认 Claude Console 输入焦点") }
        try checkWindow(window)
        let old = contents(prompt)
        guard old.isEmpty || old.hasPrefix("/* codex bridge connector */") else {
            try fail("needs-attention", "Console 中已有未提交内容，已保留，请处理后重试连接")
        }
        try checkWindow(window)
        AXUIElementSetAttributeValue(prompt, kAXFocusedAttribute as CFString, kCFBooleanTrue)
        Thread.sleep(forTimeInterval: 0.1); try check(prompt)
        if !old.isEmpty {
            try key(0, flags: .maskCommand, prompt: prompt); try key(51, prompt: prompt); try verify(prompt, "")
        }
        var settable = DarwinBoolean(false)
        if AXUIElementIsAttributeSettable(prompt, kAXValueAttribute as CFString, &settable) == .success && settable.boolValue {
            try check(prompt)
            if AXUIElementSetAttributeValue(prompt, kAXValueAttribute as CFString, source as CFString) != .success { try fail("needs-attention", "Console 拒绝输入连接脚本") }
            try verify(prompt, source)
        } else {
            let units = Array(source.utf16)
            for start in stride(from: 0, to: units.count, by: 32) {
                try check(prompt)
                let end = min(start + 32, units.count), chunk = Array(units[start..<end])
                for down in [true, false] {
                    try check(prompt)
                    guard let event = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: down) else { try fail("needs-attention", "无法创建键盘事件") }
                    event.keyboardSetUnicodeString(stringLength: chunk.count, unicodeString: chunk)
                    event.postToPid(app.processIdentifier)
                }
                Thread.sleep(forTimeInterval: 0.01)
                if end % 128 == 0 || end == units.count { try verify(prompt, String(decoding: units[0..<end], as: UTF16.self)) }
            }
        }
        try verify(prompt, source); try submit(prompt)
        output("submitted", "连接脚本已输入，正在等待 Claude 确认")
    }
    func closeConsole() throws {
        // Only called after the Python owner verifies the signed heartbeat.
        // The Claude shortcut opens/brings forward tools; it does not close.
        // Docked or unrecognized tools are left open rather than closing Claude.
        let windows = attribute(root, kAXWindowsAttribute) as? [AXUIElement] ?? []
        for window in windows {
            var owner: pid_t = 0
            guard AXUIElementGetPid(window, &owner) == .success,
                  devToolsWindow(role: text(window, kAXRoleAttribute), title: text(window, kAXTitleAttribute),
                                 owner: owner, expectedOwner: app.processIdentifier),
                  find(window, matching: { isPrompt($0) || isConsoleTab($0) }) != nil else { continue }
            // AX closes this verified window directly, including in the background.
            // Never activate Claude or send a keyboard shortcut for cleanup.
            try requireDesktop()
            try check(foreground: false)
            guard let close = child(window, kAXCloseButtonAttribute),
                  AXUIElementGetPid(close, &owner) == .success, owner == app.processIdentifier,
                  AXUIElementPerformAction(close, kAXPressAction as CFString) == .success else {
                try fail("needs-attention", "Claude 已连接，但未能关闭开发者工具窗口")
            }
            let deadline = Date().addingTimeInterval(3)
            var closed = false
            while Date() < deadline {
                if let remaining = attribute(root, kAXWindowsAttribute) as? [AXUIElement],
                   !remaining.contains(where: { CFEqual($0, window) }) { closed = true; break }
                Thread.sleep(forTimeInterval: 0.1)
            }
            if !closed { try fail("needs-attention", "Claude 已连接，但未能关闭开发者工具窗口") }
        }
        if find(root, matching: isPrompt) != nil { try fail("needs-attention", "Claude 已连接；开发者工具未自动关闭，请手动关闭") }
        output("connected", "Claude 已连接")
    }
    func inspectError() {
        let error = find(root, matching: {
            guard text($0, kAXRoleAttribute) == "AXStaticText" else { return false }
            let value = text($0, kAXValueAttribute).isEmpty ? text($0, kAXTitleAttribute) : text($0, kAXValueAttribute)
            return (value.hasPrefix("Error:") || value.hasPrefix("Uncaught")) &&
                (value.contains("尚未信任目录") || value.contains("WorkspaceTrustError") || value.contains("trust_required"))
        })
        output(error == nil ? "failed" : "needs-trust", error == nil
               ? "未收到 Claude 连接确认，请检查登录和目录信任后重试"
               : "请在 Claude Code 中确认连接目录信任，然后重新连接")
    }
}

let args = Array(CommandLine.arguments.dropFirst())
do {
    if args.first == "--self-check" {
        let parents = ["editor-child": "expected-console", "other-editor": "other-console"]
        guard focusedWithin("expected-console", focused: "editor-child", parent: { parents[$0] }, equal: ==),
              !focusedWithin("expected-console", focused: "other-editor", parent: { parents[$0] }, equal: ==),
              !focusedWithin("expected-console", focused: "other-console", parent: { parents[$0] }, equal: ==) else {
            throw SetupError(state: "error", reason: "Exact Console focus self-check failed")
        }
        guard promptName("Console prompt"), !promptName("Message Claude") else { throw SetupError(state: "error", reason: "self-check failed") }
        // Observed Claude 2.26454.0: Elements selected, Console has only a
        // description; after selecting it the editor is "Console prompt".
        guard consoleTab(role: "AXRadioButton", identifier: "", description: "Console", title: ""),
              consoleTab(role: "AXTab", identifier: "", description: "控制台", title: ""),
              consoleTab(role: "AXButton", identifier: "", description: "", title: "Console"),
              consoleTab(role: "AXTab", identifier: "Console", description: "", title: ""),
              !consoleTab(role: "AXTextArea", identifier: "", description: "Console", title: ""),
              !consoleTab(role: "AXStaticText", identifier: "", description: "Console", title: ""),
              !consoleTab(role: "AXRadioButton", identifier: "", description: "Elements", title: ""),
              !consoleTab(role: "AXButton", identifier: "", description: "Message Claude", title: "") else {
            throw SetupError(state: "error", reason: "Console tab selector self-check failed")
        }
        guard submissionAccepted(""), submissionAccepted("\n"),
              !submissionAccepted(nil), !submissionAccepted(" "), !submissionAccepted("/* codex bridge connector */script"),
              devToolsWindow(role: "AXWindow", title: "Developer Tools - app://localhost/epitaxy?coldLaunch=1", owner: 42, expectedOwner: 42),
              devToolsWindow(role: "AXWindow", title: "Developer Tools", owner: 42, expectedOwner: 42),
              !devToolsWindow(role: "AXWindow", title: "Claude", owner: 42, expectedOwner: 42),
              !devToolsWindow(role: "AXWindow", title: "Developer Tools project", owner: 42, expectedOwner: 42),
              !devToolsWindow(role: "AXGroup", title: "Developer Tools", owner: 42, expectedOwner: 42),
              !devToolsWindow(role: "AXWindow", title: "Developer Tools", owner: 77, expectedOwner: 42) else {
            throw SetupError(state: "error", reason: "Console submission and window guard self-check failed")
        }
        output("ready", "Native Console selectors, submission and window guards ready")
    } else if args.first == "--wait-desktop" {
        // Read-only wait: no activation, permission prompts, AX actions or
        // keyboard events. The Python owner cancels only this helper process.
        while currentDesktopIssue() != nil {
            RunLoop.current.run(until: Date().addingTimeInterval(2))
        }
        output("ready", "桌面会话可交互")
    } else {
        try requireDesktop()
        let prompt = args.first == "--request-permission"
        let trusted = AXIsProcessTrustedWithOptions([kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: prompt] as CFDictionary)
        guard trusted else { try fail("needs-permission", "请在系统设置中允许 Claude 连接组件使用辅助功能，授权后会继续连接") }
        if args.first == "--check" || prompt { output("ready", "辅助功能已授权") }
        else {
            guard args.count >= 3, let pid = Int32(args[1]) else { try fail("needs-attention", "连接组件参数无效") }
            let connector = try Connector(pid: pid, executable: args[2], cancelPath: args.count > 4 ? args[4] : nil)
            if args[0] == "--enable-devtools" { try connector.developerMode() }
            else if args[0] == "--connect" && args.count == 5 { try connector.inject(args[3]) }
            else if args[0] == "--close-devtools" { try connector.closeConsole() }
            else if args[0] == "--inspect-error" { connector.inspectError() }
            else { try fail("needs-attention", "连接组件参数无效") }
        }
    }
} catch let error as SetupError { output(error.state, error.reason) }
catch { output("needs-attention", "Claude 连接组件未能完成操作，请重试") }
