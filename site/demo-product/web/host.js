'use strict';
// Host identity and native layout readiness are separate from viewport size.
// These hints choose UI behavior only; native bridges still verify token/origin/frame.
const BridgeHost = (() => {
  function kind(userAgent = globalThis.navigator?.userAgent || '') {
    const match = /BridgeMobile\/[\w.-]+-(iOS|Android)(?:\s|$)/.exec(userAgent);
    return match ? (match[1] === 'iOS' ? 'ios' : 'android') : 'browser';
  }
  function isNativeApp(userAgent) { return kind(userAgent) !== 'browser'; }
  function hasNativeLayout() {
    // Native injection can finish after deferred page scripts have executed.
    return !!globalThis.document?.documentElement?.classList.contains('bridge-mobile');
  }
  return Object.freeze({kind, isNativeApp, hasNativeLayout});
})();
if (typeof module === 'object' && module.exports) module.exports = BridgeHost;
