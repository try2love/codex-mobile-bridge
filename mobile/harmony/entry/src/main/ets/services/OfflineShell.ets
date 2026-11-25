import { common } from '@kit.AbilityKit';
import { util } from '@kit.ArkTS';

/** No network or arbitrary filesystem fallback exists for the offline reader. */
export class OfflineShell {
  static readonly ORIGIN: string = 'https://offline-reader.invalid';
  private manifest: Record<string, string> = {};
  private context: common.Context;
  constructor(context: common.Context) {
    this.context = context;
    const bytes = context.resourceManager.getRawFileContentSync('generated/offline-shell/manifest.json');
    this.manifest = JSON.parse(util.TextDecoder.create('utf-8', { ignoreBOM: true }).decodeToString(bytes)) as Record<string, string>;
    for (const key of Object.keys(this.manifest)) {
      if (!/^[A-Za-z0-9_.-]+(?:\/[A-Za-z0-9_.-]+)*$/.test(this.manifest[key]) ||
        this.manifest[key].split('/').some(part => part === '.' || part === '..')) { throw new Error('无效离线资源清单'); }
    }
    if (!this.manifest['/offline-reader.html']) { throw new Error('缺少离线页面'); }
  }
  static contains(value: string): boolean {
    return value === OfflineShell.ORIGIN + '/' || value.startsWith(OfflineShell.ORIGIN + '/') && !/[?%\\]/.test(value);
  }
  response(value: string, method: string): WebResourceResponse {
    const response = new WebResourceResponse();
    const path = value.slice(OfflineShell.ORIGIN.length).split('#')[0];
    const file = OfflineShell.contains(value) && method === 'GET' ? this.manifest[path] : undefined;
    response.setResponseCode(file ? 200 : 403); response.setReasonMessage(file ? 'OK' : 'Forbidden');
    response.setResponseEncoding('utf-8');
    const types: Record<string, string> = { html: 'text/html', js: 'application/javascript', css: 'text/css', json: 'application/json', svg: 'image/svg+xml', png: 'image/png', webp: 'image/webp', woff2: 'font/woff2', woff: 'font/woff', ttf: 'font/ttf', bcmap: 'application/octet-stream' };
    const extension = file?.split('.').pop() || '';
    response.setResponseMimeType(types[extension] || 'application/octet-stream');
    response.setResponseHeader([
      { headerKey: 'Cache-Control', headerValue: 'no-store' },
      { headerKey: 'X-Content-Type-Options', headerValue: 'nosniff' },
      { headerKey: 'Content-Security-Policy', headerValue: "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'" }
    ]);
    const bytes = file ? this.context.resourceManager.getRawFileContentSync('generated/offline-shell/' + file) : new Uint8Array();
    response.setResponseData(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength));
    return response;
  }
}
