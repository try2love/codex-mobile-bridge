import { webview } from '@kit.ArkWeb';
import { scanBarcode, scanCore } from '@kit.ScanKit';
import { common } from '@kit.AbilityKit';
import { picker, statvfs } from '@kit.CoreFileKit';
import { util } from '@kit.ArkTS';
import { ComputerStore } from '../services/ComputerStore';
import { GatewayUrl } from '../services/GatewayUrl';
import { DocumentGuard } from '../services/DocumentGuard';
import { NativeBridge } from '../services/NativeBridge';
import { NativePreferences } from '../services/NativePreferences';
import { WebScriptLoader } from '../services/WebScriptLoader';
import { AttachmentDownload, DownloadState } from '../services/AttachmentDownload';
import { RdbArchiveDatabase } from '../services/ArchiveDatabase';
import { OfflineArchive, ArchiveAccount } from '../services/OfflineArchive';
import { OfflineBridge } from '../services/OfflineBridge';
import { DeviceAuthentication } from '../services/DeviceAuthentication';
import { OfflinePage } from './OfflinePage';
import { MobileIdentity } from '../services/MobileIdentity';
import { DeviceBinding } from '../services/DeviceBinding';
import { NetworkPolicy } from '../services/NetworkPolicy';
import { HistoryTransfer } from '../services/HistoryTransfer';
import { SystemBars } from '../services/SystemBars';
import { TaskNotifications } from '../services/TaskNotifications';

@Entry
@Component
struct Index {
  @State addresses: string[] = [];
  @State input: string = '';
  @State activeOrigin: string = '';
  @State webUrl: string = '';
  @State inWeb: boolean = false;
  @State ready: boolean = false;
  @State opening: boolean = false;
  @State scanning: boolean = false;
  @State errorText: string = '';
  @State scripts: ScriptItem[] = [];
  @State settingsOpen: boolean = false;
  @State renaming: string = '';
  @State computerName: string = '';
  @State download: DownloadState = new DownloadState();
  @State recordsOpen: boolean = false;
  @State offlineAccounts: ArchiveAccount[] = [];
  @State offlineAccount: ArchiveAccount | null = null;
  @State alertsEnabled: boolean = false;
  private database: RdbArchiveDatabase = new RdbArchiveDatabase();
  private archive: OfflineArchive = new OfflineArchive(this.database);
  private store: ComputerStore = new ComputerStore();
  private prefs: NativePreferences = new NativePreferences();
  private notifications: TaskNotifications = new TaskNotifications();
  private guard: DocumentGuard = new DocumentGuard();
  private token: string = util.generateRandomUUID();
  private controller: webview.WebviewController = new webview.WebviewController();
  private bridge: NativeBridge = new NativeBridge(this.controller, this.guard, this.prefs, this.token);
  private offline: OfflineBridge = new OfflineBridge(this.archive, this.guard, this.controller, this.token);
  private network: NetworkPolicy = new NetworkPolicy(() => { if (this.controllerAttached) { this.offline.publish(); } });
  private transfer: HistoryTransfer = new HistoryTransfer(this.archive, this.network,
    () => statvfs.getFreeSizeSync((getContext(this) as common.UIAbilityContext).filesDir), () => { this.toast('无法保存附件，请重试'); });
  private downloads: AttachmentDownload = new AttachmentDownload(state => {
    const copy = new DownloadState();
    copy.phase = state.phase; copy.name = state.name; copy.bytes = state.bytes;
    copy.total = state.total; copy.speed = state.speed; copy.error = state.error; this.download = copy;
  });
  private controllerAttached: boolean = false;
  private appForeground: boolean = true;
  private foregroundChanged = (foreground: boolean): void => {
    this.appForeground = foreground;
    this.notifications.visibility(foreground);
    if (foreground) { this.network.start().catch(() => {}); } else { this.network.stop(); }
    this.visibility();
  };
  private visibility(): void {
    this.guard.foreground = this.appForeground && this.inWeb && !this.offlineAccount && !this.recordsOpen;
    if (this.controllerAttached) { this.offline.publish(); }
  }

