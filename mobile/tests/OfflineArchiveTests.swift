import Foundation
import CryptoKit

@main
struct OfflineArchiveTests {
    static func check(_ result: Bool, _ reason: String) {
        guard result else { fatalError(reason) }
    }
    static func main() throws {
        let testURL = URL(fileURLWithPath: CommandLine.arguments[1])
        let archive = OfflineArchive(url: testURL)
        let route = "https://one.example", alternate = "https://two.example"
        var account = try archive.bind(address: route, name: "One", gateway: "gateway", identity: "login")
        try archive.linkRoute(from: route, to: alternate)
        check(try archive.account(address: alternate)?.id == account.id, "proven alternate route should share the archive")
        let other = try archive.bind(address: "https://other.example", name: "Other", gateway: "other", identity: "login")
        check(other.id != account.id, "different gateways must not share data")

        func merge(_ session: String = "session", id: String = "a", time: Int64 = 100, manual: Bool = true,
                   version: String = "v", part: [String: Any]? = nil, turns: Bool = true, deleted: [String] = []) throws {
            var row: [String: Any] = ["key": id + "-row", "version": version, "text": "preview", "role": "assistant", "order": 1]
            if let part { row["bodyPart"] = part }
            let turn: [String: Any] = ["id": id, "questionTime": time, "messages": [row]]
            let input: [String: Any] = ["sessionId": session, "title": session, "clearRevision": account.clearRevision,
                "syncedAt": Int64(1_700_000_000_000), "complete": true, "turns": turns ? [turn] : [], "deletedKeys": deleted]
            let request = try JSONSerialization.jsonObject(with: JSONSerialization.data(withJSONObject: input)) as! [String: Any]
            try archive.merge(account, event: request, manual: manual, legacyScope: "legacy:" + session)
        }
        func seed(_ account: OfflineArchive.Account, _ session: String = "session") throws -> [[String: Any]] {
            try archive.seed(account, event: ["sessionId": session])
        }
        try merge(part: ["offset": 0, "text": "ab", "next": 2])
        try merge(time: 0, part: ["offset": 2, "text": "cd", "next": NSNull()])
        try merge(id: "b", time: 300)
        try merge("another")
        let first = try seed(account).first { $0["id"] as? String == "a" }
        let message = (first?["messages"] as? [[String: Any]])?.first
        check(first?["questionTime"] as? Int64 == 100, "later process/body updates must preserve the original date")
        check((message?["bodyParts"] as? [[String: Any]])?.count == 2, "parts must be stored by offset")
        let body = try archive.body(account, event: ["sessionId": "session", "body": ["key": "a-row", "version": "v", "offset": Int64(2)]])
        check(body?["text"] as? String == "cd", "a requested cached chunk must be returned without other rows")
        check(try archive.legacyScopes([account.id], from: 90, before: 130) == ["legacy:session", "legacy:another"], "legacy cleanup must be scoped to affected sessions")
        check(try seed(other).isEmpty, "other gateway must not read the source archive")

        try archive.clearTurns([account.id], from: 100, before: 300)
        do { try merge(); fatalError("old clearRevision accepted") } catch OfflineArchive.Failure.invalidContent {}
        account = try archive.account(id: account.id)!
        try merge(manual: false)
        check(try archive.tombstones(account)["session"] == ["a"], "prefetch must respect local deletion")
        check(try seed(account).compactMap { $0["id"] as? String } == ["b"], "end date boundary must remain intact")
        try merge()
        check(try archive.tombstones(account)["session"] == nil, "a fresh manual read can restore its own deleted turn")
        try merge(turns: false, deleted: ["a-row"])
        check(try seed(account).compactMap { $0["id"] as? String } == ["b"], "server deletion must remove the named message")

        for index in 0..<20 {
            try merge("paged", id: String(format: "%02d", index), time: 500)
        }
        let recent = try archive.readerPage(account, session: "paged", before: nil)!
        let firstIDs = (recent["turns"] as! [[String: Any]]).map { $0["id"] as! String }
        let cursor = recent["before"] as! String
        let older = try archive.readerPage(account, session: "paged", before: cursor)!
        let olderIDs = (older["turns"] as! [[String: Any]]).map { $0["id"] as! String }
        check(firstIDs == (4..<20).map { String(format: "%02d", $0) }, "recent page must use stable turn identity ties")
        check(olderIDs == (0..<4).map { String(format: "%02d", $0) } && older["before"] is NSNull, "earlier page must not duplicate recent rows")
        check(try archive.readerPage(account, session: "session", before: cursor) == nil, "cursor belongs to its session")
        check(try archive.readerPage(other, session: "paged", before: nil) == nil, "reader pages must isolate accounts")

        try archive.status(account, "revoked")
        let revoked = try archive.account(id: account.id)!
        check(try revoked.locked && archive.sessions(revoked).isEmpty, "confirmed revocation must block every session")
        check(try archive.readerPage(revoked, session: "paged", before: nil) == nil, "revocation blocks cached reader pages")
        try archive.status(revoked, "loggedOut")
        check(try archive.account(id: account.id)!.locked, "logout cannot downgrade a confirmed hard lock")
        account = try archive.bind(address: alternate, name: "One", gateway: "gateway", identity: "login")
        check(try archive.hasCachedSession(account, session: "session"), "state can signal cached data before individual reauthorization")
        check(try seed(account).isEmpty, "reauthentication must not unlock a session before a fresh permitted read")
        try merge(turns: false)
        check(try seed(account).count == 1 && seed(account, "another").isEmpty, "an authorized read unlocks only its own session")
        let differentLogin = try archive.bind(address: route, name: "One", gateway: "gateway", identity: "new login")
        check(try differentLogin.id != account.id && seed(differentLogin).isEmpty, "reauthentication as another user cannot disclose old data")

        check(try archive.unlink(address: alternate, erase: false), "unlink should retain cache by default")
        let retained = try archive.account(id: account.id)!
        check(try retained.status == "unlinked" && archive.seed(retained, event: ["sessionId": "session"]).isEmpty, "retained content cannot be read from WebView")
        check((try archive.available(retained: true)).contains { $0.id == retained.id }, "retained archive should remain available for device verification")
        try testTransfer(archive)
        try testReclaimedBytes(archive, url: testURL)
        print("Offline archive persistence, transfer, date deletion, disk compaction, and lock checks passed")
    }

