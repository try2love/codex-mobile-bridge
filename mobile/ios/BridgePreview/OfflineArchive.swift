import Foundation
import CryptoKit
import SQLite3

/// Native archive is isolated by the gateway and the authenticated login identity.
final class OfflineArchive {
    struct Account {
        let id: Int64
        let gateway: String
        let identity: String
        let name: String
        let status: String
        let epoch: Int64
        let clearRevision: String

        var locked: Bool { status == "revoked" || status == "unknownRejected" }
        func matches(_ gateway: String, _ identity: String) -> Bool {
            self.gateway == gateway && self.identity == identity
        }
    }
    struct Session {
        let id: String
        let title: String
        let syncedAt: Int64
        let complete: Bool
    }
    struct Turn {
        let questionTime: Int64
        let content: [String: Any]
    }
    enum Failure: Error { case database, invalidContent, space }
    static let queue = DispatchQueue(label: "bridge.offline-archive")
    private var database: OpaquePointer?
    private let suppliedURL: URL?

    init(url: URL? = nil) { suppliedURL = url }
    deinit { sqlite3_close(database) }

    private func databaseURL() throws -> URL {
        let url: URL
        if let suppliedURL { url = suppliedURL }
        else {
            let files = FileManager.default
            var directory = try files.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
                .appendingPathComponent("OfflineArchive", isDirectory: true)
            try files.createDirectory(at: directory, withIntermediateDirectories: true, attributes: [.protectionKey: FileProtectionType.complete])
            var resources = URLResourceValues()
            resources.isExcludedFromBackup = true
            try directory.setResourceValues(resources)
            url = directory.appendingPathComponent("history.sqlite")
        }
        return url
    }

