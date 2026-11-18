'use strict';

class ComposerToolbarEditor {
  constructor({ preferences, describeTool, getPlaceholder, onClose }) {
    Object.assign(this, { preferences, describeTool, getPlaceholder, onClose });
    this.dialog = ComposerUI.node('dialog', 'picker toolbar-editor');
    this.dialog.setAttribute('aria-label', ComposerUI.t('定制工具栏'));
    this.buildHeader();
    this.content = ComposerUI.node('div', 'toolbar-editor-body');
    this.buildPreview();
    this.buildSettings();
    this.dialog.append(this.head, this.content, this.status);
    document.body.append(this.dialog);
    this.selectedId = 'model';
    this.toolPreviews = new Map();
    this.singlePreset = composerPreset('single');
    this.bindInteraction();
    this.unsubscribe = this.preferences.subscribe(({ layout, display }) => {
      if (this.dialog.open && (this.layout !== layout || this.display !== display))
        this.settingsChanged();
    });
    this.onLanguage = () => this.relabel();
    document.addEventListener('bridge-language', this.onLanguage);
  }
  buildHeader() {
    this.head = ComposerUI.node('div', 'picker-head');
    this.title = ComposerUI.node('h2', '', ComposerUI.t('定制工具栏'));
    const titleGroup = ComposerUI.node('div', 'toolbar-editor-title');
    titleGroup.append(this.title);
    this.singlePresetButton = ComposerUI.node('button', 'plain', ComposerUI.t('单栏默认'));
    this.singlePresetButton.type = 'button';
    this.singlePresetButton.dataset.composerPreset = 'single';
    this.singlePresetButton.onclick = () => this.applyPreset('single');
    this.doublePresetButton = ComposerUI.node('button', 'plain', ComposerUI.t('双栏默认'));
    this.doublePresetButton.type = 'button';
    this.doublePresetButton.dataset.composerPreset = 'default';
    this.doublePresetButton.onclick = () => this.applyPreset('default');
    this.closeButton = ComposerUI.node('button', 'primary toolbar-editor-close');
    this.closeButton.append(ComposerIcons.create('close'));
    this.closeButton.type = 'button';
    this.closeButton.autofocus = true;
    this.closeButton.onclick = () => this.dialog.close();
    this.head.append(
      titleGroup,
      this.singlePresetButton,
      this.doublePresetButton,
      this.closeButton,
    );
  }
  buildPreview() {
    this.previewCanvas = ComposerUI.node('section', 'toolbar-editor-canvas');
    this.groups = {};
    this.zoneLabels = {
      topLeft: '上栏左侧',
      topRight: '上栏右侧',
      bottomLeft: '下栏左侧',
      bottomRight: '下栏右侧',
      more: '工具面板',
    };
    this.previewCanvas.append(this.buildPreviewRow('top'));
    const input = ComposerUI.node('div', 'toolbar-editor-input', ComposerUI.t('消息内容'));
    this.previewCanvas.append(input, this.buildPreviewRow('bottom'));
    this.previewArea = ComposerUI.node('section', 'toolbar-editor-preview-area');
    this.previewArea.append(this.previewCanvas);
    this.content.append(this.previewArea);
    this.library = ComposerUI.node('section', 'toolbar-editor-toolbox');
    const more = ComposerUI.node('div', 'toolbar-drop-zone toolbar-editor-library');
    more.dataset.dropZone = 'more';
    more.setAttribute('aria-label', ComposerUI.t('工具面板'));
    this.groups.more = more;
    this.library.append(more);
    this.content.append(this.library);
  }
  buildPreviewRow(position) {
    const section = ComposerUI.node(
      'section',
      'toolbar-editor-row toolbar-editor-row-' + position,
    );
    const bar = ComposerUI.node('div', 'toolbar-editor-bar');
    const sendButton =
      position === 'bottom'
        ? ComposerUI.node('button', 'toolbar-editor-fixed toolbar-editor-submit')
        : null;
    if (sendButton) {
      sendButton.type = 'button';
      sendButton.dataset.editorTool = 'submit';
      sendButton.onclick = () => this.selectTool('submit');
      sendButton.setAttribute('aria-label', ComposerUI.t('发送 · 固定'));
      sendButton.append(
        ComposerUI.icon('send'),
        ComposerUI.node('small', 'toolbar-preview-caption', ComposerUI.t('发送')),
      );
    }
    for (const side of ['Left', 'Right']) {
      const key = position + side;
      const group = ComposerUI.node(
        'div',
        'toolbar-drop-zone ' + (side === 'Right' ? 'toolbar-align-right' : ''),
      );
      group.dataset.dropZone = key;
      group.setAttribute('aria-label', ComposerUI.t(this.zoneLabels[key]));
      this.groups[key] = group;
      bar.append(group);
    }
    if (position === 'bottom') {
      bar.append(sendButton);
    }
    section.append(bar);
    return section;
  }

