import { rcp } from '@kit.RemoteCommunicationKit';
import { util } from '@kit.ArkTS';
import { GatewayUrl } from './GatewayUrl';
import { MobileIdentity } from './MobileIdentity';

export interface GatewayResponse {
  status: number;
  headers: rcp.ResponseHeaders;
  data: Uint8Array;
}

/** Private native requests never inherit redirects, a page's fetch, or its JS auth claims. */
export class GatewayHttp {
  private sessions: Set<rcp.Session> = new Set();
  private timeout: number;
  constructor(timeout: number = 45000) { this.timeout = timeout; }

  private close(session: rcp.Session): void { if (this.sessions.delete(session)) { session.close(); } }
  cancel(): void { for (const session of this.sessions) { this.close(session); } }

  async request(base: string, path: string, cookie: string, limit: number,
    consume?: (data: ArrayBuffer) => void, body?: string): Promise<GatewayResponse> {
    if (GatewayUrl.origin(base) !== base || !path.startsWith('/api/') ||
      /[%\\]|(?:^|\/)\.{1,2}(?:\/|$)/.test(path.split('?')[0]) || /[\r\n]/.test(cookie)) {
      throw new Error('无效网关请求');
    }
    const chunks: Uint8Array[] = []; let bytes = 0, overflow = false, failed = false;
    let client: rcp.Session | undefined = undefined;
    client = rcp.createSession({ requestConfiguration: {
      transfer: { autoRedirect: false, timeout: { connectMs: Math.min(15000, this.timeout), transferMs: this.timeout } },
      tracing: { httpEventsHandler: { onDataReceive: (data: ArrayBuffer): number => {
        bytes += data.byteLength;
        if (bytes > limit) { overflow = true; if (client) { this.close(client); } return 0; }
        try { if (consume) { consume(data); } else { chunks.push(new Uint8Array(data)); } }
        catch { failed = true; if (client) { this.close(client); } return 0; }
        return data.byteLength;
      } } }
    } });
    const session = client; this.sessions.add(session);
    try {
      const request = new rcp.Request(base + path, body === undefined ? 'GET' : 'POST', {
        Cookie: cookie, Origin: GatewayUrl.httpOrigin(base), 'User-Agent': MobileIdentity.userAgent(),
        'Content-Type': 'application/json', 'Cache-Control': 'no-store', 'Accept-Encoding': 'identity'
      });
      if (body !== undefined) { request.content = body; }
      const response = await session.fetch(request);
      if (overflow || failed) { throw new Error('无法安全接收网关响应'); }
      // Fail closed even if the platform ever ignores the redirect option.
      if (response.effectiveUrl && response.effectiveUrl.toString() !== base + path) {
        throw new Error('网关地址发生跳转');
      }
      const joined = new Uint8Array(consume ? 0 : bytes); let offset = 0;
      if (!consume) { for (const part of chunks) { joined.set(part, offset); offset += part.length; } }
      return { status: response.statusCode, headers: response.headers, data: joined };
    } finally { this.close(session); }
  }

  async json(base: string, path: string, cookie: string, body?: string): Promise<Object> {
    const response = await this.request(base, path, cookie, 2 * 1024 * 1024, undefined, body);
    if (response.status !== 200) { throw new Error('网关请求失败 ' + response.status); }
    return JSON.parse(util.TextDecoder.create('utf-8', { ignoreBOM: true }).decodeToString(response.data)) as Object;
  }
}
