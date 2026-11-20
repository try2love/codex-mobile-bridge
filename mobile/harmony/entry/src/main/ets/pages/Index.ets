import { webview } from '@kit.ArkWeb';
import { scanBarcode, scanCore } from '@kit.ScanKit';
import { pasteboard, BusinessError } from '@kit.BasicServicesKit';
import { picker, fileIo } from '@kit.CoreFileKit';
import { common } from '@kit.AbilityKit';
import { util } from '@kit.ArkTS';
import { promptAction } from '@kit.ArkUI';
import { hilog } from '@kit.PerformanceAnalysisKit';
import { sharedStore } from '../entryability/EntryAbility';
import { ComputerRecord } from '../services/ComputerStore';
import { GatewayUrl, DeepLinkTarget } from '../services/GatewayUrl';
import { ChannelManager, MobileEventItem } from '../services/ChannelManager';
import { PushManager } from '../services/PushManager';
import { PendingDeepLink } from '../services/PendingDeepLink';
import { WebScriptLoader } from '../services/WebScriptLoader';

interface ChannelMenuRow {
  url: string;
  label: string;
  latencyMs: number;
}

interface ChannelMenuPayload {
  mode: string;
  current: string;
  localNetwork: boolean;
  rows: ChannelMenuRow[];
}

@Entry
@Component
struct Index {
  @State computers: ComputerRecord[] = [];
  @State inWeb: boolean = false;
  @State activeOrigin: string = '';
  @State activeName: string = '';
  @State webUrl: string = '';
  @State currentWebUrl: string = '';
  @State statusText: string = '';
  @State activeModal: string = '';
  @State manualInput: string = '';
  @State channelInput: string = '';
  @State showInboxDialog: boolean = false;
  @State inboxEvents: MobileEventItem[] = [];
  @State inboxLoading: boolean = false;
  @State pushEnabled: boolean = true;
  @State hmsTokenPreview: string = '';
  @State hmsErrorText: string = '';
  @State managingComputer: ComputerRecord | undefined = undefined;
  @State webLoadError: string = '';
  @State webLoading: boolean = false;

  private webController: webview.WebviewController = new webview.WebviewController();
  private bridgeToken: string = util.generateRandomUUID(true);
  private pendingDeepLink: DeepLinkTarget | undefined = undefined;
  private launchPerfStartMs: number = Date.now();
  private autoFailoverTriedUrl: string = '';
  private webControllerAttached: boolean = false;
  private pendingLoadUrl: string = '';

  aboutToAppear(): void {
    this.launchPerfStartMs = Date.now();
    hilog.info(0x0000, 'CodexBridgePerf', '[PERF] Index.aboutToAppear t=0');
    const context = getContext(this) as common.UIAbilityContext;
    sharedStore.init(context).then(async () => {
      hilog.info(0x0000, 'CodexBridgePerf', '[PERF] store.init done +%{public}dms', Date.now() - this.launchPerfStartMs);
      this.computers = sharedStore.listComputers();
      this.pushEnabled = sharedStore.isPushEnabled();
      this.refreshPushStatus();
      const pending = PendingDeepLink.consume();
      if (pending !== undefined) {
        hilog.info(0x0000, 'CodexBridgePerf', '[PERF] openDeepLink start +%{public}dms', Date.now() - this.launchPerfStartMs);
        this.openDeepLink(pending);
      } else {
        const last = sharedStore.getLastWorkspace();
        if (last !== undefined) {
          const matched = this.computers.find((c) => c.origin === last.origin || c.endpoints.includes(last.origin));
          if (matched !== undefined) {
            const base = matched.activeUrl || matched.origin;
            const targetUrl = GatewayUrl.sameOrigin(last.url, base) ? last.url : (base + '/');
            this.openWebUrl(base, matched.name || last.name, targetUrl);
          }
        }
      }
      this.probeAllComputers().catch(() => {});
    }).catch(() => {});

    PendingDeepLink.setListener((target: DeepLinkTarget) => {
      const consumed = PendingDeepLink.consume();
      if (consumed !== undefined) {
        this.openDeepLink(consumed);
      } else {
        this.openDeepLink(target);
      }
    });
  }

  aboutToDisappear(): void {
    PendingDeepLink.setListener(undefined);
  }

  onBackPress(): boolean {
    if (this.showInboxDialog) {
      this.showInboxDialog = false;
      return true;
    }
    if (this.activeModal.length > 0) {
      this.activeModal = '';
      return true;
    }
    if (this.inWeb) {
      if (this.webLoadError.length > 0) {
        this.closeWebView();
        return true;
      }
      const js = '(() => { const d = document.querySelector("dialog[open]"); if (d) { d.close(); return "true"; } if (typeof window.BridgeNavigation?.back === "function" && window.BridgeNavigation.back()) return "true"; if (window.BridgeWorkbench?.back?.()) return "true"; if (document.getElementById("app")?.classList.contains("chat-open")) { document.getElementById("back")?.click(); return "true"; } return "false"; })()';
      try {
        this.webController.runJavaScript(js, (err, res) => {
          if (err || res !== '"true"') {
            try {
              const ctx = getContext(this) as common.UIAbilityContext;
              ctx.moveAbilityToBackground().catch(() => {});
            } catch {
              // keep workspace open
            }
          }
        });
      } catch {
        // keep workspace open
      }
      return true;
    }
    return false;
  }

