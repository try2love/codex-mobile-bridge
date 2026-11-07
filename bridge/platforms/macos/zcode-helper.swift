// Explicit local operations on an existing ZCode conversation. No global key events.
import AppKit
import ApplicationServices
import Foundation

struct Failure: Error { let message: String; init(_ s: String) { message = s } }
struct Request: Decodable {
    let action, executable, sessionId, title, text: String
    let expectedPid: Int32?
}
typealias Node = AXUIElement
func attr(_ e: Node, _ a: String) -> CFTypeRef? {
    var v: CFTypeRef?
    return AXUIElementCopyAttributeValue(e, a as CFString, &v) == .success ? v : nil
}
func str(_ e: Node, _ a: String) -> String { attr(e,a) as? String ?? "" }
func el(_ e: Node, _ a: String) -> Node? {
    guard let v = attr(e,a), CFGetTypeID(v) == AXUIElementGetTypeID() else { return nil }
    return (v as! Node)
}
func children(_ e: Node) -> [Node] { attr(e,"AXChildren") as? [Node] ?? [] }
func role(_ e: Node) -> String { str(e,"AXRole") }
func named(_ e: Node, _ s: String) -> Bool {
    str(e,"AXTitle") == s || str(e,"AXDescription") == s || (role(e) == "AXStaticText" && str(e,"AXValue") == s)
}
func find(_ root: Node, _ predicate: (Node) -> Bool) -> [Node] {
    var q = [root], i = 0, result = [Node]()
    while i < q.count && q.count <= 3500 {
        let e = q[i]; i += 1
        if predicate(e) { result.append(e) }
        q += children(e)
    }
    return q.count <= 3500 ? result : []
}
func one(_ list: [Node], _ why: String) throws -> Node {
    guard list.count == 1 else { throw Failure(why) }
    return list[0]
}
func pump(_ t: TimeInterval = 0.04) { RunLoop.current.run(until: Date(timeIntervalSinceNow:t)) }
func until<T>(_ seconds: TimeInterval, _ f: () throws -> T?) rethrows -> T? {
    let deadline = ProcessInfo.processInfo.systemUptime + seconds
    repeat { if let v = try f() { return v }; pump() } while ProcessInfo.processInfo.systemUptime < deadline
    return nil
}
func press(_ e: Node, _ why: String) throws {
    guard AXUIElementPerformAction(e, kAXPressAction as CFString) == .success else { throw Failure(why) }
}
func enabled(_ e: Node) -> Bool { attr(e,"AXEnabled") as? Bool == true }
func blank(_ value: String) -> Bool { value.trimmingCharacters(in:.newlines).isEmpty }

// ZCode exposes the full native ID through its own menu. Restore the prior
// clipboard only if it still contains the ID produced by this specific copy.
func copiedId(_ item: Node) throws -> String {
    let board = NSPasteboard.general, previous = board.changeCount
    var restore = [NSPasteboardItem](), size = 0
    for entry in board.pasteboardItems ?? [] {
        let copy = NSPasteboardItem()
        for type in entry.types {
            guard let bytes = entry.data(forType:type), bytes.count <= 4*1024*1024-size else {
                throw Failure("剪贴板正被其他内容占用，未更改聊天")
            }
            size += bytes.count
            guard copy.setData(bytes,forType:type) else { throw Failure("无法保留当前剪贴板") }
        }
        restore.append(copy)
    }
    guard board.changeCount == previous else { throw Failure("剪贴板正在变化，已取消") }
    try press(item,"ZCode 未提供原生会话标识")
    guard let (id, version) = until(0.9, { () -> (String,Int)? in
        let v = board.changeCount
        guard v != previous, let s = board.string(forType:.string),
              s.hasPrefix("sess_"), UUID(uuidString:String(s.dropFirst(5))) != nil,
              v == board.changeCount else { return nil }
        return (s,v)
    }) else { throw Failure("未取得 ZCode 原生会话标识") }
    if board.changeCount == version {
        board.clearContents()
        if !restore.isEmpty { board.writeObjects(restore) }
    }
    return id
}

