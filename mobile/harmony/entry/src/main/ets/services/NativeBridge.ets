import { webview } from '@kit.ArkWeb';
import { pasteboard } from '@kit.BasicServicesKit';
import { DocumentGuard } from './DocumentGuard';
import { NativePreferences } from './NativePreferences';

interface DisplayRequest {
  id: string;
  op: string;
  key: string;
  value?: Object;
}

export class NativeBridge {
  private controller: webview.WebviewController;
  private guard: DocumentGuard;
  private prefs: NativePreferences;
  private token: string;
  offline: ((value: string) => string | null) | undefined = undefined;
  systemBars: ((top: string, bottom: string, current: () => boolean) => void) | undefined = undefined;
  constructor(controller: webview.WebviewController, guard: DocumentGuard, prefs: NativePreferences, token: string) {
    this.controller = controller; this.guard = guard; this.prefs = prefs; this.token = token;
  }

  prompt(url: string, message: string, value: string): string | null {
    if (!this.guard.accepts(url, this.controller.getUrl()) || !message.endsWith(':' + this.token)) { return null; }
    const action = message.slice(0, message.length - this.token.length - 1);
    if (action === 'codexbridge-offline') { return this.offline ? this.offline(value) : null; }
    if (action === 'codexbridge-system-bars' && value.length <= 128 && this.systemBars) {
      const colors = JSON.parse(value) as Record<string, string>;
      if (!/^#[a-f0-9]{6}$/i.test(colors['top']) || !/^#[a-f0-9]{6}$/i.test(colors['bottom'])) { return null; }
      const generation = this.guard.generation;
      this.systemBars(colors['top'], colors['bottom'], () => this.guard.accepts(this.controller.getUrl(), this.controller.getUrl(), generation));
      return 'saved';
    }
    if (action === 'codexbridge-copy') {
      if (value.length > 262144 || !this.guard.consumeTouch()) { return null; }
      pasteboard.getSystemPasteboard().setDataSync(pasteboard.createData(pasteboard.MIMETYPE_TEXT_PLAIN, value));
      return 'copied';
    }
    if (action === 'codexbridge-theme' && ['system', 'light', 'dark'].includes(value)) {
      this.prefs.save('theme', value).catch(() => {}); return 'saved';
    }
    if (action === 'codexbridge-language' && ['zh', 'en'].includes(value)) {
      this.prefs.save('language', value).catch(() => {}); return 'saved';
    }
    if (action === 'codexbridge-display' && value.length <= 65536) {
      const command = JSON.parse(value) as DisplayRequest;
      if (!/^[0-9]{1,16}$/.test(command.id) || !['read', 'write'].includes(command.op) ||
        !['appearance', 'composer-preferences'].includes(command.key)) { return null; }
      const generation = this.guard.generation;
      this.display(command, generation).catch(() => {}); return 'queued';
    }
    if (action === 'codexbridge-channels' || action === 'codexbridge-uu') { return '{}'; }
    if (action === 'codexbridge-modal') { return 'saved'; }
    return null;
  }

  private async display(command: DisplayRequest, generation: number): Promise<void> {
    let value: Object | null = null;
    if (command.op === 'write') {
      if (!command.value || typeof command.value !== 'object' || Array.isArray(command.value)) { return; }
      await this.prefs.save(command.key, JSON.stringify(command.value)); value = true;
    } else {
      const raw = this.prefs.read(command.key); value = raw ? JSON.parse(raw) as Object : null;
    }
    if (!this.guard.accepts(this.controller.getUrl(), this.controller.getUrl(), generation)) { return; }
    const reply = JSON.stringify({ id: command.id, ok: true, value });
    await this.controller.runJavaScript('window.BridgeMobileStorage?.receive(' + JSON.stringify(this.token) + ',' + reply + ')');
  }
}
