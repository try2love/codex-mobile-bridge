'use strict';

// A non-modal window scoped to one workbench tab. It never covers chat navigation.
class FloatingPanel {
  constructor({host, content, title, state, close, node, button}) {
    Object.assign(this, {host, state});
    this.element = node('section', 'wb-float'); this.element.setAttribute('role', 'region');
    this.element.setAttribute('aria-label', title);
    const bar = node('div', 'wb-float-bar');
    this.grip = button(title, () => {}, '拖动窗口；方向键移动'); this.grip.className = 'wb-float-grip';
    const handle = node('span', 'wb-drag-handle'); handle.setAttribute('aria-hidden', 'true');
    this.grip.replaceChildren(handle, node('span', 'wb-float-title', title));
    this.maximize = button('□', () => { state.maximized = !state.maximized; this.layout(); }, '最大化窗口');
    const reset = button('↺', () => { delete state.rect; state.maximized = false; this.layout(); }, '恢复默认大小');
    const dismiss = button('×', () => { this.destroy(); close(); }, '关闭详情');
    bar.append(this.grip, reset, this.maximize, dismiss);
    this.resize = button('⌟', () => {}, '调整窗口大小；方向键调整'); this.resize.className = 'wb-float-resize';
    this.top = node('div', 'wb-float-top'); this.top.setAttribute('aria-hidden', 'true');
    this.element.append(bar, content, this.top, this.resize); host.append(this.element);
    this.bind(this.grip, 'move'); this.bind(this.resize, 'resize'); this.bind(this.top, 'top');
    this.element.addEventListener('keydown', event => { if (event.key === 'Escape') { event.stopPropagation(); dismiss.click(); } });
    this.observer = new ResizeObserver(() => this.layout()); this.observer.observe(host); this.layout();
  }
  layout() {
    const width = this.host.clientWidth, height = this.host.clientHeight;
    if (!width || !height) return;
    const margin = 6, spaceW = Math.max(0, width - 2 * margin), spaceH = Math.max(0, height - 2 * margin);
    const mobile = width <= 720;
    const rect = this.state.rect || {w: Math.min(spaceW, 760), h: Math.min(spaceH, Math.max(240, height * .72)), x: mobile ? margin : Math.max(margin, width - 772), y: Math.max(margin, height * .25)};
    const w = this.state.maximized ? spaceW : Math.min(spaceW, Math.max(280, rect.w));
    const h = this.state.maximized ? spaceH : Math.min(spaceH, Math.max(180, rect.h));
    const x = this.state.maximized ? margin : Math.max(margin, Math.min(rect.x, width - w - margin));
    const y = this.state.maximized ? margin : Math.max(margin, Math.min(rect.y, height - h - margin));
    this.actual = {x, y, w, h};
    Object.assign(this.element.style, {left:x+'px', top:y+'px', width:w+'px', height:h+'px'});
    this.element.classList.toggle('maximized', !!this.state.maximized);
    this.maximize.textContent = this.state.maximized ? '❐' : '□';
    this.maximize.title = this.state.maximized ? '还原窗口' : '最大化窗口';
    this.maximize.setAttribute('aria-label', this.maximize.title);
    this.maximize.setAttribute('aria-pressed', String(!!this.state.maximized));
  }
  adjust(mode, start, dx, dy) {
    if (this.state.maximized) return;
    const rect = {...start};
    if (mode === 'move') { rect.x += dx; rect.y += dy; }
    else if (mode === 'top') { const bottom = rect.y + rect.h; rect.y = Math.max(6, Math.min(bottom - Math.min(180, this.host.clientHeight - 12), rect.y + dy)); rect.h = bottom - rect.y; }
    else { rect.w += dx; rect.h += dy; }
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
      const delta = {ArrowLeft:[-16,0],ArrowRight:[16,0],ArrowUp:[0,-16],ArrowDown:[0,16]}[event.key];
      if (!delta) return; event.preventDefault(); this.adjust(mode, this.actual, ...delta);
    });
  }
  destroy() { this.observer.disconnect(); this.element.remove(); }
}
