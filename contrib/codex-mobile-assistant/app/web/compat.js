'use strict';

// Older Android System WebViews can run the app syntax but lack these APIs.
// Load before every application script; leave native implementations untouched.
(function () {
  function define(target, name, value) {
    if (typeof target[name] !== 'function') {
      Object.defineProperty(target, name, {value: value, writable: true, configurable: true});
    }
  }

  function replaceChildren() {
    var doc = this.ownerDocument;
    var fragment = doc.createDocumentFragment();
    for (var i = 0; i < arguments.length; i++) {
      var node = arguments[i];
      fragment.appendChild(node instanceof Node ? node : doc.createTextNode(String(node)));
    }
    this.textContent = '';
    this.appendChild(fragment);
  }
  define(Element.prototype, 'replaceChildren', replaceChildren);
  define(DocumentFragment.prototype, 'replaceChildren', replaceChildren);

  define(Array.prototype, 'at', function (index) {
    if (this == null) throw new TypeError('Array.at requires an array');
    var items = Object(this);
    var length = Math.min(Math.max(Math.floor(Number(items.length)) || 0, 0), 9007199254740991);
    var offset = Number(index) || 0;
    offset = offset < 0 ? Math.ceil(offset) : Math.floor(offset);
    if (offset < 0) offset += length;
    return offset < 0 || offset >= length ? undefined : items[offset];
  });
  define(Object, 'hasOwn', function (object, key) {
    return Object.prototype.hasOwnProperty.call(object, key);
  });
  if (window.crypto && typeof window.crypto.getRandomValues === 'function') {
    define(window.crypto, 'randomUUID', function () {
      var bytes = new Uint8Array(16);
      window.crypto.getRandomValues(bytes);
      bytes[6] = (bytes[6] & 15) | 64;
      bytes[8] = (bytes[8] & 63) | 128;
      var hex = Array.prototype.map.call(bytes, function (byte) {
        return ('0' + byte.toString(16)).slice(-2);
      }).join('');
      return hex.slice(0, 8) + '-' + hex.slice(8, 12) + '-' + hex.slice(12, 16)
        + '-' + hex.slice(16, 20) + '-' + hex.slice(20);
    });
  }
}());
