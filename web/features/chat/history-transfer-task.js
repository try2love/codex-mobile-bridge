'use strict';

// A task stores its position in the immutable gateway manifest. Content stays in
// the native archive and verified chunk files, so reopening never resends it.
class HistoryTransferTask {
  constructor({request, native, changed = () => {}, delay = ms => new Promise(resolve => setTimeout(resolve, ms))}) {
    Object.assign(this, {request, native, changed, delay});
    this.generation = 0;
    this.task = null;
  }
  async state() {
    return this.native('transferStats');
  }
  async manifest(id, cursor) {
    const query = new URLSearchParams({id});
    if (cursor) query.set('cursor', cursor);
    let page;
    do {
      page = await this.request('/api/history-transfer/manifest?' + query);
      if (page.status === 'preparing') await this.delay(400);
    } while (page.status === 'preparing');
    if (page.status !== 'ready') throw Error(page.error || '无法准备会话传输，请重试');
    return page;
  }
  fields(item, state, task = this.task) {
    return {
      sessionId: item.cacheSessionId,
      turnId: item.turnId,
      questionTime: item.questionTime,
      key: item.key,
      recordVersion: item.recordVersion,
      textLength: item.textLength,
      attachmentKey: item.attachmentKey,
      path: item.path,
      version: item.version,
      kind: item.kind,
      bytes: item.bytes,
      clearRevision: state.clearRevision,
      taskClearRevision: task?.taskClearRevision ?? state.clearRevision,
    };
  }
  async missing(item, state, task) {
    const fields = this.fields(item, state, task);
    const cached = await this.native('transferCached', fields);
    if (cached.cleared || cached.present) return {...cached, missingBytes: 0};
    const chunkBytes = item.chunkBytes || 524288;
    let missingBytes = 0;
    for (let index = 0, offset = 0; offset < item.bytes; index++, offset += chunkBytes) {
      const chunk = await this.native('transferChunkHas', {...fields, index});
      if (!chunk.present) missingBytes += Math.min(chunkBytes, item.bytes - offset);
    }
    return {...cached, missingBytes};
  }
  async prepare(ids, prioritySession) {
    const requestedAt = Date.now();
    const selection = Array.isArray(ids) ? {ids} : ids;
    const created = await this.request('/api/history-transfer/manifest', {...selection, prioritySession});
    let cursor, first, downloadBytes = 0, reusedBytes = 0;
    const state = await this.state();
    do {
      const page = await this.manifest(created.id, cursor);
      first ||= page;
      for (const item of page.items || []) {
        const result = await this.missing(item, state, null);
        downloadBytes += result.missingBytes;
        reusedBytes += item.bytes - result.missingBytes;
      }
      cursor = page.nextCursor;
    } while (cursor);
    return {
      id: created.id, capturedAt: created.capturedAt ?? first.capturedAt, requestedAt,
      summary: first.summary, errors: first.errors || [], failedSessions: first.failedSessions || 0,
      downloadBytes, reusedBytes,
      fullBytes: Number(first.summary?.textBytes || 0) + Number(first.summary?.attachmentBytes || 0),
    };
  }
  async save() {
    await this.native('transferTaskSave', {task: this.task});
    this.changed(this.task);
  }
  async restore() {
    const saved = await this.native('transferTaskLoad');
    if (!saved.task) return;
    this.task = saved.task;
    this.changed(this.task);
    if (this.task.state === 'running' || ['network', 'offline', 'suspended'].includes(this.task.reason)) {
      const state = await this.state();
      if (this.task.allowMetered && this.task.meteredNetworkEpoch && state.networkEpoch !== this.task.meteredNetworkEpoch)
        this.task.allowMetered = false;
      if (state.networkAvailable !== false && (state.allowTransfer || this.task.allowMetered)) {
        this.task.state = 'running';
        delete this.task.reason;
        await this.save();
        void this.run();
      }
    }
  }
  async start(prepared, {allowMetered = false} = {}) {
    if (this.running) throw Error('请先暂停或取消当前传输');
    const state = await this.state();
    this.task = {
      manifestId: prepared.id, capturedAt: prepared.capturedAt, requestedAt: prepared.requestedAt,
      taskClearRevision: state.clearRevision, phase: 'record', cursor: null, index: 0,
      state: 'running', allowMetered, meteredNetworkEpoch: allowMetered ? state.networkEpoch : null, downloadedBytes: 0,
      expectedBytes: prepared.downloadBytes, fullBytes: prepared.fullBytes,
      reusedBytes: prepared.reusedBytes, completedItems: 0, clearedItems: 0,
      failedSessions: prepared.failedSessions,
    };
    await this.save();
    void this.run();
  }
  async pause(reason = 'manual') {
    if (!this.task || this.task.state !== 'running') return;
    this.task.state = 'paused';
    this.task.reason = reason;
    await this.save();
  }
  async resume({allowMetered = false} = {}) {
    if (!this.task || this.task.state !== 'paused') return;
    const state = await this.state();
    this.task.allowMetered = allowMetered;
    this.task.meteredNetworkEpoch = allowMetered ? state.networkEpoch : null;
    this.task.state = 'running';
    delete this.task.reason;
    await this.save();
    void this.run();
  }
  async cancel() {
    if (!this.task) return;
    this.task.state = 'cancelled';
    this.generation++;
    await this.save();
  }
  async network(state) {
    if (!this.task || !['running', 'paused'].includes(this.task.state)) return;
    if (this.task.allowMetered && this.task.meteredNetworkEpoch && state.networkEpoch && state.networkEpoch !== this.task.meteredNetworkEpoch)
      this.task.allowMetered = false;
    if (state.allowTransfer) this.task.allowMetered = false;
    if (state.networkAvailable === false) await this.pause('offline');
    else if (state.allowTransfer === false && !this.task.allowMetered) await this.pause('network');
    else if (this.task.state === 'paused' && ['offline', 'network'].includes(this.task.reason))
      await this.resume({allowMetered: this.task.allowMetered});
  }
  async download(item) {
    let state = await this.state();
    const cached = await this.native('transferCached', this.fields(item, state));
    if (cached.cleared) return 'cleared';
    if (cached.present) return 'saved';
    const chunkBytes = item.chunkBytes || 524288;
    for (let index = 0, offset = 0; offset < item.bytes; index++, offset += chunkBytes) {
      if (this.task.state !== 'running') return 'paused';
      state = await this.state();
      if (this.task.allowMetered && this.task.meteredNetworkEpoch && state.networkEpoch !== this.task.meteredNetworkEpoch)
        this.task.allowMetered = false;
      if (state.networkAvailable === false) { await this.pause('offline'); return 'paused'; }
      if (!state.allowTransfer && !this.task.allowMetered) { await this.pause('network'); return 'paused'; }
      const fields = this.fields(item, state);
      const known = await this.native('transferChunkHas', {...fields, index});
      if (known.present) continue;
      const length = Math.min(chunkBytes, item.bytes - offset);
      if (Number.isFinite(state.freeBytes) && state.freeBytes < length) {
        await this.pause('space'); return 'paused';
      }
      const query = new URLSearchParams({id: this.task.manifestId, item: item.id, offset, length});
      const chunk = item.chunks?.[index];
      const result = await this.native('transferChunkFetch', {
        ...fields, index, offset, length,
        url: '/api/history-transfer/content?' + query,
        sha256: typeof chunk === 'string' ? chunk : chunk?.sha256,
        allowMetered: this.task.allowMetered,
        networkEpoch: this.task.meteredNetworkEpoch,
      });
      if (!result.saved) {
        if (result.reason === 'cleared') return 'cleared';
        if (result.reason === 'revision') return 'retry';
        await this.pause(['space', 'network', 'offline'].includes(result.reason) ? result.reason : 'error');
        return 'paused';
      }
      this.task.downloadedBytes += length;
      await this.save();
      // Foreground chat and input remain free to issue their own requests.
      await this.delay(0);
    }
    if (this.task.state !== 'running') return 'paused';
    state = await this.state();
    const operation = item.kind === 'attachment' ? 'transferAttachmentCommit' : 'transferRecordCommit';
    const result = await this.native(operation, {
      ...item, ...this.fields(item, state),
      title: item.session?.title || '', syncedAt: this.task.requestedAt ?? this.task.capturedAt,
      mediaType: item.mime, complete: false,
    });
    if (result.saved) return 'saved';
    if (result.reason === 'cleared') return 'cleared';
    if (result.reason === 'revision') return 'retry';
    await this.pause(result.reason === 'space' ? 'space' : 'error');
    return 'paused';
  }
  async run() {
    if (this.running || !this.task || this.task.state !== 'running') return;
    this.running = true;
    const generation = this.generation;
    try {
      while (this.task.state === 'running' && generation === this.generation) {
        const page = await this.manifest(this.task.manifestId, this.task.cursor);
        const items = page.items || [];
        while (this.task.index < items.length && this.task.state === 'running' && generation === this.generation) {
          const item = items[this.task.index];
          if (this.task.phase === 'finish') {
            if (item.kind === 'record' && item.cacheSessionId !== this.task.lastCompleteSession) {
              const state = await this.state();
              await this.native('transferSessionComplete', {sessionId: item.cacheSessionId,
                clearRevision: state.clearRevision, taskClearRevision: this.task.taskClearRevision});
              this.task.lastCompleteSession = item.cacheSessionId;
            }
            this.task.index++;
            continue;
          }
          if (item.kind !== this.task.phase) { this.task.index++; continue; }
          this.task.title = item.session?.title || '';
          this.changed(this.task);
          const result = await this.download(item);
          if (result === 'paused') return;
          if (result === 'retry') continue;
          if (result === 'cleared') this.task.clearedItems++;
          else this.task.completedItems++;
          this.task.index++;
          await this.save();
        }
        if (this.task.state !== 'running' || generation !== this.generation) break;
        if (page.nextCursor) this.task.cursor = page.nextCursor;
        else if (this.task.phase === 'record') { this.task.phase = 'attachment'; this.task.cursor = null; }
        else if (this.task.phase === 'attachment') { this.task.phase = 'finish'; this.task.cursor = null; }
        else { this.task.state = 'completed'; delete this.task.title; }
        this.task.index = 0;
        await this.save();
      }
    } catch (error) {
      if (this.task?.state === 'running') {
        this.task.state = 'paused'; this.task.reason = 'error'; this.task.error = error.message;
        await this.save();
      }
    } finally { this.running = false; }
  }
}
if (typeof module !== 'undefined') module.exports = HistoryTransferTask;