  private refreshPushStatus(): void {
    const status = PushManager.getStatus();
    const tok = status.hmsToken || sharedStore.getHmsToken();
    this.hmsTokenPreview = tok.length > 20 ? `${tok.slice(0, 12)}...${tok.slice(-8)}` : (tok || '未获取（需配置 AGC 签名与 Push 权限）');
    this.hmsErrorText = status.hmsError;
  }

  private async probeAllComputers(): Promise<void> {
    const list = sharedStore.listComputers();
    if (list.length === 0) {
      return;
    }
    const updated: ComputerRecord[] = [];
    for (let i = 0; i < list.length; i++) {
      const item = list[i];
      const best = await ChannelManager.findBestEndpoint(item.endpoints);
      if (best !== undefined) {
        updated.push({
          origin: item.origin,
          name: item.name,
          endpoints: best.endpoints.length > 0 ? best.endpoints : item.endpoints,
          activeUrl: best.url,
          deviceId: best.deviceId || item.deviceId,
          lastLatencyMs: best.latencyMs,
          online: true
        });
      } else {
        updated.push({
          origin: item.origin,
          name: item.name,
          endpoints: item.endpoints,
          activeUrl: item.activeUrl || item.origin,
          deviceId: item.deviceId,
          lastLatencyMs: -1,
          online: false
        });
      }
    }
    this.computers = updated;
    await sharedStore.saveComputers(updated);
    if (this.inWeb && this.webLoading) {
      const currentCard = updated.find((c) => c.origin === this.activeOrigin || c.endpoints.includes(this.activeOrigin));
      if (currentCard && currentCard.online && currentCard.activeUrl && !GatewayUrl.sameOrigin(currentCard.activeUrl, this.activeOrigin)) {
        this.openWebUrl(currentCard.activeUrl, currentCard.name, currentCard.activeUrl + '/');
      }
    }
  }

  private async startQrScan(): Promise<void> {
    try {
      const options: scanBarcode.ScanOptions = {
        scanTypes: [scanCore.ScanType.QR_CODE],
        enableMultiMode: true,
        enableAlbum: true
      };
      const result = await scanBarcode.startScanForResult(getContext(this) as common.UIAbilityContext, options);
      if (result.originalValue && result.originalValue.length > 0) {
        await this.connectWithInput(result.originalValue);
      }
    } catch (err) {
      const bErr = err as BusinessError;
      if (bErr.code !== 1000500002) {
        this.showToast('扫码失败，请尝试手动输入网关地址');
      }
    }
  }

