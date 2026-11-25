import { common } from '@kit.AbilityKit';
import { fileIo, picker } from '@kit.CoreFileKit';
import { util } from '@kit.ArkTS';
import { GatewayHttp } from './GatewayHttp';
import { GatewayUrl } from './GatewayUrl';
import { DeviceBinding } from './DeviceBinding';

export class DownloadState {
  phase: string = 'idle';
  name: string = 'download';
  bytes: number = 0;
  total: number = 0;
  speed: number = 0;
  error: string = '';
}

export class AttachmentDownload {
  state: DownloadState = new DownloadState();
  private http: GatewayHttp = new GatewayHttp();
  private path: string = '';
  private base: string = '';
  private cookie: string = '';
  private generation: number = 0;
  private changed: (state: DownloadState) => void;

  constructor(changed: (state: DownloadState) => void) { this.changed = changed; }

  static accepts(value: string, base: string): boolean {
    try {
      const path = GatewayUrl.apiPath(value, base);
      if (value.includes('#')) { return false; }
      if (/^\/api\/(?:sessions\/[0-9a-f-]{36}\/(?:files\/[0-9a-f]{64}|workspace\/download)|desktop-sessions\/(?:claude|deepseek|zcode|antigravity)\/workspace\/download)$/.test(path)) { return true; }
      const query = value.includes('?') ? value.slice(value.indexOf('?') + 1) : '';
      if (path === '/api/sessions/diagnostics') { return !query || /^thread=[0-9a-fA-F-]{36}$/.test(query); }
      const flags = query.split('&').filter(part => part.startsWith('download='));
      return flags.length === 1 && flags[0] === 'download=1' &&
        /^\/api\/(?:sessions\/[0-9a-f-]{36}\/(?:uploads\/[0-9a-f-]{36}\/preview|desktop-images\/[a-f0-9]{64})|desktop-sessions\/(?:claude|deepseek|zcode|antigravity)\/(?:uploads\/[0-9a-f-]{36}\/preview|images\/[a-f0-9]{64}))$/.test(path);
    } catch { return false; }
  }
  static filename(value: string): string {
    const raw = /filename\*=UTF-8''([^;]+)/i.exec(value);
    const quoted = /filename="([^"]+)"|filename=([^;]+)/i.exec(value);
    let name = quoted ? (quoted[1] || quoted[2]) : 'download';
    if (raw) { try { name = decodeURIComponent(raw[1]); } catch {} }
    name = name.replace(/\\/g, '/').split('/').pop() || 'download';
    name = name.replace(/[\u0000-\u001f\u007f]/g, '_').trim();
    return !name || name === '.' || name === '..' ? 'download' : name.slice(0, 180);
  }
  private publish(): void { this.changed(this.state); }
  cancel(): void {
    this.generation++; this.http.cancel();
    if (this.path) { try { fileIo.unlinkSync(this.path); } catch {} }
    this.path = ''; this.state.phase = 'cancelled'; this.publish();
  }
  async start(context: common.UIAbilityContext, value: string, base: string, disposition: string, total: number): Promise<void> {
    if (this.state.phase === 'running' || !AttachmentDownload.accepts(value, base)) { throw new Error('此下载不属于当前电脑附件'); }
    if (this.path) { this.cancel(); }
    const cookie = DeviceBinding.cookies(base);
    if (!cookie) { throw new Error('请先登录电脑网关'); }
    this.base = base; this.cookie = cookie;
    const generation = ++this.generation;
    this.state = new DownloadState(); this.state.phase = 'running';
    this.state.name = AttachmentDownload.filename(disposition); this.state.total = total > 0 ? total : 0;
    this.path = context.cacheDir + '/bridge-download-' + util.generateRandomUUID();
    let file: fileIo.File | null = null;
    const route = value.slice(base.length); let sample = Date.now(), previous = 0;
    this.publish();
    try {
      const output = fileIo.openSync(this.path, fileIo.OpenMode.CREATE | fileIo.OpenMode.READ_WRITE | fileIo.OpenMode.TRUNC); file = output;
      const response = await this.http.request(base, route, cookie, 1024 * 1024 * 1024, data => {
        if (generation !== this.generation || DeviceBinding.cookies(base) !== cookie) { throw new Error('登录状态已变化'); }
        let offset = 0;
        while (offset < data.byteLength) {
          const written = fileIo.writeSync(output.fd, data.slice(offset));
          if (written <= 0) { throw new Error('无法保存下载文件'); } offset += written;
        }
        this.state.bytes += data.byteLength;
        const now = Date.now();
        if (now - sample >= 250) {
          this.state.speed = (this.state.bytes - previous) * 1000 / (now - sample);
          sample = now; previous = this.state.bytes; this.publish();
        }
      });
      if (generation !== this.generation) { return; }
      if (response.status !== 200 || DeviceBinding.cookies(base) !== cookie ||
        this.state.total > 0 && this.state.bytes !== this.state.total) { throw new Error('下载中断或登录状态已变化'); }
      this.state.total = this.state.bytes; this.state.phase = 'complete'; this.publish();
    } catch (error) {
      if (generation === this.generation) {
        this.state.phase = 'failed'; this.state.error = '下载失败，请重新下载'; this.publish();
        try { fileIo.unlinkSync(this.path); } catch {} this.path = '';
      }
    } finally { if (file) { fileIo.closeSync(file); } }
  }
  async save(context: common.UIAbilityContext): Promise<void> {
    if (!this.path || this.state.phase !== 'complete' || DeviceBinding.cookies(this.base) !== this.cookie) {
      throw new Error('下载不可用，请重新登录后下载');
    }
    const generation = this.generation, path = this.path;
    const options = new picker.DocumentSaveOptions(); options.newFileNames = [this.state.name];
    const selected = await new picker.DocumentViewPicker(context).save(options);
    if (selected.length !== 1 || generation !== this.generation || this.path !== path ||
      DeviceBinding.cookies(this.base) !== this.cookie) { return; }
    const target = fileIo.openSync(selected[0], fileIo.OpenMode.READ_WRITE);
    try { fileIo.copyFileSync(path, target.fd); } finally { fileIo.closeSync(target); }
    this.cancel();
  }
}
