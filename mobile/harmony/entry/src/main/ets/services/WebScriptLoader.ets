import { common } from '@kit.AbilityKit';
import { util } from '@kit.ArkTS';

// Aplier's original PR #248 loader, bound to the shared sources at build time.
export class WebScriptLoader {
  static load(context: common.Context, token: string, language: string, base: string, includeOffline: boolean = true): string {
    const decoder = util.TextDecoder.create('utf-8', { ignoreBOM: true });
    let script = '';
    const names = ['channel-link.js', 'mobile-ui.js', 'mobile-clipboard.js', 'mobile-system-bars.js'];
    if (includeOffline) { names.push('mobile-offline.js'); }
    for (const name of names) {
      const source = decoder.decodeToString(context.resourceManager.getRawFileContentSync('generated/' + name));
      script += source.split('__BRIDGE_CLIPBOARD_TOKEN__').join(token)
        .split('__BRIDGE_NATIVE_LANGUAGE__').join(language) + '\n';
    }
    // A document-start rule may also match a frame. Only the chosen top-level
    // gateway document receives the secret or the native layout marker.
    return '(function(){if(window!==window.top)return;const base=' + JSON.stringify(base) +
      ',u=new URL(base);if(location.origin!==u.origin||!(location.pathname===u.pathname||location.pathname.startsWith(u.pathname.replace(/\\/$/,"")+"/")))return;\n' + script + '\n})();';
  }
}
