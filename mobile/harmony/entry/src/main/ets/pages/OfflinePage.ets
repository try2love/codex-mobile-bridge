import { webview } from '@kit.ArkWeb';
import { common } from '@kit.AbilityKit';
import { util } from '@kit.ArkTS';
import { OfflineArchive, ArchiveAccount, ArchiveObject } from '../services/OfflineArchive';
import { OfflineShell } from '../services/OfflineShell';
import { HistoryTransfer } from '../services/HistoryTransfer';
import { DocumentGuard } from '../services/DocumentGuard';
import { NativeBridge } from '../services/NativeBridge';
import { NativePreferences } from '../services/NativePreferences';
import { WebScriptLoader } from '../services/WebScriptLoader';
import { MobileIdentity } from '../services/MobileIdentity';
import { SystemBars } from '../services/SystemBars';

@Component
export struct OfflinePage {
  archive: OfflineArchive | null = null;
  transfers: HistoryTransfer | null = null;
  prefs: NativePreferences = new NativePreferences();
  account: ArchiveAccount = new ArchiveAccount();
  onClose: () => void = () => {};
  onReconnect: () => void = () => {};
  @State error: string = '';
  @State scripts: ScriptItem[] = [];
  private controller: webview.WebviewController = new webview.WebviewController();
  private shell: OfflineShell | null = null;
  private token: string = util.generateRandomUUID();
  private generation: number = 0;
  private foreground: boolean = true;
  private guard: DocumentGuard = new DocumentGuard();
  private bridge: NativeBridge | null = null;
  private lifecycle = (foreground: boolean): void => { this.foreground = foreground; this.guard.foreground = foreground; };

