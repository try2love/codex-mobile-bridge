'use strict';

class ComposerSubmitControl {
  static nextId = 0;
  constructor({ form, send, stop, mode, frame, preferences, getState, isCollapsed, onError }) {
    Object.assign(this, {
      form,
      send,
      stop,
      mode,
      frame,
      preferences,
      getState,
      isCollapsed,
      onError,
    });
    this.buildControls();
    this.bindEvents();
  }

  buildControls() {
    // Retain the idle-send capability while follow-up pickers expose queue and steer only.
    this.mode.querySelector('[value=send]').hidden = true;
    this.root = ComposerUI.node('div', 'composer-submit');
    this.root.setAttribute('aria-label', ComposerUI.t('发送与任务控制'));
    this.caret = ComposerUI.button('发送方式', 'collapse');
    this.caret.className = 'composer-submit-caret';
    this.caret.setAttribute('aria-haspopup', 'menu');
    this.caret.setAttribute('aria-expanded', 'false');
    this.stopButton = ComposerUI.node('button', 'stop composer-submit-stop');
    this.stopButton.type = 'button';
    this.send.classList.add('composer-submit-primary');
    this.stopButton.classList.add('composer-submit-primary');
    this.menu = ComposerUI.node('dialog', 'composer-submit-menu');
    this.menu.id = 'composer-send-options-' + ++ComposerSubmitControl.nextId;
    this.menu.setAttribute('role', 'menu');
    this.caret.setAttribute('aria-controls', this.menu.id);
    this.choices = new Map();
    for (const [value, label, detail] of [
      ['queue', '完成后发送', '等待当前任务结束后执行'],
      ['steer', '补充当前任务', '立即补充到正在执行的任务'],
    ]) {
      const button = ComposerUI.node('button', 'composer-submit-option');
      button.type = 'button';
      button.dataset.sendMode = value;
      button.setAttribute('role', 'menuitemradio');
      const copy = ComposerUI.node('span');
      const title = ComposerUI.node('strong');
      const description = ComposerUI.node('small');
      copy.append(title, description);
      button.append(ComposerIcons.create(value), copy, this.checkIcon());
      button.onclick = () => {
        const action = this.action();
        if (action.choices && !action.choicesDisabled && this.choose(value)) {
          this.close();
        }
      };
      this.choices.set(value, { button, label, detail, title, description });
      this.menu.append(button);
    }
    this.menuStop = ComposerUI.node(
      'button',
      'composer-submit-option composer-submit-menu-stop',
    );
    this.menuStop.type = 'button';
    this.menuStop.setAttribute('role', 'menuitem');
    this.menuStop.onclick = () => this.interrupt();
    this.menu.append(this.menuStop);
    this.root.append(this.send, this.stopButton, this.caret);
    // Mount at the viewport so toolbar overflow cannot clip the menu.
    document.body.append(this.menu);
    this.menu.addEventListener('close', () => {
      if (!this.menu.open) ComposerUI.attribute(this.caret, 'aria-expanded', false);
    });
    this.send.removeAttribute('data-i18n');
  }

