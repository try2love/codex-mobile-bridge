'use strict';
const { test, expect } = require('@playwright/test');

test.beforeEach(async ({ page, context, baseURL }) => {
  await context.route('**/*', (route) =>
    new URL(route.request().url()).origin === baseURL ? route.continue() : route.abort(),
  );
  await page.goto('/?case=success');
  await page.waitForFunction(() => document.getElementById('bootstrap-status')?.hidden);
  await page.evaluate(() => {
    document.getElementById('login').hidden = true;
    document.getElementById('app').hidden = false;
    document.getElementById('app').classList.add('chat-open');
    document.getElementById('welcome').hidden = true;
    document.getElementById('chat').hidden = false;
    window.__toolbarTest = {
      main: composerToolbar,
      other: desktopSessions.toolbar,
      model: document.getElementById('model-button'),
      select: document.getElementById('send-mode'),
    };
    const input = document.getElementById('message');
    input.value = '保留草稿与光标';
    input.setSelectionRange(2, 4);
  });
});
async function edit(page, select = true) {
  await page.locator('#composer .toolbar-more').click();
  await page.locator('.toolbar-menu[open] .toolbar-edit-button').click();
  await expect(page.locator('.toolbar-editor')).toBeVisible();
  if (select) await page.locator('.toolbar-editor [data-drag-tool=model]').click();
}
async function drag(page, id, zone, first = false) {
  const source = page.locator('.toolbar-editor[open] [data-drag-tool=' + id + ']'),
    target = page.locator('.toolbar-editor[open] [data-drop-zone=' + zone + ']');
  await source.scrollIntoViewIfNeeded();
  const from = await source.boundingBox();
  await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
  await page.mouse.down();
  await page.waitForTimeout(380);
  const to = await target.boundingBox();
  await page.mouse.move(to.x + (first ? 8 : to.width - 8), to.y + to.height / 2, { steps: 8 });
  await expect(page.locator('.toolbar-drop-marker')).toHaveCount(1);
  await page.mouse.up();
  await expect(target.locator('[data-drag-tool=' + id + ']')).toHaveCount(1);
}
test('layout presets switch rows, synchronize composers and allow further customization', async ({
  page,
}) => {
  await expect(page.locator('dialog.toolbar-menu')).toHaveCount(0);
  const editorCSS = page.waitForResponse((response) =>
    new URL(response.url()).pathname === '/composer-toolbar-editor.css' && response.ok(),
  );
  await page.evaluate(() =>
    __toolbarTest.main.preferences.update({
      followUpMode: 'steer',
      display: { permissions: 'hidden' },
    }),
  );
  await edit(page);
  await editorCSS;
  const editor = page.locator('.toolbar-editor'),
    preset = (id) => editor.locator('[data-composer-preset=' + id + ']');
  for (const id of ['single', 'default']) {
    if (id === 'default') {
      page.once('dialog', (dialog) => dialog.accept());
      await editor.getByRole('button', { name: '双栏默认', exact: true }).click();
    } else {
      page.once('dialog', (dialog) => dialog.accept());
      await preset(id).click();
      await expect(preset(id)).toHaveAttribute('aria-pressed', 'true');
    }
    expect(
      await page.evaluate((id) => {
        const { main, other } = __toolbarTest,
          expected = composerPreset(id);
        return (
          JSON.stringify(main.layout) === JSON.stringify(expected.layout) &&
          JSON.stringify(other.layout) === JSON.stringify(expected.layout) &&
          main.followUpMode === 'steer'
        );
      }, id),
    ).toBe(true);
    await expect(page.locator('#composer .toolbar-top'))[
      id === 'single' ? 'toBeHidden' : 'toBeVisible'
    ]();
    if (id === 'single') {
      expect(
        await editor
          .locator('[data-drop-zone=bottomRight] [data-drag-tool]')
          .evaluateAll((nodes) => nodes.map((node) => node.dataset.dragTool)),
      ).toEqual(['context', 'attachments']);
      await expect(editor.locator('[data-drag-tool=collapse]')).toHaveClass(/toolbar-drag-hidden/);
    }
    await expect(page.locator('#message')).toHaveValue('保留草稿与光标');
  }
  const before = await page.evaluate(() => JSON.stringify(composerToolbar.preferences.value));
  page.once('dialog', (dialog) => {
    expect(dialog.type()).toBe('confirm');
    expect(dialog.message()).toBe('恢复单栏默认布局和显示方式？');
    return dialog.dismiss();
  });
  await preset('single').click();
  expect(await page.evaluate(() => JSON.stringify(composerToolbar.preferences.value))).toBe(before);
  page.once('dialog', (dialog) => dialog.accept());
  await preset('single').click();
  await editor.locator('[data-drag-tool=permissions]').click();
  await editor.locator('[data-display-choice=both]').click();
  await expect(preset('single')).toHaveAttribute('aria-pressed', 'false');
  await page.reload();
  await page.waitForFunction(() => document.getElementById('bootstrap-status')?.hidden);
  expect(
    await page.evaluate(
      () =>
        composerToolbar.layout.topLeft.length === 0 &&
        composerToolbar.display.permissions === 'both' &&
        composerToolbar.followUpMode === 'steer',
    ),
  ).toBe(true);
});