  buildSettings() {
    this.displaySettings = ComposerUI.node('section', 'toolbar-display-settings');
    this.selectedToolTitle = ComposerUI.node('p', 'toolbar-display-title');
    this.displayChoices = ComposerUI.node('div', 'toolbar-display-choices');
    for (const [mode, label] of [
      ['hidden', '隐藏'],
      ['icon', '仅图标'],
      ['text', '仅文字'],
      ['both', '图标＋文字'],
    ]) {
      const button = ComposerUI.node('button', '', ComposerUI.t(label));
      button.type = 'button';
      button.dataset.displayChoice = mode;
      button.onclick = () => this.changeDisplay(mode);
      this.displayChoices.append(button);
    }
    this.displayPreview = ComposerUI.node('div', 'toolbar-display-preview');
    this.displayPreview.setAttribute('aria-hidden', 'true');
    this.toolStatus = ComposerUI.node('p', 'muted toolbar-tool-status');
    const styleHead = ComposerUI.node('div', 'toolbar-display-heading');
    styleHead.append(this.selectedToolTitle, this.displayPreview);
    this.displayLabel = ComposerUI.node('p', 'toolbar-setting-label');
    this.displaySettings.append(styleHead, this.displayLabel, this.displayChoices);
    this.inspector = ComposerUI.node('section', 'toolbar-editor-inspector');
    this.inspector.append(this.displaySettings);
    this.content.append(this.inspector);
    this.positionSettings = ComposerUI.node('section', 'toolbar-move-settings');
    this.positionTitle = ComposerUI.node('p', 'toolbar-display-title');
    this.positionChoices = ComposerUI.node('div', 'toolbar-move-choices');
    for (const zone of composerZones) {
      const button = ComposerUI.node('button');
      button.type = 'button';
      button.dataset.moveZone = zone;
      button.onclick = () => this.relocate(this.selectedId, zone, this.layout[zone].length);
      this.positionChoices.append(button);
    }
    this.positionSettings.append(this.positionTitle, this.positionChoices);
    this.inspector.append(this.positionSettings, this.toolStatus);
    this.status = ComposerUI.node('p', 'muted toolbar-editor-status');
    this.status.setAttribute('aria-live', 'polite');
  }
  bindInteraction() {
    this.dragControl = new ComposerToolbarDrag({
      dialog: this.dialog,
      groups: this.groups,
      onSelect: (id) => this.selectTool(id),
      onMove: (id, zone, index) => this.relocate(id, zone, index),
      onStatus: (text) => {
        this.status.textContent = ComposerUI.t(text);
      },
    });
    this.dialog.addEventListener('click', (event) => {
      const button = event.target.closest('[data-drag-tool]');
      if (button) {
        this.selectTool(button.dataset.dragTool);
      }
    });
    this.dialog.addEventListener('keydown', (event) => this.keydown(event));
    this.dialog.addEventListener('close', () => {
      this.dragControl.cancel();
      this.onClose();
    });
    this.dialog.addEventListener('cancel', (event) => {
      if (this.dragControl.active) {
        event.preventDefault();
        this.dragControl.cancel();
      }
    });
  }