  bindEvents() {
    this.caret.onclick = () => {
      this.sync();
      if (this.caret.hidden || this.caret.disabled) {
        return;
      }
      if (!this.menu.open) {
        this.open();
      } else {
        this.close();
      }
      this.caret.focus({ preventScroll: true });
    };
    this.stopButton.onclick = () => this.interrupt();
    this.onMode = () => this.choose(this.mode.value);
    this.mode.addEventListener('change', this.onMode, true);
    this.onOutside = (event) => {
      if (
        this.menu.open &&
        !this.root.contains(event.target) &&
        !this.menu.contains(event.target)
      ) {
        this.close();
      }
    };
    document.addEventListener('pointerdown', this.onOutside);
    document.addEventListener('focusin', this.onOutside);
    this.onKey = (event) => this.handleKeydown(event);
    this.caret.addEventListener('keydown', this.onKey);
    this.menu.addEventListener('keydown', this.onKey);
    // A painted button can outlive its capabilities; submission uses live state.
    this.onSubmit = (event) => {
      if (this.action().sendDisabled) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    };
    this.form.addEventListener('submit', this.onSubmit, true);
    this.reposition = () => {
      if (!this.menu.open || this.positionFrame != null) return;
      this.positionFrame = requestAnimationFrame(() => {
        this.positionFrame = null;
        if (this.menu.open) this.position();
      });
    };
    window.addEventListener('resize', this.reposition);
    window.visualViewport?.addEventListener('resize', this.reposition);
    window.visualViewport?.addEventListener('scroll', this.reposition);
  }
  handleKeydown(event) {
    if (event.key === 'Escape' && this.menu.open) {
      event.preventDefault();
      event.stopPropagation();
      this.close();
      this.caret.focus();
      return;
    }
    if (event.key === 'Tab' && this.menu.open) {
      this.close();
      return;
    }
    const navigationKeys = ['ArrowDown', 'ArrowUp', 'Home', 'End'];
    if (!navigationKeys.includes(event.key) || this.caret.hidden) {
      return;
    }
    event.preventDefault();
    this.sync();
    if (this.caret.hidden || this.caret.disabled) {
      return;
    }
    this.open();
    const menuItems = [...this.menu.querySelectorAll('button')].filter(
      (button) => !button.disabled,
    );
    const currentIndex = menuItems.indexOf(document.activeElement);
    let nextIndex;
    if (event.key === 'Home') {
      nextIndex = 0;
    } else if (event.key === 'End') {
      nextIndex = menuItems.length - 1;
    } else if (currentIndex < 0) {
      nextIndex = event.key === 'ArrowDown' ? 0 : menuItems.length - 1;
    } else {
      const direction = event.key === 'ArrowDown' ? 1 : -1;
      nextIndex = (currentIndex + direction + menuItems.length) % menuItems.length;
    }
    menuItems[nextIndex]?.focus();
  }

  availableModes() {
    return Array.from(this.mode.options)
      .filter((option) => !option.disabled && (option.value === 'send' || !option.hidden))
      .map((option) => option.value);
  }
  action(availableModes = this.availableModes()) {
    return deriveComposerAction(
      {
        ...this.getState(),
        availableModes,
        sendEnabled: !this.send.disabled,
        stopEnabled: !this.stop.hidden && !this.stop.disabled,
        modeEnabled: !this.mode.disabled,
      },
      this.preferences.value.followUpMode,
    );
  }
  choose(value) {
    if (
      this.mode.disabled ||
      !this.choices.has(value) ||
      !this.availableModes().includes(value)
    ) {
      this.sync();
      return false;
    }
    try {
      this.preferences.update({ followUpMode: value });
      return true;
    } catch (error) {
      this.onError(error);
      this.sync();
      return false;
    }
  }
  checkIcon() {
    const icon = ComposerIcons.create('check');
    icon.classList.add('composer-submit-check');
    return icon;
  }
  iconLabel(node, label, id) {
    const text = ComposerUI.t(label);
    if (
      node.children.length === 2 &&
      node.firstElementChild.textContent === text &&
      node.lastElementChild.dataset.icon === id
    ) {
      return;
    }
    node.replaceChildren(ComposerUI.node('span', '', text), ComposerIcons.create(id));
  }
  text(node, value) {
    const text = ComposerUI.t(value);
    if (node.textContent !== text) {
      node.textContent = text;
    }
  }
  open() {
    if (this.menu.open) return;
    this.menu.show();
    ComposerUI.attribute(this.caret, 'aria-expanded', true);
    this.position();
  }
  position() {
    const viewport = window.visualViewport;
    const left = viewport?.offsetLeft || 0;
    const top = viewport?.offsetTop || 0;
    const width = viewport?.width || innerWidth;
    const height = viewport?.height || innerHeight;
    const anchor = this.caret.getBoundingClientRect();
    const frame = this.frame.getBoundingClientRect();
    const margin = 12;
    const gap = 8;
    const above = Math.max(0, Math.min(frame.top, top + height) - top - margin - gap);
    const below = Math.max(0, top + height - Math.max(frame.bottom, top) - margin - gap);
    const style = this.menu.style;
    const menuWidth = Math.max(0, Math.min(280, width - margin * 2)) + 'px';
    const menuHeight = Math.max(above, below) + 'px';
    if (style.width !== menuWidth) style.width = menuWidth;
    if (style.maxHeight !== menuHeight) style.maxHeight = menuHeight;
    const rect = this.menu.getBoundingClientRect();
    const x = Math.max(
      left + margin,
      Math.min(anchor.right - rect.width, left + width - rect.width - margin),
    );
    const y =
      above >= rect.height || above >= below
        ? frame.top - gap - rect.height
        : frame.bottom + gap;
    const menuLeft = x + 'px';
    const menuTop =
      Math.max(top + margin, Math.min(y, top + height - rect.height - margin)) + 'px';
    if (style.left !== menuLeft) style.left = menuLeft;
    if (style.top !== menuTop) style.top = menuTop;
  }
  close() {
    if (!this.menu.open) {
      return;
    }
    const focused = this.menu.contains(document.activeElement);
    this.menu.close();
    ComposerUI.attribute(this.caret, 'aria-expanded', false);
    if (focused) {
      let focusTarget = this.caret;
      if (this.caret.hidden) {
        focusTarget = this.stopButton.hidden ? this.send : this.stopButton;
      }
      focusTarget.focus({ preventScroll: true });
    }
  }

