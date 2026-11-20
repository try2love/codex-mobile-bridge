import { http } from '@kit.NetworkKit';
import { webview } from '@kit.ArkWeb';
import { hilog } from '@kit.PerformanceAnalysisKit';
import { GatewayUrl } from './GatewayUrl';

export interface ProbeResult {
  url: string;
  online: boolean;
  latencyMs: number;
  deviceId: string;
  endpoints: string[];
}

export interface AuthSnapshot {
  authenticated: boolean;
  csrf: string;
  computerName: string;
  deviceId: string;
  endpoints: string[];
}

export interface MobileEventItem {
  id: string;
  sequence: number;
  threadId: string;
  host: string;
  provider: string;
  kind: string;
  title: string;
  body: string;
  createdAt: number;
  unread: boolean;
}

export interface MobileEventsSnapshot {
  enabled: boolean;
  streamId: string;
  cursor: number;
  events: MobileEventItem[];
}

interface HealthResponse {
  ok?: boolean;
  deviceId?: string;
  endpoints?: string[];
}

interface MobileRoutesPayload {
  deviceId?: string;
  endpoints?: string[];
}

interface AuthResponse {
  authenticated?: boolean;
  csrf?: string;
  computerName?: string;
  name?: string;
  mobileRoutes?: MobileRoutesPayload;
}

interface RequestHeaders {
  Origin: string;
  'User-Agent': string;
  Cookie?: string;
  'Content-Type'?: string;
  'X-CSRF-Token'?: string;
}

export class ChannelManager {
  static readonly USER_AGENT = 'BridgeMobile/2.2.1-HarmonyOS';

  static getCookies(origin: string): string {
    try {
      return webview.WebCookieManager.fetchCookieSync(origin + '/') ?? '';
    } catch {
      return '';
    }
  }

