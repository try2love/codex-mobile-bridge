import { util } from '@kit.ArkTS';
import { ArchiveDatabase, ArchiveValue } from './ArchiveDatabase';

export type ArchiveObject = Record<string, Object | null>;
export class ArchiveAccount {
  id: number = 0;
  gateway: string = '';
  identity: string = '';
  name: string = '';
  status: string = '';
  epoch: number = 0;
  revision: string = '0';
}

/** Indexed records and body parts, rather than one JSON document per chat. */
export class OfflineArchive {
  db: ArchiveDatabase;
  private tail: Promise<void> = Promise.resolve();
  constructor(database: ArchiveDatabase) { this.db = database; }

  async init(): Promise<void> {
    for (const sql of [
      'PRAGMA foreign_keys=ON', 'PRAGMA secure_delete=ON',
      'CREATE TABLE IF NOT EXISTS accounts(id INTEGER PRIMARY KEY,gateway TEXT NOT NULL,identity TEXT NOT NULL,name TEXT NOT NULL,status TEXT NOT NULL,epoch INTEGER NOT NULL DEFAULT 0,revision INTEGER NOT NULL DEFAULT 0,UNIQUE(gateway,identity))',
      'CREATE TABLE IF NOT EXISTS routes(address TEXT PRIMARY KEY,account INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE)',
      'CREATE TABLE IF NOT EXISTS sessions(account INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,id TEXT NOT NULL,title TEXT NOT NULL,synced INTEGER NOT NULL,complete INTEGER NOT NULL,epoch INTEGER NOT NULL,PRIMARY KEY(account,id))',
      'CREATE TABLE IF NOT EXISTS turns(account INTEGER NOT NULL,session TEXT NOT NULL,id TEXT NOT NULL,asked INTEGER NOT NULL,suppressed INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(account,session,id),FOREIGN KEY(account,session) REFERENCES sessions(account,id) ON DELETE CASCADE)',
      'CREATE TABLE IF NOT EXISTS records(account INTEGER NOT NULL,session TEXT NOT NULL,key TEXT NOT NULL,turn TEXT NOT NULL,asked INTEGER NOT NULL,position REAL NOT NULL,visible INTEGER NOT NULL,version TEXT NOT NULL,synced INTEGER NOT NULL,content TEXT NOT NULL,bytes INTEGER NOT NULL,PRIMARY KEY(account,session,key),FOREIGN KEY(account,session,turn) REFERENCES turns(account,session,id) ON DELETE CASCADE)',
      'CREATE INDEX IF NOT EXISTS records_recent ON records(account,session,visible,asked DESC,turn DESC,position DESC,key DESC)',
      'CREATE INDEX IF NOT EXISTS records_process ON records(account,session,turn,position,key)',
      'CREATE TABLE IF NOT EXISTS parts(account INTEGER NOT NULL,session TEXT NOT NULL,key TEXT NOT NULL,offset INTEGER NOT NULL,version TEXT NOT NULL,content TEXT NOT NULL,PRIMARY KEY(account,session,key,offset),FOREIGN KEY(account,session,key) REFERENCES records(account,session,key) ON DELETE CASCADE)',
      'CREATE TABLE IF NOT EXISTS finished_records(account INTEGER NOT NULL,session TEXT NOT NULL,key TEXT NOT NULL,version TEXT NOT NULL,length INTEGER NOT NULL,PRIMARY KEY(account,session,key),FOREIGN KEY(account,session,key) REFERENCES records(account,session,key) ON DELETE CASCADE)',
      'CREATE TABLE IF NOT EXISTS deleted(account INTEGER NOT NULL,session TEXT NOT NULL,key TEXT NOT NULL,synced INTEGER NOT NULL,PRIMARY KEY(account,session,key),FOREIGN KEY(account,session) REFERENCES sessions(account,id) ON DELETE CASCADE)',
      'CREATE TABLE IF NOT EXISTS clears(account INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,revision INTEGER NOT NULL,start INTEGER NOT NULL,end INTEGER NOT NULL,PRIMARY KEY(account,revision))',
      'CREATE TABLE IF NOT EXISTS transfer_tasks(account INTEGER PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,content TEXT NOT NULL)',
      'CREATE TABLE IF NOT EXISTS chunks(account INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,version TEXT NOT NULL,part INTEGER NOT NULL,data BLOB NOT NULL,PRIMARY KEY(account,version,part))',
      'CREATE TABLE IF NOT EXISTS items(account INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,version TEXT NOT NULL,session TEXT NOT NULL,turn TEXT NOT NULL,asked INTEGER NOT NULL,content TEXT NOT NULL,PRIMARY KEY(account,version,session,turn))',
      'CREATE TABLE IF NOT EXISTS attachment_keys(account INTEGER NOT NULL,version TEXT NOT NULL,session TEXT NOT NULL,turn TEXT NOT NULL,key TEXT NOT NULL,PRIMARY KEY(account,session,turn,key),FOREIGN KEY(account,version,session,turn) REFERENCES items(account,version,session,turn) ON DELETE CASCADE)',
      'CREATE TABLE IF NOT EXISTS composer(account INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,session TEXT NOT NULL,revision INTEGER NOT NULL,content TEXT NOT NULL,pending INTEGER NOT NULL,active INTEGER NOT NULL,title TEXT NOT NULL,changed INTEGER NOT NULL,PRIMARY KEY(account,session))',
      'CREATE TABLE IF NOT EXISTS appearance(account INTEGER PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,content TEXT NOT NULL)'
    ]) { await this.db.run(sql); }
    const recordColumns = await this.db.rows('PRAGMA table_info(records)');
    if (!recordColumns.some(row => row[1] === 'needs_parts')) { await this.db.run('ALTER TABLE records ADD COLUMN needs_parts INTEGER NOT NULL DEFAULT 0'); }
    const partColumns = await this.db.rows('PRAGMA table_info(parts)');
    if (!partColumns.some(row => row[1] === 'characters')) { await this.db.run('ALTER TABLE parts ADD COLUMN characters INTEGER NOT NULL DEFAULT -1'); }
    if (!partColumns.some(row => row[1] === 'next_offset')) { await this.db.run('ALTER TABLE parts ADD COLUMN next_offset INTEGER DEFAULT -2'); }
  }

