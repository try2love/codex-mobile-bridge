import Foundation
import CoreFoundation
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
        CREATE TABLE IF NOT EXISTS composer(account INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE, session TEXT NOT NULL, revision INTEGER NOT NULL, content TEXT NOT NULL, pending INTEGER NOT NULL, active INTEGER NOT NULL, title TEXT NOT NULL, changed INTEGER NOT NULL, PRIMARY KEY(account,session));
        CREATE TABLE IF NOT EXISTS offline_preferences(account INTEGER PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE, appearance TEXT);
        CREATE TABLE IF NOT EXISTS turns(account INTEGER NOT NULL, session TEXT NOT NULL, id TEXT NOT NULL, asked INTEGER NOT NULL, content TEXT, suppressed INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(account,session,id), FOREIGN KEY(account,session) REFERENCES sessions(account,id) ON DELETE CASCADE);
        CREATE INDEX IF NOT EXISTS turns_recent ON turns(account,session,suppressed,asked DESC,id DESC);
        CREATE TABLE IF NOT EXISTS archive_records(account INTEGER NOT NULL,session TEXT NOT NULL,key TEXT NOT NULL,turn TEXT NOT NULL,asked INTEGER NOT NULL,position REAL NOT NULL,visible INTEGER NOT NULL,version TEXT NOT NULL,synced INTEGER NOT NULL,content TEXT NOT NULL,bytes INTEGER NOT NULL,PRIMARY KEY(account,session,key),FOREIGN KEY(account,session,turn) REFERENCES turns(account,session,id) ON DELETE CASCADE);
        CREATE INDEX IF NOT EXISTS archive_recent ON archive_records(account,session,visible,asked DESC,turn DESC,position DESC,key DESC);
        CREATE INDEX IF NOT EXISTS archive_process ON archive_records(account,session,turn,position,key);
        CREATE INDEX IF NOT EXISTS turns_legacy ON turns(account,asked DESC,id DESC) WHERE suppressed=0 AND content IS NOT NULL AND content<>'';
        CREATE INDEX IF NOT EXISTS turns_legacy_session ON turns(account,session) WHERE suppressed=0 AND content IS NOT NULL AND content<>'';
        CREATE TABLE IF NOT EXISTS archive_deleted(account INTEGER NOT NULL,session TEXT NOT NULL,key TEXT NOT NULL,synced INTEGER NOT NULL,PRIMARY KEY(account,session,key),FOREIGN KEY(account,session) REFERENCES sessions(account,id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS archive_parts(account INTEGER NOT NULL,session TEXT NOT NULL,key TEXT NOT NULL,offset INTEGER NOT NULL,version TEXT NOT NULL,content TEXT NOT NULL,PRIMARY KEY(account,session,key,offset),FOREIGN KEY(account,session,key) REFERENCES archive_records(account,session,key) ON DELETE CASCADE);
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
            else if let number = value as? Double { status = sqlite3_bind_double(statement, index, number) }
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
                try run("DELETE FROM composer WHERE account=?", [account.id])
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
        let stored = "EXISTS(SELECT 1 FROM turns t WHERE t.account=a.id AND t.suppressed=0) OR EXISTS(SELECT 1 FROM composer c WHERE c.account=a.id AND c.active=1)"
        let partial = retained ? "" : " OR EXISTS(SELECT 1 FROM transfer_items t WHERE t.account=a.id) OR EXISTS(SELECT 1 FROM transfer_tasks j WHERE j.account=a.id)"
        return try rows("SELECT a.* FROM accounts a WHERE " + filter + "(" + stored + partial + ") ORDER BY a.name,a.id", [], readAccount)
    }
    func hasContent(_ account: Account) throws -> Bool {
        try !rows("SELECT 1 FROM turns WHERE account=? AND suppressed=0 UNION ALL SELECT 1 FROM composer WHERE account=? AND active=1 LIMIT 1", [account.id, account.id]) { _ in true }.isEmpty
    }
    func appearance(_ account: Account) throws -> [String: Any]? {
        try rows("SELECT appearance FROM offline_preferences WHERE account=?", [account.id]) { try decode(text($0, 0)) }.first
    }
    func saveAppearance(_ account: Account, _ value: [String: Any]) throws {
        let data = try JSONSerialization.data(withJSONObject: value)
        guard data.count <= 4096 else { throw Failure.invalidContent }
        try run("INSERT OR REPLACE INTO offline_preferences(account,appearance) VALUES(?,?)", [account.id, String(decoding: data, as: UTF8.self)])
    }
    static let composerActions: Set<String> = ["composerRead", "composerWrite", "composerPending"]

    func composer(_ previous: Account, action: String, input: [String: Any]) throws -> [String: Any] {
        guard let account = try account(id: previous.id), account.matches(previous.gateway, previous.identity),
              account.epoch == previous.epoch, !account.locked else { throw Failure.invalidContent }
        if action == "composerPending" {
            let sessions = try rows("SELECT session FROM composer WHERE account=? AND pending=1 AND session<>'' ORDER BY session",
                                    [account.id]) { text($0, 0) }
            return ["sessions": sessions]
        }
        guard let session = input["sessionId"] as? String, session.utf8.count <= 4096,
              !session.utf8.contains(0) else { throw Failure.invalidContent }
        func read() throws -> [String: Any] {
            try rows("SELECT revision,content FROM composer WHERE account=? AND session=?", [account.id, session]) {
                ["revision": sqlite3_column_int64($0, 0), "value": try decode(text($0, 1))]
            }.first ?? ["revision": Int64(0), "value": NSNull()]
        }
        if action == "composerRead" { return try read() }
        guard action == "composerWrite", account.status == "valid",
              let expected = input["revision"] as? NSNumber, CFGetTypeID(expected) != CFBooleanGetTypeID(),
              let revision = Int64(exactly: expected.doubleValue), (0..<9_007_199_254_740_991).contains(revision),
              let value = input["value"] as? [String: Any] else { throw Failure.invalidContent }
        let encoded = try encode(value)
        guard encoded.utf8.count <= 1_048_576 else { throw Failure.invalidContent }
        return try transaction {
            var current = try read()
            guard current["revision"] as? Int64 == revision else {
                current["applied"] = false
                return current
            }
            let pending = (value["outbox"] as? [Any])?.isEmpty == false
            let draft = value["draft"] as? [String: Any] ?? [:]
            let hasDraft = (draft["text"] as? String)?.isEmpty == false ||
                ["quotes", "attachments", "references", "attachmentDrafts", "skills"].contains { (draft[$0] as? [Any])?.isEmpty == false } ||
                ["plan", "goal"].contains(draft["workMode"] as? String ?? "")
            let title = String((value["title"] as? String ?? "").prefix(512))
            try run("INSERT OR REPLACE INTO composer(account,session,revision,content,pending,active,title,changed) VALUES(?,?,?,?,?,?,?,?)",
                    [account.id, session, revision + 1, encoded, Int64(pending ? 1 : 0),
                     Int64(!session.isEmpty && (pending || hasDraft) ? 1 : 0), title, Int64(Date().timeIntervalSince1970 * 1000)])
            return ["revision": revision + 1, "value": value, "applied": true]
        }
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
                let suppressed=try rows("SELECT suppressed FROM turns WHERE account=? AND session=? AND id=?", [account.id,session,id]) { sqlite3_column_int($0,0) != 0 }.first ?? false
                if suppressed && !canRestore { continue }
                try run("INSERT OR IGNORE INTO turns(account,session,id,asked,content,suppressed) VALUES(?,?,?,?,NULL,0)",[account.id,session,id,asked])
                // Legacy bytes stay intact until a background migration commits.
                try run("UPDATE turns SET asked=CASE WHEN asked=0 THEN ? ELSE asked END,suppressed=0 WHERE account=? AND session=? AND id=?", [asked,account.id,session,id])
                let time=try rows("SELECT asked FROM turns WHERE account=? AND session=? AND id=?", [account.id,session,id]) { sqlite3_column_int64($0,0) }.first ?? asked
                try run("UPDATE archive_records SET asked=? WHERE account=? AND session=? AND turn=? AND asked=0",[time,account.id,session,id])
                for message in (turn["messages"] as? [[String:Any]] ?? []).sorted(by: { recordOrder($0)<recordOrder($1) }) {
                    try storeRecord(account,session:session,turn:id,asked:time,message:message,synced:synced)
                }
            }
            if let deleted = event["deletedKeys"] as? [String], !deleted.isEmpty {
                for key in deleted {
                    try run("DELETE FROM archive_records WHERE account=? AND session=? AND key=?", [account.id,session,key])
                    try run("INSERT INTO archive_deleted VALUES(?,?,?,?) ON CONFLICT(account,session,key) DO UPDATE SET synced=MAX(synced,excluded.synced)", [account.id,session,key,synced])
                }
            }
        }
    }
    private var migratingAccounts = Set<Int64>()
    private var migrationFocus=[Int64:String]()
    func scheduleMigration(_ account: Account, session: String? = nil) {
        if let session,!session.isEmpty {migrationFocus[account.id]=session}
        guard !account.locked, !migratingAccounts.contains(account.id) else { return }
        migratingAccounts.insert(account.id)
        func step() {
            Self.queue.asyncAfter(deadline: .now() + 0.05) { [self] in
                do {
                    guard let current = try self.account(id: account.id), !current.locked else {
                        migratingAccounts.remove(account.id); return
                    }
                    let focused = try migrationFocus[account.id].map { try migrateBatch(current, session: $0) } ?? false
                    if try focused || migrateBatch(current) { step() } else { migratingAccounts.remove(account.id);migrationFocus.removeValue(forKey: account.id) }
                } catch { migratingAccounts.remove(account.id) }
            }
        }
        step()
    }
    @discardableResult
    func migrateBatch(_ account: Account, session: String? = nil) throws -> Bool {
        guard !account.locked else { return false }
        let source = try rows("SELECT session,id,asked,content FROM turns WHERE account=? AND suppressed=0 AND content IS NOT NULL AND content<>''" + (session == nil ? "" : " AND session=?") + " ORDER BY asked DESC,id DESC LIMIT 1", session.map { [account.id,$0] as [Any] } ?? [account.id]) {
            (text($0,0),text($0,1),sqlite3_column_int64($0,2),text($0,3))
        }.first
        guard let (session,id,asked,raw) = source else { return false }
        let legacy = try decode(raw)
        var messages = Array((legacy["messages"] as? [String:[String:Any]] ?? [:]).values)
        if messages.isEmpty {
            for (field,role) in [("question","user"),("answer","assistant")] {
                if let value = legacy[field] as? String, !value.isEmpty {
                    let key=id+":"+field
                    messages.append(["key":key,"version":Self.hex(SHA256.hash(data:Data(value.utf8))),"role":role,"text":value,
                        "order":field=="question" ? 0 : 1,"row":["key":key,"role":role,"text":value]])
                }
            }
        }
        try transaction {
            for message in messages.sorted(by: { recordOrder($0) < recordOrder($1) }) {
                try storeRecord(account,session:session,turn:id,asked:asked,message:message,synced:message["_syncedAt"] as? Int64 ?? 0)
            }
            try run("UPDATE turns SET content=NULL WHERE account=? AND session=? AND id=? AND content=?", [account.id,session,id,raw])
        }
        return true
    }
    private func recordOrder(_ message: [String:Any]) -> Double {
        (message["order"] as? NSNumber)?.doubleValue ?? ((message["row"] as? [String:Any])?["order"] as? NSNumber)?.doubleValue ?? 0
    }
    private func storeRecord(_ account: Account, session: String, turn: String, asked: Int64,
                             message original: [String:Any], synced: Int64) throws {
        guard let key=original["key"] as? String, let version=original["version"] as? String, !version.isEmpty else { throw Failure.invalidContent }
        if let deleted=try rows("SELECT synced FROM archive_deleted WHERE account=? AND session=? AND key=?", [account.id,session,key],{sqlite3_column_int64($0,0)}).first {
            if synced<=deleted {return}
            try run("DELETE FROM archive_deleted WHERE account=? AND session=? AND key=?", [account.id,session,key])
        }
        let previous=try rows("SELECT version,synced FROM archive_records WHERE account=? AND session=? AND key=?", [account.id,session,key]) {
            (text($0,0),sqlite3_column_int64($0,1))
        }.first
        var message=original,row=original["row"] as? [String:Any] ?? [:]
        row["key"]=key;row["version"]=version;row["turnId"]=turn
        row["role"]=original["role"] ?? row["role"] ?? "assistant"
        let order=recordOrder(message)
        var parts=message.removeValue(forKey:"bodyParts") as? [String:[String:Any]] ?? [:]
        if let part=message.removeValue(forKey:"bodyPart") as? [String:Any], let offset=part["offset"] as? Int64 { parts[String(offset)]=part }
        let value=message["text"] as? String ?? row["text"] as? String ?? ""
        if value.unicodeScalars.count>6000 {
            if parts.isEmpty && row["truncated"] as? Bool != true {
                let scalars=Array(value.unicodeScalars)
                for offset in stride(from:0,to:scalars.count,by:16000) {
                    let end=min(offset+16000,scalars.count)
                    parts[String(offset)]=["offset":Int64(offset),"text":String(String.UnicodeScalarView(scalars[offset..<end])),
                                          "next":end==scalars.count ? NSNull() : Int64(end) as Any]
                }
            }
            row["truncated"]=true
        }
        let preview=String(value.unicodeScalars.prefix(6000))
        row["text"]=preview;message["text"]=preview;message["row"]=row
        message.removeValue(forKey:"_syncedAt")
        if previous == nil || synced >= previous!.1 {
            if previous?.0 != version { try run("DELETE FROM archive_parts WHERE account=? AND session=? AND key=?", [account.id,session,key]) }
            let process = row["kind"] as? String=="processPage" || row["phase"] as? String=="commentary" ||
                row["interaction"] as? String=="response" || !["assistant","user","error","fileChanges"].contains(row["role"] as? String ?? "")
            let visible: Int64 = row["kind"] as? String=="processPage" || !process ? 1 : 0
            if visible==0 {
                let prior=try rows("SELECT key,visible,content FROM archive_records WHERE account=? AND session=? AND turn=? AND position<? ORDER BY position DESC,key DESC LIMIT 1", [account.id,session,turn,order]) {
                    (text($0,0),sqlite3_column_int($0,1),try decode(text($0,2)))
                }.first
                if prior == nil || prior!.1==1 && (prior!.2["row"] as? [String:Any])?["kind"] as? String != "processPage" {
                    let headerKey="offline-process:"+turn+":"+(prior?.0 ?? "start")
                    var header=row;header["key"]=headerKey;header["kind"]="processPage";header["role"]="activity";header["text"]="";header["order"]=order-0.25
                    try storeRecord(account,session:session,turn:turn,asked:asked,message:["key":headerKey,"version":version,"role":"activity","order":order-0.25,"text":"","row":header],synced:synced)
                }
            }
            let content=try encode(message)
            try run("INSERT INTO archive_records(account,session,key,turn,asked,position,visible,version,synced,content,bytes) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(account,session,key) DO UPDATE SET turn=excluded.turn,asked=excluded.asked,position=excluded.position,visible=excluded.visible,version=excluded.version,synced=excluded.synced,content=excluded.content,bytes=excluded.bytes",
                    [account.id,session,key,turn,asked,order,visible,version,synced,content,Int64(content.utf8.count)])
        } else if previous?.0 != version { return }
        for (offset,part) in parts {
            guard let position=Int64(offset), position>=0 else { throw Failure.invalidContent }
            let operation = previous != nil && synced < previous!.1 ? "INSERT OR IGNORE" : "INSERT OR REPLACE"
            guard let text=part["text"] as? String else {throw Failure.invalidContent}
            let scalars=Array(text.unicodeScalars)
            for start in stride(from:0,to:max(1,scalars.count),by:16000) {
                let end=min(scalars.count,start+16000)
                var piece=part;piece["offset"]=position+Int64(start)
                piece["text"]=String(String.UnicodeScalarView(scalars[start..<end]))
                piece["next"]=end==scalars.count ? (part["next"] ?? NSNull()) : position+Int64(end)
                try run(operation+" INTO archive_parts(account,session,key,offset,version,content) VALUES(?,?,?,?,?,?)",
                        [account.id,session,key,position+Int64(start),version,try encode(piece)])
            }
        }
    }
    private func storedRecord(_ account: Account, session: String, key: String, parts: Bool = false) throws -> [String:Any]? {
        guard var message=try rows("""
            SELECT r.content FROM archive_records r JOIN sessions s ON s.account=r.account AND s.id=r.session
            JOIN turns t ON t.account=r.account AND t.session=r.session AND t.id=r.turn
            WHERE r.account=? AND r.session=? AND r.key=? AND s.epoch=? AND t.suppressed=0
            """,[account.id,session,key,account.epoch],{ try decode(text($0,0)) }).first else { return nil }
        if parts {
            let values=try rows("SELECT offset,content FROM archive_parts WHERE account=? AND session=? AND key=? ORDER BY offset",
                               [account.id,session,key]) { (String(sqlite3_column_int64($0,0)),try decode(text($0,1))) }
            if !values.isEmpty { message["bodyParts"]=Dictionary(uniqueKeysWithValues:values) }
        }
        return message
    }
    func recordPage(_ account: Account, session: String, event: [String:Any]) throws -> [String:Any]? {
        guard !account.locked else { return nil }
        let history = try rows("SELECT title,synced,complete FROM sessions WHERE account=? AND id=? AND epoch=?",
            [account.id,session,account.epoch],{ (text($0,0),sqlite3_column_int64($0,1),sqlite3_column_int($0,2) != 0) }).first
        guard let saved = try history ?? rows("SELECT title,changed,0 FROM composer WHERE account=? AND session=? AND active=1",
            [account.id,session], { (text($0, 0),sqlite3_column_int64($0, 1),false) }).first else { return nil }
        let mode=event["mode"] as? String ?? "recent"
        var values:[Any]=[account.id,session,account.epoch],filter="",sort="r.asked DESC,r.turn DESC,r.position DESC,r.key DESC"
        if mode=="body" {
            guard let key=event["key"] as? String,let version=event["version"] as? String,let offset=event["offset"] as? Int64 else { return nil }
            return try body(account,event:["sessionId":session,"body":["key":key,"version":version,"offset":offset]])
        }
        if mode=="process" {
            guard let key=event["key"] as? String,let header=try rows("SELECT turn,position FROM archive_records WHERE account=? AND session=? AND key=? AND visible=1",
                [account.id,session,key],{ (text($0,0),sqlite3_column_double($0,1)) }).first else { return nil }
            let end=try rows("SELECT position FROM archive_records WHERE account=? AND session=? AND turn=? AND visible=1 AND position>? ORDER BY position,key LIMIT 1",
                [account.id,session,header.0,header.1],{sqlite3_column_double($0,0)}).first ?? Double.greatestFiniteMagnitude
            filter=" AND r.turn=? AND r.visible=0 AND r.position>? AND r.position<?"
            values += [header.0,header.1,end];sort="r.position,r.key"
        } else { filter=" AND r.visible=1" }
        if let cursor=event[mode=="process" ? "after" : "before"] as? String {
            guard let data=Data(base64Encoded:cursor),let parts=try JSONSerialization.jsonObject(with:data) as? [String],
                  parts.count==7,parts[0]==session,parts[1]==account.clearRevision,parts[2]==String(account.epoch),
                  let asked=Int64(parts[3]),let position=Double(parts[5]) else { return nil }
            if mode=="process" { filter+=" AND (r.position,r.key)>(?,?)";values += [position,parts[6]] }
            else { filter+=" AND (r.asked,r.turn,r.position,r.key)<(?,?,?,?)";values += [asked,parts[4],position,parts[6]] }
        }
        let candidates=try rows("""
            SELECT r.key,r.turn,r.asked,r.position,r.bytes FROM archive_records r
            JOIN sessions s ON s.account=r.account AND s.id=r.session
            JOIN turns t ON t.account=r.account AND t.session=r.session AND t.id=r.turn
            WHERE r.account=? AND r.session=? AND s.epoch=? AND t.suppressed=0
            """+filter+" ORDER BY "+sort+" LIMIT 41",values) {
                (text($0,0),text($0,1),sqlite3_column_int64($0,2),sqlite3_column_double($0,3),sqlite3_column_int64($0,4))
            }
        var bytes:Int64=0,selected=[(String,String,Int64,Double,Int64)]()
        for row in candidates.prefix(40) {
            if !selected.isEmpty && bytes+row.4>192*1024 { break }
            selected.append(row);bytes+=row.4
        }
        var messages=[[String:Any]]()
        for item in selected {
            if let message=try storedRecord(account,session:session,key:item.0) { messages.append(message) }
        }
        if mode != "process" { messages.reverse() }
        var output=[[String:Any]](),turns=[String:[String:Any]]()
        for (index,message) in messages.enumerated() {
            var row=message["row"] as? [String:Any] ?? [:]
            row["key"]=message["key"];row["version"]=message["version"];row["text"]=message["text"];row["order"]=index
            let turn=row["turnId"] as? String ?? ""
            var meta=turns[turn] ?? ["id":turn,"processes":[[String:Any]]()]
            for (a,b) in [("turnStatus","status"),("turnCompletionKnown","completionKnown"),("turnStartedAt","startedAt"),("turnCompletedAt","completedAt"),("turnError","error")] {
                if let value=row[a] { meta[b]=value }
            }
            if row["kind"] as? String=="processPage" && mode != "process" {
                var groups=meta["processes"] as! [[String:Any]]
                groups.append(["key":row["key"]!,"version":row["version"]!,"order":index,"processCursor":row["key"]!]);meta["processes"]=groups
            } else { output.append(row) }
            turns[turn]=meta
        }
        var next:Any=NSNull()
        if candidates.count>selected.count,let tail=selected.last {
            next=try JSONSerialization.data(withJSONObject:[session,account.clearRevision,String(account.epoch),String(tail.2),tail.1,String(tail.3),tail.0]).base64EncodedString()
        }
        let legacy=try !rows("SELECT 1 FROM turns WHERE account=? AND session=? AND suppressed=0 AND content IS NOT NULL AND content<>'' LIMIT 1",
            [account.id,session],{_ in true}).isEmpty
        let gaps=try hasGaps(account,session:session)
        return ["recordPage":1,"id":session,"title":saved.0,"syncedAt":saved.1,"gaps":!saved.2 || gaps,
                "rows":output,"index":output,"turns":Array(turns.values),"before":mode=="process" ? NSNull():next,
                "next":mode=="process" ? next:NSNull(),"hasMore":!(next is NSNull),"migrating":legacy,"readRecords":selected.count]
    }
    private func turnContent(_ account: Account, session: String, turn: String) throws -> [String:Any] {
        let messages=try rows("SELECT key,content FROM archive_records WHERE account=? AND session=? AND turn=? ORDER BY position,key", [account.id,session,turn]) { (text($0,0),try storedRecord(account,session:session,key:text($0,0),parts:true)!) }
        return ["messages":Dictionary(uniqueKeysWithValues:messages)]
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
        return try rows("""
            SELECT s.id,s.title,s.synced,s.complete FROM sessions s WHERE s.account=? AND s.epoch=?
            AND EXISTS(SELECT 1 FROM turns t WHERE t.account=s.account AND t.session=s.id AND t.suppressed=0)
            UNION ALL SELECT c.session,c.title,c.changed,0 FROM composer c WHERE c.account=? AND c.active=1
            AND NOT EXISTS(SELECT 1 FROM sessions s JOIN turns t ON s.account=t.account AND s.id=t.session
            WHERE s.account=c.account AND s.id=c.session AND s.epoch=? AND t.suppressed=0)
            ORDER BY 3 DESC,1
            """, [account.id, account.epoch, account.id, account.epoch]) {
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
            let content=try turnContent(account,session:id,turn:key)
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
        return try rows("SELECT t.asked,t.content,t.id FROM turns t JOIN sessions s ON t.account=s.account AND t.session=s.id WHERE t.account=? AND t.session=? AND t.suppressed=0 AND s.epoch=? ORDER BY t.asked,t.rowid",
                        [account.id, session, account.epoch]) {
            Turn(questionTime: sqlite3_column_int64($0, 0), content: try turnContent(account,session:session,turn:text($0,2)))
        }
    }
    func body(_ account: Account, event: [String: Any]) throws -> [String: Any]? {
        guard account.status == "valid", let session = event["sessionId"] as? String, let body = event["body"] as? [String: Any],
              let key = body["key"] as? String, let version = body["version"] as? String, let offset = body["offset"] as? Int64 else { return nil }
        guard let message=try storedRecord(account,session:session,key:key),message["version"] as? String==version else { return nil }
        return try rows("SELECT content FROM archive_parts WHERE account=? AND session=? AND key=? AND offset=? AND version=?",
            [account.id,session,key,offset,version]) { row in
                var part=try decode(text(row,0));part["key"]=key;part["version"]=version;return part
            }.first
    }

    func seed(_ account: Account, event: [String: Any]) throws -> [[String: Any]] {
        guard account.status == "valid", let session = event["sessionId"] as? String else { return [] }
        let requested=Array((event["keys"] as? [String] ?? []).prefix(100))
        let keys: [String]
        if !requested.isEmpty { keys=requested }
        else {
            keys=try rows("SELECT key FROM archive_records WHERE account=? AND session=? ORDER BY asked DESC,turn DESC,position DESC,key DESC LIMIT 40", [account.id,session]) {text($0,0)}
        }
        var groups=[String:[String:Any]](),bytes=0
        for key in keys {
            guard var message=try storedRecord(account,session:session,key:key) else { continue }
            let info=try rows("SELECT turn,asked FROM archive_records WHERE account=? AND session=? AND key=?", [account.id,session,key]) { (text($0,0),sqlite3_column_int64($0,1)) }.first!
            if let ids=event["turnIds"] as? [String],!ids.isEmpty,!ids.contains(info.0) {continue}
            let parts=try rows("SELECT content FROM archive_parts WHERE account=? AND session=? AND key=? ORDER BY offset LIMIT 2", [account.id,session,key]) {try decode(text($0,0))}
            if !parts.isEmpty {message["bodyParts"]=parts}
            let size=try JSONSerialization.data(withJSONObject:message).count
            if bytes+size>512_000 {break};bytes+=size
            var group=groups[info.0] ?? ["id":info.0,"questionTime":info.1,"messages":[[String:Any]]()]
            var messages=group["messages"] as! [[String:Any]];messages.append(message);group["messages"]=messages;groups[info.0]=group
        }
        return Array(groups.values)
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
        try run("DELETE FROM archive_records WHERE account=? AND (session,turn) IN (SELECT session,id FROM turns WHERE account=? AND asked>=? AND asked<?)", [id,id,start,end])
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
            let records=event["records"] as? [[String:Any]] ?? [["key":key,"recordVersion":version,"textLength":event["textLength"] as Any]]
            guard !records.isEmpty,records.count<=64 else {throw Failure.invalidContent}
            present=try records.allSatisfy { record in
                guard let key=record["key"] as? String,let version=record["recordVersion"] as? String else {return false}
                let message=try storedRecord(account,session:item.session,key:key,parts:true)
                return message?["version"] as? String==version && Self.completeMessage(message,length:record["textLength"] as? Int64)
            }
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