  aboutToAppear(): void {
    const context = getContext(this) as common.UIAbilityContext;
    context.eventHub.on('bridge-foreground', this.foregroundChanged);
    const archive = this.database.init(context).then(() => this.archive.init());
    this.offline.enableTransfers(this.transfer, this.network, context);
    this.offline.authorized = (account, base, cookie) => { this.notifications.configure(account, base, cookie); this.alertsEnabled = this.notifications.enabled(); };
    this.offline.unauthorized = () => { this.notifications.detach(); this.alertsEnabled = false; };
    this.network.start().catch(() => {});
    this.bridge.offline = value => this.offline.enqueue(value);
    this.bridge.systemBars = (top, _bottom, current) => { SystemBars.apply(context, top, current).catch(() => {}); };
    Promise.all([this.store.init(context), this.prefs.init(context), this.notifications.init(context), archive]).then(() => {
      this.addresses = this.store.list();
      this.ready = true;
    }).catch(() => { this.errorText = '无法读取本机配置或离线存储'; });
  }

  aboutToDisappear(): void {
    (getContext(this) as common.UIAbilityContext).eventHub.off('bridge-foreground', this.foregroundChanged);
    this.guard.foreground = false; this.downloads.cancel(); this.offline.detach(); this.network.stop(); this.notifications.detach();
  }

  private toast(message: string): void {
    this.getUIContext().getPromptAction().showToast({ message, duration: 2200 });
  }

  private async connect(raw: string): Promise<void> {
    if (!this.ready || this.opening || (this.webUrl && !this.controllerAttached)) { return; }
    this.opening = true;
    try {
      const target = GatewayUrl.connection(raw);
      const origin = GatewayUrl.origin(target.split('#')[0]);
      if (this.webUrl && (origin !== this.activeOrigin || target.includes('#pair='))) {
        const confirmed = await this.getUIContext().getPromptAction().showDialog({
          title: '打开另一地址或重新配对',
          message: '这会重新加载工作台，当前页面的未发送内容可能丢失。是否继续？',
          buttons: [{ text: '取消', color: '#64748B' }, { text: '继续', color: '#2563EB' }]
        }).then(result => result.index === 1).catch(() => false);
        if (!confirmed) { return; }
      }
      const next = this.addresses.includes(origin) ? this.addresses : [origin, ...this.addresses];
      // Persist only an address; the one-time pairing fragment stays in memory.
      await this.archive.serial(async () => { await this.archive.unlinkRoute(origin); return null; });
      await this.store.save(next);
      this.addresses = next;
      this.input = '';
      if (this.webUrl && origin === this.activeOrigin && !target.includes('#pair=')) {
        this.inWeb = true; this.visibility();
        return;
      }
      const previousOrigin = this.activeOrigin;
      const script = WebScriptLoader.load(getContext(this) as common.UIAbilityContext, this.token,
        this.prefs.read('language') || 'zh', origin);
      this.scripts = [{ script, scriptRules: ['*'] }];
      this.activeOrigin = origin;
      this.guard.navigate(origin);
      try {
        if (!target.includes('#pair=')) { await DeviceBinding.restore(origin, () => this.appForeground && this.activeOrigin === origin); }
        if (this.controllerAttached) { this.controller.loadUrl(target); }
        else { this.webUrl = target; }
      } catch (error) {
        this.activeOrigin = previousOrigin;
        if (previousOrigin) { this.guard.navigate(previousOrigin); }
        throw error;
      }
      this.errorText = '';
      this.inWeb = true; this.visibility();
    } catch { this.toast('无法打开地址，请检查网关地址和本机存储'); }
    finally { this.opening = false; }
  }

  private async remove(origin: string): Promise<void> {
    if (!this.ready || this.opening || this.scanning) { return; }
    this.opening = true;
    try {
      const next = this.addresses.filter((value: string) => value !== origin);
      await this.store.save(next);
      this.addresses = next;
    } catch { this.toast('未能保存地址列表'); }
    finally { this.opening = false; }
  }

  private async scan(): Promise<void> {
    if (!this.ready || this.opening || this.scanning) { return; }
    this.scanning = true;
    try {
      const result = await scanBarcode.startScanForResult(getContext(this) as common.UIAbilityContext, {
        scanTypes: [scanCore.ScanType.QR_CODE], enableMultiMode: false, enableAlbum: true
      });
      if (result.originalValue) { await this.connect(result.originalValue); }
    } catch { this.toast('扫码未完成，可手动输入地址'); }
    finally { this.scanning = false; }
  }