final class Conversation {
    let r: Request, app: NSRunningApplication, root: Node, window: Node
    init(_ r: Request) throws {
        self.r = r
        guard r.action == "resume", !r.title.isEmpty, r.sessionId.hasPrefix("sess_"),
              UUID(uuidString:String(r.sessionId.dropFirst(5))) != nil else { throw Failure("原会话标识无效") }
        let path = URL(fileURLWithPath:r.executable).resolvingSymlinksInPath()
        let found = NSRunningApplication.runningApplications(withBundleIdentifier:"dev.zcode.app").filter {
            !$0.isTerminated && $0.executableURL?.resolvingSymlinksInPath() == path
        }
        guard found.count == 1, let a = found.first, r.expectedPid == nil || r.expectedPid == a.processIdentifier else {
            throw Failure("ZCode 进程已变化，请重新接入会话")
        }
        guard AXIsProcessTrusted() else { throw Failure("请在 Mac 的辅助功能中允许当前网关操作 ZCode") }
        let session = CGSessionCopyCurrentDictionary() as? [String:Any]
        guard session?[kCGSessionOnConsoleKey as String] as? Bool == true,
              session?["CGSSessionScreenIsLocked"] as? Bool != true else { throw Failure("Mac 已锁定，暂不能操作 ZCode 原窗口") }
        app = a; root = AXUIElementCreateApplication(a.processIdentifier)
        AXUIElementSetMessagingTimeout(root,0.4)
        a.activate(options:[])
        guard until(1,{ NSWorkspace.shared.frontmostApplication?.processIdentifier == a.processIdentifier ? true : nil }) != nil,
              let w = el(root,"AXFocusedWindow"), str(w,"AXSubrole") == "AXStandardWindow" else {
            throw Failure("未取得 ZCode 原窗口")
        }
        window = w
    }
    func checkWindow() throws {
        guard !app.isTerminated, NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier,
              let w = el(root,"AXFocusedWindow"), CFEqual(w,window) else { throw Failure("窗口已切换，已停止操作") }
    }
    func heading() -> Node? {
        let hits = find(window) { role($0) == "AXHeading" && named($0,r.title) && (attr($0,"AXValue") as? NSNumber)?.intValue == 1 }
        return hits.count == 1 ? hits[0] : nil
    }
    func select() throws {
        if heading() != nil { return }
        let labels = find(window) { role($0) == "AXStaticText" && named($0,r.title) }
        let rows = labels.compactMap { label -> Node? in
            var n: Node? = label, selectable: Node?
            for _ in 0..<7 {
                guard let e = n else { break }
                if role(e) == "AXList" && str(e,"AXSubrole") == "AXContentList" { return selectable }
                var actions: CFArray?
                if AXUIElementCopyActionNames(e,&actions) == .success,
                   (actions as? [String])?.contains("AXPress") == true { selectable = e }
                n = el(e,"AXParent")
            }
            return nil
        }
        let row = try one(rows,"未能唯一定位原窗口中的聊天，请先在 ZCode 侧栏显示该聊天")
        try checkWindow(); try press(row,"无法选择 ZCode 原聊天")
        guard until(1,{self.heading()}) != nil else { throw Failure("ZCode 没有打开指定聊天") }
    }
    func verifyId() throws {
        try checkWindow()
        guard let h = heading(), let p = el(h,"AXParent") else { throw Failure("聊天已变化，已停止操作") }
        let menu = try one(children(p).filter { role($0) == "AXPopUpButton" && named($0,"更多") },"未找到原会话菜单")
        try press(menu,"无法打开会话菜单")
        guard let copy = until(0.8,{find(self.window) { role($0) == "AXMenuItem" && named($0,"复制会话 ID") }.first}) else {
            throw Failure("原窗口没有提供会话 ID")
        }
        try checkWindow()
        guard try copiedId(copy) == r.sessionId else { throw Failure("原生会话 ID 不一致，已停止操作") }
        guard until(0.8,{self.heading()}) != nil else { throw Failure("聊天已切换，已停止操作") }
    }
    func perform() throws -> String {
        try select(); try verifyId()
        return "ready"
    }
}

if CommandLine.arguments.dropFirst() == ["--self-check"] {
    guard blank("\n") && !blank(" ") else { exit(1) }
    exit(0)
}
var payload: [String:Any]
do {
    let req = try JSONDecoder().decode(Request.self,from:FileHandle.standardInput.readDataToEndOfFile())
    let connection = try Conversation(req)
    payload = ["status":try connection.perform(),"pid":connection.app.processIdentifier]
} catch {
    payload = ["status":"failed","reason":(error as? Failure)?.message ?? "无法读取 ZCode 操作"]
}
let result = try JSONSerialization.data(withJSONObject:payload)
FileHandle.standardOutput.write(result)