  private async connectWithInput(raw: string): Promise<void> {
    try {
      let normalized = raw.trim();
      if (!/^https?:\/\//i.test(normalized)) {
        const hostOnly = normalized.split('/')[0].split(':')[0];
        normalized = (GatewayUrl.isLocalHost(hostOnly) ? 'http://' : 'https://') + normalized;
      } else if (/^https:\/\//i.test(normalized)) {
        const hostOnly = normalized.replace(/^https:\/\//i, '').split('/')[0].split(':')[0];
        if (GatewayUrl.isLocalHost(hostOnly)) {
          normalized = 'http://' + normalized.slice(8);
        }
      }
      const targetUrl = GatewayUrl.connection(normalized);
      const baseOrigin = GatewayUrl.origin(targetUrl.split('#')[0]);
      this.statusText = '正在探测网关...';
      const probe = await ChannelManager.probeEndpoint(baseOrigin);
      const endpoints = probe.endpoints.length > 0 ? probe.endpoints : [baseOrigin];
      const nextList = await sharedStore.upsertComputer(baseOrigin, '', endpoints, probe.deviceId);
      this.computers = nextList;
      this.statusText = '';
      this.openWebUrl(baseOrigin, baseOrigin.replace(/^https?:\/\//, ''), targetUrl);
    } catch (err) {
      this.statusText = '';
      const msg = err instanceof Error ? err.message : '地址格式无效';
      this.showToast(msg);
    }
  }

  private openComputer(comp: ComputerRecord): void {
    this.autoFailoverTriedUrl = '';
    const chosen = comp.activeUrl || comp.origin;
    this.openWebUrl(chosen, comp.name, chosen + '/');
    this.probeAllComputers().catch(() => {});
  }

  private async tryFallbackEndpoint(failedUrl: string, errorDesc: string): Promise<void> {
    const failedOrigin = this.activeOrigin;
    const comp = this.computers.find((c) => c.origin === failedOrigin || c.endpoints.includes(failedOrigin));
    hilog.info(0x0000, 'CodexBridgeFailover', 'failedUrl=%{public}s failedOrigin=%{public}s endpoints=%{public}s', failedUrl, failedOrigin, comp ? comp.endpoints.join(',') : 'none');
    if (comp !== undefined && comp.endpoints.length > 1 && this.autoFailoverTriedUrl !== failedOrigin) {
      this.autoFailoverTriedUrl = failedOrigin;
      this.webLoading = true;
      this.webLoadError = '';
      try {
        const best = await ChannelManager.findBestEndpoint(comp.endpoints);
        hilog.info(0x0000, 'CodexBridgeFailover', 'best=%{public}s', best ? `${best.url} (${best.latencyMs}ms)` : 'undefined');
        if (best !== undefined && !GatewayUrl.sameOrigin(best.url, failedOrigin)) {
          this.showToast(`已自动切换到可用地址：${best.url}`);
          this.openWebUrl(best.url, comp.name, best.url + '/');
          this.probeAllComputers().catch(() => {});
          return;
        }
      } catch {
        // fall through to show error page
      }
    }
    this.webLoading = false;
    this.webLoadError = errorDesc;
  }

  private openWebUrl(baseOrigin: string, name: string, fullUrl: string): void {
    const prevUrl = this.webUrl;
    const hadError = this.webLoadError.length > 0;
    this.activeOrigin = baseOrigin;
    this.activeName = name || baseOrigin.replace(/^https?:\/\//, '');
    this.currentWebUrl = fullUrl;
    this.webLoadError = '';
    this.webLoading = true;
    this.inWeb = true;
    sharedStore.setLastWorkspace(baseOrigin, this.activeName, fullUrl).catch(() => {});
    if (prevUrl.length === 0 || !this.webControllerAttached) {
      this.webUrl = fullUrl;
      this.pendingLoadUrl = fullUrl;
    } else if (prevUrl !== fullUrl || hadError || this.currentWebUrl !== fullUrl) {
      this.pendingLoadUrl = '';
      try {
        this.webController.loadUrl(fullUrl);
      } catch {
        this.webUrl = fullUrl;
      }
    } else {
      this.webLoading = false;
    }
  }

  private closeWebView(): void {
    this.inWeb = false;
    this.probeAllComputers().catch(() => {});
  }

  private async openDeepLink(target: DeepLinkTarget): Promise<void> {
    const matched = this.computers.find((c) => c.origin === target.origin || c.endpoints.includes(target.origin));
    const base = matched ? (matched.activeUrl || matched.origin) : target.origin;
    const name = matched ? matched.name : base.replace(/^https?:\/\//, '');
    if (matched === undefined) {
      try {
        const cleanOrigin = GatewayUrl.origin(target.origin);
        this.computers = await sharedStore.upsertComputer(cleanOrigin, name, [cleanOrigin], '');
      } catch {
        // ignore invalid origin
      }
    }

    const sameComputer = this.webUrl.length > 0 &&
      (GatewayUrl.sameOrigin(this.activeOrigin, base) ||
        (matched !== undefined && matched.endpoints.some((ep) => GatewayUrl.sameOrigin(this.activeOrigin, ep))));
    if (sameComputer) {
      this.inWeb = true;
      const hash = GatewayUrl.chatHash(target.thread, target.host);
      const js = `(() => typeof window.BridgeOpenChatLink === "function" && window.BridgeOpenChatLink(${JSON.stringify(hash)}))()`;
      try {
        this.webController.runJavaScript(js, (err, res) => {
          if (err || res !== 'true') {
            const fullUrl = GatewayUrl.chatUrl(this.activeOrigin || base, target.thread, target.host);
            this.openWebUrl(this.activeOrigin || base, name, fullUrl);
          } else {
            const fullUrl = GatewayUrl.chatUrl(this.activeOrigin || base, target.thread, target.host);
            this.currentWebUrl = fullUrl;
            sharedStore.setLastWorkspace(this.activeOrigin || base, name, fullUrl).catch(() => {});
          }
        });
        return;
      } catch {
        // fall through to full load
      }
    }

    try {
      const fullUrl = GatewayUrl.chatUrl(base, target.thread, target.host);
      this.openWebUrl(base, name, fullUrl);
    } catch {
      this.showToast('通知会话链接无效');
    }
  }

  private handleWebPrompt(url: string, message: string, value: string): string | undefined {
    if (!GatewayUrl.sameOrigin(url, this.activeOrigin)) {
      return undefined;
    }
    if (message === `codexbridge-copy:${this.bridgeToken}`) {
      try {
        const pasteData = pasteboard.createData(pasteboard.MIMETYPE_TEXT_PLAIN, value);
        pasteboard.getSystemPasteboard().setDataSync(pasteData);
        return 'copied';
      } catch {
        return '';
      }
    }
    if (message === `codexbridge-session:${this.bridgeToken}`) {
      webview.WebCookieManager.saveCookieAsync().catch(() => {});
      ChannelManager.fetchAuth(this.activeOrigin).then(async (auth) => {
        if (auth.authenticated) {
          this.computers = await sharedStore.upsertComputer(
            this.activeOrigin,
            auth.computerName || this.activeName,
            auth.endpoints.length > 0 ? auth.endpoints : [this.activeOrigin],
            auth.deviceId
          );
          if (auth.computerName) {
            this.activeName = auth.computerName;
          }
          await PushManager.registerOrigin(sharedStore, this.activeOrigin);
          this.refreshPushStatus();
        }
      }).catch(() => {});
      return 'saved';
    }
    if (message === `codexbridge-logout:${this.bridgeToken}`) {
      webview.WebCookieManager.saveCookieAsync().catch(() => {});
      return 'cleared';
    }
    if (message === `codexbridge-auth-lost:${this.bridgeToken}`) {
      this.webController.loadUrl(this.activeOrigin + '/');
      return 'recovering';
    }
    if (message === `codexbridge-theme:${this.bridgeToken}`) {
      if (value === 'system' || value === 'light' || value === 'dark') {
        sharedStore.setTheme(value).catch(() => {});
      }
      return 'saved';
    }
    if (message === `codexbridge-language:${this.bridgeToken}`) {
      if (value === 'zh' || value === 'en') {
        sharedStore.setLanguage(value).catch(() => {});
      }
      return 'saved';
    }
    if (message === `codexbridge-channels:${this.bridgeToken}`) {
      const comp = this.computers.find((c) => c.origin === this.activeOrigin || c.endpoints.includes(this.activeOrigin));
      const rows: ChannelMenuRow[] = (comp ? comp.endpoints : [this.activeOrigin]).map((ep): ChannelMenuRow => ({
        url: ep,
        label: ep.startsWith('http://') ? '局域网' : 'HTTPS',
        latencyMs: ep === this.activeOrigin && comp ? comp.lastLatencyMs : -1
      }));
      const menu: ChannelMenuPayload = {
        mode: 'auto',
        current: this.activeOrigin,
        localNetwork: true,
        rows: rows
      };
      return JSON.stringify(menu);
    }
    if (message === `codexbridge-modal:${this.bridgeToken}` ||
      message === `codexbridge-system-bars:${this.bridgeToken}` ||
      message === `codexbridge-chat-visible:${this.bridgeToken}` ||
      message === `codexbridge-channel-failure:${this.bridgeToken}`) {
      return 'ok';
    }
    return undefined;
  }

  private async openInbox(): Promise<void> {
    if (!this.activeOrigin) {
      return;
    }
    this.showInboxDialog = true;
    this.inboxLoading = true;
    const snap = await ChannelManager.fetchMobileEvents(this.activeOrigin);
    this.inboxLoading = false;
    if (snap && Array.isArray(snap.events)) {
      this.inboxEvents = [...snap.events].reverse();
    } else {
      this.inboxEvents = [];
    }
  }

  private async openFileChooser(result: FileSelectorResult): Promise<void> {
    try {
      const ctx = getContext(this) as common.UIAbilityContext;
      const docPicker = new picker.DocumentViewPicker(ctx);
      const uris = await docPicker.select({ maxSelectNumber: 5 });
      if (!uris || uris.length === 0) {
        result.handleFileList([]);
        return;
      }
      const localPaths: string[] = [];
      hilog.info(0x0000, 'CodexBridgeUpload', '[UPLOAD] picker selected count=%{public}d', uris.length);
      for (let i = 0; i < uris.length; i++) {
        const uri = uris[i];
        const t0 = Date.now();
        try {
          const dotIdx = uri.lastIndexOf('.');
          const rawExt = dotIdx >= 0 ? uri.slice(dotIdx).replace(/[^a-zA-Z0-9.]/g, '') : '';
          const ext = rawExt.length > 0 && rawExt.length <= 8 ? rawExt : '';
          const destPath = `${ctx.cacheDir}/upload_${Date.now()}_${i}${ext}`;
          const src = fileIo.openSync(uri, fileIo.OpenMode.READ_ONLY);
          try {
            fileIo.copyFileSync(src.fd, destPath);
            const stat = fileIo.statSync(destPath);
            hilog.info(0x0000, 'CodexBridgeUpload', '[UPLOAD] copied uri=%{public}s -> %{public}s size=%{public}d ms=%{public}d', uri, destPath, stat.size, Date.now() - t0);
            localPaths.push(destPath);
          } finally {
            fileIo.closeSync(src);
          }
        } catch (err) {
          hilog.error(0x0000, 'CodexBridgeUpload', '[UPLOAD] copy failed uri=%{public}s err=%{public}s', uri, JSON.stringify(err));
          localPaths.push(uri);
        }
      }
      result.handleFileList(localPaths);
    } catch {
      result.handleFileList([]);
    }
  }

  private showToast(msg: string): void {
    try {
      promptAction.showToast({ message: msg, duration: 2200 });
    } catch {
      // ignore
    }
  }

  build() {
    Stack() {
      Column() {
        this.HomeView()
      }
      .width('100%')
      .height('100%')
      .visibility(this.inWeb ? Visibility.None : Visibility.Visible)

      if (this.webUrl.length > 0) {
        Column() {
          this.WebWorkbenchView()
        }
        .width('100%')
        .height('100%')
        .visibility(this.inWeb ? Visibility.Visible : Visibility.None)
      }
    }
    .width('100%')
    .height('100%')
    .backgroundColor('#F6F7F9')
  }

  @Builder
  HomeView() {
    Column() {
      Row() {
        Text('Codex Bridge')
          .fontSize(22)
          .fontWeight(FontWeight.Bold)
          .fontColor('#111827')
          .layoutWeight(1)

        Button('刷新')
          .fontSize(13)
          .height(34)
          .backgroundColor('#E5E7EB')
          .fontColor('#111827')
          .margin({ right: 8 })
          .onClick(() => {
            this.probeAllComputers();
          })

        Button('推送设置')
          .fontSize(13)
          .height(34)
          .backgroundColor('#E5E7EB')
          .fontColor('#111827')
          .onClick(() => {
            this.refreshPushStatus();
            this.activeModal = 'settings';
          })
      }
      .width('100%')
      .padding({ left: 16, right: 16, top: 14, bottom: 12 })

      if (this.statusText.length > 0) {
        Text(this.statusText)
          .fontSize(13)
          .fontColor('#2563EB')
          .padding({ left: 16, right: 16, bottom: 8 })
          .width('100%')
      }

      if (this.computers.length === 0) {
        Column() {
          Text('尚未添加电脑')
            .fontSize(17)
            .fontWeight(FontWeight.Medium)
            .fontColor('#374151')
            .margin({ bottom: 8 })
          Text('请扫描桌面端“Codex Mobile Bridge”概览页的配对二维码，或手动输入网关地址。')
            .fontSize(14)
            .fontColor('#6B7280')
            .textAlign(TextAlign.Center)
            .padding({ left: 28, right: 28 })
        }
        .layoutWeight(1)
        .justifyContent(FlexAlign.Center)
        .width('100%')
      } else {
        List({ space: 10 }) {
          ForEach(this.computers, (comp: ComputerRecord) => {
            ListItem() {
              Column() {
                Row() {
                  Column() {
                    Text(comp.name)
                      .fontSize(17)
                      .fontWeight(FontWeight.Bold)
                      .fontColor('#111827')
                      .maxLines(1)
                    Text(comp.activeUrl || comp.origin)
                      .fontSize(12)
                      .fontColor('#6B7280')
                      .margin({ top: 3 })
                      .maxLines(1)
                  }
                  .alignItems(HorizontalAlign.Start)
                  .layoutWeight(1)

                  Text(comp.online ? `● 在线 ${comp.lastLatencyMs}ms` : '○ 离线')
                    .fontSize(12)
                    .fontColor(comp.online ? '#059669' : '#9CA3AF')
                    .fontWeight(FontWeight.Medium)
                }
                .width('100%')

                Row() {
                  Button('打开工作台')
                    .fontSize(13)
                    .height(34)
                    .backgroundColor('#111827')
                    .fontColor('#FFFFFF')
                    .layoutWeight(1)
                    .margin({ right: 8 })
                    .onClick(() => {
                      this.openComputer(comp);
                    })

                  Button('信道 / 管理')
                    .fontSize(13)
                    .height(34)
                    .backgroundColor('#F3F4F6')
                    .fontColor('#374151')
                    .margin({ right: 8 })
                    .onClick(() => {
                      this.managingComputer = comp;
                      this.activeModal = 'channel';
                    })

                  Button('删除')
                    .fontSize(13)
                    .height(34)
                    .backgroundColor('#FEE2E2')
                    .fontColor('#DC2626')
                    .onClick(() => {
                      AlertDialog.show({
                        title: '移除电脑',
                        message: `确定移除「${comp.name}」（${comp.activeUrl || comp.origin}）吗？`,
                        primaryButton: {
                          value: '取消',
                          action: () => {}
                        },
                        secondaryButton: {
                          value: '删除',
                          fontColor: '#DC2626',
                          action: () => {
                            sharedStore.removeComputer(comp.origin).then((nextList) => {
                              this.computers = nextList;
                            }).catch(() => {});
                          }
                        }
                      });
                    })
                }
                .width('100%')
                .margin({ top: 12 })
              }
              .padding(14)
              .backgroundColor('#FFFFFF')
              .borderRadius(14)
            }
          }, (comp: ComputerRecord) => comp.origin)
        }
        .layoutWeight(1)
        .width('100%')
        .padding({ left: 16, right: 16 })
      }

      Row() {
        Button('扫码连接电脑')
          .fontSize(15)
          .height(44)
          .backgroundColor('#2563EB')
          .fontColor('#FFFFFF')
          .layoutWeight(1)
          .margin({ right: 10 })
          .onClick(() => {
            this.startQrScan();
          })

        Button('手动输入地址')
          .fontSize(15)
          .height(44)
          .backgroundColor('#FFFFFF')
          .fontColor('#111827')
          .border({ width: 1, color: '#D1D5DB' })
          .layoutWeight(1)
          .onClick(() => {
            this.manualInput = '';
            this.activeModal = 'manual';
          })
      }
      .width('100%')
      .padding({ left: 16, right: 16, top: 12, bottom: 18 })
    }
    .width('100%')
    .height('100%')
    .bindContentCover(this.activeModal.length > 0, this.ActiveModalView(), { modalTransition: ModalTransition.DEFAULT })
  }

  @Builder
  ActiveModalView() {
    if (this.activeModal === 'manual') {
      this.ManualAddressModal()
    } else if (this.activeModal === 'settings') {
      this.PushSettingsModal()
    } else if (this.activeModal === 'channel') {
      this.ManageComputerModal()
    }
  }

  @Builder
  WebWorkbenchView() {
    Column() {
      Stack() {
        Web({ src: this.webUrl, controller: this.webController })
          .width('100%')
          .height('100%')
          .javaScriptAccess(true)
          .domStorageAccess(true)
          .fileAccess(true)
          .mixedMode(MixedMode.All)
          .onControllerAttached(() => {
            this.webControllerAttached = true;
            if (this.pendingLoadUrl.length > 0 && this.pendingLoadUrl !== this.webUrl) {
              const target = this.pendingLoadUrl;
              this.pendingLoadUrl = '';
              try {
                this.webController.loadUrl(target);
              } catch {
                // ignore
              }
            } else {
              this.pendingLoadUrl = '';
            }
          })
          .onLoadIntercept((event) => {
            if (event && event.data.getRequestUrl().startsWith('codexbridge://home')) {
              this.closeWebView();
              return true;
            }
            return false;
          })
          .onPageBegin((event) => {
            this.webLoading = true;
            this.webLoadError = '';
            if (event && event.url) {
              this.currentWebUrl = event.url;
              hilog.info(0x0000, 'CodexBridgePerf', '[PERF] Web.onPageBegin +%{public}dms url=%{public}s', Date.now() - this.launchPerfStartMs, event.url);
            }
          })
          .onPageEnd((event) => {
            this.webLoading = false;
            if (event && event.url) {
              this.currentWebUrl = event.url;
              hilog.info(0x0000, 'CodexBridgePerf', '[PERF] Web.onPageEnd +%{public}dms url=%{public}s', Date.now() - this.launchPerfStartMs, event.url);
              if (GatewayUrl.sameOrigin(event.url, this.activeOrigin)) {
                sharedStore.setLastWorkspace(this.activeOrigin, this.activeName, event.url).catch(() => {});
                const combined = WebScriptLoader.loadCombined(getContext(this), this.bridgeToken, 'zh');
                if (combined.length > 0) {
                  this.webController.runJavaScript(combined);
                }
                PushManager.registerOrigin(sharedStore, this.activeOrigin).catch(() => {});
              }
            }
          })
          .onErrorReceive((event) => {
            if (event && event.request.isMainFrame()) {
              const desc = event.error.getErrorInfo() || `错误码 ${event.error.getErrorCode()}`;
              this.tryFallbackEndpoint(event.request.getRequestUrl(), `无法连接工作台：${desc}`);
            }
          })
          .onHttpErrorReceive((event) => {
            if (event && event.request.isMainFrame() && event.response.getResponseCode() >= 400) {
              this.tryFallbackEndpoint(event.request.getRequestUrl(), `网关返回 HTTP ${event.response.getResponseCode()}，请检查服务状态或切换信道`);
            }
          })
          .onSslErrorEventReceive((event) => {
            if (event) {
              event.handler.handleCancel();
              this.tryFallbackEndpoint(this.currentWebUrl || this.webUrl, 'HTTPS 证书校验失败，请检查公网证书或切换局域网信道');
            }
          })
          .onRefreshAccessedHistory(() => {
            try {
              const url = this.webController.getUrl();
              if (url && GatewayUrl.sameOrigin(url, this.activeOrigin)) {
                this.currentWebUrl = url;
                sharedStore.setLastWorkspace(this.activeOrigin, this.activeName, url).catch(() => {});
              }
            } catch {
              // ignore
            }
          })
          .onConsole((event) => {
            if (event) {
              const msg = event.message.getMessage();
              if (msg.includes('[PERF]') || msg.includes('[UPLOAD]') || event.message.getMessageLevel() >= 3) {
                hilog.info(0x0000, 'CodexBridgeWeb', '%{public}s', msg);
              }
            }
            return false;
          })
          .onPrompt((event) => {
            if (!event) {
              return false;
            }
            const reply = this.handleWebPrompt(event.url, event.message, event.value);
            if (reply !== undefined) {
              event.result.handlePromptConfirm(reply);
              return true;
            }
            return false;
          })
          .onConfirm((event) => {
            if (!event) {
              return false;
            }
            AlertDialog.show({
              title: '确认操作',
              message: event.message,
              primaryButton: {
                value: '取消',
                action: () => {
                  event.result.handleCancel();
                }
              },
              secondaryButton: {
                value: '确定',
                action: () => {
                  event.result.handleConfirm();
                }
              },
              cancel: () => {
                event.result.handleCancel();
              }
            });
            return true;
          })
          .onAlert((event) => {
            if (!event) {
              return false;
            }
            AlertDialog.show({
              title: '提示',
              message: event.message,
              confirm: {
                value: '确定',
                action: () => {
                  event.result.handleConfirm();
                }
              },
              cancel: () => {
                event.result.handleConfirm();
              }
            });
            return true;
          })
          .onShowFileSelector((event) => {
            if (event) {
              this.openFileChooser(event.result);
              return true;
            }
            return false;
          })

        if (this.webLoading && this.webLoadError.length === 0) {
          Row() {
            LoadingProgress()
              .width(18)
              .height(18)
              .color('#2563EB')
              .margin({ right: 8 })
            Text('正在连接工作台…')
              .fontSize(13)
              .fontColor('#374151')
          }
          .padding({ left: 14, right: 14, top: 8, bottom: 8 })
          .backgroundColor('#FFFFFF')
          .borderRadius(20)
          .shadow({ radius: 8, color: '#1A000000', offsetY: 2 })
          .margin({ top: 16 })
        }

        if (this.webLoadError.length > 0) {
          Column() {
            Text('工作台加载失败')
              .fontSize(18)
              .fontWeight(FontWeight.Bold)
              .fontColor('#111827')
              .margin({ bottom: 8 })
            Text(this.webLoadError)
              .fontSize(14)
              .fontColor('#DC2626')
              .textAlign(TextAlign.Center)
              .margin({ bottom: 8 })
            Text(this.currentWebUrl || this.webUrl)
              .fontSize(12)
              .fontColor('#6B7280')
              .textAlign(TextAlign.Center)
              .margin({ bottom: 20 })

            Row() {
              Button('重试')
                .height(40)
                .layoutWeight(1)
                .backgroundColor('#2563EB')
                .fontColor('#FFFFFF')
                .margin({ right: 8 })
                .onClick(() => {
                  const retryUrl = this.currentWebUrl || this.webUrl;
                  this.webLoadError = '';
                  this.webLoading = true;
                  try {
                    this.webController.loadUrl(retryUrl);
                  } catch {
                    this.webController.refresh();
                  }
                })

              Button('返回电脑列表')
                .height(40)
                .layoutWeight(1)
                .backgroundColor('#E5E7EB')
                .fontColor('#111827')
                .onClick(() => {
                  this.closeWebView();
                })
            }
            .width('100%')
          }
          .width('100%')
          .height('100%')
          .justifyContent(FlexAlign.Center)
          .padding({ left: 28, right: 28 })
          .backgroundColor('#F6F7F9')
        }
      }
      .alignContent(Alignment.Top)
      .layoutWeight(1)
      .width('100%')
    }
    .width('100%')
    .height('100%')
    .bindContentCover(this.showInboxDialog, this.InboxModal(), { modalTransition: ModalTransition.DEFAULT })
  }

  @Builder
  ManualAddressModal() {
    Column() {
      Text('手动添加电脑网关')
        .fontSize(18)
        .fontWeight(FontWeight.Bold)
        .margin({ bottom: 12 })

      TextInput({ text: this.manualInput, placeholder: 'https://your-host.ts.net:8443 或 http://192.168.1.x:9999' })
        .width('100%')
        .height(44)
        .onChange((val: string) => {
          this.manualInput = val;
        })

      Row() {
        Button('取消')
          .layoutWeight(1)
          .backgroundColor('#E5E7EB')
          .fontColor('#111827')
          .margin({ right: 8 })
          .onClick(() => {
            this.activeModal = '';
          })

        Button('连接')
          .layoutWeight(1)
          .backgroundColor('#2563EB')
          .fontColor('#FFFFFF')
          .onClick(() => {
            const val = this.manualInput;
            this.activeModal = '';
            this.connectWithInput(val);
          })
      }
      .width('100%')
      .margin({ top: 16 })
    }
    .width('100%')
    .height('100%')
    .padding({ top: 52, left: 24, right: 24, bottom: 24 })
    .justifyContent(FlexAlign.Center)
    .backgroundColor('#F6F7F9')
  }

  @Builder
  PushSettingsModal() {
    Column() {
      Row() {
        Text('华为离线推送设置')
          .fontSize(18)
          .fontWeight(FontWeight.Bold)
          .layoutWeight(1)
        Button('完成')
          .height(34)
          .backgroundColor('#111827')
          .fontColor('#FFFFFF')
          .onClick(() => {
            this.activeModal = '';
          })
      }
      .width('100%')
      .margin({ bottom: 18 })

      Row() {
        Column() {
          Text('启用华为离线推送')
            .fontSize(16)
            .fontWeight(FontWeight.Medium)
          Text('App 在后台或被关闭时，由电脑网关通过华为 Push Kit 下发系统通知，点击直达对应会话。')
            .fontSize(12)
            .fontColor('#6B7280')
            .margin({ top: 4 })
        }
        .alignItems(HorizontalAlign.Start)
        .layoutWeight(1)

        Toggle({ type: ToggleType.Switch, isOn: this.pushEnabled })
          .onChange((isOn: boolean) => {
            this.pushEnabled = isOn;
            sharedStore.setPushEnabled(isOn).catch(() => {});
          })
      }
      .width('100%')
      .padding(14)
      .backgroundColor('#FFFFFF')
      .borderRadius(12)
      .margin({ bottom: 12 })

      Column() {
        Text('HMS Push Token')
          .fontSize(13)
          .fontColor('#6B7280')
        Text(this.hmsTokenPreview)
          .fontSize(13)
          .fontColor('#111827')
          .margin({ top: 4 })
        if (this.hmsErrorText.length > 0) {
          Text(this.hmsErrorText)
            .fontSize(12)
            .fontColor('#DC2626')
            .margin({ top: 6 })
        }
      }
      .alignItems(HorizontalAlign.Start)
      .width('100%')
      .padding(14)
      .backgroundColor('#FFFFFF')
      .borderRadius(12)
      .margin({ bottom: 14 })

      Button('重新向已保存电脑注册推送')
        .width('100%')
        .height(42)
        .backgroundColor('#2563EB')
        .fontColor('#FFFFFF')
        .onClick(async () => {
          await PushManager.registerAllComputers(sharedStore);
          this.refreshPushStatus();
          this.showToast('已尝试向所有已登录电脑注册华为推送');
        })
    }
    .width('100%')
    .height('100%')
    .padding({ top: 52, left: 20, right: 20, bottom: 24 })
    .backgroundColor('#F6F7F9')
  }

  @Builder
  ManageComputerModal() {
    Column() {
      Row() {
        Text(this.managingComputer ? this.managingComputer.name : '管理电脑')
          .fontSize(18)
          .fontWeight(FontWeight.Bold)
          .layoutWeight(1)
        Button('关闭')
          .height(34)
          .backgroundColor('#E5E7EB')
          .fontColor('#111827')
          .onClick(() => {
            this.activeModal = '';
          })
      }
      .width('100%')
      .margin({ bottom: 14 })

      if (this.managingComputer !== undefined) {
        Text('可用信道（点击直接打开）：')
          .fontSize(13)
          .fontColor('#6B7280')
          .width('100%')
          .margin({ bottom: 8 })

        ForEach(this.managingComputer.endpoints, (ep: string) => {
          Row() {
            Button(ep)
              .layoutWeight(1)
              .height(40)
              .backgroundColor('#FFFFFF')
              .fontColor('#111827')
              .onClick(() => {
                const name = this.managingComputer ? this.managingComputer.name : '';
                this.activeModal = '';
                this.openWebUrl(ep, name, ep + '/');
              })

            if (this.managingComputer && this.managingComputer.endpoints.length > 1) {
              Button('删除')
                .height(40)
                .margin({ left: 8 })
                .backgroundColor('#FEE2E2')
                .fontColor('#DC2626')
                .onClick(async () => {
                  if (this.managingComputer === undefined) {
                    return;
                  }
                  this.computers = await sharedStore.removeEndpoint(this.managingComputer.origin, ep);
                  this.managingComputer = this.computers.find((c) => c.origin === this.managingComputer?.origin);
                  this.probeAllComputers().catch(() => {});
                })
            }
          }
          .width('100%')
          .margin({ bottom: 8 })
        }, (ep: string) => ep)

        Row() {
          TextInput({ text: this.channelInput, placeholder: '添加备用信道 (如 http://192.168.1.41:9999)' })
            .layoutWeight(1)
            .height(40)
            .onChange((val: string) => {
              this.channelInput = val;
            })
          Button('添加')
            .height(40)
            .margin({ left: 8 })
            .backgroundColor('#2563EB')
            .fontColor('#FFFFFF')
            .onClick(async () => {
              if (this.managingComputer === undefined || !this.channelInput.trim()) {
                return;
              }
              try {
                let normalized = this.channelInput.trim();
                if (!/^https?:\/\//i.test(normalized)) {
                  const hostOnly = normalized.split('/')[0].split(':')[0];
                  normalized = (GatewayUrl.isLocalHost(hostOnly) ? 'http://' : 'https://') + normalized;
                }
                const clean = GatewayUrl.origin(normalized);
                this.computers = await sharedStore.addEndpoint(this.managingComputer.origin, clean);
                this.managingComputer = this.computers.find((c) => c.origin === this.managingComputer?.origin);
                this.channelInput = '';
                this.probeAllComputers().catch(() => {});
              } catch (err) {
                this.showToast(err instanceof Error ? err.message : '地址无效');
              }
            })
        }
        .width('100%')
        .margin({ top: 8, bottom: 8 })

        Button('移除此电脑')
          .width('100%')
          .height(42)
          .backgroundColor('#FEE2E2')
          .fontColor('#DC2626')
          .margin({ top: 16 })
          .onClick(async () => {
            if (this.managingComputer !== undefined) {
              this.computers = await sharedStore.removeComputer(this.managingComputer.origin);
            }
            this.activeModal = '';
          })
      }
    }
    .width('100%')
    .height('100%')
    .padding({ top: 52, left: 20, right: 20, bottom: 24 })
    .backgroundColor('#F6F7F9')
  }

  @Builder
  InboxModal() {
    Column() {
      Row() {
        Text('通知收件箱')
          .fontSize(18)
          .fontWeight(FontWeight.Bold)
          .layoutWeight(1)
        Button('关闭')
          .height(34)
          .backgroundColor('#111827')
          .fontColor('#FFFFFF')
          .onClick(() => {
            this.showInboxDialog = false;
          })
      }
      .width('100%')
      .margin({ bottom: 12 })

      if (this.inboxLoading) {
        Text('正在读取通知...')
          .fontSize(14)
          .fontColor('#6B7280')
          .margin({ top: 24 })
      } else if (this.inboxEvents.length === 0) {
        Text('暂无通知记录')
          .fontSize(14)
          .fontColor('#6B7280')
          .margin({ top: 24 })
      } else {
        List({ space: 10 }) {
          ForEach(this.inboxEvents, (item: MobileEventItem) => {
            ListItem() {
              Column() {
                Text(item.title || '任务提醒')
                  .fontSize(15)
                  .fontWeight(FontWeight.Bold)
                  .fontColor('#111827')
                if (item.body && item.body.length > 0) {
                  Text(item.body)
                    .fontSize(13)
                    .fontColor('#4B5563')
                    .margin({ top: 4 })
                }
                Button('跳转到对应会话 ↗')
                  .fontSize(13)
                  .height(34)
                  .backgroundColor('#2563EB')
                  .fontColor('#FFFFFF')
                  .margin({ top: 10 })
                  .onClick(() => {
                    this.showInboxDialog = false;
                    this.openDeepLink({
                      origin: this.activeOrigin,
                      thread: item.threadId,
                      host: item.host || 'local'
                    });
                  })
              }
              .alignItems(HorizontalAlign.Start)
              .padding(14)
              .width('100%')
              .backgroundColor('#FFFFFF')
              .borderRadius(12)
            }
          }, (item: MobileEventItem) => item.id)
        }
        .layoutWeight(1)
        .width('100%')
      }
    }
    .width('100%')
    .height('100%')
    .padding({ top: 52, left: 20, right: 20, bottom: 24 })
    .backgroundColor('#F6F7F9')
  }
}