    static func testTransfer(_ archive: OfflineArchive) throws {
        let address = "https://transfer.example"
        var account = try archive.bind(address: address, name: "Transfer", gateway: "transfer", identity: "owner")
        let session = #"["codex","local","thread"]"#, turn = "transfer-turn"
        func hash(_ value: Data) -> String { SHA256.hash(data: value).map { String(format: "%02x", $0) }.joined() }
        func event(_ data: Data, kind: String, asked: Int64 = 1000) -> [String: Any] {
            ["sessionId": session, "turnId": turn, "questionTime": asked, "version": hash(data), "kind": kind,
             "clearRevision": account.clearRevision, "taskClearRevision": account.clearRevision,
             "bytes": Int64(data.count), "index": Int64(0), "key": "row", "recordVersion": "body-v1",
             "title": "Transferred chat", "syncedAt": Int64(1500), "complete": false, "attachmentKey": "image1", "mediaType": "image/png"]
        }
        func save(_ value: Data, event: [String: Any]) throws {
            let chunkSize = OfflineArchive.transferChunkBytes
            for offset in stride(from: 0, to: value.count, by: chunkSize) {
                let bytes = value.subdata(in: offset..<min(value.count, offset + chunkSize))
                var chunk = event
                chunk["index"] = Int64(offset / chunkSize)
                check(try archive.putTransferChunk(account, chunk, data: bytes, hash: hash(bytes))["saved"] as? Bool == true,
                      "Only verified chunks should be saved")
            }
        }
        let text = String(repeating: "abc", count: 200_000)
        let record: [String: Any] = ["id": turn, "questionTime": 1000, "messages": [[
            "key": "row", "version": "body-v1", "text": "abc", "role": "assistant",
            "row": ["truncated": true], "bodyParts": [
            "0": ["offset": 0, "text": "😀", "next": 1], "1": ["offset": 1, "text": text, "next": NSNull()]
           ]
        ]]]
        let data = try JSONSerialization.data(withJSONObject: record)
        var recordEvent = event(data, kind: "record")
        recordEvent["textLength"] = Int64(text.utf16.count + 2)
        let bad = try archive.putTransferChunk(account, recordEvent, data: data.prefix(OfflineArchive.transferChunkBytes), hash: "wrong")
        check(bad["reason"] as? String == "integrity", "a bad hash must be rejected")
        try save(data, event: recordEvent)
        check(try archive.transferChunkHas(account, version: hash(data), index: 1), "checked second chunk is durable")
        _ = try archive.saveTransferTask(account, ["task": ["state": "paused", "index": 1]])
        check(try (archive.transferTask(account)["task"] as? [String: Any])?["index"] as? Int == 1, "task cursor survives independent reads")
        check(try archive.commitTransfer(account, recordEvent, attachment: false)["saved"] as? Bool == true, "record commits into the normal archive")
        check(try archive.transferCached(account, recordEvent)["present"] as? Bool == true, "verified row and complete parts are reusable")
        let page = try archive.readerPage(account, session: session, before: nil)!
        check(page["gaps"] as? Bool == true, "a partial job may not mark a history complete")
        let rows = page["turns"] as! [[String: Any]]
        let content = rows[0]["content"] as! [String: Any]
        let message = (content["messages"] as! [String: [String: Any]])["row"]!
        check((message["bodyParts"] as? [String: [String: Any]])?["1"]?["text"] as? String == text, "full body survives commit with scalar offsets")

        let image = Data(repeating: 42, count: OfflineArchive.transferChunkBytes + 11)
        let attachment = event(image, kind: "attachment")
        try save(image, event: attachment)
        check(try archive.commitTransfer(account, attachment, attachment: true)["saved"] as? Bool == true, "verified attachment commits")
        let lookup = ["sessionId": session, "turnId": turn, "attachmentKey": "image1"]
        var alias = attachment
        alias["attachmentKey"] = "different-path.png"
        check(try archive.transferCached(account, alias)["missingBytes"] as? Int64 == 0, "aliases reuse bytes")
        check(try archive.transferCached(account, alias)["present"] as? Bool == false, "new alias must still bind")
        check(try archive.commitTransfer(account, alias, attachment: true)["saved"] as? Bool == true, "alias binds without a new fetch")
        check(try archive.transferAttachment(account, ["sessionId": session,"turnId": turn,"attachmentKey": "different-path.png"])?["bytes"] as? Int64 == Int64(image.count),
              "both names resolve to the same verified content")
        check(try archive.transferAttachment(account, lookup)?["bytes"] as? Int64 == Int64(image.count), "existing row can resolve original by attachment identity")
        let exported = try archive.exportTransferAttachment(account, lookup, name: "image.png")!
        check(try Data(contentsOf: exported) == image, "native opening exports original bytes")
        let finalized = try archive.completeTransferSession(account, ["sessionId": session,
            "clearRevision": account.clearRevision, "taskClearRevision": account.clearRevision])
        check(finalized["complete"] as? Bool == true, "completion confirmation marks an intact session")
        check(try archive.readerPage(account, session: session, before: nil)?["gaps"] as? Bool == false,
              "an intact completed job clears the partial label")
        try archive.clearTurns([account.id], from: 1000, before: 1001)
        account = try archive.account(id: account.id)!
        var late = attachment
        late["clearRevision"] = account.clearRevision
        check(try archive.transferCached(account, late)["cleared"] as? Bool == true, "older task sees the date clear with current revision")
        check(try archive.completeTransferSession(account, late)["complete"] as? Bool == false, "cleared session never finalizes as complete")
        check(try archive.putTransferChunk(account, late, data: image.prefix(OfflineArchive.transferChunkBytes), hash: "wrong")["reason"] as? String == "cleared",
              "late receipt cannot write cleared dates")
        check(try archive.transferAttachment(account, lookup) == nil, "cleared attachment cannot be opened")
        check(!FileManager.default.fileExists(atPath: exported.path), "clear removes app-owned opening copy too")
        let outside = event(image, kind: "attachment", asked: 1001)
        var unaffected = outside
        unaffected["taskClearRevision"] = recordEvent["taskClearRevision"]
        check(try archive.transferCached(account, unaffected)["cleared"] as? Bool == false, "date clear must not block another turn")
        check(try archive.transferCached(account, event(image, kind: "attachment"))["cleared"] as? Bool == false, "a new deliberate job can restore dates")
    }

