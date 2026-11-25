import { util } from '@kit.ArkTS';
import { cryptoFramework } from '@kit.CryptoArchitectureKit';
import { OfflineArchive, ArchiveAccount, ArchiveObject } from './OfflineArchive';
import { GatewayHttp, GatewayResponse } from './GatewayHttp';
import { Digest } from './Digest';
import { RecordStream } from './RecordStream';
import { NetworkPolicy } from './NetworkPolicy';
import { common } from '@kit.AbilityKit';
import { fileIo, picker } from '@kit.CoreFileKit';
import { AttachmentDownload } from './AttachmentDownload';

export class HistoryTransfer {
  static readonly CHUNK: number = 524288;
  private archive: OfflineArchive;
  private network: NetworkPolicy;
  private freeBytes: () => number;
  private http: GatewayHttp = new GatewayHttp();
  private fetching: boolean = false;
  private exportFailed: () => void;
  constructor(archive: OfflineArchive, network: NetworkPolicy, freeBytes: () => number, exportFailed: () => void = () => {}) {
    this.archive = archive; this.network = network; this.freeBytes = freeBytes; this.exportFailed = exportFailed;
  }
  cancel(): void { this.http.cancel(); }
  private outcome(reason: string | null): ArchiveObject { return { saved: reason === null, reason: reason || '' }; }
  async stats(account: ArchiveAccount): Promise<ArchiveObject> {
    const current = await this.archive.current(account);
    return { recordBatch: 1, freeBytes: this.freeBytes(), clearRevision: current.revision,
      allowTransfer: this.network.available && this.network.unmetered, networkAvailable: this.network.available, networkEpoch: this.network.epoch };
  }
  private validate(event: ArchiveObject): void {
    if (!/^[a-f0-9]{64}$/.test(event['version'] as string) || typeof event['sessionId'] !== 'string' || !(event['sessionId'] as string).length ||
      typeof event['turnId'] !== 'string' || !(event['turnId'] as string).length || (event['sessionId'] as string).length > 4096 || (event['turnId'] as string).length > 512 ||
      !Number.isSafeInteger(event['bytes']) || (event['bytes'] as number) < 1 ||
      !Number.isSafeInteger(event['questionTime']) || (event['questionTime'] as number) < 0 ||
      !/^[0-9]+$/.test(event['clearRevision'] as string) || !/^[0-9]+$/.test((event['taskClearRevision'] || event['clearRevision']) as string)) {
      throw new Error('无效传输对象');
    }
  }
  private async admission(account: ArchiveAccount, event: ArchiveObject): Promise<string | null> {
    this.validate(event); const current = await this.archive.current(account);
    const clear = await this.archive.db.rows('SELECT 1 FROM clears WHERE account=? AND revision>? AND start<=? AND end>? LIMIT 1',
      [current.id, Number(event['taskClearRevision'] || event['clearRevision']), event['questionTime'] as number, event['questionTime'] as number]);
    if (clear.length) { return 'cleared'; } return current.revision === event['clearRevision'] ? null : 'revision';
  }
  private async retain(account: ArchiveAccount, event: ArchiveObject): Promise<void> {
    await this.archive.db.run('INSERT OR IGNORE INTO items VALUES(?,?,?,?,?,?)', [account.id, event['version'] as string, event['sessionId'] as string,
      event['turnId'] as string, event['questionTime'] as number, JSON.stringify(event)]);
  }
  private async chunk(account: ArchiveAccount, version: string, index: number): Promise<Uint8Array | null> {
    if (!/^[a-f0-9]{64}$/.test(version) || !Number.isSafeInteger(index) || index < 0) { throw new Error('无效传输块'); }
    const rows = await this.archive.db.rows('SELECT data FROM chunks WHERE account=? AND version=? AND part=?', [account.id, version, index]);
    return rows.length ? rows[0][0] as Uint8Array : null;
  }
  private async put(account: ArchiveAccount, event: ArchiveObject, data: Uint8Array, hash: string): Promise<ArchiveObject> {
    const reason = await this.admission(account, event); if (reason) { return this.outcome(reason); }
    const index = event['index'] as number, bytes = event['bytes'] as number;
    if (!Number.isSafeInteger(index) || index < 0 || index * HistoryTransfer.CHUNK >= bytes || data.length !== Math.min(HistoryTransfer.CHUNK, bytes - index * HistoryTransfer.CHUNK) ||
      !/^[a-f0-9]{64}$/.test(hash) || await Digest.sha256(data) !== hash) { return this.outcome('integrity'); }
    if (this.freeBytes() < data.length * 2 + 1048576) { return this.outcome('space'); }
    const previous = await this.chunk(account, event['version'] as string, index);
    if (previous) { return this.outcome(await Digest.sha256(previous) === hash ? null : 'integrity'); }
    await this.archive.transaction(async () => {
      await this.retain(account, event);
      await this.archive.db.run('INSERT OR REPLACE INTO chunks VALUES(?,?,?,?)', [account.id, event['version'] as string, index, data]);
    });
    return this.outcome(null);
  }
  private static header(response: GatewayResponse, name: string): string {
    for (const key of Object.keys(response.headers)) { if (key.toLowerCase() === name) { return typeof response.headers[key] === 'string' ? response.headers[key] as string : ''; } }
    return '';
  }
  static contentRoute(event: ArchiveObject): string {
    const path = event['url'] as string;
    if (typeof path !== 'string' || path.length > 512 || !path.startsWith('/api/history-transfer/content?')) { throw new Error('无效传输地址'); }
    const query: Record<string, string> = {};
    for (const part of path.slice(path.indexOf('?') + 1).split('&')) {
      const pair = part.split('='); if (pair.length !== 2 || query[pair[0]] !== undefined) { throw new Error('无效传输地址'); } query[pair[0]] = pair[1];
    }
    const offset = event['offset'] as number, index = event['index'] as number, bytes = event['bytes'] as number;
    if (Object.keys(query).length !== 4 || !/^[a-f0-9]{32}$/.test(query['id']) || !/^[a-f0-9]{64}$/.test(query['item']) ||
      !Number.isSafeInteger(index) || index < 0 || offset !== index * HistoryTransfer.CHUNK || offset >= bytes || query['offset'] !== String(offset) ||
      query['length'] !== String(Math.min(HistoryTransfer.CHUNK, bytes - offset))) { throw new Error('无效传输范围'); }
    return path;
  }
  async fetch(account: ArchiveAccount, event: ArchiveObject, base: string, cookie: string, current: () => boolean): Promise<Object | null> {
    let networkEpoch = '';
    const admitted = await this.archive.serial(async () => {
      const reason = await this.admission(account, event);
      if (reason) { return this.outcome(reason); }
      if (!current()) { return this.outcome('permission'); }
      if (!this.network.available || !this.network.unmetered && !(event['allowMetered'] && event['networkEpoch'] === this.network.epoch)) { return this.outcome('network'); }
      if (this.fetching) { return this.outcome('busy'); }
      networkEpoch = this.network.epoch; this.fetching = true; return null;
    });
    if (admitted) { return admitted; }
    try {
      const route = HistoryTransfer.contentRoute(event), offset = event['offset'] as number, bytes = event['bytes'] as number;
      const response = await this.http.request(base, route, cookie, HistoryTransfer.CHUNK);
      if (!current()) { return this.outcome('permission'); }
      if (!this.network.available || this.network.epoch !== networkEpoch) { return this.outcome('network'); }
      if ([401, 403].includes(response.status)) { return this.outcome('permission'); }
      if ([404, 409, 410].includes(response.status)) { return this.outcome('manifest'); }
      const hash = HistoryTransfer.header(response, 'x-chunk-sha256'), length = Math.min(HistoryTransfer.CHUNK, bytes - offset);
      if (response.status !== 206 || HistoryTransfer.header(response, 'content-range') !== 'bytes ' + offset + '-' + (offset + length - 1) + '/' + bytes ||
        HistoryTransfer.header(response, 'etag') !== '"sha256-' + event['version'] + '"' ||
        event['sha256'] && event['sha256'] !== hash) { return this.outcome('integrity'); }
      return await this.archive.serial(async () => current() ? this.put(account, event, response.data, hash) : this.outcome('permission'));
    } catch { return this.outcome('network'); } finally { this.fetching = false; }
  }
  private expected(event: ArchiveObject): ArchiveObject[] {
    const records = event['records'] as ArchiveObject[] || [{ key: event['key'], recordVersion: event['recordVersion'], textLength: event['textLength'] }];
    if (!records.length || records.length > 64 || records.some(row => typeof row['key'] !== 'string' || typeof row['recordVersion'] !== 'string' || !Number.isSafeInteger(row['textLength']))) {
      throw new Error('无效传输记录索引');
    }
    return records;
  }
  private async cached(account: ArchiveAccount, event: ArchiveObject): Promise<ArchiveObject> {
    const reason = await this.admission(account, event);
    if (reason) { return { present: false, cleared: reason === 'cleared', missingBytes: reason === 'cleared' ? 0 : event['bytes'] }; }
    const items = await this.archive.db.rows('SELECT content FROM items WHERE account=? AND version=? AND session=? AND turn=?',
      [account.id, event['version'] as string, event['sessionId'] as string, event['turnId'] as string]);
    const item = items.length ? JSON.parse(items[0][0] as string) as ArchiveObject : null;
    let present = !!item && item['committed'] === true;
    if (present && event['kind'] === 'attachment') {
      const key = event['attachmentKey'] || event['path'] || event['key'];
      const aliases = await this.archive.db.rows('SELECT 1 FROM attachment_keys WHERE account=? AND version=? AND session=? AND turn=? AND key=?',
        [account.id, event['version'] as string, event['sessionId'] as string, event['turnId'] as string, key as string]);
      if (!aliases.length) {
        // The content is reusable, but this logical attachment still needs its
        // own alias before it can be opened from the offline conversation.
        present = false;
      }
    }
    if (event['kind'] !== 'attachment') {
      present = true;
      for (const expected of this.expected(event)) {
        const row = await this.archive.db.rows('SELECT version,content FROM records WHERE account=? AND session=? AND key=?', [account.id, event['sessionId'] as string, expected['key'] as string]);
        if (!row.length || row[0][0] !== expected['recordVersion']) { present = false; break; }
        const content = JSON.parse(row[0][1] as string) as ArchiveObject;
        if (!content['truncated'] && String(content['text'] || '').length === expected['textLength']) { continue; }
        const finished = await this.archive.db.rows('SELECT 1 FROM finished_records WHERE account=? AND session=? AND key=? AND version=? AND length=?',
          [account.id, event['sessionId'] as string, expected['key'] as string, expected['recordVersion'] as string, expected['textLength'] as number]);
        if (!finished.length) { present = false; break; }
      }
    }
    const stored = await this.archive.db.rows('SELECT COALESCE(SUM(length(data)),0) FROM chunks WHERE account=? AND version=?', [account.id, event['version'] as string]);
    return { present, missingBytes: present ? 0 : Math.max(0, (event['bytes'] as number) - Number(stored[0][0])) };
  }
  async command(account: ArchiveAccount, type: string, event: ArchiveObject): Promise<Object | null> {
    await this.archive.current(account);
    if (type === 'transferStats') { return this.stats(account); }
    if (type === 'transferTaskLoad') {
      const row = await this.archive.db.rows('SELECT content FROM transfer_tasks WHERE account=?', [account.id]); return { task: row.length ? JSON.parse(row[0][0] as string) as Object : null };
    }
    if (type === 'transferTaskSave') {
      if (event['task'] === null) { await this.archive.db.run('DELETE FROM transfer_tasks WHERE account=?', [account.id]); }
      else {
        const task = JSON.stringify(event['task']); if (!task || task.length > 262144) { throw new Error('传输任务过大'); }
        await this.archive.db.run('INSERT OR REPLACE INTO transfer_tasks VALUES(?,?)', [account.id, task]);
      }
      return this.outcome(null);
    }
    if (type === 'transferCached') { return this.cached(account, event); }
    if (type === 'transferAttachment') { return this.attachment(account, event); }
    if (type === 'transferChunkHas') { return { present: await this.chunk(account, event['version'] as string, event['index'] as number) !== null }; }
    if (type === 'transferChunkGet') {
      const chunk = await this.chunk(account, event['version'] as string, event['index'] as number);
      return { data: chunk ? new util.Base64Helper().encodeToStringSync(chunk) : null };
    }
    if (type === 'transferChunkPut') {
      const data = event['data'] as string; if (data.length > 700000) { throw new Error('传输块过大'); }
      return this.put(account, event, new util.Base64Helper().decodeSync(data), event['sha256'] as string);
    }
    if (type === 'transferSessionComplete') {
      if (event['clearRevision'] !== (await this.archive.current(account)).revision) { return this.outcome('revision'); }
      if (await this.archive.hasGaps(account, event['sessionId'] as string)) { return { saved: true, complete: false }; }
      await this.archive.db.run('UPDATE sessions SET complete=1 WHERE account=? AND id=?', [account.id, event['sessionId'] as string]); return { saved: true, complete: true };
    }
    throw new Error('不支持的传输命令');
  }
  async commit(account: ArchiveAccount, event: ArchiveObject, attachment: boolean, current: () => boolean): Promise<ArchiveObject> {
    if (!current()) { return this.outcome('permission'); }
    const reason = await this.archive.serial(() => this.admission(account, event)); if (reason) { return this.outcome(reason as string); }
    const count = Math.ceil((event['bytes'] as number) / HistoryTransfer.CHUNK), md = cryptoFramework.createMd('SHA256'); let bytes = 0;
    for (let index = 0; index < count; index++) {
      if (!current()) { return this.outcome('permission'); }
      const chunk = await this.archive.serial(() => this.chunk(account, event['version'] as string, index)) as Uint8Array | null;
      if (!chunk) { return this.outcome('missing'); }
      bytes += chunk.length; await md.update({ data: chunk });
    }
    if (bytes !== event['bytes'] || Digest.hex((await md.digest()).data) !== event['version']) { return this.outcome('integrity'); }
    if (!attachment) {
      const expected = this.expected(event), seen: Set<string> = new Set(); let index = 0;
      const decoder = util.TextDecoder.create('utf-8', { fatal: true, ignoreBOM: true });
      const reader = new RecordStream(async () => {
        if (!current()) { throw new Error('permission'); }
        if (index >= count) { return null; }
        const chunk = await this.archive.serial(() => this.chunk(account, event['version'] as string, index++)) as Uint8Array | null;
        if (!chunk) { throw new Error('传输块缺失'); } return decoder.decodeToString(chunk, { stream: index < count });
      });
      const save = async (message: ArchiveObject): Promise<void> => {
        if (!current()) { throw new Error('permission'); }
        const reason = await this.admission(account, event); if (reason) { throw new Error(reason); }
        const value = JSON.parse(JSON.stringify(message)) as ArchiveObject;
        const merge: ArchiveObject = { sessionId: event['sessionId'], title: event['title'] || '', syncedAt: event['syncedAt'], source: 'manual', clearRevision: event['clearRevision'],
          complete: false, turns: [{ id: event['turnId'], questionTime: event['questionTime'], messages: [value] }] };
        await this.archive.merge(account, merge);
      };
      try {
        await reader.read(event['turnId'] as string, event['questionTime'] as number, async message => {
          const match = expected.find(row => row['key'] === message['key'] && row['recordVersion'] === message['version']);
          if (!match || seen.has(message['key'] as string)) { throw new Error('记录索引不匹配'); }
          seen.add(message['key'] as string); await this.archive.serial(async () => { await save(message); return null; });
        }, async (message, part) => {
          await this.archive.serial(async () => {
            if (!current()) { throw new Error('permission'); }
            const reason = await this.admission(account, event); if (reason) { throw new Error(reason); }
            await this.archive.storePart(account, event['sessionId'] as string, message['key'] as string, message['version'] as string, part); return null;
          });
        }, async (message, length) => {
          const match = expected.find(row => row['key'] === message['key']); if (!match || match['textLength'] !== length) { throw new Error('记录长度不匹配'); }
          await this.archive.serial(async () => {
            if (!current()) { throw new Error('permission'); }
            const reason = await this.admission(account, event); if (reason) { throw new Error(reason); }
            const row = await this.archive.db.rows('SELECT version FROM records WHERE account=? AND session=? AND key=?', [account.id, event['sessionId'] as string, message['key'] as string]);
            if (row.length && row[0][0] === message['version']) {
              await this.archive.db.run('INSERT OR REPLACE INTO finished_records VALUES(?,?,?,?,?)',
                [account.id, event['sessionId'] as string, message['key'] as string, message['version'] as string, length]);
            }
            return null;
          });
        });
        if (seen.size !== expected.length) { return this.outcome('integrity'); }
      } catch (error) {
        const reason = (error as Error).message; return this.outcome(['cleared', 'revision', 'permission'].includes(reason) ? reason : 'integrity');
      }
    }
    await this.archive.serial(async () => {
      if (!current()) { throw new Error('permission'); }
      const reason = await this.admission(account, event); if (reason) { throw new Error(reason); }
      if (attachment) {
        const key = event['attachmentKey'] || event['path'] || event['key'];
        if (typeof key !== 'string' || !key.length || key.length > 4096 || !/^[a-zA-Z0-9.+-]+\/[a-zA-Z0-9.+-]+$/.test(event['mediaType'] as string)) { throw new Error('无效附件'); }
        const item = JSON.parse(JSON.stringify(event)) as ArchiveObject; item['committed'] = true;
        await this.archive.db.run('INSERT INTO items VALUES(?,?,?,?,?,?) ON CONFLICT(account,version,session,turn) DO UPDATE SET content=excluded.content', [account.id, event['version'] as string, event['sessionId'] as string, event['turnId'] as string, event['questionTime'] as number, JSON.stringify(item)]);
        await this.archive.db.run('INSERT OR REPLACE INTO attachment_keys VALUES(?,?,?,?,?)', [account.id, event['version'] as string, event['sessionId'] as string, event['turnId'] as string, key]);
      } else {
        await this.archive.db.run('DELETE FROM items WHERE account=? AND version=? AND session=? AND turn=?', [account.id, event['version'] as string, event['sessionId'] as string, event['turnId'] as string]);
        await this.archive.db.run('DELETE FROM chunks WHERE account=? AND version=? AND NOT EXISTS(SELECT 1 FROM items WHERE account=? AND version=?)', [account.id, event['version'] as string, account.id, event['version'] as string]);
      }
      return null;
    });
    return this.outcome(null);
  }
  async attachment(account: ArchiveAccount, event: ArchiveObject): Promise<ArchiveObject> {
    await this.archive.current(account, false);
    const session = event['sessionId'] as string, turn = event['turnId'] as string;
    let version = event['version'] as string;
    if (!version) {
      const alias = await this.archive.db.rows('SELECT version FROM attachment_keys WHERE account=? AND session=? AND turn=? AND key=?', [account.id, session, turn, event['attachmentKey'] as string || '']);
      if (!alias.length) { return {}; } version = alias[0][0] as string;
    }
    if (!/^[a-f0-9]{64}$/.test(version)) { return {}; }
    const rows = await this.archive.db.rows('SELECT content FROM items WHERE account=? AND version=? AND session=? AND turn=?', [account.id, version, session, turn]);
    if (!rows.length) { return {}; }
    const item = JSON.parse(rows[0][0] as string) as ArchiveObject;
    if (!item['committed'] || item['kind'] !== 'attachment') { return {}; }
    if (event['index'] !== undefined) {
      const chunk = await this.chunk(account, version, event['index'] as number);
      return { data: chunk ? new util.Base64Helper().encodeToStringSync(chunk) : null };
    }
    return { version, bytes: item['bytes'], mediaType: item['mediaType'], questionTime: item['questionTime'], chunkBytes: HistoryTransfer.CHUNK };
  }
  async exportAttachment(context: common.UIAbilityContext, account: ArchiveAccount, event: ArchiveObject, current: () => boolean): Promise<ArchiveObject> {
    const meta = await this.archive.serial(() => this.attachment(account, event)) as ArchiveObject;
    if (!meta['version'] || !current() || this.freeBytes() < (meta['bytes'] as number) + 1048576) { return { opened: false }; }
    const path = context.cacheDir + '/bridge-offline-' + util.generateRandomUUID();
    const file = fileIo.openSync(path, fileIo.OpenMode.CREATE | fileIo.OpenMode.READ_WRITE | fileIo.OpenMode.TRUNC);
    let selecting = false;
    try {
      const md = cryptoFramework.createMd('SHA256'); let total = 0;
      for (let index = 0; total < (meta['bytes'] as number); index++) {
        if (!current()) { return { opened: false }; }
        const chunk = await this.archive.serial(() => this.chunk(account, meta['version'] as string, index)) as Uint8Array | null;
        if (!chunk) { return { opened: false }; }
        await md.update({ data: chunk }); let offset = 0;
        while (offset < chunk.length) {
          const written = fileIo.writeSync(file.fd, chunk.buffer.slice(chunk.byteOffset + offset, chunk.byteOffset + chunk.byteLength));
          if (written <= 0) { throw new Error('无法保存附件'); } offset += written;
        }
        total += chunk.length;
      }
      if (total !== meta['bytes'] || Digest.hex((await md.digest()).data) !== meta['version']) { return { opened: false }; }
      const options = new picker.DocumentSaveOptions();
      options.newFileNames = [AttachmentDownload.filename("attachment; filename*=UTF-8''" + encodeURIComponent(event['name'] as string || 'download'))];
      // A system picker can remain open indefinitely. Acknowledge opening it
      // now, rather than timing out the page's short native-message request.
      const selection = new picker.DocumentViewPicker(context).save(options);
      selecting = true;
      selection.then(async selected => {
        if (selected.length !== 1 || !current()) { return; }
        const retained = await this.archive.serial(() => this.attachment(account, event)) as ArchiveObject;
        if (retained['version'] !== meta['version'] || !current()) { return; }
        const target = fileIo.openSync(selected[0], fileIo.OpenMode.READ_WRITE);
        try { fileIo.copyFileSync(path, target.fd); } finally { fileIo.closeSync(target); }
      }).catch(() => { if (current()) { this.exportFailed(); } }).finally(() => { try { fileIo.unlinkSync(path); } catch {} });
      return { opened: true };
    } finally { fileIo.closeSync(file); if (!selecting) { try { fileIo.unlinkSync(path); } catch {} } }
  }
}
