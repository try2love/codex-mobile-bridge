'use strict';
const {test, expect} = require('@playwright/test');
const {spawn} = require('node:child_process');
const {createInterface} = require('node:readline');
const path = require('node:path');

test('phone selection retains choices through a space warning and resumes verified missing content', async ({page, context}, info) => {
  const root = path.resolve(__dirname, '../..');
  const server = spawn('python3', ['-B', 'tests/browser/session-transfer-http-fixture.py', '--provider', 'codex'], {cwd: root});
  let stderr = '';
  server.stderr.on('data', chunk => { stderr += chunk; });
  const lines = createInterface({input: server.stdout});
  let prepared=false,manifestPosts=0;
  try {
    const origin = await new Promise((resolve, reject) => {
      lines.once('line', resolve); server.once('error', reject);
      server.once('exit', code => reject(Error('fixture exited ' + code + ' ' + stderr)));
    });
    await context.addInitScript(() => {
      const state = window.transferFixture = {freeBytes: 1000, calls: [], chunks: ['file:0'], committed: ['text']};
      window.CodexBridgeNativeOffline = {async request(event) {
        state.calls.push(event);
        const network = {foreground: true, allowPrefetch: false, allowTransfer: true, networkAvailable: true,
          freeBytes: state.freeBytes, clearRevision: '0', tombstones: {}, recordBatch:1};
        if (event.type === 'state' || event.type === 'auth' || event.type === 'transferStats') return network;
        if (event.type === 'read') return {turns: []};
        if (event.type === 'transferTaskLoad') return {task: null};
        if (event.type === 'transferCached') return {present: state.committed.includes(event.version)};
        if (event.type === 'transferChunkHas') return {present: state.chunks.includes(event.version + ':' + event.index)};
        if (event.type === 'transferChunkFetch') { state.chunks.push(event.version + ':' + event.index); return {saved: true}; }
        if (event.type.endsWith('Commit')) state.committed.push(event.version);
        return {saved: true};
      }};
    });
    await page.route('**/api/history-transfer/**', async route => {
      const url = new URL(route.request().url());
      if (url.pathname.endsWith('/catalog')) return route.fulfill({json: url.searchParams.has('cursor') ? {sessions: [
        {id:'claude-selected',sessionId:'claude-native',title:'Claude portable history',client:'claude',host:'local'},
      ], unavailable:[{client:'zcode',entry:'zcode'}]} : {sessions: [
        {id: 'selected', sessionId: 'native', title: 'Portable history', client: 'codex', host: 'local'},
      ],nextCursor:'100',sessionCount:2,recordBatch:1}});

      if (route.request().method() === 'POST') {
        manifestPosts++;expect(route.request().postDataJSON().batchRecords).toBe(true);
        return route.fulfill({json:{id:'manifest',capturedAt:100,sessionCount:2}});
      }
      if(!prepared)return route.fulfill({json:{id:'manifest',status:'preparing',sessionCount:2,summary:{processed:1,currentTitle:'Portable history'}}});
      return route.fulfill({json: {id: 'manifest', status: 'ready', capturedAt: 100,
        summary: {sessions: 1, records: 1, attachments: 1, textBytes: 8, attachmentBytes: 600000},
        items: [
          {id: 'text', kind: 'record', version: 'text', bytes: 8, key: 'text', recordVersion: 'original', cacheSessionId: 'native', turnId: 'turn', questionTime: 1, session: {title: 'Portable history'}},
          {id: 'file', kind: 'attachment', version: 'file', bytes: 600000, chunkBytes: 524288, key: 'image', cacheSessionId: 'native', turnId: 'turn', questionTime: 1, session: {title: 'Portable history'}},
        ]}});
    });
    await page.goto(origin);
    await page.locator('#username').fill('fixture'); await page.locator('#password').fill('session-transfer-test-only');
    await page.locator('#login-button').click();
    await expect(page.locator('#app')).toBeVisible();
    await page.locator('#sidebar [data-open-appearance]').click();
    await page.locator('#history-transfer-open').click();
    const dialog = page.locator('.local-transfer-dialog');
    await expect(dialog.getByText('Portable history', {exact: true})).toBeVisible();
    await dialog.locator('input[type=checkbox]').check();
    await expect(dialog.getByRole('tab',{name:'Claude (1)',exact:true})).toBeVisible();
    await expect(dialog.locator('.local-transfer-count')).toHaveText('已选择 1 个');
    const alignment=await dialog.locator('.local-transfer-choice').evaluate(row=>{
      const input=row.querySelector('input').getBoundingClientRect(),title=row.querySelector('strong').getBoundingClientRect();
      return {inputTop:input.top,titleTop:title.top,titleBottom:title.bottom};
    });
    expect(alignment.inputTop).toBeLessThan(alignment.titleBottom+10);
    await dialog.getByRole('tab',{name:'Claude (1)',exact:true}).click();
    await dialog.locator('input[type=checkbox]').check();
    await expect(dialog.locator('.local-transfer-count')).toHaveText('已选择 2 个');
    await dialog.getByRole('tab',{name:'ZCode (0)',exact:true}).click();
    await expect(dialog.getByRole('status')).toContainText('部分接入读取失败');
    await dialog.getByRole('tab',{name:'Codex (1)',exact:true}).click();
    await expect(dialog.locator('input[type=checkbox]')).toBeChecked();
    await page.screenshot({path:info.outputPath('transfer-selection.png')});

    await dialog.getByRole('button', {name: '查看下载量', exact: true}).click();
    const preparing=page.locator('.local-transfer-progress');
    await expect(preparing).toContainText('正在准备会话');
    await dialog.getByRole('button',{name:'最小化',exact:true}).click();
    await expect(page.locator('.local-transfer-dialog')).toHaveCount(0);
    await expect(preparing).toBeVisible();
    expect(await preparing.evaluate(node=>node.classList.contains('download-panel'))).toBe(true);
    await page.screenshot({path:info.outputPath('transfer-preparing.png')});
    await preparing.getByRole('button',{name:'查看任务',exact:true}).click();
    await expect(page.locator('.local-transfer-dialog')).toContainText('正在准备会话');
    prepared=true;
    const restoredDialog=page.locator('.local-transfer-dialog');
    await expect(restoredDialog.getByText('预计最多下载', {exact: true})).toBeVisible();
    expect(manifestPosts).toBe(1);
    await restoredDialog.getByRole('button',{name:'最小化',exact:true}).click();
    await expect(preparing).toContainText('准备完成，等待开始');
    await preparing.getByRole('button',{name:'查看任务',exact:true}).click();
    await expect(page.locator('.local-transfer-dialog').getByText('空间还差', {exact: false})).toBeVisible();
    await page.locator('.local-transfer-dialog').getByRole('button', {name: '调整选择', exact: true}).click();
    await expect(page.locator('.local-transfer-dialog input[type=checkbox]')).toBeChecked();
    await page.locator('.local-transfer-dialog').getByRole('button', {name: '查看下载量', exact: true}).click();
    await page.locator('.local-transfer-dialog').getByRole('button', {name: '先传一部分', exact: true}).click();
    const panel = page.locator('.local-transfer-progress');
    await expect(panel).toContainText('空间不足，已暂停');
    await page.evaluate(() => { transferFixture.freeBytes = 4e6; });
    await panel.getByRole('button', {name: '继续', exact: true}).click();
    await expect(panel).toContainText('传输完成');
    const downloads = await page.evaluate(() => transferFixture.calls.filter(event => event.type === 'transferChunkFetch'));
    expect(downloads).toHaveLength(1); expect(downloads[0].version).toBe('file');
    expect(downloads[0].offset).toBe(524288); expect(downloads[0].length).toBe(75712);
    await page.screenshot({path: info.outputPath('transfer-completed.png')});
    await page.evaluate(async () => {
      BridgeChatSyncCache.owner = {gatewayId: 'f'.repeat(32), identity: 'e'.repeat(64)};
      await BridgeHistoryTransfer.connect(async () => ({}));
    });
    await expect(panel).toBeHidden();
  } finally { lines.close(); server.kill('SIGTERM'); }
});