  open() {
    this.selectedId = 'model';
    this.status.textContent = '';
    this.relabel();
    this.settingsChanged();
    this.dialog.showModal();
  }
  settingsChanged() {
    this.dragControl.cancel();
    this.layout = this.preferences.value.layout;
    this.display = this.preferences.value.display;
    const focused = this.dialog.contains(document.activeElement)
      ? document.activeElement.closest('[data-drag-tool]')?.dataset.dragTool
      : null;
    this.render(focused);
  }
  relabel() {
    const t = (text) => ComposerUI.t(text);
    this.dialog.setAttribute('aria-label', t('定制工具栏'));
    this.title.textContent = t('定制工具栏');
    this.doublePresetButton.textContent = t('双栏默认');
    this.closeButton.setAttribute('aria-label', t('关闭'));
    this.singlePresetButton.textContent = t('单栏默认');
    this.displayLabel.textContent = t('显示方式');
    this.dialog.querySelectorAll('.toolbar-editor-fixed').forEach((node) => {
      const label = t('发送 · 固定');
      node.querySelector('small').textContent = t('发送');
      node.setAttribute('aria-label', label);
    });
    this.dialog.querySelector('.toolbar-editor-input').textContent =
      this.getPlaceholder() || t('消息内容');
    for (const zone of composerZones) {
      this.groups[zone].setAttribute('aria-label', t(this.zoneLabels[zone]));
    }
    const styleLabels = { icon: '仅图标', text: '仅文字', both: '图标＋文字', hidden: '隐藏' };
    this.displayChoices
      .querySelectorAll('button')
      .forEach((button) => (button.textContent = t(styleLabels[button.dataset.displayChoice])));
    this.positionTitle.textContent = t('移动到');
    const shortZones = {
      topLeft: '上左',
      topRight: '上右',
      bottomLeft: '下左',
      bottomRight: '下右',
      more: '工具面板',
    };
    this.positionChoices.querySelectorAll('button').forEach((button) => {
      const zone = button.dataset.moveZone;
      button.textContent = t(shortZones[zone]);
      button.setAttribute('aria-label', t(this.zoneLabels[zone]));
    });
    if (this.dialog.open) this.render();
  }
  render(focusId) {
    for (const key of composerZones) {
      const group = this.groups[key];
      this.layout[key].forEach((id, index) => {
        const button = this.updateToolPreview(id, key);
        const at = group.children[index];
        if (at !== button) group.insertBefore(button, at || null);
      });
    }
    const single = this.singlePreset;
    ComposerUI.attribute(
      this.singlePresetButton,
      'aria-pressed',
      JSON.stringify(single.layout) === JSON.stringify(this.layout) &&
        JSON.stringify(single.display) === JSON.stringify(this.display),
    );
    this.renderStyle();
    const focused = this.toolPreviews.get(focusId)?.button;
    if (focused && document.activeElement !== focused) focused.focus({ preventScroll: true });
  }
  updateToolPreview(id, zone) {
    const tool = this.describeTool(id);
    const isHidden = this.display[id] === 'hidden';
    const inLibrary = zone === 'more';
    const hiddenSuffix = isHidden ? ' · ' + ComposerUI.t('隐藏') : '';
    const previewNames = { model: '模型', collapse: '收起', plan: '计划', goal: '目标' };
    const label = ComposerUI.t(previewNames[id] || tool.shortLabel);
    const iconKey = tool.icon?.permissionMode ?? tool.icon?.collapsed;
    let preview = this.toolPreviews.get(id);
    if (!preview) {
      const button = ComposerUI.node('button', 'toolbar-drag-tool');
      button.type = 'button';
      button.dataset.dragTool = id;
      const icon = ComposerUI.icon(id, tool.icon);
      const caption = ComposerUI.node('span');
      button.append(icon, caption);
      preview = { button, icon, iconKey, caption };
      this.toolPreviews.set(id, preview);
    } else if (preview.iconKey !== iconKey) {
      const icon = ComposerUI.icon(id, tool.icon);
      preview.icon.replaceWith(icon);
      Object.assign(preview, { icon, iconKey });
    }
    const { button, caption } = preview;
    button.classList.toggle('toolbar-drag-hidden', isHidden);
    ComposerUI.attribute(button, 'aria-label', ComposerUI.t(tool.label) + hiddenSuffix);
    ComposerUI.attribute(
      caption,
      'class',
      inLibrary ? 'toolbar-library-caption' : 'toolbar-preview-caption',
    );
    const captionText = inLibrary ? label + hiddenSuffix : label;
    if (caption.textContent !== captionText) caption.textContent = captionText;
    ComposerUI.attribute(
      button,
      'title',
      tool.disabled
        ? tool.reason || ComposerUI.t('当前接入不支持此功能')
        : ComposerUI.t(tool.label),
    );
    return button;
  }