test('independent send mode defaults to More, moves to a toolbar and shares the merged selection', async ({
  page,
}) => {
  await page.evaluate(() => {
    const t = __toolbarTest.main;
    t.controls.sendMode.disabled = false;
    for (const option of t.controls.sendMode.options) option.disabled = false;
    t.submitControl.getState = () => ({ running: true, hasDraft: true });
    t.sync();
  });
  await page.locator('#composer .toolbar-more').click();
  await page.locator('[data-menu-tool=sendMode]').click();
  await expect(page.locator('.toolbar-choice-option:visible')).toHaveText([
    '完成后发送',
    '补充当前任务',
  ]);
  await page.locator('.toolbar-choice-option').filter({ hasText: '补充当前任务' }).click();
  await expect(page.locator('#send')).toContainText('引导');
  await page.locator('#composer .composer-submit-caret').click();
  await page.locator('.composer-submit-menu[open] [data-send-mode=queue]').click();
  expect(await page.evaluate(() => __toolbarTest.select.value)).toBe('queue');
  await page.evaluate(() => {
    const t = __toolbarTest.main,
      layout = structuredClone(t.layout);
    layout.more = layout.more.filter((id) => id !== 'sendMode');
    layout.topLeft.push('sendMode');
    t.preferences.update({ layout });
  });
  await expect(page.locator('#send-mode')).toBeVisible();
  await expect(page.locator('#send-mode')).toHaveValue('queue');
  expect(
    await page
      .locator('#send-mode option')
      .evaluateAll((options) =>
        options.filter((option) => !option.hidden).map((option) => option.value),
      ),
  ).toEqual(['queue', 'steer']);
  await page.evaluate(() => {
    const t = __toolbarTest.main;
    t.submitControl.getState = () => ({ running: false, hasDraft: true });
    t.sync();
  });
  await expect(page.locator('#send')).toHaveText('发送');
  await expect(page.locator('#send')).toHaveAttribute('aria-disabled', 'false');
  await expect(page.locator('#composer .composer-submit-caret')).toBeHidden();
  await page.evaluate(() => {
    const t = __toolbarTest.main;
    t.submitControl.getState = () => ({ running: true, hasDraft: true });
    t.sync();
  });
  await page.locator('#send-mode').selectOption('steer');
  await expect(page.locator('#send')).toHaveText('引导');
  await page.locator('#composer .toolbar-more').click();
  await expect(page.locator('[data-menu-tool=sendMode]')).toHaveCount(0);
  await page.keyboard.press('Escape');
  expect(
    await page.evaluate(() => __toolbarTest.main.controls.sendMode === __toolbarTest.select),
  ).toBe(true);
});

test('mouse drag persists layout while preserving original controls and drafts', async ({
  page,
}) => {
  await edit(page);
  await drag(page, 'model', 'topRight');
  await drag(page, 'model', 'bottomLeft', true);
  expect(
    await page
      .locator('[data-drop-zone=bottomLeft] [data-drag-tool]')
      .evaluateAll((nodes) => nodes.map((n) => n.dataset.dragTool)),
  ).toEqual(['model', 'attachments', 'quota']);
  const preserved = await page.evaluate(() => ({
    sameModel: __toolbarTest.main.controls.model === __toolbarTest.model,
    sameSelect: __toolbarTest.main.controls.sendMode === __toolbarTest.select,
    text: document.getElementById('message').value,
    selection: [
      document.getElementById('message').selectionStart,
      document.getElementById('message').selectionEnd,
    ],
  }));
  expect(preserved).toEqual({
    sameModel: true,
    sameSelect: true,
    text: '保留草稿与光标',
    selection: [2, 4],
  });
  await page.locator('.toolbar-editor-close').click();
  await expect(page.locator('.toolbar-editor')).not.toBeVisible();
  const result = await page.evaluate(() => ({
    layout: __toolbarTest.main.layout,
    other: __toolbarTest.other.layout,
  }));
  expect(result.layout.bottomLeft).toEqual(['model', 'attachments', 'quota']);
  expect(result.other).toEqual(result.layout);
  await page.reload();
  await page.waitForFunction(() => document.getElementById('bootstrap-status')?.hidden);
  expect(await page.evaluate(() => composerToolbar.layout.bottomLeft)).toEqual([
    'model',
    'attachments',
    'quota',
  ]);
});
test('touch distinguishes long press, cancellation and ordinary scrolling', async ({
  page,
  browserName,
}) => {
  test.skip(browserName !== 'chromium', 'Real touch input is dispatched through Chromium CDP.');
  await edit(page);
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Emulation.setTouchEmulationEnabled', { enabled: true });
  const from = await page.locator('[data-drag-tool=skills]').boundingBox();
  const to = await page.locator('[data-drop-zone=bottomRight]').boundingBox();
  await cdp.send('Input.dispatchTouchEvent', {
    type: 'touchStart',
    touchPoints: [{ x: from.x + from.width / 2, y: from.y + from.height / 2 }],
  });
  await page.waitForTimeout(380);
  await cdp.send('Input.dispatchTouchEvent', {
    type: 'touchMove',
    touchPoints: [{ x: to.x + to.width - 8, y: to.y + to.height / 2 }],
  });
  await page.waitForTimeout(50);
  await cdp.send('Input.dispatchTouchEvent', {
    type: 'touchEnd',
    touchPoints: [],
  });
  await expect(page.locator('[data-drop-zone=bottomRight] [data-drag-tool=skills]')).toHaveCount(1);
  const moved = await page.locator('[data-drag-tool=skills]').boundingBox();
  await cdp.send('Input.dispatchTouchEvent', {
    type: 'touchStart',
    touchPoints: [{ x: moved.x + 10, y: moved.y + 10 }],
  });
  await page.waitForTimeout(380);
  await cdp.send('Input.dispatchTouchEvent', {
    type: 'touchCancel',
    touchPoints: [],
  });
  await expect(page.locator('.toolbar-drag-ghost')).toHaveCount(0);
  await expect(page.locator('[data-drop-zone=bottomRight] [data-drag-tool=skills]')).toHaveCount(1);
  await page.locator('.toolbar-editor-close').click();
  await page.setViewportSize({ width: 390, height: 500 });
  await edit(page);
  const before = await page.evaluate(() => JSON.stringify(composerToolbar.layout)),
    r = await page.locator('[data-drag-tool=permissions]').boundingBox();
  await cdp.send('Input.dispatchTouchEvent', {
    type: 'touchStart',
    touchPoints: [{ x: r.x + r.width / 2, y: r.y + r.height / 2 }],
  });
  for (let step = 1; step <= 5; step++)
    await cdp.send('Input.dispatchTouchEvent', {
      type: 'touchMove',
      touchPoints: [{ x: r.x + r.width / 2, y: r.y + r.height / 2 - step * 12 }],
    });
  await cdp.send('Input.dispatchTouchEvent', {
    type: 'touchEnd',
    touchPoints: [],
  });
  await expect
    .poll(() => page.locator('.toolbar-editor').evaluate((dialog) => dialog.scrollTop))
    .toBeGreaterThan(0);
  expect(await page.evaluate(() => JSON.stringify(composerToolbar.layout))).toBe(before);
  await expect(page.locator('.toolbar-drag-ghost')).toHaveCount(0);
  await cdp.detach();
});
test('same-origin windows synchronize settings into an open editor', async ({ page, context }) => {
  await edit(page);
  const other = await context.newPage();
  await other.goto('/?case=success');
  await other.waitForFunction(() => document.getElementById('bootstrap-status')?.hidden);
  await other.evaluate(() => {
    const t = composerToolbar,
      layout = structuredClone(t.layout);
    layout.bottomLeft = layout.bottomLeft.filter((id) => id !== 'model');
    layout.topRight.push('model');
    t.preferences.update({ layout, display: { ...t.display, model: 'both' } });
  });
  await expect(page.locator('[data-drop-zone=topRight] [data-drag-tool=model]')).toHaveCount(1);
  await page.locator('[data-drag-tool=model]').click();
  await page.locator('[data-move-zone=more]').click();
  await expect
    .poll(() => other.evaluate(() => composerToolbar.layout.more.includes('model')))
    .toBe(true);
  await other.close();
});

