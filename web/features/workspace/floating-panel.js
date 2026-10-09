'use strict';

// A non-modal window scoped to one workbench tab. It never covers chat navigation.
class FloatingPanel {
  constructor({host, content, title, state, close, node, button}) {
    Object.assign(this, {host, state});
    this.element = node('section', 'wb-float'); this.element.setAttribute('role', 'region');
    this.element.setAttribute('aria-label', title);
    const bar = node('div', 'wb-float-bar');
    this.grip = button(title, () => {}, BridgeI18n.t('调整上边缘高度；上下方向键调整')); this.grip.className = 'wb-float-grip';
    const handle = node('span', 'wb-drag-handle'); handle.setAttribute('aria-hidden', 'true');
    this.grip.replaceChildren(handle, node('span', 'wb-float-title', title));
    this.maximize = button('□', () => { state.maximized = !state.maximized; this.layout(); }, BridgeI18n.t('展开至完整高度'));
    const reset = button('↺', () => { delete state.rect; state.maximized = false; this.layout(); }, BridgeI18n.t('恢复默认高度'));
    const dismiss = button('×', () => { this.destroy(); close(); }, BridgeI18n.t('关闭详情'));
    dismiss.classList.add('wb-float-close');
    bar.append(this.grip, reset, this.maximize, dismiss);
    this.resize = button('', () => {}, BridgeI18n.t('调整下边缘高度；上下方向键调整')); this.resize.className = 'wb-float-resize';
    const bottomHandle = node('span', 'wb-drag-handle'); bottomHandle.setAttribute('aria-hidden', 'true'); this.resize.append(bottomHandle);
    this.element.append(bar, content, this.resize); host.append(this.element);
    this.bind(this.grip, 'top'); this.bind(this.resize, 'bottom');
    this.element.addEventListener('keydown', event => { if (event.key === 'Escape') { event.stopPropagation(); dismiss.click(); } });
    this.observer = new ResizeObserver(() => this.layout()); this.observer.observe(host); this.layout();
  }
  layout() {
    const width = this.host.clientWidth, height = this.host.clientHeight;
    if (!width || !height) return;
    const margin = 6, spaceW = Math.max(0, width - 2 * margin), spaceH = Math.max(0, height - 2 * margin);
    this.minimumHeight = Math.min(280, spaceH);
    const rect = this.state.rect || {h: Math.max(this.minimumHeight, height * .72), y: height * .25};
    // Width follows the host; users resize height only.
    const w = spaceW, x = margin;
    const h = this.state.maximized ? spaceH : Math.min(spaceH, Math.max(this.minimumHeight, rect.h));
    const y = this.state.maximized ? margin : Math.max(margin, Math.min(rect.y, height - h - margin));
    this.actual = {x, y, w, h};
    Object.assign(this.element.style, {left:x+'px', top:y+'px', width:w+'px', height:h+'px'});
    this.element.classList.toggle('maximized', !!this.state.maximized);
    this.maximize.textContent = this.state.maximized ? '❐' : '□';
    this.maximize.title = this.state.maximized ? BridgeI18n.t('还原高度') : BridgeI18n.t('展开至完整高度');
    this.maximize.setAttribute('aria-label', this.maximize.title);
    this.maximize.setAttribute('aria-pressed', String(!!this.state.maximized));
  }
  adjust(mode, start, dx, dy) {
    if (this.state.maximized) return;
    const rect = {...start};
    if (mode === 'top') {
      const bottom = start.y + start.h;
      rect.y = Math.max(6, Math.min(bottom - this.minimumHeight, start.y + dy));
      rect.h = bottom - rect.y;
    } else if (mode === 'bottom') {
      rect.h = Math.max(this.minimumHeight, Math.min(this.host.clientHeight - 6 - start.y, start.h + dy));
    } else return;
    this.state.rect = rect; this.layout(); this.state.rect = {...this.actual};
  }
  bind(handle, mode) {
    let drag;
    handle.addEventListener('pointerdown', event => {
      if (event.button !== 0 || this.state.maximized) return;
      event.preventDefault(); handle.focus?.({preventScroll:true});
      drag = {id:event.pointerId, x:event.clientX, y:event.clientY, rect:{...this.actual}};
      handle.setPointerCapture(event.pointerId); this.element.classList.add('dragging');
    });
    handle.addEventListener('pointermove', event => { if (drag?.id === event.pointerId) this.adjust(mode, drag.rect, event.clientX - drag.x, event.clientY - drag.y); });
    const end = () => { drag = null; this.element.classList.remove('dragging'); };
    handle.addEventListener('lostpointercapture', end); handle.addEventListener('pointercancel', end);
    handle.addEventListener('pointerup', event => { if (handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId); end(); });
    handle.addEventListener('keydown', event => {
      const delta = {ArrowUp:[0,-16],ArrowDown:[0,16]}[event.key];
      if (!delta) return; event.preventDefault(); this.adjust(mode, this.actual, ...delta);
    });
  }
  destroy() { this.observer.disconnect(); this.element.remove(); }
}
