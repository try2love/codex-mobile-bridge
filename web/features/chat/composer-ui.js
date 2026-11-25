'use strict';

const ComposerUI = Object.freeze({
  t(text) {
    return BridgeI18n.t(text);
  },
  node(tag, cls = '', text) {
    const node = document.createElement(tag);
    node.className = cls;
    if (text !== undefined) {
      node.textContent = text;
    }
    return node;
  },
  attribute(node, name, value) {
    if (node.getAttribute(name) !== String(value)) {
      node.setAttribute(name, String(value));
    }
  },
  hidden(node, value) {
    if (node.hidden !== !!value) {
      node.hidden = !!value;
    }
  },
  textButton(label, action) {
    const button = this.node('button', 'secondary', this.t(label));
    button.type = 'button';
    button.onclick = action;
    return button;
  },
  icon(id, { permissionMode = '', collapsed = false, sendMode = 'queue' } = {}) {
    const icon = id === 'permissions' ? permissionIcon(permissionMode)
      : ComposerIcons.create(id === 'sendMode' ? sendMode : id);
    if (id === 'permissions') {
      icon.dataset.permissionMode = permissionMode;
    }
    if (id === 'collapse' && collapsed) {
      icon.style.transform = 'rotate(180deg)';
    }
    return icon;
  },
  button(label, id) {
    const button = this.node('button', 'toolbar-icon');
    button.type = 'button';
    this.attribute(button, 'aria-label', this.t(label));
    button.title = this.t(label);
    button.append(this.icon(id));
    return button;
  },
});