test('chat follows the latest message when the keyboard or composer resizes the viewport', async ({
  page,
}) => {
  await page.evaluate(async () => {
    await BridgeFeatures.load('chat');
    window.__resizeTimeline = chatTimeline = new ChatTimeline({
      url: (action) => action,
      request: async () => ({}),
      renderMeta: () => {},
      renderText: (node, text) => {
        node.textContent = text;
      },
      status: () => {},
    });
    __resizeTimeline.apply(
      {
        epoch: 'keyboard-resize',
        sequence: 1,
        hasMore: false,
        before: null,
        meta: {},
        rows: Array.from({ length: 30 }, (_, i) => ({
          key: 'keyboard-' + i,
          order: i,
          role: 'assistant',
          text: '消息 ' + i + '：用于验证键盘弹出和收起时的聊天位置。'.repeat(4),
        })),
      },
      true,
    );
  });
  const distanceFromBottom = () =>
    page
      .locator('#timeline')
      .evaluate((node) => node.scrollHeight - node.clientHeight - node.scrollTop);
  await expect.poll(distanceFromBottom).toBeLessThan(2);
  const original = page.viewportSize();
  await page.setViewportSize({
    width: original.width,
    height: original.height - 280,
  });
  await expect.poll(distanceFromBottom).toBeLessThan(2);
  await page.setViewportSize(original);
  await expect.poll(distanceFromBottom).toBeLessThan(2);
  const input = page.locator('#message');
  const initialHeight = await input.evaluate((node) => node.clientHeight);
  await page.evaluate(() => {
    const input = document.getElementById('message');
    input.value = Array(5).fill('多行草稿').join('\n');
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await expect.poll(() => input.evaluate((node) => node.clientHeight)).toBeGreaterThan(initialHeight);
  await expect.poll(distanceFromBottom).toBeLessThan(2);
  await page.waitForFunction(() => !__resizeTimeline.suppress);
  await page.locator('#timeline').evaluate((node) => {
    node.scrollTop -= 550;
  });
  await expect.poll(() => page.evaluate(() => __resizeTimeline.following)).toBe(false);
  const anchor = await page.evaluate(() => __resizeTimeline.capture());
  await input.fill('缩回单行');
  await expect.poll(() => input.evaluate((node) => node.clientHeight)).toBe(initialHeight);
  await page.setViewportSize({
    width: original.width,
    height: original.height - 280,
  });
  await expect
    .poll(() =>
      page.evaluate((key) => {
        const timeline = __resizeTimeline;
        return (
          timeline.nodes.get(key).node.getBoundingClientRect().top -
          timeline.viewport.getBoundingClientRect().top
        );
      }, anchor.key),
    )
    .toBeCloseTo(anchor.offset, 0);
  await page.evaluate(() => __resizeTimeline.dispose());
});

test('compact and dark layouts keep the editor and follow-up controls reachable', async ({
  page,
}) => {
  await page.evaluate(() => {
    document.documentElement.dataset.theme = 'dark';
    const t = composerToolbar;
    t.submitControl.getState = () => ({ running: true, hasDraft: true });
    t.submitControl.send.disabled = false;
    t.controls.sendMode.disabled = false;
    for (const option of t.controls.sendMode.options) option.disabled = false;
    t.preferences.update({ followUpMode: 'steer' });
    t.sync();
  });
  for (const [width, height] of [
    [320, 600],
    [844, 390],
  ]) {
    await page.setViewportSize({ width, height });
    await expect(page.locator('#send')).toHaveText('引导');
    await expect(page.locator('#send > svg')).toBeHidden();
    expect(
      await page.locator('#composer').evaluate((node) => node.scrollWidth <= node.clientWidth + 1),
    ).toBe(true);
    const caret = page.locator('#composer .composer-submit-caret');
    await caret.click();
    await page.mouse.move(0, 0);
    await expect(caret).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
    const line = await caret.evaluate((node) => {
      const style = getComputedStyle(node, '::before');
      return { inset: parseFloat(style.top), opacity: parseFloat(style.opacity) };
    });
    expect(line.inset).toBeGreaterThan(0);
    expect(line.opacity).toBeGreaterThan(0);
    expect(line.opacity).toBeLessThanOrEqual(0.25);
    const menu = page.locator('dialog.composer-submit-menu[open]');
    await expect(menu).toBeInViewport();
    // Native Back closes the active HTML dialog, just like the host does.
    await menu.evaluate((dialog) => dialog.close());
    await expect(caret).toHaveAttribute('aria-expanded', 'false');
    await edit(page);
    await expect(page.locator('.toolbar-editor-close')).toBeInViewport();
    await page.locator('.toolbar-editor-close').click();
  }
});

test('eight bottom controls scroll between fixed endpoints in both languages and themes', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 320, height: 760 });
  await page.evaluate(() => {
    const t = composerToolbar;
    t.preferences.update({
      layout: {
        topLeft: [], topRight: [],
        bottomLeft: ['more', 'model', 'skills', 'permissions'],
        bottomRight: ['sendMode', 'context', 'attachments'],
        more: ['collapse', 'plan', 'goal', 'quota'],
      },
      display: Object.fromEntries(['more', 'model', 'skills', 'permissions', 'sendMode', 'context', 'attachments'].map(id => [id, 'both'])),
    });
    for (const id of ['model', 'skills', 'permissions', 'sendMode', 'context', 'attachments']) {
      t.controls[id].hidden = false;
      t.controls[id].disabled = false;
    }
    t.submitControl.getState = () => ({ running: true, hasDraft: true });
    t.submitControl.send.disabled = false;
    for (const option of t.controls.sendMode.options) option.disabled = false;
    t.sync();
  });
  const strip = page.locator('#composer .toolbar-bottom-scroll');
  const pin = page.locator('#composer .toolbar-pin');
  const submit = page.locator('#composer .composer-submit');
  await expect(pin.locator('[data-tool=more]')).toBeVisible();
  for (const language of ['zh', 'en']) for (const theme of ['light', 'dark']) {
    await page.evaluate(({ language, theme }) => {
      BridgeI18n.setLanguage(language);
      document.documentElement.dataset.theme = theme;
      composerToolbar.sync();
      composerToolbar.bottomScroll.scrollLeft = 0;
    }, { language, theme });
    await expect.poll(() => strip.evaluate(n => n.scrollWidth > n.clientWidth)).toBe(true);
    const before = { left: await pin.boundingBox(), right: await submit.boundingBox() };
    await strip.evaluate(n => n.scrollLeft = n.scrollWidth);
    await expect.poll(() => strip.evaluate(n => n.scrollLeft > 0)).toBe(true);
    const after = { left: await pin.boundingBox(), right: await submit.boundingBox() };
    expect(after.left.x).toBeCloseTo(before.left.x, 0);
    expect(after.right.x).toBeCloseTo(before.right.x, 0);
    expect(after.right.x + after.right.width).toBeLessThanOrEqual(320);
    expect(await page.locator('#composer').evaluate(n => n.scrollWidth <= n.clientWidth + 1)).toBe(true);
    await expect(page.locator('#composer [data-tool=attachments]')).toBeInViewport();
    await page.screenshot({ path: testInfo.outputPath(`eight-tools-${language}-${theme}.png`) });
  }
  await page.evaluate(() => {
    composerToolbar.setCollapsed(true);
    composerToolbar.setCollapsed(false);
  });
  expect(await page.locator('#composer .toolbar-bottom-scroll [data-tool]').evaluateAll(nodes => nodes.map(n => n.dataset.tool)))
    .toEqual(['model', 'skills', 'permissions', 'sendMode', 'context', 'attachments']);
});