  aboutToAppear(): void {
    const context = getContext(this) as common.UIAbilityContext;
    context.eventHub.on('bridge-foreground', this.lifecycle);
    try {
      this.shell = new OfflineShell(context);
      this.guard.navigate(OfflineShell.ORIGIN);
      this.bridge = new NativeBridge(this.controller, this.guard, this.prefs, this.token);
      this.bridge.systemBars = (top, _bottom, current) => { SystemBars.apply(context, top, current).catch(() => {}); };
      const script = '(function(){if(window!==window.top||location.origin!==' + JSON.stringify(OfflineShell.ORIGIN) + ')return;' +
        'window.OfflineChatHost=Object.freeze({postMessage:value=>window.prompt(' + JSON.stringify('codexbridge-reader:' + this.token) + ',value)});})();';
      // The offline reader has its own transport. Injecting mobile-offline here
      // would incorrectly suspend that transport while no gateway is connected.
      this.scripts = [{ script: WebScriptLoader.load(context, this.token, this.prefs.read('language') || 'zh', OfflineShell.ORIGIN, false) + script, scriptRules: ['*'] }];
    } catch { this.error = '无法读取离线资源，请重新编译安装包'; }
  }
  aboutToDisappear(): void {
    this.generation++; this.foreground = false; this.guard.foreground = false;
    (getContext(this) as common.UIAbilityContext).eventHub.off('bridge-foreground', this.lifecycle);
  }
  private async read(value: string, generation: number): Promise<void> {
    const event = JSON.parse(value) as ArchiveObject, id = event['id'] as string;
    const archive = this.archive;
    if (!archive || !/^[0-9]{1,16}$/.test(id)) { return; }
    try {
      let result: Object | null;
      const action = event['action'] as string;
      if (action === 'close') { this.onClose(); return; }
      if (action === 'reconnect') { this.onReconnect(); return; }
      if (action === 'attachmentOpen' && this.transfers) {
        if (!this.guard.consumeTouch()) { throw new Error('请点击附件后重试'); }
        result = await this.transfers.exportAttachment(getContext(this) as common.UIAbilityContext, this.account, event,
          () => generation === this.generation && this.foreground);
      } else {
        result = await archive.serial(async () => {
          if (generation !== this.generation || !this.foreground) { throw new Error('离线页面已关闭'); }
          const account = await archive.current(this.account, false);
          if (OfflineArchive.composerAction(action)) { return archive.composer(account, action, event); }
          if (action === 'list') {
            const listing = await archive.list(account);
            const saved = await archive.db.rows('SELECT content FROM appearance WHERE account=?', [account.id]);
            const raw = this.prefs.read('appearance') || (saved.length ? saved[0][0] as string : '');
            if (raw) { listing['appearance'] = JSON.parse(raw) as Object; }
            listing['lang'] = this.prefs.read('language') || 'zh';
            listing['nativeTheme'] = { theme: this.prefs.read('theme') || 'system' }; return listing;
          }
          if (action === 'session') { return archive.page(account, event['sessionId'] as string, event); }
          if (action === 'attachment' && this.transfers) { return this.transfers.attachment(account, event); }
          throw new Error('此附件未保存到本机，请联网上看。');
        });
      }
      if (generation === this.generation && this.foreground && this.controller.getUrl().startsWith(OfflineShell.ORIGIN + '/')) {
        await this.controller.runJavaScript('window.OfflineChatReader?.receive(' + JSON.stringify(id) + ',' + JSON.stringify(result) + ')');
      }
    } catch {
      if (generation === this.generation && this.foreground) {
        await this.controller.runJavaScript('window.OfflineChatReader?.receive(' + JSON.stringify(id) + ',{"error":"无法读取离线缓存"})');
      }
    }
  }
  build() {
    Column() {
      Row() {
        Button('返回').onClick(() => { this.controller.runJavaScript('window.OfflineChatReader?.back()').catch(() => { this.onClose(); }); })
        Text(this.account.status === 'valid' ? '离线' : '离线 · 只读').layoutWeight(1).fontSize(14)
        Button('显示设置').onClick(() => { this.controller.runJavaScript('document.querySelector("[data-open-appearance]")?.click()').catch(() => {}); })
        Button('重新连接').onClick(() => { this.onReconnect(); })
      }.padding(8).width('100%')
      if (this.error) { Text(this.error).padding(16) }
      if (this.shell) {
        Web({ src: OfflineShell.ORIGIN + '/offline-reader.html', controller: this.controller })
          .width('100%').layoutWeight(1).javaScriptAccess(true).domStorageAccess(true).fileAccess(false).mixedMode(MixedMode.None)
          .runJavaScriptOnDocumentStart(this.scripts)
          .onControllerAttached(() => { this.controller.setCustomUserAgent(this.controller.getUserAgent() + ' ' + MobileIdentity.userAgent()); })
          .onTouch(event => { if (event.type === TouchType.Up) { this.guard.touch(); } })
          .onLoadIntercept(event => !event || !OfflineShell.contains(event.data.getRequestUrl()))
          .onInterceptRequest(event => this.shell?.response(event?.request.getRequestUrl() || '', event?.request.getRequestMethod() || '') || null)
          .onPageBegin(() => { this.generation++; this.guard.navigate(OfflineShell.ORIGIN); })
          .onPrompt(event => {
            if (!event) { return false; }
            try {
              if (event.message === 'codexbridge-reader:' + this.token) {
                if (event.value.length > 2000000 || !this.guard.accepts(event.url, this.controller.getUrl())) { event.result.handleCancel(); return true; }
                event.result.handlePromptConfirm('queued'); this.read(event.value, this.generation).catch(() => {});
              } else {
                const result = this.bridge?.prompt(event.url, event.message, event.value);
                if (result === null || result === undefined) { event.result.handleCancel(); } else { event.result.handlePromptConfirm(result); }
              }
            } catch { event.result.handleCancel(); }
            return true;
          })
          .onShowFileSelector(event => { event?.result.handleFileList([]); return true; })
          .onSslErrorEventReceive(event => { event?.handler.handleCancel(); })
      }
    }.width('100%').height('100%').backgroundColor($r('sys.color.ohos_id_color_background'))
  }
}
