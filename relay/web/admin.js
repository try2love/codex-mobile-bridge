(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const base = '/relay/admin/';
  let csrf = '';
  let relayOrigin = '';
  let revokeId = '';

  function status(message = '', error = false) {
    $('status').textContent = message;
    $('status').classList.toggle('error', error);
  }
  function clearInvitation() {
    $('invitation-text').value = '';
    $('invitation-result').hidden = true;
  }
  function signedOut() {
    csrf = '';
    relayOrigin = '';
    $('dashboard').hidden = true;
    $('logout').hidden = true;
    $('login-panel').hidden = false;
    $('admin-key').value = '';
    $('devices').replaceChildren();
    $('invitations').replaceChildren();
    $('revoke-dialog').close();
    revokeId = '';
    clearInvitation();
  }
  async function api(path, body) {
    let response;
    try {
      response = await fetch(base + path, {
        method: body === undefined ? 'GET' : 'POST',
        headers: body === undefined ? {} : { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
        body: body === undefined ? undefined : JSON.stringify(body),
        credentials: 'same-origin', cache: 'no-store', redirect: 'error',
      });
    } catch {
      throw new Error(body === undefined ? '无法连接中继，请检查网络后刷新。' : '连接中断，操作结果待确认，请先刷新状态，勿重复提交。');
    }
    if (response.status === 401) signedOut();
    if (!response.ok) {
      const value = await response.json().catch(() => ({}));
      throw new Error(response.status === 429 ? '请求过于频繁，请稍后再试。' : value.error || '请求失败，请刷新后重试。');
    }
    return response.json();
  }
  async function run(button, action) {
    button.disabled = true;
    status();
    try { await action(); } catch (error) { status(error.message, true); }
    finally { button.disabled = false; }
  }
  const date = (seconds) => new Date(seconds * 1000).toLocaleString();
  function cell(row, text) {
    const td = document.createElement('td');
    td.textContent = text;
    td.title = text;
    row.append(td);
    return td;
  }
  function badge(row, text, online = false) {
    const span = document.createElement('span');
    span.className = online ? 'badge online' : 'badge';
    span.textContent = text;
    cell(row, '').append(span);
  }
  async function refresh() {
    const data = await api('overview');
    relayOrigin = data.origin;
    $('origin').textContent = data.origin;
    $('online-count').textContent = data.devices.filter((d) => d.online).length;
    $('device-count').textContent = data.devices.filter((d) => !d.revoked).length;
    $('invite-count').textContent = data.invitations.filter((i) => !i.used && i.expires > data.now).length;
    $('devices').replaceChildren();
    for (const device of data.devices) {
      const row = document.createElement('tr');
      cell(row, device.name); cell(row, device.owner);
      badge(row, device.revoked ? '已撤销' : device.online ? '在线' : '离线', device.online);
      cell(row, date(device.created));
      const action = cell(row, device.revoked ? '—' : '');
      if (!device.revoked) {
        const button = document.createElement('button');
        button.className = 'danger'; button.textContent = '撤销';
        button.setAttribute('aria-label', '撤销 ' + device.name);
        button.addEventListener('click', () => {
          revokeId = device.id;
          $('revoke-description').textContent = `撤销“${device.name}”（${device.owner}）及其全部手机的访问权限？`;
          $('revoke-dialog').showModal();
        });
        action.append(button);
      }
      $('devices').append(row);
    }
    $('devices-empty').hidden = data.devices.length > 0;
    $('devices-table').hidden = !data.devices.length;
    $('invitations').replaceChildren();
    for (const invitation of data.invitations) {
      const row = document.createElement('tr');
      cell(row, invitation.owner);
      badge(row, invitation.used ? '已使用' : invitation.expires <= data.now ? '已过期' : '可使用');
      cell(row, date(invitation.expires));
      $('invitations').append(row);
    }
    $('invitations-empty').hidden = data.invitations.length > 0;
    $('invitations-table').hidden = !data.invitations.length;
    $('updated').textContent = '状态更新于 ' + date(data.now);
    $('login-panel').hidden = true;
    $('dashboard').hidden = false;
    $('logout').hidden = false;
  }
  $('login-form').addEventListener('submit', (event) => {
    event.preventDefault();
    run(event.submitter, async () => {
      const key = $('admin-key').value.trim();
      $('admin-key').value = '';
      const result = await api('login', { key });
      csrf = result.csrf;
      await refresh();
    });
  });
  $('invite-form').addEventListener('submit', (event) => {
    event.preventDefault();
    run(event.submitter, async () => {
      const owner = $('owner').value.trim();
      if (!owner) throw new Error('请填写用户备注。');
      if (!$('invitation-result').hidden) throw new Error('请先保存当前邀请码，再收起并清除后生成下一个。');
      const hours = Number($('hours').value);
      const result = await api('invite', { owner, hours });
      $('invitation-text').value = `中继地址：${relayOrigin}\n邀请码：${result.invitation}\n有效期：${hours} 小时（仅限注册一台电脑）\n在电脑客户端的“共享中继”中填写以上地址和邀请码。`;
      $('invitation-result').hidden = false;
      await refresh();
    });
  });
  $('copy').addEventListener('click', () => run($('copy'), async () => {
    try {
      await navigator.clipboard.writeText($('invitation-text').value);
      status('接入信息已复制，请私下发送给对应用户。');
    } catch {
      $('invitation-text').focus(); $('invitation-text').select();
      status('请复制已选中的接入信息。');
    }
  }));
  $('dismiss-invitation').addEventListener('click', clearInvitation);
  $('refresh').addEventListener('click', () => run($('refresh'), refresh));
  $('cancel-revoke').addEventListener('click', () => $('revoke-dialog').close());
  $('confirm-revoke').addEventListener('click', () => run($('confirm-revoke'), async () => {
    await api('revoke', { deviceId: revokeId });
    $('revoke-dialog').close();
    await refresh();
    status('设备及其手机授权已撤销。');
  }));
  $('logout').addEventListener('click', () => run($('logout'), async () => {
    await api('logout', {});
    signedOut();
    status('已退出管理后台。');
  }));
  // Clear visible secrets when navigating away, including pages retained in bfcache.
  window.addEventListener('pagehide', signedOut);
  async function restore() {
    try {
      const session = await api('session');
      if (session.authenticated) { csrf = session.csrf; await refresh(); }
      else if (!session.configured) $('login-help').textContent = '尚未设置管理密钥。请先在服务器终端运行 admin-token 命令，详见部署文档。';
    } catch (error) { status(error.message, true); }
  }
  window.addEventListener('pageshow', (event) => { if (event.persisted) restore(); });
  restore();
})();