test('follow-up icon, integrated select and slash placeholder stay synchronized', async ({ page }) => {
  await page.evaluate(() => {
    const t = composerToolbar, layout = structuredClone(t.layout);
    layout.more = layout.more.filter(id => id !== 'sendMode');
    layout.topLeft.push('sendMode');
    t.preferences.update({ layout, display: { sendMode: 'both' }, followUpMode: 'queue' });
    t.controls.sendMode.disabled = false;
    for (const option of t.controls.sendMode.options) option.disabled = false;
    t.submitControl.send.disabled = false;
    t.submitControl.getState = () => ({ running: true, hasDraft: true });
    t.sync();
  });
  const wrapper = page.locator('#composer [data-tool=sendMode]');
  const icon = wrapper.locator('.toolbar-select-icon');
  await expect(icon).toHaveAttribute('data-icon', 'queue');
  await expect(page.locator('#message')).toHaveAttribute('placeholder', '完成后发送 · 输入 / 查看指令');
  const bounds = { outer: await wrapper.boundingBox(), icon: await icon.boundingBox(), select: await page.locator('#send-mode').boundingBox() };
  expect(bounds.icon.x).toBeGreaterThan(bounds.outer.x);
  expect(bounds.icon.x).toBeGreaterThanOrEqual(bounds.select.x);
  await page.locator('#send-mode').selectOption('steer');
  await expect(icon).toHaveAttribute('data-icon', 'steer');
  await expect(page.locator('#message')).toHaveAttribute('placeholder', '继续这条聊天 · 输入 / 查看指令');
  await page.evaluate(() => BridgeI18n.setLanguage('en'));
  await expect(page.locator('#message')).toHaveAttribute('placeholder', 'Continue chat · / commands');
  await page.locator('#send-mode').selectOption('queue');
  await expect(page.locator('#message')).toHaveAttribute('placeholder', 'Send when done · / commands');
  await page.evaluate(() => {
    composerToolbar.submitControl.getState = () => ({ running: false, hasDraft: true });
    composerToolbar.sync();
  });
  await expect(page.locator('#message')).toHaveAttribute('placeholder', 'Continue chat · / commands');
  await expect(page.locator('#message')).toHaveValue('保留草稿与光标');
  await page.locator('#composer .toolbar-more').click();
  await expect(page.locator('#composer .toolbar-more')).toHaveCSS('outline-style', 'none');
  await page.locator('.toolbar-menu[open] .toolbar-edit-button').click();
  const preview = page.locator('.toolbar-editor [data-drag-tool=sendMode] svg');
  await expect(preview).toHaveAttribute('data-icon', 'queue');
  await page.evaluate(() => {
    const t = composerToolbar;
    t.controls.sendMode.disabled = false;
    for (const option of t.controls.sendMode.options) option.disabled = false;
    t.preferences.update({ followUpMode: 'steer' });
    t.sync();
  });
  await expect(preview).toHaveAttribute('data-icon', 'steer');
});