  async serial(work: () => Promise<Object | null>): Promise<Object | null> {
    const operation = this.tail.then(work); this.tail = operation.then(() => {}, () => {}); return operation;
  }
  async transaction(work: () => Promise<void>): Promise<void> {
    await this.db.run('BEGIN');
    try { await work(); await this.db.run('COMMIT'); } catch (error) { await this.db.run('ROLLBACK'); throw error; }
  }
  private accountRow(row: ArchiveValue[]): ArchiveAccount {
    const account = new ArchiveAccount();
    account.id = row[0] as number; account.gateway = row[1] as string; account.identity = row[2] as string;
    account.name = row[3] as string; account.status = row[4] as string; account.epoch = row[5] as number; account.revision = String(row[6]);
    return account;
  }
  async account(id: number): Promise<ArchiveAccount | null> {
    const rows = await this.db.rows('SELECT * FROM accounts WHERE id=?', [id]); return rows.length ? this.accountRow(rows[0]) : null;
  }
  async forAddress(address: string): Promise<ArchiveAccount | null> {
    const rows = await this.db.rows('SELECT a.* FROM accounts a JOIN routes r ON r.account=a.id WHERE r.address=?', [address]);
    return rows.length ? this.accountRow(rows[0]) : null;
  }
  async bind(address: string, name: string, gateway: string, identity: string): Promise<ArchiveAccount> {
    if (!/^[a-f0-9]{32}$/.test(gateway) || !/^[a-f0-9]{64}$/.test(identity)) { throw new Error('无效离线身份'); }
    await this.transaction(async () => {
      const prior = await this.forAddress(address);
      if (prior && (prior.gateway !== gateway || prior.identity !== identity)) { await this.db.run("UPDATE accounts SET status=CASE WHEN status='valid' THEN 'loggedOut' ELSE status END WHERE id=?", [prior.id]); }
      await this.db.run("INSERT OR IGNORE INTO accounts(gateway,identity,name,status) VALUES(?,?,?,'valid')", [gateway, identity, name]);
      await this.db.run("UPDATE accounts SET name=?,epoch=epoch+CASE WHEN status='valid' THEN 0 ELSE 1 END,status='valid' WHERE gateway=? AND identity=?", [name, gateway, identity]);
      await this.db.run('INSERT OR REPLACE INTO routes(address,account) SELECT ?,id FROM accounts WHERE gateway=? AND identity=?', [address, gateway, identity]);
    });
    const account = await this.forAddress(address); if (!account) { throw new Error('无法绑定离线身份'); } return account;
  }
  async status(account: ArchiveAccount, status: string): Promise<void> {
    if (!['loggedOut', 'expired', 'revoked', 'unknownRejected'].includes(status)) { throw new Error('无效登录状态'); }
    await this.db.run('UPDATE accounts SET status=?,epoch=epoch+1 WHERE id=?', [status, account.id]);
  }
  async current(previous: ArchiveAccount, valid: boolean = true): Promise<ArchiveAccount> {
    const account = await this.account(previous.id);
    if (!account || account.epoch !== previous.epoch || account.gateway !== previous.gateway || account.identity !== previous.identity || ['revoked', 'unknownRejected'].includes(account.status) || valid && account.status !== 'valid') {
      throw new Error('离线身份已变化');
    }
    return account;
  }
  static composerAction(action: string): boolean {
    return ['composerRead', 'composerWrite', 'composerPending'].includes(action);
  }
  async composer(account: ArchiveAccount, action: string, input: ArchiveObject): Promise<ArchiveObject> {
    const current = await this.current(account, action === 'composerWrite');
    if (action === 'composerPending') {
      const pending = await this.db.rows("SELECT session FROM composer WHERE account=? AND pending=1 AND session<>'' ORDER BY session", [current.id]);
      return { sessions: pending.map(row => row[0] as string) };
    }
    const session = input['sessionId'];
    const encoder = new util.TextEncoder();
    if (typeof session !== 'string' || session.includes(String.fromCharCode(0)) || encoder.encodeInto(session).length > 4096) { throw new Error('无效离线会话'); }
    if (action === 'composerRead') { return this.readComposer(current, session); }
    const revision = input['revision'], value = input['value'];
    if (action !== 'composerWrite' || typeof revision !== 'number' || !Number.isSafeInteger(revision) ||
      revision < 0 || revision >= Number.MAX_SAFE_INTEGER || !value || typeof value !== 'object' || Array.isArray(value)) {
      throw new Error('无效离线输入');
    }
    const record = value as ArchiveObject, encoded = JSON.stringify(record);
    if (encoder.encodeInto(encoded).length > 1048576) { throw new Error('离线输入过大'); }
    let result: ArchiveObject = {};
    await this.transaction(async () => {
      const saved = await this.readComposer(current, session);
      if (saved['revision'] !== revision) {
        saved['applied'] = false; result = saved; return;
      }
      const pending = this.composerItems(record['outbox']), draft = record['draft'] as ArchiveObject | null;
      const active = !!session && (pending || !!draft && (typeof draft['text'] === 'string' && !!draft['text'] ||
        this.composerItems(draft['quotes']) || this.composerItems(draft['attachments']) || this.composerItems(draft['references']) ||
        this.composerItems(draft['attachmentDrafts']) || this.composerItems(draft['skills']) ||
        draft['workMode'] === 'plan' || draft['workMode'] === 'goal'));
      const title = typeof record['title'] === 'string' ? (record['title'] as string).slice(0, 512) : '';
      await this.db.run('INSERT OR REPLACE INTO composer(account,session,revision,content,pending,active,title,changed) VALUES(?,?,?,?,?,?,?,?)',
        [current.id, session, revision + 1, encoded, pending ? 1 : 0, active ? 1 : 0, title, Date.now()]);
      result = { revision: revision + 1, value: record, applied: true };
    });
    return result;
  }
  private composerItems(value: Object | null): boolean {
    return Array.isArray(value) && value.length > 0;
  }
  private async readComposer(account: ArchiveAccount, session: string): Promise<ArchiveObject> {
    const rows = await this.db.rows('SELECT revision,content FROM composer WHERE account=? AND session=?', [account.id, session]);
    return rows.length ? { revision: rows[0][0] as number, value: JSON.parse(rows[0][1] as string) as ArchiveObject } : { revision: 0, value: null };
  }
  async unlinkRoute(address: string): Promise<void> {
    await this.transaction(async () => {
      const account = await this.forAddress(address);
      if (!account) { return; }
      await this.db.run('DELETE FROM routes WHERE address=?', [address]);
      await this.db.run("UPDATE accounts SET status='unlinked',epoch=epoch+1 WHERE id=? AND status NOT IN ('revoked','unknownRejected') AND NOT EXISTS(SELECT 1 FROM routes WHERE account=?)",
        [account.id, account.id]);
    });
  }
  async state(account: ArchiveAccount, session: string = ''): Promise<ArchiveObject> {
    const current = await this.current(account);
    const tombstones: Record<string, string[]> = {};
    for (const row of await this.db.rows('SELECT session,id FROM turns WHERE account=? AND suppressed=1', [current.id])) {
      const id = row[0] as string; if (!tombstones[id]) { tombstones[id] = []; } tombstones[id].push(row[1] as string);
    }
    const cached = session ? await this.db.rows('SELECT 1 FROM records WHERE account=? AND session=? LIMIT 1', [current.id, session]) : [];
    return { recordPages: 1, clearRevision: current.revision, tombstones, hasCachedSession: cached.length > 0 };
  }
  async merge(previous: ArchiveAccount, event: ArchiveObject): Promise<void> {
    const account = await this.current(previous), session = event['sessionId'] as string;
    if (event['clearRevision'] !== account.revision || !session || session.length > 4096 || !Array.isArray(event['turns']) || !Number.isSafeInteger(event['syncedAt'])) {
      throw new Error('无效离线同步');
    }
    const synced = event['syncedAt'] as number, manual = event['source'] === 'manual';
    await this.transaction(async () => {
      await this.db.run('INSERT OR IGNORE INTO sessions VALUES(?,?,?,?,?,?)', [account.id, session, event['title'] as string || '', synced, event['complete'] ? 1 : 0, account.epoch]);
      await this.db.run("UPDATE sessions SET title=CASE WHEN synced<=? AND ?<>'' THEN ? ELSE title END,synced=MAX(synced,?),complete=MAX(complete,?),epoch=? WHERE account=? AND id=?",
        [synced, event['title'] as string || '', event['title'] as string || '', synced, event['complete'] ? 1 : 0, account.epoch, account.id, session]);
      for (const turn of event['turns'] as ArchiveObject[]) {
        const id = turn['id'] as string, asked = turn['questionTime'] as number;
        if (!id || !Number.isSafeInteger(asked) || asked < 0) { throw new Error('无效离线回合'); }
        const prior = await this.db.rows('SELECT suppressed FROM turns WHERE account=? AND session=? AND id=?', [account.id, session, id]);
        if (prior[0]?.[0] === 1 && !manual) { continue; }
        await this.db.run('INSERT OR IGNORE INTO turns VALUES(?,?,?,?,0)', [account.id, session, id, asked]);
        await this.db.run('UPDATE turns SET asked=CASE WHEN asked=0 THEN ? ELSE asked END,suppressed=0 WHERE account=? AND session=? AND id=?', [asked, account.id, session, id]);
        const clock = await this.db.rows('SELECT asked FROM turns WHERE account=? AND session=? AND id=?', [account.id, session, id]);
        await this.db.run('UPDATE records SET asked=? WHERE account=? AND session=? AND turn=? AND asked=0', [clock[0][0], account.id, session, id]);
        for (const message of turn['messages'] as ArchiveObject[] || []) { await this.storeRecord(account, session, id, clock[0][0] as number, message, synced); }
      }
      for (const key of event['deletedKeys'] as string[] || []) {
        await this.db.run('DELETE FROM records WHERE account=? AND session=? AND key=?', [account.id, session, key]);
        await this.db.run('INSERT INTO deleted VALUES(?,?,?,?) ON CONFLICT(account,session,key) DO UPDATE SET synced=MAX(synced,excluded.synced)', [account.id, session, key, synced]);
      }
    });
  }
  private async storeRecord(account: ArchiveAccount, session: string, turn: string, asked: number, message: ArchiveObject, synced: number): Promise<void> {
    const key = message['key'] as string, version = message['version'] as string;
    if (!key || !version) { throw new Error('无效离线记录'); }
    const deleted = await this.db.rows('SELECT synced FROM deleted WHERE account=? AND session=? AND key=?', [account.id, session, key]);
    if (deleted.length && synced <= (deleted[0][0] as number)) { return; }
    const prior = await this.db.rows('SELECT version,synced FROM records WHERE account=? AND session=? AND key=?', [account.id, session, key]);
    const same = !prior.length || synced >= (prior[0][1] as number);
    if (!same && prior[0][0] !== version) { return; }
    const row = JSON.parse(JSON.stringify(message['row'] || {})) as ArchiveObject;
    row['key'] = key; row['version'] = version; row['turnId'] = turn; row['role'] = message['role'] || row['role'] || 'assistant';
    const position = Number(message['order'] || row['order'] || 0);
    let text = (message['text'] || row['text'] || '') as string;
    const parts = message['bodyParts'] as Record<string, ArchiveObject> || {};
    const part = message['bodyPart'] as ArchiveObject; if (part) { parts[String(part['offset'])] = part; }
    if (text.length > 6000) {
      if (!row['truncated'] && !Object.keys(parts).length) {
        for (let offset = 0; offset < text.length;) {
          let end = Math.min(offset + 16000, text.length);
          if (end < text.length && text.charCodeAt(end - 1) >= 0xd800 && text.charCodeAt(end - 1) <= 0xdbff) { end--; }
          parts[String(offset)] = { offset, text: text.slice(offset, end), next: end < text.length ? end : null }; offset = end;
        }
      }
      row['truncated'] = true; let end = 6000;
      if (text.charCodeAt(end - 1) >= 0xd800 && text.charCodeAt(end - 1) <= 0xdbff) { end--; } text = text.slice(0, end);
    }
    row['text'] = text;
    if (same) {
      if (prior.length && prior[0][0] !== version) { await this.db.run('DELETE FROM parts WHERE account=? AND session=? AND key=?', [account.id, session, key]); }
      const process = row['kind'] === 'processPage' || row['phase'] === 'commentary' || row['interaction'] === 'response' ||
        !['assistant', 'user', 'error', 'fileChanges'].includes(row['role'] as string);
      if (process && row['kind'] !== 'processPage') {
        const header = await this.db.rows('SELECT content FROM records WHERE account=? AND session=? AND turn=? AND visible=1 AND position<? ORDER BY position DESC,key DESC LIMIT 1', [account.id, session, turn, position]);
        const before = header.length ? JSON.parse(header[0][0] as string) as ArchiveObject : null;
        if (!before || before['kind'] !== 'processPage') {
          const anchor = before ? before['key'] as string : 'start', headerKey = 'offline-process:' + turn + ':' + anchor;
          const summary = JSON.parse(JSON.stringify(row)) as ArchiveObject;
          summary['key'] = headerKey; summary['kind'] = 'processPage'; summary['role'] = 'activity'; summary['text'] = '';
          await this.db.run('INSERT OR IGNORE INTO records(account,session,key,turn,asked,position,visible,version,synced,content,bytes) VALUES(?,?,?,?,?,?,?,?,?,?,?)', [account.id, session, headerKey, turn, asked, position - 0.25, 1, version, synced, JSON.stringify(summary), 512]);
        }
      }
      const encoded = JSON.stringify(row);
      await this.db.run('INSERT INTO records(account,session,key,turn,asked,position,visible,version,synced,content,bytes,needs_parts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(account,session,key) DO UPDATE SET turn=excluded.turn,asked=CASE WHEN excluded.asked=0 THEN records.asked ELSE excluded.asked END,position=excluded.position,visible=excluded.visible,version=excluded.version,synced=excluded.synced,content=excluded.content,bytes=excluded.bytes,needs_parts=excluded.needs_parts',
        [account.id, session, key, turn, asked, position, row['kind'] === 'processPage' || !process ? 1 : 0, version, synced, encoded, encoded.length * 3, row['truncated'] ? 1 : 0]);
      await this.db.run('DELETE FROM deleted WHERE account=? AND session=? AND key=?', [account.id, session, key]);
    }
    for (const offset of Object.keys(parts)) {
      const part = parts[offset];
      if (String(part['offset']) !== offset || typeof part['text'] !== 'string' || (part['text'] as string).length > 65536 ||
        part['next'] !== null && (!Number.isSafeInteger(part['next']) || (part['next'] as number) <= Number(offset))) { throw new Error('无效离线正文块'); }
      await this.db.run('INSERT OR REPLACE INTO parts(account,session,key,offset,version,content,characters,next_offset) VALUES(?,?,?,?,?,?,?,?)',
        [account.id, session, key, Number(offset), version, JSON.stringify(part), (part['text'] as string).length, part['next'] as number | null]);
    }
    if (Object.keys(parts).length) { await this.finishParts(account, session, key, version); }
  }
  async list(account: ArchiveAccount): Promise<ArchiveObject> {
    await this.current(account, false);
    const sessions: ArchiveObject[] = [];
    for (const row of await this.db.rows('SELECT s.id,s.title,s.synced FROM sessions s WHERE account=? AND EXISTS(SELECT 1 FROM records r WHERE r.account=s.account AND r.session=s.id) ' +
      'UNION ALL SELECT c.session,c.title,c.changed FROM composer c WHERE c.account=? AND c.active=1 ' +
      'AND NOT EXISTS(SELECT 1 FROM records r WHERE r.account=c.account AND r.session=c.session) ORDER BY 3 DESC,1', [account.id, account.id])) {
      sessions.push({ id: row[0] as string, title: row[1] as string, syncedAt: row[2] as number });
    }
    return { sessions, name: account.name, composerWritable: account.status === 'valid' };
  }
  async available(): Promise<ArchiveAccount[]> {
    const rows = await this.db.rows('SELECT a.* FROM accounts a WHERE EXISTS(SELECT 1 FROM records r WHERE r.account=a.id) OR EXISTS(SELECT 1 FROM composer c WHERE c.account=a.id AND c.active=1) ORDER BY a.name,a.id');
    return rows.map(row => this.accountRow(row));
  }
  async page(account: ArchiveAccount, session: string, event: ArchiveObject): Promise<ArchiveObject | null> {
    const current = await this.current(account, false), mode = event['mode'] as string || 'recent';
    let saved = await this.db.rows('SELECT title,synced,complete FROM sessions WHERE account=? AND id=?', [current.id, session]);
    if (!saved.length) { saved = await this.db.rows('SELECT title,changed,0 AS complete FROM composer WHERE account=? AND session=? AND active=1', [current.id, session]); }
    if (!saved.length) { return null; }
    if (mode === 'body') { return this.body(current, session, event); }
    const values: ArchiveValue[] = [current.id, session]; let filter = ' AND visible=1', sort = 'asked DESC,turn DESC,position DESC,key DESC';
    if (mode === 'process') {
      const header = await this.db.rows('SELECT turn,position FROM records WHERE account=? AND session=? AND key=? AND visible=1', [current.id, session, event['key'] as string]);
      if (!header.length) { return null; }
      const end = await this.db.rows('SELECT position FROM records WHERE account=? AND session=? AND turn=? AND visible=1 AND position>? ORDER BY position,key LIMIT 1', [current.id, session, header[0][0], header[0][1]]);
      filter = ' AND visible=0 AND turn=? AND position>? AND position<?'; values.push(header[0][0], header[0][1], end.length ? end[0][0] : Number.MAX_VALUE); sort = 'position,key';
    }
    const cursor = event[mode === 'process' ? 'after' : 'before'] as string;
    if (cursor) {
      const parts = JSON.parse(cursor) as ArchiveValue[];
      if (parts.length !== 7 || parts[0] !== session || parts[1] !== current.revision || parts[2] !== current.epoch) { return null; }
      if (mode === 'process') { filter += ' AND (position,key)>(?,?)'; values.push(parts[5], parts[6]); }
      else { filter += ' AND (asked,turn,position,key)<(?,?,?,?)'; values.push(parts[3], parts[4], parts[5], parts[6]); }
    }
    const candidates = await this.db.rows('SELECT key,turn,asked,position,bytes FROM records WHERE account=? AND session=?' + filter + ' ORDER BY ' + sort + ' LIMIT 41', values);
    const selected: ArchiveValue[][] = []; let bytes = 0;
    for (const entry of candidates.slice(0, 40)) { if (selected.length && bytes + (entry[4] as number) > 192 * 1024) { break; } selected.push(entry); bytes += entry[4] as number; }
    const rows: ArchiveObject[] = [], turns: Record<string, ArchiveObject> = {}; let position = 0;
    for (const entry of mode === 'process' ? selected : [...selected].reverse()) {
      const record = await this.db.rows('SELECT content FROM records WHERE account=? AND session=? AND key=?', [current.id, session, entry[0]]);
      const row = JSON.parse(record[0][0] as string) as ArchiveObject; row['order'] = position++;
      const id = row['turnId'] as string;
      const turn: ArchiveObject = turns[id] || { id, processes: [] };
      for (const pair of [['turnStatus', 'status'], ['turnStartedAt', 'startedAt'], ['turnCompletedAt', 'completedAt'], ['turnCompletionKnown', 'completionKnown'], ['turnError', 'error']]) {
        if (row[pair[0]] !== undefined) { turn[pair[1]] = row[pair[0]]; }
      }
      if (row['kind'] === 'processPage' && mode !== 'process') {
        (turn['processes'] as ArchiveObject[]).push({ key: row['key'], version: row['version'], order: row['order'], processCursor: row['key'] });
      } else { rows.push(row); }
      turns[id] = turn;
    }
    const tail = selected[selected.length - 1];
    const next = tail && candidates.length > selected.length ? JSON.stringify([session, current.revision, current.epoch, tail[2], tail[1], tail[3], tail[0]]) : null;
    return { recordPage: 1, id: session, title: saved[0][0] as string, syncedAt: saved[0][1] as number, gaps: saved[0][2] !== 1 || await this.hasGaps(current, session),
      rows, index: rows, turns: Object.values(turns), before: mode === 'process' ? null : next, next: mode === 'process' ? next : null, hasMore: next !== null, readRecords: selected.length };
  }
  async body(account: ArchiveAccount, session: string, event: ArchiveObject): Promise<ArchiveObject | null> {
    const rows = await this.db.rows('SELECT p.content FROM parts p JOIN records r ON r.account=p.account AND r.session=p.session AND r.key=p.key AND r.version=p.version WHERE p.account=? AND p.session=? AND p.key=? AND p.version=? AND p.offset=?',
      [account.id, session, event['key'] as string, event['version'] as string, Number(event['offset'] || 0)]);
    if (!rows.length) { return null; }
    const part = JSON.parse(rows[0][0] as string) as ArchiveObject; part['key'] = event['key']; part['version'] = event['version']; return part;
  }
  async storePart(account: ArchiveAccount, session: string, key: string, version: string, part: ArchiveObject): Promise<void> {
    await this.current(account);
    const row = await this.db.rows('SELECT version FROM records WHERE account=? AND session=? AND key=?', [account.id, session, key]);
    if (!row.length || row[0][0] !== version) { return; }
    await this.db.run('INSERT OR REPLACE INTO parts(account,session,key,offset,version,content,characters,next_offset) VALUES(?,?,?,?,?,?,?,?)',
      [account.id, session, key, Number(part['offset']), version, JSON.stringify(part), (part['text'] as string).length, part['next'] as number | null]);
    await this.finishParts(account, session, key, version);
  }
  private async finishParts(account: ArchiveAccount, session: string, key: string, version: string): Promise<void> {
    const tail = await this.db.rows('SELECT 1 FROM parts WHERE account=? AND session=? AND key=? AND version=? AND next_offset IS NULL LIMIT 1', [account.id, session, key, version]);
    if (!tail.length) { return; }
    const rows = await this.db.rows('SELECT offset,characters,next_offset FROM parts WHERE account=? AND session=? AND key=? AND version=? ORDER BY offset', [account.id, session, key, version]);
    let next = 0, complete = false;
    for (const row of rows) {
      if (complete || row[0] !== next || (row[1] as number) < 0) { return; }
      next += row[1] as number; complete = row[2] === null;
      if (!complete && row[2] !== next) { return; }
    }
    if (complete) { await this.db.run('INSERT OR REPLACE INTO finished_records VALUES(?,?,?,?,?)', [account.id, session, key, version, next]); }
  }
  async hasGaps(account: ArchiveAccount, session: string): Promise<boolean> {
    const rows = await this.db.rows('SELECT 1 FROM records r WHERE r.account=? AND r.session=? AND r.needs_parts=1 AND NOT EXISTS(SELECT 1 FROM finished_records f WHERE f.account=r.account AND f.session=r.session AND f.key=r.key AND f.version=r.version) LIMIT 1', [account.id, session]);
    return rows.length > 0;
  }
  async clear(account: ArchiveAccount, start: number, end: number, eraseComposer: boolean = false): Promise<void> {
    await this.transaction(async () => {
      const current = await this.account(account.id);
      if (!current || current.epoch !== account.epoch) { throw new Error('离线身份已变化'); }
      await this.db.run('UPDATE accounts SET revision=revision+1 WHERE id=?', [current.id]);
      await this.db.run('INSERT INTO clears SELECT id,revision,?,? FROM accounts WHERE id=?', [start, end, current.id]);
      await this.db.run('DELETE FROM records WHERE account=? AND asked>=? AND asked<?', [current.id, start, end]);
      if (eraseComposer) { await this.db.run('DELETE FROM composer WHERE account=?', [current.id]); }
      await this.db.run('UPDATE turns SET suppressed=1 WHERE account=? AND asked>=? AND asked<?', [current.id, start, end]);
      await this.db.run('DELETE FROM items WHERE account=? AND asked>=? AND asked<?', [current.id, start, end]);
      await this.db.run('DELETE FROM chunks WHERE account=? AND version NOT IN(SELECT version FROM items WHERE account=?)', [current.id, current.id]);
    });
    await this.db.run('VACUUM');
  }
}
