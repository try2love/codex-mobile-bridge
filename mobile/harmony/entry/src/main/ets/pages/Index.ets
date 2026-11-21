import { webview } from '@kit.ArkWeb';
import { scanBarcode, scanCore } from '@kit.ScanKit';
import { common } from '@kit.AbilityKit';
import { ComputerStore } from '../services/ComputerStore';
import { GatewayUrl } from '../services/GatewayUrl';

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
  private store: ComputerStore = new ComputerStore();
  private controller: webview.WebviewController = new webview.WebviewController();
  private controllerAttached: boolean = false;

  aboutToAppear(): void {
    this.store.init(getContext(this) as common.UIAbilityContext).then(() => {
      this.addresses = this.store.list();
      this.ready = true;
    }).catch(() => { this.errorText = '无法读取地址列表'; });
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
      await this.store.save(next);
      this.addresses = next;
      this.input = '';
      if (this.webUrl && origin === this.activeOrigin && !target.includes('#pair=')) {
        this.inWeb = true;
        return;
      }
      const previousOrigin = this.activeOrigin;
      this.activeOrigin = origin;
      try {
        if (this.controllerAttached) { this.controller.loadUrl(target); }
        else { this.webUrl = target; }
      } catch (error) {
        this.activeOrigin = previousOrigin;
        throw error;
      }
      this.errorText = '';
      this.inWeb = true;
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
    if (this.opening || this.scanning) { return true; }
    if (this.inWeb) { this.inWeb = false; return true; }
    return false;
  }

  build() {
    Stack() {
      Column({ space: 16 }) {
        Text('Codex Bridge · Harmony 实验版').fontSize(22).fontWeight(FontWeight.Bold)
        Text('扫码或输入电脑网关地址，登录后打开工作台。').fontSize(14)
        Text('暂不支持自动换线、后台推送和原生文件上传下载。').fontSize(13).fontColor('#64748B')
        TextInput({ text: this.input, placeholder: 'https://… 或 http://192.168.x.x:端口' })
          .enabled(!this.opening && !this.scanning)
          .onChange((value: string) => { this.input = value; })
        Row({ space: 12 }) {
          Button('连接').enabled(this.ready && !this.opening && !this.scanning).onClick(() => { this.connect(this.input); })
          Button('扫码连接').enabled(this.ready && !this.opening && !this.scanning).onClick(() => { this.scan(); })
        }
        List({ space: 12 }) {
          ForEach(this.addresses, (origin: string) => {
            ListItem() {
              Row({ space: 8 }) {
                Button(origin).layoutWeight(1).enabled(!this.opening && !this.scanning).onClick(() => { this.connect(origin); })
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
            Button('电脑列表').onClick(() => { this.inWeb = false; })
            Text(this.activeOrigin).fontSize(12).maxLines(1).layoutWeight(1)
          }.width('100%').padding(8)
          if (this.errorText) { Text(this.errorText).fontColor('#B91C1C').padding(8) }
          Web({ src: this.webUrl, controller: this.controller })
            .width('100%').layoutWeight(1)
            .javaScriptAccess(true).domStorageAccess(true)
            .fileAccess(false).mixedMode(MixedMode.None)
            .onControllerAttached(() => { this.controllerAttached = true; })
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
              if (event) { event.result.handleFileList([]); }
              this.toast('此实验版暂不支持原生文件选择');
              return true;
            })
        }.width('100%').height('100%')
          .visibility(this.inWeb ? Visibility.Visible : Visibility.None)
      }
    }.width('100%').height('100%')
  }
}