test('bottom right tools stay beside send until actual overflow and return after widening', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.evaluate(() => {
    const t = composerToolbar;
    t.preferences.update({ layout: {
      topLeft: [], topRight: [], bottomLeft: ['more', 'model'], bottomRight: ['sendMode'],
      more: ['skills', 'permissions', 'context', 'collapse', 'attachments', 'plan', 'goal'],
    }, display: { sendMode: 'both' } });
    t.controls.sendMode.disabled = false;
    for (const option of t.controls.sendMode.options) option.disabled = false;
    t.submitControl.getState = () => ({ running: true, hasDraft: true });
    t.submitControl.send.disabled = false;t.sync();
  });
  const strip = page.locator('#composer .toolbar-bottom-scroll');
  const send = page.locator('#composer .composer-submit');
  const mode = page.locator('#composer [data-tool=sendMode]');
  const model = page.locator('#composer [data-tool=model]');
  async function fitted() {
    await expect.poll(() => strip.evaluate(n => n.scrollWidth - n.clientWidth)).toBeLessThanOrEqual(1);
    const [right, submit, left] = await Promise.all([mode.boundingBox(), send.boundingBox(), model.boundingBox()]);
    expect(submit.x - right.x - right.width).toBeLessThanOrEqual(8);
    expect(right.x - left.x - left.width).toBeGreaterThan(50);
    await strip.evaluate(n => n.scrollLeft = 999);
    expect(await strip.evaluate(n => n.scrollLeft)).toBe(0);
  }
  await fitted();
  await page.setViewportSize({ width: 320, height: 760 });
  await expect.poll(() => strip.evaluate(n => n.scrollWidth > n.clientWidth + 1)).toBe(true);
  const pin = await page.locator('#composer .toolbar-pin').boundingBox();
  const submit = await send.boundingBox();
  await strip.evaluate(n => n.scrollLeft = n.scrollWidth);
  expect((await page.locator('#composer .toolbar-pin').boundingBox()).x).toBeCloseTo(pin.x, 0);
  expect((await send.boundingBox()).x).toBeCloseTo(submit.x, 0);
  await page.setViewportSize({ width: 1280, height: 900 });await fitted();
  await page.evaluate(() => {
    const t = composerToolbar, layout = structuredClone(t.layout);
    layout.topLeft = ['more'];layout.bottomLeft = [];layout.more.push('model');
    t.preferences.update({ layout });t.sync();
  });
  await expect(page.locator('#composer .toolbar-pin')).toBeHidden();
  const [rightOnly, submitOnly] = await Promise.all([mode.boundingBox(), send.boundingBox()]);
  expect(submitOnly.x - rightOnly.x - rightOnly.width).toBeLessThanOrEqual(8);
});

