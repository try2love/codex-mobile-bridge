'use strict';

class ComposerToolbarDrag {
  static longPressDelayMs = 320;
  static movementThresholdPx = 9;

  constructor({ dialog, groups, onSelect, onMove, onStatus }) {
    Object.assign(this, { dialog, groups, onSelect, onMove, onStatus });
    this.eventController = new AbortController();
    this.bindEvents();
  }

  bindEvents() {
    const listen = (type, listener, options = {}) =>
      this.dialog.addEventListener(type, listener, {
        ...options,
        signal: this.eventController.signal,
      });
    listen('pointerdown', (event) => {
      if (event.pointerType !== 'touch') {
        this.press(event);
      }
    });
    listen('pointermove', (event) => {
      if (event.pointerType !== 'touch') {
        this.move(event);
      }
    });
    listen('pointerup', (event) => {
      if (event.pointerType !== 'touch') {
        this.release(event);
      }
    });
    listen('pointercancel', (event) => {
      if (event.pointerType !== 'touch') {
        this.cancel();
      }
    });
    listen('lostpointercapture', (event) => {
      if (event.pointerType !== 'touch') {
        this.cancel();
      }
    });
    const pointerEventFromTouch = (event, touch) => ({
      target: event.target,
      button: 0,
      pointerId: touch.identifier,
      clientX: touch.clientX,
      clientY: touch.clientY,
      isTouch: true,
      preventDefault: () => event.preventDefault(),
    });
    listen(
      'touchstart',
      (event) => {
        if (event.touches.length === 1) {
          this.press(pointerEventFromTouch(event, event.touches[0]));
        } else {
          this.cancel();
        }
      },
      { passive: true },
    );
    listen(
      'touchmove',
      (event) => {
        if (event.touches.length !== 1) {
          this.cancel();
          return;
        }
        this.move(pointerEventFromTouch(event, event.touches[0]));
      },
      { passive: false },
    );
    listen(
      'touchend',
      (event) => {
        if (this.activeDrag) {
          event.preventDefault();
        }
        if (event.changedTouches[0]) {
          this.release(pointerEventFromTouch(event, event.changedTouches[0]));
        }
      },
      { passive: false },
    );
    listen('touchcancel', () => this.cancel());
    listen('contextmenu', (event) => {
      if (event.target.closest('[data-drag-tool]')) {
        event.preventDefault();
      }
    });
    document.addEventListener(
      'visibilitychange',
      () => {
        if (document.hidden) {
          this.cancel();
        }
      },
      { signal: this.eventController.signal },
    );
  }
  get active() {
    return !!(this.activeDrag || this.pendingPress);
  }
  press(event) {
    const button = event.target.closest('[data-drag-tool]');
    if (!button || event.button !== 0) {
      return;
    }
    this.cancel();
    const press = {
      id: button.dataset.dragTool,
      button,
      pointerId: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      isTouch: !!event.isTouch,
    };
    this.pendingPress = press;
    if (!event.isTouch) {
      this.dialog.setPointerCapture(event.pointerId);
    }
    press.timer = setTimeout(() => this.startDrag(press), ComposerToolbarDrag.longPressDelayMs);
  }
  startDrag(press) {
    if (this.pendingPress !== press) {
      return;
    }
    clearTimeout(press.timer);
    this.pendingPress = null;
    // Keep drop targets stable while the drag layout exposes empty rows.
    this.dialog.style.height = this.dialog.getBoundingClientRect().height + 'px';
    this.activeDrag = {
      id: press.id,
      pointerId: press.pointerId,
      x: press.x,
      y: press.y,
      target: null,
    };
    press.button.classList.add('toolbar-lifted');
    this.dialog.classList.add('toolbar-editing-drag');
    this.dragPreview = press.button.cloneNode(true);
    this.dragPreview.removeAttribute('data-drag-tool');
    this.dragPreview.removeAttribute('aria-label');
    this.dragPreview.setAttribute('aria-hidden', 'true');
    this.dragPreview.className = 'toolbar-drag-ghost';
    this.dragPreview.disabled = true;
    this.dialog.append(this.dragPreview);
    this.dropMarker = ComposerUI.node('span', 'toolbar-drop-marker');
    this.onStatus('拖到目标位置后松开；Esc 取消移动。');
    this.paint();
  }
  move(event) {
    if (
      this.pendingPress &&
      event.pointerId === this.pendingPress.pointerId &&
      Math.hypot(event.clientX - this.pendingPress.x, event.clientY - this.pendingPress.y) >
        ComposerToolbarDrag.movementThresholdPx
    ) {
      if (this.pendingPress.isTouch) {
        this.cancel();
        return;
      }
      this.startDrag(this.pendingPress);
    }
    if (!this.activeDrag || event.pointerId !== this.activeDrag.pointerId) {
      return;
    }
    event.preventDefault();
    this.activeDrag.x = event.clientX;
    this.activeDrag.y = event.clientY;
    if (!this.animationFrame) {
      this.animationFrame = requestAnimationFrame(() => {
        this.animationFrame = null;
        this.paint();
      });
    }
  }
  paint(autoScroll = true) {
    const drag = this.activeDrag;
    if (!drag) {
      return;
    }
    const dialogBounds = this.dialog.getBoundingClientRect();
    const previousScrollTop = this.dialog.scrollTop;
    if (autoScroll && drag.y < dialogBounds.top + 70) {
      this.dialog.scrollTop -= 10;
    } else if (autoScroll && drag.y > dialogBounds.bottom - 35) {
      this.dialog.scrollTop += 10;
    }
    if (this.dialog.scrollTop !== previousScrollTop && !this.animationFrame) {
      this.animationFrame = requestAnimationFrame(() => {
        this.animationFrame = null;
        this.paint();
      });
    }
    const x = drag.x - dialogBounds.left + this.dialog.scrollLeft - 30;
    const y = drag.y - dialogBounds.top + this.dialog.scrollTop - 60;
    const elementAtPointer = document.elementFromPoint(drag.x, drag.y);
    const group = elementAtPointer?.closest('[data-drop-zone]');
    const target =
      group &&
      this.dialog.contains(group) &&
      !(drag.id === 'more' && group.dataset.dropZone === 'more')
        ? group
        : null;
    if (!target) {
      this.dragPreview.style.transform = `translate3d(${x}px, ${y}px, 0)`;
      this.clearTarget();
      return;
    }
    const otherTools = Array.from(target.querySelectorAll('[data-drag-tool]')).filter(
      (node) => node.dataset.dragTool !== drag.id,
    );
    const insertBefore = otherTools.find((node) => {
      const rect = node.getBoundingClientRect();
      return (
        drag.y < rect.top - 3 ||
        (drag.y <= rect.bottom + 3 && drag.x < rect.left + rect.width / 2)
      );
    });
    this.dragPreview.style.transform = `translate3d(${x}px, ${y}px, 0)`;
    const index = insertBefore ? otherTools.indexOf(insertBefore) : otherTools.length;
    const key = target.dataset.dropZone;
    if (drag.target?.key === key && drag.target.index === index) {
      return;
    }
    this.clearTarget();
    target.classList.add('toolbar-drop-over');
    target.insertBefore(this.dropMarker, insertBefore || null);
    drag.target = { key, index };
  }
  clearTarget() {
    this.dropMarker?.remove();
    if (this.activeDrag?.target) {
      this.groups[this.activeDrag.target.key].classList.remove('toolbar-drop-over');
      this.activeDrag.target = null;
    }
  }
  release(event) {
    if (this.activeDrag?.pointerId === event.pointerId) {
      this.activeDrag.x = event.clientX;
      this.activeDrag.y = event.clientY;
      this.paint(false);
      const { id, target } = this.activeDrag;
      this.cancel();
      if (target) {
        this.onMove(id, target.key, target.index);
      }
    } else if (this.pendingPress?.pointerId === event.pointerId) {
      const id = this.pendingPress.id;
      this.cancel();
      this.onSelect(id);
    }
    if (!event.isTouch && this.dialog.hasPointerCapture(event.pointerId)) {
      this.dialog.releasePointerCapture(event.pointerId);
    }
  }
  cancel() {
    const pointerId = this.activeDrag?.pointerId ?? this.pendingPress?.pointerId;
    this.dialog.classList.remove('toolbar-editing-drag');
    this.dialog.style.height = '';
    if (this.pendingPress) {
      clearTimeout(this.pendingPress.timer);
    }
    this.pendingPress = null;
    if (this.animationFrame) {
      cancelAnimationFrame(this.animationFrame);
    }
    this.animationFrame = null;
    this.clearTarget();
    this.dragPreview?.remove();
    this.dragPreview = null;
    this.dropMarker = null;
    this.activeDrag = null;
    this.dialog.querySelector('.toolbar-lifted')?.classList.remove('toolbar-lifted');
    if (pointerId !== undefined && this.dialog.hasPointerCapture(pointerId)) {
      this.dialog.releasePointerCapture(pointerId);
    }
  }
  dispose() {
    this.cancel();
    this.eventController.abort();
  }
}
