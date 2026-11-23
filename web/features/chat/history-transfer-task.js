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
  async manifest(id, cursor, progress) {
    const query = new URLSearchParams({id});
    if (cursor) query.set('cursor', cursor);
    let page;
    do {
      page = await this.request('/api/history-transfer/manifest?' + query);
      if (page.status === 'preparing') { progress?.(page); await this.delay(400); }
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
      records: item.records,
      bytes: item.bytes,
      clearRevision: state.clearRevision,
      taskClearRevision: task?.taskClearRevision ?? state.clearRevision,
    };
  }
  prepare(ids, prioritySession, progress) {
    if (this.preparing) return this.preparing;
    this.preparing = this.beginPreparation(ids, prioritySession, progress).catch(async error => {
      if (this.task?.state === 'preparing') {
        this.task.state = 'prepare-error'; this.task.error = error.message;
        await this.save().catch(() => {});
      }
      throw error;
    }).finally(() => { this.preparing = null; });
    return this.preparing;
  }
  async beginPreparation(ids, prioritySession, progress) {
    const state = await this.state(), requestedAt = Date.now();
    const selection = Array.isArray(ids) ? {ids} : ids;
    this.task = {state:'preparing', dismissed:false, requestedAt, selection, preparingSessions:0};
    await this.save();
    const created = await this.request('/api/history-transfer/manifest', {
      ...selection, prioritySession,
      ...(state.recordBatch === 1 && this.recordBatch === 1 ? {batchRecords:true} : {}),
    });
    Object.assign(this.task, {manifestId:created.id,capturedAt:created.capturedAt,sessionCount:created.sessionCount});
    await this.save();
    return this.finishPreparation(progress);
  }
  async finishPreparation(progress) {
    const task = this.task;
    const page = await this.manifest(task.manifestId, null, value => {
      if (this.task !== task) return;
      task.preparingSessions = value.summary?.processed || 0;
      task.sessionCount = value.sessionCount; task.title = value.summary?.currentTitle || '';
      this.changed(task); progress?.(value);
    });
    if (this.task !== task) throw Error('传输任务已变化');
    const fullBytes = Number(page.summary?.textBytes || 0) + Number(page.summary?.attachmentBytes || 0);
    const prepared = {
      id:task.manifestId, capturedAt:task.capturedAt ?? page.capturedAt, requestedAt:task.requestedAt,
      summary:page.summary, errors:page.errors || [], warnings:page.warnings || [], failedSessions:page.failedSessions || 0,
      downloadBytes:fullBytes, reusedBytes:0, fullBytes, estimated:true,
    };
    task.state='ready'; task.prepared=prepared; delete task.title;
    await this.save();
    return prepared;
  }
  async save() {
    await this.native('transferTaskSave', {task: this.task});
    this.changed(this.task);
  }
  async dismiss() {
    if (!this.task || !['completed','prepare-error'].includes(this.task.state)) return;
    this.task.dismissed = true;
    await this.save();
  }
  async restore() {
    const saved = await this.native('transferTaskLoad');
    if (!saved.task) return;
    this.task = saved.task;
    if (this.task.state === 'completed' && this.task.dismissed === undefined) {
      this.task.dismissed = true;
      await this.save();
    } else this.changed(this.task);
    if (this.task.state === 'preparing') {
      if (!this.task.manifestId) { this.task.state='prepare-error';this.task.error='准备请求已中断，请重新选择会话';await this.save();return; }
      this.preparing=this.finishPreparation().catch(async error=>{
        this.task.state='prepare-error';this.task.error=error.message;await this.save().catch(()=>{});
      }).finally(()=>{this.preparing=null;});
      return;
    }
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
      state: 'running', dismissed:false, allowMetered, meteredNetworkEpoch: allowMetered ? state.networkEpoch : null, downloadedBytes: 0,
      expectedBytes: prepared.downloadBytes, fullBytes: prepared.fullBytes, processedBytes:0, currentBytes:0,
      reusedBytes: prepared.reusedBytes, completedItems: 0, clearedItems: 0,
      failedSessions: prepared.failedSessions,
      incompleteSessions: (prepared.warnings || []).map(item => item.cacheSessionId).filter(Boolean),
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
    if (cached.present) { this.task.currentBytes=item.bytes; return 'saved'; }
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
      if (known.present) { this.task.currentBytes=Math.min(item.bytes,offset+chunkBytes); continue; }
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
      this.task.currentBytes=offset+length;
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
            if (item.kind === 'record' && item.cacheSessionId !== this.task.lastCompleteSession &&
                !this.task.incompleteSessions?.includes(item.cacheSessionId)) {
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
          this.task.currentBytes=0;
          this.changed(this.task);
          const result = await this.download(item);
          if (result === 'paused') return;
          if (result === 'retry') continue;
          if (result === 'cleared') this.task.clearedItems++;
          else this.task.completedItems++;
          this.task.processedBytes=(this.task.processedBytes||0)+item.bytes;this.task.currentBytes=0;
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