    private func connect() throws -> OpaquePointer {
        if let database { return database }
        var connection: OpaquePointer?
        guard sqlite3_open_v2(try databaseURL().path, &connection, SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE, nil) == SQLITE_OK, let connection else {
            sqlite3_close(connection)
            throw Failure.database
        }
        let schema = """
        PRAGMA foreign_keys=ON;
        PRAGMA secure_delete=ON;
        CREATE TABLE IF NOT EXISTS accounts(id INTEGER PRIMARY KEY, gateway TEXT NOT NULL, identity TEXT NOT NULL, name TEXT NOT NULL, status TEXT NOT NULL, retained INTEGER NOT NULL DEFAULT 0, epoch INTEGER NOT NULL DEFAULT 0, clear_revision INTEGER NOT NULL DEFAULT 0, UNIQUE(gateway,identity));
        CREATE TABLE IF NOT EXISTS routes(address TEXT PRIMARY KEY, account INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS sessions(account INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE, id TEXT NOT NULL, title TEXT NOT NULL, synced INTEGER NOT NULL, complete INTEGER NOT NULL, epoch INTEGER NOT NULL, PRIMARY KEY(account,id));
        CREATE TABLE IF NOT EXISTS legacy_scopes(account INTEGER NOT NULL,session TEXT NOT NULL,scope TEXT NOT NULL,PRIMARY KEY(account,session,scope),FOREIGN KEY(account,session) REFERENCES sessions(account,id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS transfer_clears(account INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE, revision INTEGER NOT NULL, start INTEGER NOT NULL, end INTEGER NOT NULL, PRIMARY KEY(account,revision));
        CREATE TABLE IF NOT EXISTS transfer_tasks(account INTEGER PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE, task TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS transfer_chunks(account INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE, version TEXT NOT NULL, part INTEGER NOT NULL, data BLOB NOT NULL, PRIMARY KEY(account,version,part));
        CREATE TABLE IF NOT EXISTS transfer_items(account INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE, version TEXT NOT NULL, session TEXT NOT NULL, turn TEXT NOT NULL, asked INTEGER NOT NULL, media TEXT, size INTEGER, attachment_key TEXT, PRIMARY KEY(account,version,session,turn));
        CREATE TABLE IF NOT EXISTS transfer_attachment_keys(account INTEGER NOT NULL, version TEXT NOT NULL, session TEXT NOT NULL, turn TEXT NOT NULL, key TEXT NOT NULL, PRIMARY KEY(account,version,session,turn,key), FOREIGN KEY(account,version,session,turn) REFERENCES transfer_items(account,version,session,turn) ON DELETE CASCADE);
        CREATE INDEX IF NOT EXISTS transfer_items_dates ON transfer_items(account,asked);
        CREATE TABLE IF NOT EXISTS offline_preferences(account INTEGER PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE, appearance TEXT);
        CREATE TABLE IF NOT EXISTS turns(account INTEGER NOT NULL, session TEXT NOT NULL, id TEXT NOT NULL, asked INTEGER NOT NULL, content TEXT, suppressed INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(account,session,id), FOREIGN KEY(account,session) REFERENCES sessions(account,id) ON DELETE CASCADE);
        """
        guard sqlite3_exec(connection, schema, nil, nil, nil) == SQLITE_OK else {
            sqlite3_close(connection)
            throw Failure.database
        }
        database = connection
        return connection
    }
    private func rows<T>(_ sql: String, _ values: [Any] = [], _ read: (OpaquePointer) throws -> T) throws -> [T] {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(try connect(), sql, -1, &statement, nil) == SQLITE_OK, let statement else {
            throw Failure.database
        }
        defer { sqlite3_finalize(statement) }
        for (offset, value) in values.enumerated() {
            let index = Int32(offset + 1)
            let status: Int32
            if let number = value as? Int64 { status = sqlite3_bind_int64(statement, index, number) }
            else if let string = value as? String { status = sqlite3_bind_text(statement, index, string, -1, unsafeBitCast(-1, to: sqlite3_destructor_type.self)) }
            else if let data = value as? Data {
                status = data.withUnsafeBytes {
                    sqlite3_bind_blob(statement, index, $0.baseAddress, Int32($0.count), unsafeBitCast(-1, to: sqlite3_destructor_type.self))
                }
            }
            else { throw Failure.invalidContent }
            guard status == SQLITE_OK else { throw Failure.database }
        }
        var result: [T] = []
        while true {
            switch sqlite3_step(statement) {
            case SQLITE_ROW: result.append(try read(statement))
            case SQLITE_DONE: return result
            case SQLITE_FULL: throw Failure.space
            default: throw Failure.database
            }
        }
    }
    private func run(_ sql: String, _ values: [Any] = []) throws { _ = try rows(sql, values) { _ in () } }
    private func transaction<T>(_ block: () throws -> T) throws -> T {
        try run("BEGIN IMMEDIATE")
        do {
            let result = try block()
            try run("COMMIT")
            return result
        } catch {
            try? run("ROLLBACK")
            throw error
        }
    }
    private func text(_ row: OpaquePointer, _ column: Int32) -> String {
        guard let text = sqlite3_column_text(row, column) else { return "" }
        let size = Int(sqlite3_column_bytes(row, column))
        return String(decoding: UnsafeBufferPointer(start: text, count: size), as: UTF8.self)
    }
    private func readAccount(_ row: OpaquePointer) -> Account {
        Account(id: sqlite3_column_int64(row, 0), gateway: text(row, 1), identity: text(row, 2), name: text(row, 3),
                status: text(row, 4), epoch: sqlite3_column_int64(row, 6), clearRevision: String(sqlite3_column_int64(row, 7)))
    }
    func account(address: String) throws -> Account? {
        try rows("SELECT a.* FROM accounts a JOIN routes r ON r.account=a.id WHERE r.address=?", [address], readAccount).first
    }
    func account(id: Int64) throws -> Account? {
        try rows("SELECT * FROM accounts WHERE id=?", [id], readAccount).first
    }
    func bind(address: String, name: String, gateway: String, identity: String) throws -> Account {
        try transaction {
            if let previous = try account(address: address), !previous.matches(gateway, identity) {
                try run("UPDATE accounts SET retained=1, status=CASE WHEN status='valid' THEN 'loggedOut' ELSE status END WHERE id=?", [previous.id])
            }
            try run("INSERT OR IGNORE INTO accounts(gateway,identity,name,status) VALUES(?,?,?,'valid')", [gateway, identity, name])
            try run("UPDATE accounts SET name=?,epoch=epoch+CASE WHEN status IN ('loggedOut','expired','unlinked') THEN 1 ELSE 0 END,status='valid',retained=0 WHERE gateway=? AND identity=?", [name, gateway, identity])
            try run("INSERT OR REPLACE INTO routes(address,account) SELECT ?,id FROM accounts WHERE gateway=? AND identity=?", [address, gateway, identity])
            guard let account = try account(address: address) else { throw Failure.database }
            return account
        }
    }
    func linkRoute(from: String, to: String) throws {
        try run("INSERT OR IGNORE INTO routes(address,account) SELECT ?,account FROM routes WHERE address=?", [to, from])
    }
    func status(_ account: Account?, _ value: String) throws {
        guard let account else { return }
        let lock = value == "revoked" || value == "unknownRejected"
        try run("UPDATE accounts SET status=?, epoch=epoch+? WHERE id=? AND status NOT IN ('revoked','unknownRejected')",
                [value, Int64(lock ? 1 : 0), account.id])
    }
    func unlink(address: String, erase: Bool) throws -> Bool {
        guard let account = try account(address: address) else { return false }
        let retained = try transaction {
            if erase {
                try clearAccountTurns(account.id, from: .min, before: .max)
                try run("DELETE FROM transfer_tasks WHERE account=?", [account.id])
            }
            try status(account, "unlinked")
            try run("UPDATE accounts SET retained=1 WHERE id=?", [account.id])
            try run("DELETE FROM routes WHERE account=?", [account.id])
            return try !erase && hasContent(account)
        }
        if erase {
            _ = try clearAttachmentViews([account.id], from: .min, before: .max)
            _ = try clearStagedRecords([account.id], from: .min, before: .max)
            try compact()
        }
        return retained
    }
    func available(retained: Bool) throws -> [Account] {
        let filter = retained ? "a.retained=1 AND " : ""
        let stored = "EXISTS(SELECT 1 FROM turns t WHERE t.account=a.id AND t.suppressed=0)"
        let partial = retained ? "" : " OR EXISTS(SELECT 1 FROM transfer_items t WHERE t.account=a.id) OR EXISTS(SELECT 1 FROM transfer_tasks j WHERE j.account=a.id)"
        return try rows("SELECT a.* FROM accounts a WHERE " + filter + "(" + stored + partial + ") ORDER BY a.name,a.id", [], readAccount)
    }
    func hasContent(_ account: Account) throws -> Bool {
        try !rows("SELECT 1 FROM turns WHERE account=? AND suppressed=0 LIMIT 1", [account.id]) { _ in true }.isEmpty
    }
    func appearance(_ account: Account) throws -> [String: Any]? {
        try rows("SELECT appearance FROM offline_preferences WHERE account=?", [account.id]) { try decode(text($0, 0)) }.first
    }
    func saveAppearance(_ account: Account, _ value: [String: Any]) throws {
        let data = try JSONSerialization.data(withJSONObject: value)
        guard data.count <= 4096 else { throw Failure.invalidContent }
        try run("INSERT OR REPLACE INTO offline_preferences(account,appearance) VALUES(?,?)", [account.id, String(decoding: data, as: UTF8.self)])
    }
    func tombstones(_ account: Account, session: String = "") throws -> [String: [String]] {
        let query = "SELECT session,id FROM turns WHERE account=? AND suppressed=1"
        let parameters: [Any] = session.isEmpty ? [account.id] : [account.id, session]
        let values = try rows(query + (session.isEmpty ? "" : " AND session=?"), parameters) { (text($0, 0), text($0, 1)) }
        var result: [String: [String]] = [:]
        for (session, id) in values { result[session, default: []].append(id) }
        return result
    }
    func merge(_ previous: Account, event: [String: Any], manual: Bool, legacyScope: String = "") throws {
        guard let account = try account(id: previous.id), account.status == "valid", event["clearRevision"] as? String == account.clearRevision,
              let session = event["sessionId"] as? String,
              let synced = event["syncedAt"] as? Int64, let turns = event["turns"] as? [[String: Any]] else {
            throw Failure.invalidContent
        }
        let complete: Int64 = event["complete"] as? Bool == true ? 1 : 0
        let title = event["title"] as? String ?? ""
        let canRestore = manual
        try transaction {
            try run("INSERT OR IGNORE INTO sessions(account,id,title,synced,complete,epoch) VALUES(?,?,?,?,?,?)",
                    [account.id, session, title, synced, complete, account.epoch])
            try run("UPDATE sessions SET title=CASE WHEN synced<=? AND ?<>'' THEN ? ELSE title END,synced=MAX(synced,?),complete=MAX(complete,?),epoch=? WHERE account=? AND id=?",
                    [synced, title, title, synced, complete, account.epoch, account.id, session])
            if !legacyScope.isEmpty { try run("INSERT OR IGNORE INTO legacy_scopes(account,session,scope) VALUES(?,?,?)", [account.id, session, legacyScope]) }
            for turn in turns {
                guard let id = turn["id"] as? String, !id.isEmpty, let asked = turn["questionTime"] as? Int64 else { throw Failure.invalidContent }
                let stored = try rows("SELECT content,suppressed FROM turns WHERE account=? AND session=? AND id=?", [account.id, session, id]) {
                    (text($0, 0), sqlite3_column_int($0, 1) != 0)
                }.first
                if stored?.1 == true && !canRestore { continue }
                var content = try stored.flatMap { $0.0.isEmpty ? nil : try decode($0.0) } ?? [:]
                var messages = content["messages"] as? [String: [String: Any]] ?? [:]
                for var message in turn["messages"] as? [[String: Any]] ?? [] {
                    guard let key = message["key"] as? String, let version = message["version"] as? String, !version.isEmpty else { throw Failure.invalidContent }
                    if synced >= (messages[key]?["_syncedAt"] as? Int64 ?? Int64.min) {
                        let previous = messages[key]
                        var parts = previous?["version"] as? String == version ? previous?["bodyParts"] as? [String: [String: Any]] ?? [:] : [:]
                        if let imported = message.removeValue(forKey: "bodyParts") as? [String: [String: Any]] {
                            parts.merge(imported) { _, incoming in incoming }
                        }
                        if let part = message.removeValue(forKey: "bodyPart") as? [String: Any], let offset = part["offset"] as? Int64 {
                            parts[String(offset)] = part
                        }
                        if !parts.isEmpty { message["bodyParts"] = parts }
                        message["_syncedAt"] = synced
                        messages[key] = message
                    } else if var current = messages[key], current["version"] as? String == version,
                              let verified = message["bodyParts"] as? [String: [String: Any]] {
                        var parts = current["bodyParts"] as? [String: [String: Any]] ?? [:]
                        parts.merge(verified) { previous, _ in previous }
                        current["bodyParts"] = parts
                        messages[key] = current
                    }
                }
                content["messages"] = messages
                for field in ["question", "answer"] { if let value = turn[field] as? String { content[field] = value } }
                let encoded = try encode(content)
                try run("INSERT OR IGNORE INTO turns(account,session,id,asked,content,suppressed) VALUES(?,?,?,?,?,0)",
                        [account.id, session, id, asked, encoded])
                try run("UPDATE turns SET asked=CASE WHEN asked=0 THEN ? ELSE asked END,content=?,suppressed=0 WHERE account=? AND session=? AND id=?",
                        [asked, encoded, account.id, session, id])
            }
            if let deleted = event["deletedKeys"] as? [String], !deleted.isEmpty {
                let stored = try rows("SELECT id,content FROM turns WHERE account=? AND session=? AND suppressed=0", [account.id, session]) {
                    (text($0, 0), try decode(text($0, 1)))
                }
                for (id, original) in stored {
                    var content = original
                    guard var messages = content["messages"] as? [String: [String: Any]] else { continue }
                    let count = messages.count
                    for key in deleted { messages.removeValue(forKey: key) }
                    if messages.count != count {
                        content["messages"] = messages
                        content.removeValue(forKey: "question")
                        content.removeValue(forKey: "answer")
                        try run("UPDATE turns SET content=? WHERE account=? AND session=? AND id=?", [try encode(content), account.id, session, id])
                    }
                }
            }
        }
    }
    private func decode(_ string: String) throws -> [String: Any] {
        guard let object = try JSONSerialization.jsonObject(with: Data(string.utf8)) as? [String: Any] else { throw Failure.invalidContent }
        return object
    }
    private func encode(_ content: [String: Any]) throws -> String {
        String(decoding: try JSONSerialization.data(withJSONObject: content), as: UTF8.self)
    }
    func sessions(_ account: Account) throws -> [Session] {
        guard !account.locked else { return [] }
        return try rows("SELECT s.id,s.title,s.synced,s.complete FROM sessions s WHERE s.account=? AND s.epoch=? " +
                        "AND EXISTS(SELECT 1 FROM turns t WHERE t.account=s.account AND t.session=s.id AND t.suppressed=0) ORDER BY s.synced DESC",
                        [account.id, account.epoch]) {
            Session(id: text($0, 0), title: text($0, 1), syncedAt: sqlite3_column_int64($0, 2), complete: sqlite3_column_int($0, 3) != 0)
        }
    }
    func readerPage(_ account: Account, session id: String, before: String?) throws -> [String: Any]? {
        guard !account.locked, let session = try rows(
            "SELECT id,title,synced,complete FROM sessions WHERE account=? AND id=? AND epoch=?",
            [account.id, id, account.epoch], {
                Session(id: text($0, 0), title: text($0, 1), syncedAt: sqlite3_column_int64($0, 2), complete: sqlite3_column_int($0, 3) != 0)
            }).first else { return nil }
        var parameters: [Any] = [account.id, id]
        var filter = ""
        if let before {
            guard before.utf8.count <= 8192, let data = Data(base64Encoded: before),
                  let cursor = try JSONSerialization.jsonObject(with: data) as? [String],
                  cursor.count == 5, cursor[0] == id, cursor[1] == account.clearRevision,
                  cursor[2] == String(account.epoch), let time = Int64(cursor[3]) else { return nil }
            filter = " AND (asked<? OR (asked=? AND id<?))"
            parameters += [time, time, cursor[4]]
        }
        let pageSize = 16
        let entries = try rows("SELECT id,asked,length(CAST(content AS BLOB)) FROM turns WHERE account=? AND session=? AND suppressed=0"
                               + filter + " ORDER BY asked DESC,id DESC LIMIT ?", parameters + [Int64(pageSize + 1)]) {
            (text($0, 0), sqlite3_column_int64($0, 1), sqlite3_column_int64($0, 2))
        }
        var visible: [[String: Any]] = []
        var totalBytes: Int64 = 0
        for (key, time, size) in entries.prefix(pageSize) {
            totalBytes += size
            if !visible.isEmpty && totalBytes > 2 * 1024 * 1024 { break }
            guard let content = try rows("SELECT content FROM turns WHERE account=? AND session=? AND id=? AND suppressed=0",
                                         [account.id, id, key], { try decode(text($0, 0)) }).first else { throw Failure.invalidContent }
            visible.append(["id": key, "questionTime": time, "content": content])
        }
        var next: Any = NSNull()
        if entries.count > visible.count && !visible.isEmpty {
            let (key, stamp, _) = entries[visible.count - 1]
            next = try JSONSerialization.data(withJSONObject: [id, account.clearRevision, String(account.epoch), String(stamp), key]).base64EncodedString()
        }
        let suppressed = try hasGaps(account, session: id)
        let gaps = !session.complete || suppressed
        return ["id": id, "title": session.title, "syncedAt": session.syncedAt,
                "gaps": gaps, "turns": Array(visible.reversed()), "before": next]
    }
    func turns(_ account: Account, session: String) throws -> [Turn] {
        guard !account.locked else { return [] }
        return try rows("SELECT t.asked,t.content FROM turns t JOIN sessions s ON t.account=s.account AND t.session=s.id WHERE t.account=? AND t.session=? AND t.suppressed=0 AND s.epoch=? ORDER BY t.asked,t.rowid",
                        [account.id, session, account.epoch]) {
            Turn(questionTime: sqlite3_column_int64($0, 0), content: try decode(text($0, 1)))
        }
    }
    func body(_ account: Account, event: [String: Any]) throws -> [String: Any]? {
        guard account.status == "valid", let session = event["sessionId"] as? String, let body = event["body"] as? [String: Any],
              let key = body["key"] as? String, let version = body["version"] as? String, let offset = body["offset"] as? Int64 else { return nil }
        var found: [String: Any]?
        _ = try rows("SELECT t.content FROM turns t JOIN sessions s ON t.account=s.account AND t.session=s.id WHERE t.account=? AND t.session=? AND t.suppressed=0 AND s.epoch=?",
                     [account.id, session, account.epoch]) { row in
            guard found == nil else { return }
            let content = try decode(text(row, 0))
            guard let message = (content["messages"] as? [String: [String: Any]])?[key], message["version"] as? String == version,
                  var part = (message["bodyParts"] as? [String: [String: Any]])?[String(offset)] else { return }
            part["key"] = key
            part["version"] = version
            found = part
        }
        return found
    }
    func seed(_ account: Account, event: [String: Any]) throws -> [[String: Any]] {
        guard account.status == "valid", let session = event["sessionId"] as? String else { return [] }
        let keys = Set((event["keys"] as? [String] ?? []).prefix(100))
        let ids = Set((event["turnIds"] as? [String] ?? []).prefix(100))
        let filtered = !keys.isEmpty || !ids.isEmpty
        var bytes = 0
        var values: [[String: Any]] = []
        let suffix = filtered ? "" : " LIMIT 100"
        _ = try rows("SELECT t.id,t.asked,t.content FROM turns t JOIN sessions s ON t.account=s.account AND t.session=s.id WHERE t.account=? AND t.session=? AND t.suppressed=0 AND s.epoch=? ORDER BY t.asked DESC,t.rowid DESC" + suffix,
                     [account.id, session, account.epoch]) { row in
            guard values.count < 100, bytes < 512_000 else { return }
            let id = text(row, 0), content = try decode(text(row, 2))
            let stored = content["messages"] as? [String: [String: Any]] ?? [:]
            let messages = stored.compactMap { key, message -> [String: Any]? in
                guard !filtered || ids.contains(id) || keys.contains(key) else { return nil }
                var message = message
                message.removeValue(forKey: "_syncedAt")
                if let parts = message.removeValue(forKey: "bodyParts") as? [String: [String: Any]] {
                    var chunks: [[String: Any]] = [], offset: Int64 = 0
                    while chunks.count < 2, let part = parts[String(offset)] {
                        chunks.append(part)
                        guard let next = part["next"] as? Int64, next > offset else { break }
                        offset = next
                    }
                    if !chunks.isEmpty { message["bodyParts"] = chunks }
                }
                return message
            }
            guard !messages.isEmpty else { return }
            let item: [String: Any] = ["id": id, "questionTime": sqlite3_column_int64(row, 1), "messages": messages]
            let size = try JSONSerialization.data(withJSONObject: item).count
            guard bytes + size <= 512_000 else { return }
            bytes += size
            values.append(item)
        }
        return values
    }
    func hasCachedSession(_ account: Account, session: String) throws -> Bool {
        try !rows("SELECT 1 FROM turns WHERE account=? AND session=? AND suppressed=0 LIMIT 1", [account.id, session]) { _ in true }.isEmpty
    }
    func hasGaps(_ account: Account, session: String) throws -> Bool {
        try !rows("SELECT 1 FROM turns WHERE account=? AND session=? AND suppressed=1 LIMIT 1", [account.id, session]) { _ in true }.isEmpty
    }
    func legacyScopes(_ accounts: [Int64], from start: Int64, before end: Int64) throws -> Set<String> {
        var scopes = Set<String>()
        for id in accounts {
            let matches = try rows("SELECT DISTINCT l.scope FROM legacy_scopes l JOIN turns t ON t.account=l.account AND t.session=l.session WHERE t.account=? AND t.asked>=? AND t.asked<? AND t.suppressed=0", [id, start, end]) { text($0, 0) }
            scopes.formUnion(matches)
        }
        return scopes
    }
    private func databaseBytes() throws -> Int64 {
        let path = try databaseURL().path
        let files = FileManager.default
        return ["", "-wal", "-journal", "-shm"].reduce(0) { total, suffix in
            total + ((try? files.attributesOfItem(atPath: path + suffix)[.size] as? NSNumber)?.int64Value ?? 0)
        }
    }
    @discardableResult
    func clearTurns(_ accounts: [Int64], from start: Int64, before end: Int64) throws -> Int64 {
        let before = try databaseBytes()
        try transaction { for id in accounts { try clearAccountTurns(id, from: start, before: end) } }
        let files = try clearAttachmentViews(accounts, from: start, before: end) + clearStagedRecords(accounts, from: start, before: end)
        try compact()
        return max(0, before - (try databaseBytes())) + files
    }
    private func clearAccountTurns(_ id: Int64, from start: Int64, before end: Int64) throws {
        try run("UPDATE accounts SET clear_revision=clear_revision+1 WHERE id=?", [id])
        try run("INSERT INTO transfer_clears(account,revision,start,end) SELECT id,clear_revision,?,? FROM accounts WHERE id=?", [start, end, id])
        try run("UPDATE turns SET content=NULL,suppressed=1 WHERE account=? AND asked>=? AND asked<? AND suppressed=0", [id, start, end])
        try run("""
            INSERT OR IGNORE INTO sessions(account,id,title,synced,complete,epoch)
            SELECT t.account,t.session,'',0,0,a.epoch FROM transfer_items t JOIN accounts a ON a.id=t.account
            WHERE t.account=? AND t.asked>=? AND t.asked<?
            """, [id, start, end])
        try run("""
            INSERT OR IGNORE INTO turns(account,session,id,asked,suppressed)
            SELECT t.account,t.session,t.turn,t.asked,1 FROM transfer_items t
            JOIN sessions s ON s.account=t.account AND s.id=t.session
            WHERE t.account=? AND t.asked>=? AND t.asked<?
            """, [id, start, end])
        try run("DELETE FROM transfer_items WHERE account=? AND asked>=? AND asked<?", [id, start, end])
        try dropOrphanChunks(id)
    }
    private func dropOrphanChunks(_ id: Int64) throws {
        try run("""
            DELETE FROM transfer_chunks WHERE account=? AND NOT EXISTS(
              SELECT 1 FROM transfer_items i WHERE i.account=transfer_chunks.account AND i.version=transfer_chunks.version)
            """, [id])
    }

