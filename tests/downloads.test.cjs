'use strict';
const test = require('node:test'), assert = require('node:assert/strict');
const {createHash} = require('node:crypto');
const {Task, eligible, native, filename} = require('../web/downloads.js');
const MB = 1024 * 1024, tag = '"sha256-original"';
const payload = Uint8Array.from({length:2 * MB + 723}, (_, i) => i % 251);
const hash = value => createHash('sha256').update(value).digest('hex');
function source(bytes = payload, options = {}) {
  const requests = [];
  const fetcher = async (url, request) => {
    const [first, last] = request.headers.Range.slice(6).split('-').map(Number);
    requests.push({first, last, ...request});
    if (first >= bytes.length) return new Response(null, {status:416, headers:{ETag:tag, 'Content-Range':`bytes */${bytes.length}`}});
    const end = Math.min(last, bytes.length - 1);
    let position = first;
    const stream = new ReadableStream({
      pull(controller) {
        if (request.signal.aborted) { controller.error(new DOMException('Aborted', 'AbortError')); return; }
        if (position > end) { controller.close(); return; }
        const next = Math.min(position + 16384, end + 1);
        controller.enqueue(bytes.slice(position, next)); position = next;
      }
    });
    return new Response(stream, {status:206, headers:{ETag:tag,
      'Content-Range':`bytes ${first}-${end}/${options.unknown ? '*' : bytes.length}`,
      'Content-Length':String(end - first + 1), 'Content-Disposition':"attachment; filename*=UTF-8''report%20final.zip"}});
  };
  return {fetcher, requests};
}
test('bounded requests reconstruct exact bytes and carry the validator', async () => {
  const server = source(), task = new Task(server);
  await task.start('/file', 'fallback.zip');
  assert.equal(task.status, 'complete'); assert.equal(task.name, 'report final.zip');
  assert.equal(hash(new Uint8Array(await task.blob().arrayBuffer())), hash(payload));
  assert.equal(server.requests.length, 3);
  assert.equal(server.requests[0].headers['If-Range'], undefined);
  for (const request of server.requests.slice(1)) assert.equal(request.headers['If-Range'], tag);
  for (const request of server.requests) {
    assert.ok(request.last - request.first < MB);
    assert.equal(request.credentials, 'same-origin'); assert.equal(request.redirect, 'error');
  }
});
test('pause aborts in-flight transfer; resume begins at retained bytes without changing final hash', async () => {
  const server = source(); let now = 0, paused = false;
  const task = new Task({...server, clock:() => now += 250, onChange:task => {
    if (!paused && task.bytes >= 32768 && task.status === 'downloading') { paused = true; task.pause(); }
  }});
  await task.start('/file', 'file.zip');
  const retained = task.bytes;
  assert.equal(task.status, 'paused'); assert.ok(retained > 0 && retained < MB);
  assert.equal(server.requests[0].signal.aborted, true); assert.equal(task.speed, 0);
  await task.resume();
  assert.equal(server.requests[1].first, retained);
  assert.equal(hash(new Uint8Array(await task.blob().arrayBuffer())), hash(payload));
});
test('unknown total shows no fabricated percentage and finishes with validated 416', async () => {
  const server = source(payload, {unknown:true}), totals = [];
  const task = new Task({...server, onChange:task => totals.push(task.total)});
  await task.start('/file', 'unknown.zip');
  assert.equal(task.status, 'complete'); assert.equal(task.total, payload.length);
  assert.ok(totals.slice(0, -1).every(total => total === null));
  assert.equal(hash(new Uint8Array(await task.blob().arrayBuffer())), hash(payload));
});
test('empty files can complete without a fabricated range', async () => {
  const task = new Task(source(new Uint8Array())); await task.start('/empty', 'empty');
  assert.equal(task.status, 'complete'); assert.equal(task.blob().size, 0);
});
for (const mode of ['200', 'etag', 'total', 'offset', 'length']) test(`resuming rejects changed or invalid response: ${mode}`, async () => {
  const server = source(); let paused = false, now = 0, resume = false;
  const task = new Task({clock:() => now += 250, onChange:task => {
    if (!paused && task.bytes && task.status === 'downloading') { paused = true; task.pause(); }
  }, fetcher:async (...args) => {
    if (!resume) return server.fetcher(...args);
    const first = Number(args[1].headers.Range.match(/\d+/)[0]);
    return new Response(new Uint8Array(20), {status:mode === '200' ? 200 : 206, headers:{
      ETag:mode === 'etag' ? '"new"' : tag,
      'Content-Range':`bytes ${mode === 'offset' ? 0 : first}-${first + 19}/${payload.length + (mode === 'total' ? 1 : 0)}`,
      'Content-Length':mode === 'length' ? '21' : '20'
    }});
  }});
  await task.start('/file', 'file.zip'); const retained = task.bytes; resume = true;
  await task.resume();
  assert.equal(task.status, 'error'); assert.equal(task.restartRequired, true); assert.equal(task.bytes, retained);
  assert.throws(() => task.blob());
});
test('older servers returning 200 work, but partial 200 requires explicit restart', async () => {
  const complete = new Task({fetcher:async () => new Response(payload)});
  await complete.start('/file', 'file'); assert.equal(complete.status, 'complete');
  let now = 0, paused = false;
  const task = new Task({clock:() => now += 250, fetcher:async () => new Response(payload), onChange:task => {
    if (!paused && task.bytes && task.status === 'downloading') { paused = true; task.pause(); }
  }});
  await task.start('/file', 'file'); assert.equal(task.status, 'paused'); assert.equal(task.restartRequired, true);
  await task.resume(); assert.equal(task.status, 'error');
  await task.restart(); assert.equal(task.status, 'complete');
  assert.equal(hash(new Uint8Array(await task.blob().arrayBuffer())), hash(payload));
});
test('truncated valid response can resume remaining bytes', async () => {
  const server = source(); let first = true;
  const task = new Task({fetcher:async (...args) => {
    if (!first) return server.fetcher(...args); first = false;
    return new Response(payload.slice(0, 1000), {status:206, headers:{ETag:tag,
      'Content-Range':`bytes 0-${MB - 1}/${payload.length}`, 'Content-Length':String(MB)}});
  }});
  await task.start('/file', 'file'); assert.equal(task.status, 'error'); assert.equal(task.bytes, 1000);
  await task.resume(); assert.equal(server.requests[0].first, 1000);
  assert.equal(hash(new Uint8Array(await task.blob().arrayBuffer())), hash(payload));
});
test('cancel discards bytes and late response cannot overwrite a new task', async () => {
  let deliver;
  const task = new Task({fetcher:() => new Promise(resolve => { deliver = resolve; })});
  const old = task.start('/old', 'old'); task.cancel();
  task.fetcher = source(new Uint8Array([9, 8, 7])).fetcher;
  await task.start('/new', 'new');
  deliver(new Response(new Uint8Array([1, 2]))); await old;
  assert.equal(task.url, '/new'); assert.equal(task.status, 'complete');
  assert.deepEqual(new Uint8Array(await task.blob().arrayBuffer()), new Uint8Array([9, 8, 7]));
  task.cancel(); assert.equal(task.parts.length, 0); assert.equal(task.bytes, 0); assert.equal(task.status, 'idle');
});
test('authentication failures and invalid lengths cannot become completed files', async () => {
  for (const response of [new Response(null,{status:401}), new Response(null,{status:403}),
    new Response('bad',{headers:{'Content-Length':String(51 * MB)}}),
    new Response('bad',{status:206, headers:{ETag:tag,'Content-Range':'bytes 0-2/100'}})]) {
    const task = new Task({fetcher:async () => response}); await task.start('/file', 'file');
    assert.equal(task.status, 'error'); assert.throws(() => task.blob());
  }
});
for (const code of ['download_changed', 'desktop_unavailable']) test(`relay 409 preserves the right recovery action: ${code}`, async () => {
  const server = source(); let paused = false, conflict = false, now = 0;
  const task = new Task({clock:() => now += 250, onChange:task => {
    if (!paused && task.bytes && task.status === 'downloading') { paused = true; task.pause(); }
  }, fetcher:(...args) => conflict ? Promise.resolve(Response.json({code}, {status:409})) : server.fetcher(...args)});
  await task.start('/file', 'file'); const retained = task.bytes; conflict = true;
  await task.resume();
  assert.equal(task.status, 'error'); assert.equal(task.bytes, retained);
  assert.equal(task.restartRequired, code === 'download_changed');
  conflict = false;
  if (task.restartRequired) await task.restart(); else await task.resume();
  assert.equal(hash(new Uint8Array(await task.blob().arrayBuffer())), hash(payload));
});
test('only same-origin known file routes are intercepted; both native Apps keep native downloads', () => {
  const base = 'https://bridge.example/', id = '12345678-1234-1234-1234-123456789012';
  for (const path of [`api/sessions/${id}/files/${'a'.repeat(64)}`, `api/sessions/${id}/workspace/download?host=ssh&path=x`,
    'api/desktop-sessions/claude/workspace/download?sessionId=x&path=y', 'api/desktop-sessions/deepseek/workspace/download?sessionId=x']) {
    assert.equal(eligible(path, base), true); assert.equal(eligible('https://other.example/'+path, base), false);
  }
  for (const value of ['/api/logout','/file.zip',`/api/sessions/${id}/uploads/x/preview`,'javascript:alert(1)','https://x:y@bridge.example/api/sessions/'+id+'/workspace/download']) assert.equal(eligible(value, base), false);
  assert.equal(native('Safari BridgeMobile/0.1-iOS'), true); assert.equal(native('Chrome BridgeMobile/0.1-Android'), true); assert.equal(native('Chrome'), false);
  assert.equal(filename('attachment; filename="../../hello.zip"', 'fallback'), 'hello.zip');
});
test('all download interface and error messages have English translations', () => {
  const fs = require('node:fs'), i18n = require('../web/i18n.js');
  i18n.setLanguage('en');
  for (const match of fs.readFileSync(require.resolve('../web/downloads.js'),'utf8').matchAll(/'([^'\n]*[\u4e00-\u9fff][^'\n]*)'/g)) assert.doesNotMatch(i18n.t(match[1]), /[\u4e00-\u9fff]/, match[1]);
  i18n.setLanguage('zh');
});