  onBackPress(): boolean {
    if (this.offlineAccount) { this.offlineAccount = null; this.visibility(); return true; }
    if (this.recordsOpen) { this.recordsOpen = false; this.visibility(); return true; }
    if (this.settingsOpen) { this.settingsOpen = false; return true; }
    if (this.renaming) { this.renaming = ''; return true; }
    if (this.opening || this.scanning) { return true; }
    if (this.inWeb) { this.inWeb = false; this.visibility(); return true; }
    return false;
  }

  private async records(): Promise<void> {
    if (!this.ready) { return; }
    this.offlineAccounts = await this.archive.serial(() => this.archive.available()) as ArchiveAccount[];
    this.settingsOpen = false; this.recordsOpen = true; this.visibility();
  }
  private async browse(account: ArchiveAccount): Promise<void> {
    if (['revoked', 'unknownRejected'].includes(account.status)) { this.toast('登录状态需要验证，请使用原账号重新连接'); return; }
    if (account.status !== 'valid' && !await DeviceAuthentication.verify()) { this.toast('身份验证未完成，请使用设备屏幕锁验证'); return; }
    const current = await this.archive.serial(() => this.archive.current(account, false)) as ArchiveAccount;
    this.offlineAccount = current; this.visibility();
  }
  private async clearRecords(account: ArchiveAccount): Promise<void> {
    const result = await this.getUIContext().getPromptAction().showDialog({ title: '清除离线缓存？',
      message: '清除“' + account.name + '”保存在此设备上的聊天、附件、草稿和待发送内容，电脑上的内容和绑定保持不变。',
      buttons: [{ text: '取消', color: '#64748B' }, { text: '清除', color: '#DC2626' }] });
    if (result.index !== 1) { return; }
    await this.archive.serial(async () => { await this.archive.clear(account, 0, Number.MAX_SAFE_INTEGER, true); return null; });
    this.offline.publish(); await this.records();
  }
  private async reconnectOffline(): Promise<void> {
    const account = this.offlineAccount; if (!account) { return; }
    const routes = await this.archive.serial(() => this.archive.db.rows('SELECT address FROM routes WHERE account=?', [account.id])) as Object[][];
    const saved = this.store.list(), addresses = routes.map(row => row[0] as string).filter(address => saved.includes(address));
    const address = addresses.includes(this.activeOrigin) ? this.activeOrigin : addresses[0];
    if (!address) { this.toast('请先扫码或输入原电脑地址'); return; }
    await this.connect(address);
    if (this.activeOrigin === address && this.inWeb) { this.offlineAccount = null; this.recordsOpen = false; this.visibility(); }
  }

