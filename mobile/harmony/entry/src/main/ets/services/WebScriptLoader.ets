import { common } from '@kit.AbilityKit';
import { util } from '@kit.ArkTS';

const SCRIPT_FILES: string[] = [
  'channel-link.js',
  'mobile-ui.js',
  'mobile-clipboard.js',
  'mobile-system-bars.js',
  'mobile-session.js'
];

export class WebScriptLoader {
  static loadCombined(context: common.Context, token: string, language: string): string {
    const resMgr = context.resourceManager;
    const decoder = util.TextDecoder.create('utf-8', { ignoreBOM: true });
    let combined = '';
    for (let i = 0; i < SCRIPT_FILES.length; i++) {
      const name = SCRIPT_FILES[i];
      try {
        const bytes = resMgr.getRawFileContentSync(name);
        let text = decoder.decodeToString(bytes);
        text = text.split('__BRIDGE_CLIPBOARD_TOKEN__').join(token);
        text = text.split('__BRIDGE_NATIVE_LANGUAGE__').join(language);
        combined += text + '\n';
      } catch {
        // skip missing optional script
      }
    }
    return combined;
  }
}