    static let transferChunkBytes = 524_288

    func freeBytes() throws -> Int64 {
        let attributes = try FileManager.default.attributesOfFileSystem(forPath: databaseURL().deletingLastPathComponent().path)
        return (attributes[.systemFreeSize] as? NSNumber)?.int64Value ?? 0
    }

    static func transferVersion(_ event: [String: Any]) throws -> String {
        guard let version = event["version"] as? String,
              version.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil else { throw Failure.invalidContent }
        return version
    }
    static func transferIndex(_ event: [String: Any]) throws -> Int64 {
        guard let index = event["index"] as? Int64, (0...16_777_215).contains(index) else { throw Failure.invalidContent }
        return index
    }
    private struct TransferItem {
        let version: String
        let session: String
        let turn: String
        let asked: Int64
        let bytes: Int64
        let taskRevision: Int64
        init(_ event: [String: Any]) throws {
            version = try OfflineArchive.transferVersion(event)
            guard let session = event["sessionId"] as? String, (1...4096).contains(session.utf8.count),
                  let turn = event["turnId"] as? String, (1...4096).contains(turn.utf8.count),
                  let asked = event["questionTime"] as? Int64, asked >= 0,
                  let bytes = event["bytes"] as? Int64, (1...8_796_093_022_208).contains(bytes),
                  let revision = (event["taskClearRevision"] ?? event["clearRevision"]) as? String,
                  let taskRevision = Int64(revision), taskRevision >= 0 else { throw Failure.invalidContent }
            self.session = session
            self.turn = turn
            self.asked = asked
            self.bytes = bytes
            self.taskRevision = taskRevision
        }
        var count: Int64 { (bytes - 1) / Int64(OfflineArchive.transferChunkBytes) + 1 }
    }