  build() {
    Stack() {
      Column({ space: 16 }) {
        Text('Codex Bridge · Harmony 实验版').fontSize(22).fontWeight(FontWeight.Bold)
        Text('扫码或输入电脑网关地址，登录后打开工作台。').fontSize(14)
        Text('电脑继续执行任务，手机查看并继续对话。').fontSize(13).fontColor('#64748B')
        TextInput({ text: this.input, placeholder: 'https://… 或 http://192.168.x.x:端口' })
          .enabled(!this.opening && !this.scanning)
          .onChange((value: string) => { this.input = value; })
        Row({ space: 12 }) {
          Button('连接').enabled(this.ready && !this.opening && !this.scanning).onClick(() => { this.connect(this.input); })
          Button('扫码连接').enabled(this.ready && !this.opening && !this.scanning).onClick(() => { this.scan(); })
        }
        Button('保存的离线记录').enabled(this.ready).onClick(() => { this.records().catch(() => { this.toast('无法读取离线缓存'); }); })
        List({ space: 12 }) {
          ForEach(this.addresses, (origin: string) => {
            ListItem() {
              Row({ space: 8 }) {
                Button(this.store.name(origin)).layoutWeight(1).enabled(!this.opening && !this.scanning).onClick(() => { this.connect(origin); })
                Button('重命名').enabled(!this.opening && !this.scanning).onClick(() => {
                  this.computerName = this.store.name(origin); this.renaming = origin;
                })
                Button('移除').enabled(!this.opening && !this.scanning).onClick(() => { this.remove(origin); })
              }
            }
          }, (origin: string) => origin)
        }.layoutWeight(1)
        if (this.errorText) { Text(this.errorText).fontColor('#B91C1C') }
      }.padding(20).width('100%').height('100%')
        .visibility(this.inWeb ? Visibility.None : Visibility.Visible)

      if (this.webUrl) {
        Column() {
          Row() {
            Button('电脑列表').onClick(() => { this.inWeb = false; this.visibility(); })
            Text(this.activeOrigin).fontSize(12).maxLines(1).layoutWeight(1)
            Button('更多').onClick(() => { this.settingsOpen = true; })
          }.width('100%').padding(8)
          if (this.errorText) { Text(this.errorText).fontColor('#B91C1C').padding(8) }
          Web({ src: this.webUrl, controller: this.controller })
            .width('100%').layoutWeight(1)
            .javaScriptAccess(true).domStorageAccess(true)
            .fileAccess(false).mixedMode(MixedMode.None)
            .runJavaScriptOnDocumentStart(this.scripts)
            .onControllerAttached(() => {
              this.controllerAttached = true;
              this.controller.setCustomUserAgent(this.controller.getUserAgent() + ' ' + MobileIdentity.userAgent());
            })
            .onTouch((event) => { if (event.type === TouchType.Up) { this.guard.touch(); } })
            .onPageBegin(() => { this.offline.detach(); this.notifications.detach(); this.alertsEnabled = false; this.guard.navigate(this.activeOrigin); })
            .onPrompt((event) => {
              if (!event) { return false; }
              try {
                const result = this.bridge.prompt(event.url, event.message, event.value);
                if (result === null) { event.result.handleCancel(); } else { event.result.handlePromptConfirm(result); }
              } catch { event.result.handleCancel(); }
              return true;
            })
            .onLoadIntercept((event) => {
              if (!event) { return true; }
              const target = event.data.getRequestUrl();
              if (!event.data.isMainFrame() && target === 'about:blank') { return false; }
              return !GatewayUrl.sameOrigin(target, this.activeOrigin);
            })
            .onPageEnd((event) => {
              if (event && GatewayUrl.sameOrigin(event.url, this.activeOrigin)) {
                this.errorText = '';
                webview.WebCookieManager.saveCookieAsync().catch(() => {});
                this.offline.publish();
              }
            })
            .onErrorReceive((event) => {
              if (event && event.request.isMainFrame()) {
                this.errorText = '连接中断，请检查电脑网关；页面和未发送内容仍保留。';
              }
            })
            .onSslErrorEventReceive((event) => {
              if (event) { event.handler.handleCancel(); }
              this.errorText = 'HTTPS 证书校验失败';
            })
            .onShowFileSelector((event) => {
              if (!event) { return false; }
              if (!this.guard.accepts(this.controller.getUrl(), this.controller.getUrl()) || !this.guard.consumeTouch() ||
                ![FileSelectorMode.FileOpenMode, FileSelectorMode.FileOpenMultipleMode].includes(event.fileSelector.getMode())) {
                event.result.handleFileList([]); return true;
              }
              const generation = this.guard.generation;
              const options = new picker.DocumentSelectOptions();
              options.maxSelectNumber = event.fileSelector.getMode() === FileSelectorMode.FileOpenMultipleMode ? 10 : 1;
              new picker.DocumentViewPicker(getContext(this) as common.UIAbilityContext).select(options).then(files => {
                event.result.handleFileList(this.guard.accepts(this.controller.getUrl(), this.controller.getUrl(), generation) ? files : []);
              }).catch(() => { event.result.handleFileList([]); });
              return true;
            })
            .onDownloadStart((event) => {
              if (!event || !this.guard.accepts(event.url, this.controller.getUrl()) || !this.guard.consumeTouch()) { return; }
              this.downloads.start(getContext(this) as common.UIAbilityContext, event.url, this.activeOrigin,
                event.contentDisposition, event.contentLength).catch(() => { this.toast('无法开始下载，请检查登录和附件地址'); });
            })
          if (['running', 'complete', 'failed'].includes(this.download.phase)) {
            Column({ space: 8 }) {
              Text(this.download.name).maxLines(1)
              if (this.download.phase === 'running') {
                Text('已下载 ' + (this.download.bytes / 1048576).toFixed(1) + ' MB · ' +
                  (this.download.speed / 1024).toFixed(0) + ' KB/s').fontSize(13)
                if (this.download.total > 0) { Progress({ value: this.download.bytes, total: this.download.total, type: ProgressType.Linear }) }
              } else if (this.download.phase === 'complete') {
                Button('保存文件').onClick(() => {
                  this.downloads.save(getContext(this) as common.UIAbilityContext).catch(() => { this.toast('无法保存文件，请重试'); });
                })
              } else { Text(this.download.error).fontSize(13) }
              Button(this.download.phase === 'running' ? '取消下载' : '关闭').onClick(() => { this.downloads.cancel(); })
            }.padding(12).width('100%')
          }
        }.width('100%').height('100%')
          .visibility(this.inWeb ? Visibility.Visible : Visibility.None)
      }
      if (this.settingsOpen) {
        Column({ space: 16 }) {
          Text('手机设置').fontSize(22).fontWeight(FontWeight.Bold)
          Button('外观与显示').onClick(() => {
            this.settingsOpen = false;
            this.controller.runJavaScript('document.querySelector("[data-open-appearance]")?.click()').catch(() => {});
          })
          Button('刷新页面').onClick(() => { this.settingsOpen = false; this.controller.refresh(); })
          Button('保存的离线记录').onClick(() => { this.records().catch(() => { this.toast('无法读取离线缓存'); }); })
          Button(this.alertsEnabled ? '关闭当前电脑前台任务提醒' : '开启当前电脑前台任务提醒').onClick(() => {
            this.notifications.enable(!this.alertsEnabled).then(() => { this.alertsEnabled = this.notifications.enabled(); })
              .catch(() => { this.toast('请先登录电脑，并允许系统通知'); });
          })
          Button('返回电脑列表').onClick(() => { this.settingsOpen = false; this.inWeb = false; this.visibility(); })
          Text('HarmonyOS 实验版 · 0.1.0').fontSize(13)
          Button('关闭').onClick(() => { this.settingsOpen = false; })
        }.padding(24).width('100%').height('100%').backgroundColor($r('sys.color.ohos_id_color_background'))
      }
      if (this.recordsOpen) {
        Column({ space: 16 }) {
          Text('保存的离线记录').fontSize(22).fontWeight(FontWeight.Bold)
          if (!this.offlineAccounts.length) { Text('暂无保留的离线记录') }
          ForEach(this.offlineAccounts, (account: ArchiveAccount) => {
            Row({ space: 12 }) {
              Button(account.name).layoutWeight(1).onClick(() => { this.browse(account).catch(() => { this.toast('无法读取离线缓存'); }); })
              Button('清除').onClick(() => { this.clearRecords(account).catch(() => { this.toast('无法清除离线缓存'); }); })
            }
          }, (account: ArchiveAccount) => String(account.id))
          Button('关闭').onClick(() => { this.recordsOpen = false; this.visibility(); })
        }.padding(24).width('100%').height('100%').backgroundColor($r('sys.color.ohos_id_color_background'))
      }
      if (this.offlineAccount) {
        OfflinePage({ archive: this.archive, account: this.offlineAccount, transfers: this.transfer, prefs: this.prefs,
          onClose: () => { this.offlineAccount = null; this.visibility(); },
          onReconnect: () => { this.reconnectOffline().catch(() => { this.toast('无法重新连接电脑'); }); }
        })
      }
      if (this.renaming) {
        Column({ space: 16 }) {
          Text('重命名电脑').fontSize(22)
          TextInput({ text: this.computerName }).maxLength(80).onChange(value => { this.computerName = value; })
          Button('保存').onClick(() => {
            this.store.rename(this.renaming, this.computerName).then(() => {
              this.renaming = ''; this.addresses = [...this.store.list()];
            }).catch(() => { this.toast('未能保存电脑名称'); });
          })
          Button('取消').onClick(() => { this.renaming = ''; })
        }.padding(24).width('100%').height('100%').backgroundColor($r('sys.color.ohos_id_color_background'))
      }
    }.width('100%').height('100%')
  }
}