test('send mode icon is centered without a focus frame and placeholder fits one line', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 320, height: 760 });
  await page.evaluate(() => {
    const t = composerToolbar;
    t.input.value = '';
    t.preferences.update({ layout: {
      topLeft: [], topRight: [], bottomLeft: ['more', 'model'], bottomRight: ['sendMode'],
      more: ['skills', 'permissions', 'context', 'collapse', 'attachments', 'plan', 'goal'],
    }, display: { sendMode: 'icon' } });
    t.controls.sendMode.disabled = false;for (const option of t.controls.sendMode.options) option.disabled = false;
    t.submitControl.getState = () => ({ running: true, hasDraft: true });t.submitControl.send.disabled = false;t.sync();
  });
  const wrapper = page.locator('#composer [data-tool=sendMode]');
  await page.locator('#send-mode').focus();
  await expect(wrapper).toHaveCSS('outline-style', 'none');
  await expect(wrapper).toHaveCSS('border-top-width', '0px');
  const box = await wrapper.boundingBox(), icon = await wrapper.locator('.toolbar-select-icon').boundingBox();
  expect(box.width).toBe(44);expect(box.height).toBe(44);
  expect(icon.x + icon.width / 2).toBeCloseTo(box.x + box.width / 2, 0);
  expect(icon.y + icon.height / 2).toBeCloseTo(box.y + box.height / 2, 0);
  for (const language of ['zh', 'en']) for (const followUpMode of ['queue', 'steer']) {
    await page.evaluate(({ language, followUpMode }) => {
      BridgeI18n.setLanguage(language);composerToolbar.preferences.update({ followUpMode });composerToolbar.sync();
    }, { language, followUpMode });
    const result = await page.locator('#message').evaluate(n => {
      const style = getComputedStyle(n), placeholder = getComputedStyle(n, '::placeholder'), probe = document.createElement('span');
      // WebKit's Canvas text metrics can differ from its DOM shaping. Measure
      // the rendered placeholder font in the same layout engine as the input.
      probe.textContent = n.placeholder;probe.style.cssText = 'position:fixed;visibility:hidden;white-space:pre';
      probe.style.font = placeholder.font || style.font;
      probe.style.letterSpacing = placeholder.letterSpacing;probe.style.wordSpacing = placeholder.wordSpacing;
      probe.style.whiteSpace = placeholder.whiteSpace;probe.style.textTransform = placeholder.textTransform;probe.style.transform = placeholder.transform;
      document.body.append(probe);const width = probe.getBoundingClientRect().width;probe.remove();
      const ctx = document.createElement('canvas').getContext('2d');ctx.font = placeholder.font || style.font;
      return { text: n.placeholder, width, canvasWidth: ctx.measureText(n.placeholder).width,
        font: placeholder.font, available: n.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight) };
    });
    await page.screenshot({ path: testInfo.outputPath('placeholder-' + language + '-' + followUpMode + '.png') });
    expect(result.text).not.toContain('\n');expect(result.text).toContain('/');expect(result.width, JSON.stringify(result)).toBeLessThanOrEqual(result.available);
  }
});


test('send mode draws one arrow for text and both, and none for icon only', async ({ page }) => {
  await page.evaluate(() => {
    const t = composerToolbar;
    t.preferences.update({ layout: {
      topLeft: [], topRight: [], bottomLeft: ['more', 'model'], bottomRight: ['sendMode'],
      more: ['skills', 'permissions', 'context', 'collapse', 'attachments', 'plan', 'goal'],
    } });
    t.controls.sendMode.disabled = false;for (const option of t.controls.sendMode.options) option.disabled = false;
    t.submitControl.getState = () => ({ running: true, hasDraft: true });t.submitControl.send.disabled = false;t.sync();
  });
  for (const theme of ['light', 'dark']) for (const language of ['zh', 'en']) for (const display of ['text', 'both', 'icon']) {
    await page.evaluate(({ theme, language, display }) => {
      document.documentElement.dataset.theme = theme;BridgeI18n.setLanguage(language);
      composerToolbar.preferences.update({ display: { sendMode: display } });composerToolbar.sync();
    }, { theme, language, display });
    const styles = await page.locator('#composer [data-tool=sendMode]').evaluate(n => ({
      background: getComputedStyle(n.querySelector('select')).backgroundImage,
      arrow: getComputedStyle(n, '::after').display,
    }));
    expect(styles.background).toBe('none');
    expect(styles.arrow === 'none').toBe(display === 'icon');
  }
});