    static func testReclaimedBytes(_ archive: OfflineArchive, url: URL) throws {
        let account = try archive.bind(address: "https://space.example", name: "Space", gateway: "space", identity: "owner")
        let word = String(repeating: "x", count: 1024 * 1024)
        for index in 0..<16 {
            let message: [String: Any] = ["key": "row-\(index)", "version": "v1", "text": word, "role": "assistant"]
            let turn: [String: Any] = ["id": String(index), "questionTime": Int64(2000), "messages": [message]]
            try archive.merge(account, event: ["sessionId": "large", "clearRevision": account.clearRevision,
                                               "syncedAt": Int64(2001), "turns": [turn]], manual: true)
        }
        let before = try FileManager.default.attributesOfItem(atPath: url.path)[.size] as! NSNumber
        let released = try archive.clearTurns([account.id], from: 2000, before: 2001)
        let after = try FileManager.default.attributesOfItem(atPath: url.path)[.size] as! NSNumber
        check(released >= 16 * 1024 * 1024 && before.int64Value - after.int64Value == released,
              "clearing 16 MiB must lower physical size and report the reclaimed bytes")
        check(try archive.tombstones(account)["large"]?.count == 16, "cleared content remains represented by small tombstones")
        print("Archive disk size: \(before) -> \(after) bytes; reclaimed \(released)")
    }

}