  selectTool(id) {
    if (this.selectedId !== id) {
      this.selectedId = id;
      this.renderStyle();
    }
  }
  renderStyle() {
    for (const [id, { button }] of this.toolPreviews) {
      ComposerUI.attribute(button, 'aria-pressed', id === this.selectedId);
    }
    ComposerUI.attribute(
      this.dialog.querySelector('[data-editor-tool=submit]'),
      'aria-pressed',
      this.selectedId === 'submit',
    );
    this.updateToolState();
    const id = this.selectedId;
    const mode = this.display[id];
    this.selectedToolTitle.textContent = ComposerUI.t(
      id === 'submit' ? composerSubmitTool.label : this.describeTool(id).shortLabel,
    );
    this.positionSettings.inert = id === 'submit';
    this.positionSettings.style.visibility = id === 'submit' ? 'hidden' : '';
    this.displayChoices.setAttribute('role', 'group');
    this.displayChoices.setAttribute('aria-label', ComposerUI.t('显示方式'));
    this.displayChoices.querySelectorAll('button').forEach((button) => {
      button.setAttribute('aria-pressed', String(button.dataset.displayChoice === mode));
      button.hidden =
        button.dataset.displayChoice === 'hidden' && ['more', 'submit'].includes(id);
    });
    const zone = this.zoneOf(id);
    this.positionChoices.querySelectorAll('button').forEach((button) => {
      ComposerUI.attribute(button, 'aria-pressed', button.dataset.moveZone === zone);
      button.disabled = id === 'more' && button.dataset.moveZone === 'more';
    });
  }
  updateToolState() {
    if (!this.selectedId) {
      return;
    }
    const tool = this.describeTool(this.selectedId);
    let reason = '';
    if (this.display[this.selectedId] === 'hidden') {
      reason = ComposerUI.t('已隐藏，可在此恢复显示。');
    } else if (tool.disabled) {
      reason = ComposerUI.t(tool.reason || '当前接入不支持此功能');
    }
    if (this.toolStatus.textContent !== reason) {
      this.toolStatus.textContent = reason;
    }
    ComposerUI.hidden(this.toolStatus, !reason);
    const id = this.selectedId;
    const mode = this.display[id];
    const label = tool.previewLabel;
    const key = JSON.stringify([id, mode, label, tool.icon]);
    if (this.previewKey !== key) {
      this.previewKey = key;
      this.displayPreview.replaceChildren();
      if (!['text', 'hidden'].includes(mode)) {
        this.displayPreview.append(
          id === 'submit' ? ComposerIcons.create('send') : ComposerUI.icon(id, tool.icon),
        );
      }
      if (mode !== 'icon') {
        const text = ComposerUI.node(
          'span',
          '',
          mode === 'hidden' ? ComposerUI.t('隐藏') : label,
        );
        if (id === 'submit') {
          this.displayPreview.prepend(text);
        } else {
          this.displayPreview.append(text);
        }
      }
    }
  }
  changeDisplay(mode) {
    if (mode === 'hidden' && ['more', 'submit'].includes(this.selectedId)) {
      return;
    }
    try {
      this.preferences.setDisplay(this.selectedId, mode);
      this.status.textContent = '';
    } catch (error) {
      this.status.textContent = ComposerUI.t(error.message);
    }
  }
  applyPreset(id) {
    const single = id === 'single';
    const question = single ? '恢复单栏默认布局和显示方式？' : '恢复默认工具栏布局和显示方式？';
    if (!window.confirm(ComposerUI.t(question))) {
      return;
    }
    this.dragControl.cancel();
    try {
      this.preferences.applyPreset(id);
      this.status.textContent = ComposerUI.t(single ? '已应用单栏布局' : '已恢复默认工具栏');
    } catch (error) {
      this.status.textContent = ComposerUI.t(error.message);
    }
  }
  zoneOf(id) {
    return composerZones.find((key) => this.layout[key].includes(id));
  }
  relocate(id, key, index) {
    if (id === 'more' && key === 'more') {
      return;
    }
    this.selectedId = id;
    try {
      this.preferences.moveTool(id, key, index);
      this.dialog.querySelector('[data-drag-tool=' + id + ']')?.focus({ preventScroll: true });
      this.status.textContent =
        ComposerUI.t('已移动到') + ' ' + ComposerUI.t(this.zoneLabels[key]);
    } catch (error) {
      this.status.textContent = ComposerUI.t(error.message);
    }
  }
  keydown(event) {
    const button = event.target.closest('[data-drag-tool]');
    if (
      !button ||
      !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Delete', 'Backspace'].includes(
        event.key,
      )
    ) {
      return;
    }
    event.preventDefault();
    const id = button.dataset.dragTool;
    const currentZone = this.zoneOf(id);
    const currentIndex = this.layout[currentZone].indexOf(id);
    if (['Delete', 'Backspace'].includes(event.key)) {
      this.relocate(id, 'more', this.layout.more.length);
      return;
    }
    if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
      const row = event.key === 'ArrowUp' ? 'top' : 'bottom';
      const alignment = currentZone.endsWith('Right') ? 'Right' : 'Left';
      const targetZone = row + alignment;
      this.relocate(id, targetZone, this.layout[targetZone].length);
      return;
    }
    if (event.shiftKey) {
      const row = currentZone.startsWith('bottom') ? 'bottom' : 'top';
      const alignment = event.key === 'ArrowLeft' ? 'Left' : 'Right';
      const targetZone = row + alignment;
      this.relocate(id, targetZone, this.layout[targetZone].length);
      return;
    }
    const direction = event.key === 'ArrowLeft' ? -1 : 1;
    const targetIndex = Math.max(
      0,
      Math.min(currentIndex + direction, this.layout[currentZone].length - 1),
    );
    this.relocate(id, currentZone, targetIndex);
  }

  dispose() {
    this.dragControl.dispose();
    this.unsubscribe();
    document.removeEventListener('bridge-language', this.onLanguage);
    this.dialog.remove();
  }
}