test.describe('touch composer controls', () => {
  test.use({ hasTouch: true, isMobile: true });

  for (const nativeHost of [false, true]) {
    test(`touch focus and independent submit targets in ${nativeHost ? 'the mobile host layout' : 'a mobile browser'}`, async ({ page, browserName }) => {
      test.skip(browserName !== 'webkit', 'Exercises the WebKit text-field focus transfer.');
      await page.setViewportSize({ width: 320, height: 680 });
      await page.evaluate((nativeHost) => {
        document.documentElement.classList.toggle('bridge-mobile', nativeHost);
        const toolbar = composerToolbar;
        toolbar.submitControl.getState = () => ({ running: true, hasDraft: true });
        toolbar.submitControl.send.disabled = false;
        toolbar.controls.sendMode.disabled = false;
        for (const option of toolbar.controls.sendMode.options) option.disabled = false;
        toolbar.preferences.update({ followUpMode: 'steer' });
        contextUsageControl.update({ id: 'fixture', connected: true });
        toolbar.sync();
      }, nativeHost);
      const input = page.locator('#message');
      const caret = page.locator('#composer .composer-submit-caret');
      const send = page.locator('#send');
      const menu = page.locator('.composer-submit-menu[open]');
      await input.tap();
      await caret.tap();
      await expect(menu).toBeVisible();
      await expect(caret).toBeFocused();
      await expect(caret).toHaveCSS('outline-style', 'none');
      await page.keyboard.press('Escape');
      await expect(menu).toHaveCount(0);
      await expect(caret).toBeFocused();
      await expect(caret).toHaveCSS('outline-style', 'solid');
      await page.keyboard.press('Home');
      await expect(menu.locator('[data-send-mode=queue]')).toBeFocused();
      await page.keyboard.press('Escape');
      await expect(input).toHaveValue('保留草稿与光标');

      await input.tap();
      await page.locator('#composer .context-usage').tap();
      const context = page.locator('dialog[open]').filter({ hasText: '上下文使用量' });
      const close = context.getByRole('button', { name: '关闭' });
      await expect(close).toBeFocused();
      await expect(close).toHaveCSS('outline-style', 'none');
      await close.tap();
      await expect(context).toHaveCount(0);

      const assertTarget = async (button) => {
        const bounds = await button.boundingBox();
        const inputBounds = await input.boundingBox();
        expect(bounds.width).toBeGreaterThanOrEqual(44);
        expect(bounds.height).toBeGreaterThanOrEqual(44);
        expect(bounds.y - (inputBounds.y + inputBounds.height)).toBeGreaterThanOrEqual(4);
        expect(await button.evaluate((node) => {
          const bounds = node.getBoundingClientRect();
          return node.contains(document.elementFromPoint(bounds.x + bounds.width / 2, bounds.y + 1));
        })).toBe(true);
      };
      await assertTarget(send);
      await assertTarget(caret);
      await page.evaluate(() => {
        composerToolbar.submitControl.getState = () => ({ running: true, hasDraft: false });
        composerToolbar.submitControl.stop.hidden = false;
        composerToolbar.submitControl.stop.disabled = false;
        composerToolbar.sync();
      });
      await assertTarget(page.locator('#composer .composer-submit-stop'));
      expect(await page.locator('#composer .composer-submit').evaluate((node) => {
        const paint = getComputedStyle(node, '::before');
        return node.offsetHeight - parseFloat(paint.top) - parseFloat(paint.bottom);
      })).toBe(32);
    });
  }
});

for (const provider of ['codex', 'claude']) {
  test(`the ${provider} input survives browser disconnection and keeps draft B apart from outbox A`, async ({ page }) => {
    await page.route('**/api/__offline-probe', route => route.fulfill({ status: 503 }));
    if (provider === 'claude') await page.route('**/api/auth', route => route.fulfill({ status:503, json:{error:'Gateway unreachable'} }));
    await page.evaluate(async (provider) => {
      const route = { provider, host: 'local', session: 'offline-test' };
      delete window.CodexBridgeNativeOffline;
      const auth = { authenticated: true, sessionSync: { version: 1, gatewayId: 'a'.repeat(32), identity: 'b'.repeat(64) } };
      const store = window.BridgeOfflineComposer;
      await BridgeChatSyncCache.bind(auth);
      store.bind(auth, async () => { throw Error('Unexpected delivery while offline'); });
      await store.observe(route, { account: 'fixture-account', canSend: true });
      const enqueue = store.enqueue;
      store.enqueue = async (...args) => {
        const writing = enqueue(...args);
        await new Promise(resolve => { window.__finishOutboxWrite = resolve; });
        return writing;
      };
      let panel;
      if (provider === 'codex') {
        currentId = route.session;
        clientNavigation.provider = 'codex';
        window.__offlineToolbar = composerToolbar;
        attachments.open('local|' + route.session);
        panel = attachments;
        selectedSkills.add('prepared-skill');
      } else {
        await desktopSessions.choose(provider, { previewOnly: true, loadList: false });
        clientNavigation.provider = provider;
        document.getElementById('chat').hidden = true;
        desktopSessions.prepareChat(route.session);
        window.__offlineToolbar = desktopSessions.toolbar;
        panel = desktopSessions.attachments;
        desktopSessions.selectedSkills().set('prepared-skill', { id: 'prepared-skill', name: 'Prepared skill' });
      }
      panel.rows.push({ id: 'uploaded-id', name: 'notes.txt', size: 64, status: 'ready' });
      panel.rows.push({ id: 'project-reference', kind: 'file', name: 'plan.txt', size: 42, status: 'ready',
        reference: { path: 'plan.txt', absolutePath: '/project/plan.txt' } });
      if (provider === 'codex') window.dispatchEvent(new Event('offline'));
      else await api('/api/__offline-probe').catch(() => {});
    }, provider);
    const form = page.locator('form[data-offline-composer]:visible');
    const input = form.locator('textarea');
    await expect(input).toBeEditable();
    await page.evaluate(() => __offlineToolbar.setCollapsed(true));
    await expect(input).toBeHidden();
    await page.evaluate(() => __offlineToolbar.setCollapsed(false));
    await input.fill('消息 A');
    await form.getByRole('button', { name: '存入待发' }).click();
    await expect.poll(() => page.evaluate(() => !!window.__finishOutboxWrite)).toBe(true);
    await input.fill('新草稿 B');
    await page.evaluate(() => __finishOutboxWrite());

    const pending = page.locator('.offline-outbox:visible');
    await expect(pending).toContainText('消息 A');
    await expect(input).toHaveValue('新草稿 B');
    const record = await page.evaluate(provider => BridgeOfflineComposer.get({ provider, host: 'local', session: 'offline-test' }), provider);
    expect(record.outbox[0].body).toMatchObject({
      text: '消息 A', mode: 'send', attachments: ['uploaded-id'],
      references: [{ path: 'plan.txt', absolutePath: '/project/plan.txt' }],
      [provider === 'codex' ? 'skills' : 'plugins']: ['prepared-skill'],
    });
    expect(record.draft).toMatchObject({ text: '新草稿 B', attachmentDrafts: [], skills: [] });
    await pending.getByRole('button', { name: '编辑', exact: true }).click();
    const editor = page.locator('.offline-outbox-editor[open]');
    await editor.locator('textarea').fill('编辑后的消息 A');
    await editor.getByRole('button', { name: '保存', exact: true }).click();
    await expect(pending).toContainText('编辑后的消息 A');
    await page.evaluate(async (provider) => {
      const auth = { authenticated: true, sessionSync: { version: 1, gatewayId: 'a'.repeat(32), identity: 'b'.repeat(64) } };
      let ready = true;
      BridgeOfflineComposer.bind(auth, async (path, body) => {
        if (path === '/api/auth') return auth;
        if (!body) return { deliveryVersion:1, account:'fixture-account', ready, status:'notFound' };
        ready = false;
        throw Object.assign(Error('Skill unavailable'), { code:'offline_delivery_rejected' });
      });
      OfflineChatReader.disableHot();
      await BridgeOfflineComposer.flush();
    }, provider);
    await expect(pending).toContainText('待发消息发送失败');
    await pending.getByRole('button', { name: '编辑', exact: true }).click();
    await editor.locator('textarea').fill('重新编辑的消息 A');
    await editor.getByRole('button', { name: '保存', exact: true }).click();
    await expect(pending).toContainText('重新编辑的消息 A');
    await expect(pending).not.toContainText('待发消息发送失败');
    const corrected = await page.evaluate(provider => BridgeOfflineComposer.get({ provider, host:'local', session:'offline-test' }), provider);
    expect(corrected.outbox[0].id).not.toBe(record.outbox[0].id);
    expect(corrected.draft.text).toBe('新草稿 B');
    await pending.getByRole('button', { name: '撤回', exact: true }).click();
    await expect(pending).toHaveCount(0);
    await expect(input).toHaveValue('新草稿 B');
    await expect(form.locator('[data-composer-tool=permissions]')).toBeDisabled();
  });
}

