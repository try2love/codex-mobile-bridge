const ProjectMounts = (() => {
  let target = null, data = null, generation = 0, busy = false;
  const active = version => version === generation && $('project-mounts-dialog').open;

  function syncControls() {
    $('project-mounts-dialog').querySelectorAll('form button,form input,form select,form textarea,#mount-list button,#mount-list input')
      .forEach(node => node.disabled = busy || !data);
    const manual = !$('mount-project').value;
    $('mount-path-field').hidden = !manual;
    $('mount-path').required = manual;
    $('mount-path').disabled = busy || !data || !manual;
    const count = $('mount-list').querySelectorAll('input:checked').length;
    $('mount-check-submit').disabled = busy || !data || count === 0;
    $('mount-selection').textContent = '已选 ' + count + ' 个 · 每次最多 12 个';
  }

  function renderMounts() {
    const selected = new Set(Array.from($('mount-list').querySelectorAll('input:checked'), node => node.value));
    $('mount-list').replaceChildren();
    for (const mount of data.mounts) {
      const row = el('div', 'mount-row'), label = el('label', 'mount-choice');
      const checkbox = el('input'); checkbox.type = 'checkbox'; checkbox.value = mount.id;
      checkbox.checked = selected.has(mount.id); checkbox.onchange = syncControls;
      const detail = el('span', 'mount-detail');
      detail.append(el('strong', '', mount.name), el('small', '', mount.roots.join('\n')));
      label.append(checkbox, detail);
      const remove = el('button', 'plain', '解除'); remove.type = 'button';
      remove.setAttribute('aria-label', '解除挂载 ' + mount.name);
      remove.onclick = () => mutate({action: 'remove', id: mount.id});
      row.append(label, remove); $('mount-list').append(row);
    }
    if (!data.mounts.length) $('mount-list').append(el('p', 'muted', '尚未挂载子项目。添加后可在这里选择检查范围。'));
    syncControls();
  }

  async function open(id, host) {
    const version = ++generation;
    target = {id, host}; data = null; busy = true;
    $('mount-add-form').reset(); $('mount-check-form').reset();
    $('mount-project').replaceChildren(new Option('输入项目路径', ''));
    $('mount-parent').textContent = '正在读取挂载信息…';
    $('mount-parent-paths').textContent = '';
    $('mount-error').textContent = ''; $('mount-status').textContent = '';
    $('mount-list').replaceChildren();
    if (!$('project-mounts-dialog').open) $('project-mounts-dialog').showModal();
    syncControls();
    try {
      const result = await api(sessionUrl(id, 'project-mounts', host));
      if (!active(version)) return;
      data = result;
      $('mount-parent').textContent = '主项目：' + data.parent.name;
      $('mount-parent-paths').textContent = data.parent.roots.join('\n');
      for (const project of data.projects) $('mount-project').append(new Option(project.name + ' · ' + project.cwd, project.key));
      $('mount-path').placeholder = host === 'local' ? '填写电脑上的项目绝对路径' : '填写此 SSH 主机上的项目绝对路径';
      renderMounts();
    } catch (error) {
      if (active(version)) { $('mount-parent').textContent = '无法读取挂载信息'; $('mount-error').textContent = error.message; }
    } finally {
      if (active(version)) { busy = false; syncControls(); }
    }
  }

  async function mutate(body) {
    if (busy || !data) return;
    const version = generation, destination = {...target}, parentKey = data.parent.key;
    busy = true; syncControls(); $('mount-error').textContent = ''; $('mount-status').textContent = '';
    try {
      const result = await api(sessionUrl(destination.id, 'project-mounts', destination.host), {...body, parentKey});
      if (!active(version)) return;
      data.mounts = result.mounts; renderMounts();
      if (body.action === 'add') $('mount-add-form').reset();
      $('mount-status').textContent = body.action === 'add' ? '已保存挂载关联，尚未读取项目内容。' : '已解除关联，项目文件保留。';
    } catch (error) {
      if (active(version)) $('mount-error').textContent = error.message;
    } finally {
      if (active(version)) { busy = false; syncControls(); }
    }
  }

  async function prepareCheck(event) {
    event.preventDefault();
    if (busy || !data) return;
    const ids = Array.from($('mount-list').querySelectorAll('input:checked'), node => node.value);
    if (!ids.length || ids.length > 12) { $('mount-error').textContent = '每次请选择 1–12 个子项目'; return; }
    const version = generation, destination = {...target};
    busy = true; syncControls(); $('mount-error').textContent = ''; $('mount-status').textContent = '';
    try {
      const result = await api(sessionUrl(destination.id, 'project-mounts', destination.host), {
        action: 'check', parentKey: data.parent.key, ids, focus: $('mount-focus').value.trim()
      });
      if (!active(version)) return;
      const sameChat = currentId === destination.id && currentHost === destination.host;
      if (sameChat && sending) throw Error('消息正在发送，请发送完成后再加入检查请求');
      const draftKey = 'draft:' + chatKey(destination.id, destination.host);
      const draft = sameChat ? $('message').value : (sessionStorage.getItem(draftKey) || '');
      const next = (draft.trim() ? draft + '\n\n' : '') + result.text;
      if (next.length > 100000) throw Error('合并后的草稿超过 100000 字，请先缩短原草稿');
      sessionStorage.setItem(draftKey, next);
      if (sameChat) { $('message').value = next; resizeMessage(); }
      $('project-mounts-dialog').close();
      if (!sameChat || !$('app').classList.contains('chat-open')) {
        // openChat saves the current composer first, so set it above when reusing it.
        try { await openChat(destination.id, destination.host); }
        catch (error) { toast('检查请求已保存到该对话草稿。' + error.message); return; }
      }
      if (currentId === destination.id && currentHost === destination.host) {
        $('message').focus(); toast('检查请求已加入草稿，点击发送后读取项目。');
      }
    } catch (error) {
      if (active(version)) $('mount-error').textContent = error.message;
    } finally {
      if (active(version)) { busy = false; syncControls(); }
    }
  }

  function init() {
    $('mount-project').onchange = syncControls;
    $('mount-add-form').onsubmit = event => {
      event.preventDefault();
      mutate({action: 'add', projectKey: $('mount-project').value,
        path: $('mount-path').value.trim(), name: $('mount-name').value.trim()});
    };
    $('mount-check-form').onsubmit = prepareCheck;
    $('project-mounts-dialog').addEventListener('close', () => { generation++; busy = false; });
    $('manage-mounts').onclick = () => {
      if (!managementTarget) return;
      const destination = {...managementTarget}; $('manage-dialog').close();
      open(destination.id, destination.host);
    };
  }
  return {open, init};
})();

function openProjectMounts(id, host) { return ProjectMounts.open(id, host); }