  private static isLocalEndpoint(targetUrl: string): boolean {
    const match = /^https?:\/\/([^\/:?#]+)/i.exec(targetUrl.trim());
    return match ? GatewayUrl.isLocalHost(match[1]) : false;
  }

  static async probeEndpoint(targetUrl: string): Promise<ProbeResult> {
    const isLocal = ChannelManager.isLocalEndpoint(targetUrl);
    const timeoutMs = isLocal ? 2500 : 8000;
    const maxAttempts = isLocal ? 1 : 2;
    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
      const started = Date.now();
      const client = http.createHttp();
      try {
        const headers: RequestHeaders = {
          Origin: GatewayUrl.httpOrigin(targetUrl),
          'User-Agent': ChannelManager.USER_AGENT
        };
        const resp = await client.request(targetUrl + '/api/health', {
          method: http.RequestMethod.GET,
          header: headers,
          connectTimeout: timeoutMs,
          readTimeout: timeoutMs,
          expectDataType: http.HttpDataType.STRING
        });
        const elapsed = Math.max(1, Date.now() - started);
        const raw = String(resp.result ?? '');
        if (resp.responseCode === 200) {
          let deviceId = '';
          let endpoints: string[] = [];
          try {
            const parsed = JSON.parse(raw) as HealthResponse;
            if (typeof parsed.deviceId === 'string') {
              deviceId = parsed.deviceId;
            }
            if (Array.isArray(parsed.endpoints)) {
              endpoints = parsed.endpoints;
            }
          } catch {
            // ignore JSON parse errors
          }
          return { url: targetUrl, online: true, latencyMs: elapsed, deviceId: deviceId, endpoints: endpoints };
        }
        // Gateway /api/health returns 403 over public Tailscale Funnel when unauthenticated/remote;
        // that still confirms the gateway is alive and reachable.
        if ((resp.responseCode === 403 && raw.includes('仅允许本机状态检查')) || resp.responseCode === 401) {
          return { url: targetUrl, online: true, latencyMs: elapsed, deviceId: '', endpoints: [targetUrl] };
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        hilog.info(0x0000, 'CodexBridgeFailover', 'probe fail url=%{public}s attempt=%{public}d err=%{public}s', targetUrl, attempt, msg);
        // retry on transient public/TLS wake failure
      } finally {
        client.destroy();
      }
    }
    return { url: targetUrl, online: false, latencyMs: -1, deviceId: '', endpoints: [] };
  }

  static findBestEndpoint(endpoints: string[]): Promise<ProbeResult | undefined> {
    if (endpoints.length === 0) {
      return Promise.resolve(undefined);
    }
    return new Promise<ProbeResult | undefined>((resolve) => {
      let settled = false;
      let remaining = endpoints.length;
      let publicWinner: ProbeResult | undefined = undefined;
      let localPending = 0;
      for (let i = 0; i < endpoints.length; i++) {
        if (ChannelManager.isLocalEndpoint(endpoints[i])) {
          localPending++;
        }
      }
      for (let i = 0; i < endpoints.length; i++) {
        const ep = endpoints[i];
        const isLocal = ChannelManager.isLocalEndpoint(ep);
        ChannelManager.probeEndpoint(ep).then((res) => {
          if (isLocal) {
            localPending--;
          }
          if (!settled && res.online) {
            if (isLocal || localPending <= 0) {
              settled = true;
              resolve(res);
              return;
            }
            if (publicWinner === undefined || res.latencyMs < publicWinner.latencyMs) {
              publicWinner = res;
            }
            // Give LAN a brief 250ms window to beat public HTTPS when at home.
            setTimeout(() => {
              if (!settled && publicWinner !== undefined) {
                settled = true;
                resolve(publicWinner);
              }
            }, 250);
          }
          remaining--;
          if (!settled && remaining <= 0) {
            settled = true;
            resolve(publicWinner);
          }
        }).catch(() => {
          if (isLocal) {
            localPending--;
          }
          remaining--;
          if (!settled && remaining <= 0) {
            settled = true;
            resolve(publicWinner);
          }
        });
      }
    });
  }

  static async fetchAuth(origin: string): Promise<AuthSnapshot> {
    const client = http.createHttp();
    try {
      const cookie = ChannelManager.getCookies(origin);
      const headers: RequestHeaders = {
        Origin: GatewayUrl.httpOrigin(origin),
        'User-Agent': ChannelManager.USER_AGENT
      };
      if (cookie.length > 0) {
        headers.Cookie = cookie;
      }
      const resp = await client.request(origin + '/api/auth', {
        method: http.RequestMethod.GET,
        header: headers,
        connectTimeout: 6000,
        readTimeout: 6000,
        expectDataType: http.HttpDataType.STRING
      });
      if (resp.responseCode !== 200) {
        return { authenticated: false, csrf: '', computerName: '', deviceId: '', endpoints: [] };
      }
      const data = JSON.parse(String(resp.result)) as AuthResponse;
      const routes = data.mobileRoutes;
      return {
        authenticated: data.authenticated === true,
        csrf: data.csrf ?? '',
        computerName: data.computerName ?? data.name ?? '',
        deviceId: routes?.deviceId ?? '',
        endpoints: Array.isArray(routes?.endpoints) ? routes!.endpoints! : []
      };
    } catch {
      return { authenticated: false, csrf: '', computerName: '', deviceId: '', endpoints: [] };
    } finally {
      client.destroy();
    }
  }

  static async postJson(origin: string, path: string, bodyJson: string, csrf: string): Promise<number> {
    const client = http.createHttp();
    try {
      const cookie = ChannelManager.getCookies(origin);
      const headers: RequestHeaders = {
        Origin: GatewayUrl.httpOrigin(origin),
        'User-Agent': ChannelManager.USER_AGENT,
        'Content-Type': 'application/json',
        'X-CSRF-Token': csrf
      };
      if (cookie.length > 0) {
        headers.Cookie = cookie;
      }
      const resp = await client.request(origin + path, {
        method: http.RequestMethod.POST,
        header: headers,
        extraData: bodyJson,
        connectTimeout: 7000,
        readTimeout: 7000,
        expectDataType: http.HttpDataType.STRING
      });
      return resp.responseCode;
    } finally {
      client.destroy();
    }
  }

  static async fetchMobileEvents(origin: string): Promise<MobileEventsSnapshot | undefined> {
    const client = http.createHttp();
    try {
      const cookie = ChannelManager.getCookies(origin);
      if (cookie.length === 0) {
        return undefined;
      }
      const headers: RequestHeaders = {
        Origin: GatewayUrl.httpOrigin(origin),
        'User-Agent': ChannelManager.USER_AGENT,
        Cookie: cookie
      };
      const resp = await client.request(origin + '/api/mobile/events', {
        method: http.RequestMethod.GET,
        header: headers,
        connectTimeout: 7000,
        readTimeout: 7000,
        expectDataType: http.HttpDataType.STRING
      });
      if (resp.responseCode !== 200) {
        return undefined;
      }
      return JSON.parse(String(resp.result)) as MobileEventsSnapshot;
    } catch {
      return undefined;
    } finally {
      client.destroy();
    }
  }
}