test('the cold archive restores the original toolbar and its prepared draft from the host', async ({ page }) => {
  await page.addInitScript(() => {
    const archiveId = JSON.stringify(['codex', 'local', 'offline-cold']);
    const title = 'Offline archive composer';
    const rows = new Map([
      ['', { revision: 1, value: { display: { submit: 'icon' }, followUpMode: 'steer', layout: {
        topLeft: ['more'], topRight: ['collapse'], bottomLeft: ['model'], bottomRight: ['context'],
        more: ['skills', 'quota', 'attachments', 'permissions', 'plan', 'goal', 'sendMode'],
      } } }],
      [archiveId, { revision: 1, value: { account: 'fixture-account', gateway: 'fixture-gateway', canSend: true,
        draft: { text: '冷启动草稿', selectionStart: 1, selectionEnd: 3, workMode: 'plan', quotes: [], skills: ['cached-skill'],
          attachmentDrafts: [
            { id: 'cached-upload', name: 'uploaded.txt', size: 42, status: 'ready' },
            { id: 'local-file', name: 'pending.txt', size: 24, status: 'reselect' },
          ],
        }, outbox: [], context: {},
      } }],
    ]);
    window.OfflineChatHost = {
      postMessage(message) {
        const { id, action, sessionId, revision, value } = JSON.parse(message);
        let result;
        if (action === 'list') result = { sessions: [{ id: archiveId, title }], composerWritable: true };
        else if (action === 'session' && sessionId === archiveId) result = { id: archiveId, title, turns: [], before: null };
        else if (action === 'composerRead') result = rows.get(sessionId) || { revision: 0, value: null };
        else if (action === 'composerWrite') {
          result = { revision: revision + 1, value, applied: true };
          rows.set(sessionId, result);
        }
        queueMicrotask(() => window.OfflineChatReader.receive(id, result || { error: 'Unexpected host operation' }));
      },
    };
  });
  await page.route(/\/\?case=session-menu$/, async (route) => {
    const response = await route.fetch();
    await route.fulfill({ response, body: (await response.text()).replace('<html ', '<html data-bridge-offline ') });
  });
  await page.goto('/?case=session-menu');
  await page.locator('#sessions .session').filter({ hasText: 'Offline archive composer' }).click();
  const form = page.locator('#composer');
  await expect(form.locator('#message')).toHaveValue('冷启动草稿');
  await expect(form.locator('.attachment-list')).toContainText('uploaded.txt');
  await expect(form.locator('.attachment-list')).toContainText('pending.txt');
  await expect(form.locator('.attachment-list')).toContainText('文件尚未上传，请重新选择后上传。');
  await expect(form.locator('.composer-submit')).toHaveAttribute('data-display', 'icon');
  expect(await page.evaluate(() => workModes.selection)).toBe('plan');
  await form.getByRole('button', { name: '存入待发' }).click();
  await expect(form.locator('#send-error')).toContainText('草稿已保留');
  await expect(form.locator('#message')).toHaveValue('冷启动草稿');
  await expect(page.locator('.offline-outbox:visible')).toHaveCount(0);
});
