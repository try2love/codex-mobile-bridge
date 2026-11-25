import { ArchiveObject } from './OfflineArchive';

/** The gateway's turn fragments are streamed field by field; bodyParts never
 * become a single in-memory dictionary, even for a multi-megabyte tool output. */
export class RecordStream {
  private source: () => Promise<string | null>;
  private buffer: string = '';
  private offset: number = 0;
  private ended: boolean = false;
  constructor(source: () => Promise<string | null>) { this.source = source; }

  private async fill(): Promise<boolean> {
    while (this.offset >= this.buffer.length && !this.ended) {
      const next = await this.source(); this.buffer = next || ''; this.offset = 0; this.ended = next === null;
    }
    return this.offset < this.buffer.length;
  }
  private async peek(): Promise<string> { return await this.fill() ? this.buffer[this.offset] : ''; }
  private async space(): Promise<void> {
    while (await this.fill()) {
      while (this.offset < this.buffer.length && /[\t\r\n ]/.test(this.buffer[this.offset])) { this.offset++; }
      if (this.offset < this.buffer.length) { return; }
    }
  }
  private async expect(value: string): Promise<void> {
    await this.space(); if (await this.peek() !== value) { throw new Error('无效传输记录结构'); } this.offset++;
  }
  private async value(): Promise<Object | null> {
    await this.space(); const first = await this.peek();
    if (!first) { throw new Error('传输记录不完整'); }
    const structured = first === '{' || first === '[', string = first === '"';
    let quoted = false, escaped = false, depth = 0, done = false, size = 0;
    const pieces: string[] = [];
    while (await this.fill()) {
      const start = this.offset;
      while (this.offset < this.buffer.length) {
        const char = this.buffer[this.offset];
        if (!structured && !string && /[\s,}\]]/.test(char)) { done = true; break; }
        this.offset++;
        if (quoted) {
          if (escaped) { escaped = false; }
          else if (char === '\\') { escaped = true; }
          else if (char === '"') { quoted = false; if (string) { done = true; break; } }
        } else if (char === '"') { quoted = true; }
        else if (char === '{' || char === '[') { depth++; }
        else if (char === '}' || char === ']') { depth--; if (structured && depth === 0) { done = true; break; } }
      }
      const piece = this.buffer.slice(start, this.offset); size += piece.length;
      if (size > 2 * 1024 * 1024) { throw new Error('传输记录字段过大'); }
      pieces.push(piece); if (done) { break; }
    }
    return JSON.parse(pieces.join('')) as Object | null;
  }
  private async object(visit: (key: string) => Promise<void>): Promise<void> {
    await this.expect('{'); await this.space(); if (await this.peek() === '}') { this.offset++; return; }
    while (true) {
      const key = await this.value(); if (typeof key !== 'string') { throw new Error('无效传输记录字段'); }
      await this.expect(':'); await visit(key); await this.space();
      const next = await this.peek(); this.offset++;
      if (next === '}') { return; } if (next !== ',') { throw new Error('传输记录不完整'); }
    }
  }
  private async array(visit: () => Promise<void>): Promise<void> {
    await this.expect('['); await this.space(); if (await this.peek() === ']') { this.offset++; return; }
    while (true) {
      await visit(); await this.space(); const next = await this.peek(); this.offset++;
      if (next === ']') { return; } if (next !== ',') { throw new Error('传输记录不完整'); }
    }
  }
  async read(turn: string, asked: number, message: (value: ArchiveObject) => Promise<void>,
    part: (value: ArchiveObject, part: ArchiveObject) => Promise<void>,
    finish: (value: ArchiveObject, characters: number) => Promise<void>): Promise<void> {
    let identified = false, dated = false, messages = false;
    await this.object(async key => {
      if (key === 'id') { if (await this.value() !== turn) { throw new Error('传输回合不匹配'); } identified = true; }
      else if (key === 'questionTime') { if (await this.value() !== asked) { throw new Error('传输日期不匹配'); } dated = true; }
      else if (key === 'messages') {
        if (!identified || !dated || messages) { throw new Error('无效传输记录顺序'); } messages = true;
        await this.array(async () => {
          const metadata: ArchiveObject = {}; let started = false, characters = 0, next = 0, body = false, last = false;
          await this.object(async field => {
            if (field === 'bodyParts') {
              if (started) { throw new Error('重复正文块'); } started = true; await message(metadata);
              await this.object(async offset => {
                const chunk = await this.value() as ArchiveObject;
                if (last || !chunk || String(chunk['offset']) !== offset || Number(offset) !== next || typeof chunk['text'] !== 'string') { throw new Error('正文块不连续'); }
                const count = (chunk['text'] as string).length; characters += count;
                next += count; last = chunk['next'] === null; body = true;
                if (!last && chunk['next'] !== next) { throw new Error('正文块偏移不匹配'); }
                await part(metadata, chunk);
              });
            } else {
              if (started) { throw new Error('无效传输记录字段顺序'); }
              metadata[field] = await this.value();
            }
          });
          if (!started) { await message(metadata); }
          if (body && !last) { throw new Error('正文块不完整'); }
          await finish(metadata, body ? characters : String(metadata['text'] || '').length);
        });
      } else { await this.value(); }
    });
    await this.space(); if (!messages || await this.peek()) { throw new Error('传输记录不完整'); }
  }
}