    private func cleared(_ account: Account, _ item: TransferItem) throws -> Bool {
        try !rows("SELECT 1 FROM transfer_clears WHERE account=? AND revision>? AND start<=? AND end>? LIMIT 1",
                  [account.id, item.taskRevision, item.asked, item.asked]) { _ in true }.isEmpty
    }
    func transferAdmission(_ account: Account, _ event: [String: Any]) throws -> String? {
        let item = try TransferItem(event)
        if try cleared(account, item) { return "cleared" }
        if account.clearRevision != event["clearRevision"] as? String { return "revision" }
        return nil
    }
    private func transferOutcome(_ reason: String?) -> [String: Any] {
        if let reason { return ["saved": false, "reason": reason] }
        return ["saved": true]
    }
    func transferChunk(_ account: Account, version: String, index: Int64) throws -> Data? {
        try rows("SELECT data FROM transfer_chunks WHERE account=? AND version=? AND part=?", [account.id, version, index]) { row in
            guard let bytes = sqlite3_column_blob(row, 0) else { throw Failure.database }
            return Data(bytes: bytes, count: Int(sqlite3_column_bytes(row, 0)))
        }.first
    }
    func transferChunkHas(_ account: Account, version: String, index: Int64) throws -> Bool {
        try !rows("SELECT 1 FROM transfer_chunks WHERE account=? AND version=? AND part=?", [account.id, version, index]) { _ in true }.isEmpty
    }
    private func retainTransferItem(_ account: Account, _ item: TransferItem) throws {
        try run("INSERT OR IGNORE INTO transfer_items(account,version,session,turn,asked) VALUES(?,?,?,?,?)",
                [account.id, item.version, item.session, item.turn, item.asked])
    }
    func putTransferChunk(_ account: Account, _ event: [String: Any], data: Data, hash: String) throws -> [String: Any] {
        guard let current = try self.account(id: account.id), current.epoch == account.epoch, current.status == "valid"
        else { return transferOutcome("permission") }
        if let reason = try transferAdmission(current, event) { return transferOutcome(reason) }
        let item = try TransferItem(event), index = try Self.transferIndex(event)
        let expected = min(Int64(Self.transferChunkBytes), item.bytes - index * Int64(Self.transferChunkBytes))
        guard index < item.count, data.count == expected, Self.hex(SHA256.hash(data: data)) == hash else { return transferOutcome("integrity") }
        if try !transferChunkHas(current, version: item.version, index: index), try freeBytes() < Int64(data.count) * 2 + 1_048_576 {
            return transferOutcome("space")
        }
        do {
            try transaction {
                try retainTransferItem(current, item)
                try run("INSERT OR IGNORE INTO transfer_chunks(account,version,part,data) VALUES(?,?,?,?)",
                        [current.id, item.version, index, data])
            }
            return transferOutcome(nil)
        } catch Failure.space { return transferOutcome("space") }
    }
    private static func hex(_ digest: SHA256.Digest) -> String {
        digest.map { String(format: "%02x", $0) }.joined()
    }
    private func storedTransferBytes(_ account: Account, _ item: TransferItem) throws -> Int64 {
        try rows("SELECT COALESCE(SUM(length(data)),0) FROM transfer_chunks WHERE account=? AND version=? AND part<?",
                 [account.id, item.version, item.count]) { sqlite3_column_int64($0, 0) }.first ?? 0
    }
    func transferCached(_ account: Account, _ event: [String: Any]) throws -> [String: Any] {
        let item = try TransferItem(event)
        if try cleared(account, item) {
            if event["kind"] as? String == "record" {
                try transaction {
                    try run("INSERT OR IGNORE INTO sessions(account,id,title,synced,complete,epoch) VALUES(?,?,'',0,0,?)",
                            [account.id, item.session, account.epoch])
                    try run("INSERT OR IGNORE INTO turns(account,session,id,asked,suppressed) VALUES(?,?,?,?,1)",
                            [account.id, item.session, item.turn, item.asked])
                }
            }
            return ["present": false, "cleared": true, "missingBytes": 0]
        }
        let present: Bool
        if event["kind"] as? String == "attachment" {
            let key = Self.transferAttachmentKey(event)
            present = try !rows("""
                SELECT 1 FROM transfer_items i WHERE i.account=? AND i.version=? AND i.session=? AND i.turn=?
                AND i.media IS NOT NULL AND (i.attachment_key=? OR EXISTS(SELECT 1 FROM transfer_attachment_keys k
                    WHERE k.account=i.account AND k.version=i.version AND k.session=i.session AND k.turn=i.turn AND k.key=?))
                """, [account.id, item.version, item.session, item.turn, key, key]) { _ in true }.isEmpty
        } else if let key = event["key"] as? String, let version = event["recordVersion"] as? String {
            present = try rows("""
                SELECT t.content FROM turns t JOIN sessions s ON t.account=s.account AND t.session=s.id
                WHERE t.account=? AND t.session=? AND t.id=? AND t.suppressed=0 AND s.epoch=?
                """, [account.id, item.session, item.turn, account.epoch]) { row in
                    let content = try decode(text(row, 0))
                    let message = (content["messages"] as? [String: [String: Any]])?[key]
                    return message?["version"] as? String == version && Self.completeMessage(message, length: event["textLength"] as? Int64)
                }.first ?? false
        } else { present = false }
        let missing = present ? 0 : max(0, item.bytes - (try storedTransferBytes(account, item)))
        return ["present": present, "cleared": false, "missingBytes": missing]
    }
    private static func completeMessage(_ message: [String: Any]?, length: Int64?) -> Bool {
        guard let message, let row = message["row"] as? [String: Any] else { return false }
        if row["truncated"] as? Bool != true, let full = (message["text"] ?? row["text"]) as? String {
            return length == nil || length == Int64(full.utf16.count)
        }
        let parts = message["bodyParts"] as? [String: [String: Any]] ?? [:]
        var offset: Int64 = 0, utf16Length: Int64 = 0
        while let part = parts[String(offset)], let text = part["text"] as? String {
            let width = Int64(text.utf16.count)
            utf16Length += width
            if part["next"] is NSNull { return length == nil || length == utf16Length }
            guard let next = part["next"] as? Int64, next > offset,
                  next - offset == Int64(text.unicodeScalars.count) || next - offset == width else { break }
            offset = next
        }
        return false
    }
    func completeTransferSession(_ account: Account, _ event: [String: Any]) throws -> [String: Any] {
        guard let session = event["sessionId"] as? String, (1...4096).contains(session.utf8.count) else { throw Failure.invalidContent }
        guard account.clearRevision == event["clearRevision"] as? String else { return transferOutcome("revision") }
        guard let raw = event["taskClearRevision"] as? String, let revision = Int64(raw), revision >= 0 else { throw Failure.invalidContent }
        let gaps = try !rows("""
            SELECT 1 FROM turns t WHERE t.account=? AND t.session=? AND
            (t.suppressed=1 OR EXISTS(SELECT 1 FROM transfer_clears c WHERE c.account=t.account AND c.revision>?
                AND c.start<=t.asked AND c.end>t.asked)) LIMIT 1
            """, [account.id, session, revision]) { _ in true }.isEmpty
        if gaps { return ["saved": true, "complete": false] }
        try run("UPDATE sessions SET complete=1 WHERE account=? AND id=? AND epoch=?", [account.id, session, account.epoch])
        return ["saved": true, "complete": true]
    }
    func transferTask(_ account: Account) throws -> [String: Any] {
        let value = try rows("SELECT task FROM transfer_tasks WHERE account=?", [account.id]) { row in try decode(text(row, 0)) }.first
        return ["task": value as Any? ?? NSNull()]
    }
    func saveTransferTask(_ account: Account, _ event: [String: Any]) throws -> [String: Any] {
        if event["task"] is NSNull {
            try run("DELETE FROM transfer_tasks WHERE account=?", [account.id])
        } else {
            guard let task = event["task"] as? [String: Any] else { throw Failure.invalidContent }
            let data = try JSONSerialization.data(withJSONObject: task)
            guard data.count <= 262_144 else { throw Failure.invalidContent }
            try run("INSERT OR REPLACE INTO transfer_tasks(account,task) VALUES(?,?)", [account.id, String(decoding: data, as: UTF8.self)])
        }
        return transferOutcome(nil)
    }
    func commitTransfer(_ account: Account, _ event: [String: Any], attachment: Bool) throws -> [String: Any] {
        if let reason = try transferAdmission(account, event) { return transferOutcome(reason) }
        let item = try TransferItem(event)
        var digest = SHA256()
        var count: Int64 = 0
        let temp = try databaseURL().deletingLastPathComponent()
            .appendingPathComponent(".transfer-\(account.id)-\(item.asked)-\(UUID().uuidString)")
        var output: FileHandle?
        defer {
            try? output?.close()
            if !attachment { try? FileManager.default.removeItem(at: temp) }
        }
        do {
            if !attachment {
                if try freeBytes() < item.bytes * 3 + 1_048_576 { return transferOutcome("space") }
                guard FileManager.default.createFile(atPath: temp.path, contents: nil) else { return transferOutcome("space") }
                output = try FileHandle(forWritingTo: temp)
            }
            for index in 0..<item.count {
                guard let block = try transferChunk(account, version: item.version, index: index) else { return transferOutcome("missing") }
                count += Int64(block.count)
                digest.update(data: block)
                try output?.write(contentsOf: block)
            }
            guard count == item.bytes, Self.hex(digest.finalize()) == item.version else { return transferOutcome("integrity") }
            if attachment {
                let media = event["mediaType"] as? String ?? "application/octet-stream"
                let key = Self.transferAttachmentKey(event)
                guard media.range(of: "^[a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+$", options: .regularExpression) != nil,
                      media.utf8.count <= 128, key.utf8.count <= 4096 else { throw Failure.invalidContent }
                try transaction {
                    try retainTransferItem(account, item)
                    try run("UPDATE transfer_items SET media=?,size=?,attachment_key=COALESCE(attachment_key,?) WHERE account=? AND version=? AND session=? AND turn=?",
                            [media, item.bytes, key, account.id, item.version, item.session, item.turn])
                    try run("INSERT OR IGNORE INTO transfer_attachment_keys(account,version,session,turn,key) VALUES(?,?,?,?,?)",
                            [account.id, item.version, item.session, item.turn, key])
                }
            } else {
                try output?.close()
                output = nil
                guard let stream = InputStream(url: temp) else { throw Failure.invalidContent }
                stream.open()
                defer { stream.close() }
                guard let turn = try JSONSerialization.jsonObject(with: stream) as? [String: Any],
                      turn["id"] as? String == item.turn, turn["questionTime"] as? Int64 == item.asked else { return transferOutcome("integrity") }
                var mergeEvent = event
                mergeEvent["turns"] = [turn]
                try merge(account, event: mergeEvent, manual: true)
                try transaction {
                    try run("DELETE FROM transfer_items WHERE account=? AND version=? AND session=? AND turn=?",
                            [account.id, item.version, item.session, item.turn])
                    try dropOrphanChunks(account.id)
                }
            }
            return transferOutcome(nil)
        } catch Failure.space { return transferOutcome("space") }
          catch let error as CocoaError where error.code == .fileWriteOutOfSpace { return transferOutcome("space") }
    }
    private static func transferAttachmentKey(_ event: [String: Any]) -> String {
        event["attachmentKey"] as? String ?? event["path"] as? String ?? event["key"] as? String ?? ""
    }
    func transferAttachment(_ account: Account, _ event: [String: Any]) throws -> [String: Any]? {
        guard let session = event["sessionId"] as? String, (1...4096).contains(session.utf8.count),
              let turn = event["turnId"] as? String, (1...4096).contains(turn.utf8.count) else { return nil }
        let isVersion = event["version"] != nil
        let key: String
        if isVersion { key = try Self.transferVersion(event) }
        else {
            guard let requested = event["attachmentKey"] as? String, (1...4096).contains(requested.utf8.count) else { return nil }
            key = requested
        }
        let matches = isVersion ? "i.version=?" : """
            (i.attachment_key=? OR EXISTS(SELECT 1 FROM transfer_attachment_keys k
                 WHERE k.account=i.account AND k.version=i.version AND k.session=i.session AND k.turn=i.turn AND k.key=?))
            """
        let scope: [Any] = [account.id, session, turn] + (isVersion ? [key] : [key, key]) + [account.epoch]
        let value = try rows("""
            SELECT i.version,i.size,i.media,i.asked FROM transfer_items i
            JOIN sessions s ON s.account=i.account AND s.id=i.session
            JOIN turns t ON t.account=i.account AND t.session=i.session AND t.id=i.turn
            WHERE i.account=? AND i.session=? AND i.turn=? AND \(matches) AND i.media IS NOT NULL AND s.epoch=? AND t.suppressed=0
            ORDER BY i.rowid DESC LIMIT 1
            """, scope) { row in
                (text(row, 0), sqlite3_column_int64(row, 1), text(row, 2), sqlite3_column_int64(row, 3))
            }.first
        guard let (version, bytes, media, asked) = value else { return nil }
        if event["index"] != nil {
            let index = try Self.transferIndex(event)
            guard let data = try transferChunk(account, version: version, index: index) else { return nil }
            return ["data": data.base64EncodedString()]
        }
        return ["version": version, "bytes": bytes, "mediaType": media, "chunkBytes": Self.transferChunkBytes, "questionTime": asked]
    }
    private func clearStagedRecords(_ accounts: [Int64], from start: Int64, before end: Int64) throws -> Int64 {
        guard suppliedURL == nil else { return 0 }
        let folder = try databaseURL().deletingLastPathComponent(), manager = FileManager.default
        var released: Int64 = 0
        for file in try manager.contentsOfDirectory(at: folder, includingPropertiesForKeys: [.fileSizeKey]) {
            let name = file.lastPathComponent
            guard name.hasPrefix(".transfer-") else { continue }
            let parts = name.dropFirst(10).split(separator: "-", maxSplits: 2)
            guard parts.count == 3, let account = Int64(parts[0]), accounts.contains(account),
                  let asked = Int64(parts[1]), start <= asked, asked < end else { continue }
            let bytes = try file.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
            try manager.removeItem(at: file)
            released += Int64(bytes)
        }
        return released
    }
    private func attachmentViews() throws -> URL {
        if let suppliedURL { return suppliedURL.appendingPathExtension("attachments") }
        return try FileManager.default.url(for: .cachesDirectory, in: .userDomainMask, appropriateFor: nil, create: false)
            .appendingPathComponent("OfflineAttachmentView", isDirectory: true)
    }
    private func clearAttachmentViews(_ accounts: [Int64], from start: Int64, before end: Int64) throws -> Int64 {
        let root = try attachmentViews(), manager = FileManager.default
        var bytes: Int64 = 0
        for account in accounts {
            let folder = root.appendingPathComponent(String(account), isDirectory: true)
            guard manager.fileExists(atPath: folder.path) else { continue }
            for date in try manager.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil) {
                guard let time = Int64(date.lastPathComponent), start <= time, time < end else { continue }
                if let entries = manager.enumerator(at: date, includingPropertiesForKeys: [.isRegularFileKey, .fileSizeKey]) {
                    for case let file as URL in entries {
                        let props = try file.resourceValues(forKeys: [.isRegularFileKey, .fileSizeKey])
                        if props.isRegularFile == true { bytes += Int64(props.fileSize ?? 0) }
                    }
                }
                try manager.removeItem(at: date)
            }
        }
        return bytes
    }
    func exportTransferAttachment(_ account: Account, _ event: [String: Any], name: String) throws -> URL? {
        guard event["index"] == nil, let meta = try transferAttachment(account, event),
              let version = meta["version"] as? String, let bytes = meta["bytes"] as? Int64,
              let asked = meta["questionTime"] as? Int64, try freeBytes() >= bytes else { return nil }
        let folder = try attachmentViews().appendingPathComponent(String(account.id), isDirectory: true)
            .appendingPathComponent(String(asked), isDirectory: true).appendingPathComponent(UUID().uuidString, isDirectory: true)
        let manager = FileManager.default
        try manager.createDirectory(at: folder, withIntermediateDirectories: true, attributes: [.protectionKey: FileProtectionType.complete])
        let file = folder.appendingPathComponent(name)
        do {
            guard manager.createFile(atPath: file.path, contents: nil) else { throw Failure.space }
            let handle = try FileHandle(forWritingTo: file)
            defer { try? handle.close() }
            let part = Int64(Self.transferChunkBytes), count = (bytes - 1) / part + 1
            for index in 0..<count {
                guard let data = try transferChunk(account, version: version, index: index) else { throw Failure.invalidContent }
                try handle.write(contentsOf: data)
            }
            return file
        } catch {
            try? manager.removeItem(at: folder)
            throw error
        }
    }
    private func compact() throws {
        try run("VACUUM")
        try run("PRAGMA wal_checkpoint(TRUNCATE)")
    }
}