  interrupt() {
    if (this.action().stopDisabled) {
      return;
    }
    this.close();
    this.stop.click();
  }
  sync() {
    const available = this.availableModes();
    const action = this.action(available);
    const offline = this.getState().offline === true;
    const selected = action.followUpMode || this.preferences.value.followUpMode;
    if (this.mode.value !== selected) this.mode.value = selected;
    ComposerUI.attribute(this.root, 'data-display', this.preferences.value.display.submit);
    this.root.classList.toggle('is-stop', action.stop);
    this.send.classList.toggle('composer-submit-hidden', action.stop);
    ComposerUI.hidden(this.stopButton, !action.stop);
    if (this.stopButton.disabled !== action.stopDisabled) {
      this.stopButton.disabled = action.stopDisabled;
    }
    this.iconLabel(this.stopButton, '停止', 'stop');
    ComposerUI.attribute(this.stopButton, 'aria-label', ComposerUI.t('停止当前任务'));
    ComposerUI.attribute(this.stopButton, 'title', ComposerUI.t('停止当前任务'));
    ComposerUI.hidden(this.caret, !action.choices);
    if (this.caret.disabled !== action.choicesDisabled) {
      this.caret.disabled = action.choicesDisabled;
    }
    this.iconLabel(this.send, offline ? '存入待发' : action.label, action.mode === 'send' ? 'send' : action.mode);
    const modeLabel = this.choices.has(action.mode)
      ? ComposerUI.t(this.choices.get(action.mode).label)
      : ComposerUI.t('发送新消息');
    ComposerUI.attribute(
      this.send,
      'aria-label',
      offline ? ComposerUI.t('存入待发') : action.label === '正在发送…' ? ComposerUI.t('正在发送…') : modeLabel,
    );
    ComposerUI.attribute(this.send, 'aria-disabled', action.sendDisabled);
    ComposerUI.attribute(this.send, 'title', this.send.getAttribute('aria-label'));
    ComposerUI.attribute(this.caret, 'aria-label', ComposerUI.t('发送方式') + '：' + modeLabel);
    ComposerUI.attribute(this.caret, 'title', this.caret.getAttribute('aria-label'));
    ComposerUI.attribute(this.menu, 'aria-label', ComposerUI.t('发送方式'));
    this.syncChoices(action, available);
    if (!action.choices || action.choicesDisabled || this.isCollapsed()) {
      this.close();
    } else {
      this.reposition();
    }
  }
  syncChoices(action, available) {
    for (const [value, { button, label, detail, title, description }] of this.choices) {
      const disabled = !available.includes(value) || action.choicesDisabled;
      if (button.disabled !== disabled) {
        button.disabled = disabled;
      }
      ComposerUI.attribute(button, 'aria-checked', action.mode === value);
      this.text(title, label);
      this.text(description, detail);
    }
    this.iconLabel(this.menuStop, '停止当前任务', 'stop');
    if (this.menuStop.disabled !== action.stopDisabled) {
      this.menuStop.disabled = action.stopDisabled;
    }
  }

  dispose() {
    this.close();
    if (this.positionFrame != null) cancelAnimationFrame(this.positionFrame);
    this.mode.removeEventListener('change', this.onMode, true);
    document.removeEventListener('pointerdown', this.onOutside);
    document.removeEventListener('focusin', this.onOutside);
    this.form.removeEventListener('submit', this.onSubmit, true);
    this.caret.removeEventListener('keydown', this.onKey);
    this.menu.removeEventListener('keydown', this.onKey);
    this.menu.remove();
    window.removeEventListener('resize', this.reposition);
    window.visualViewport?.removeEventListener('resize', this.reposition);
    window.visualViewport?.removeEventListener('scroll', this.reposition);
  }
}
