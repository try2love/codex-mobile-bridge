export interface DeepLinkTarget {
  origin: string;
  thread: string;
  host: string;
}

export class GatewayUrl {
  static isLocalHost(host: string): boolean {
    const lower = host.toLowerCase();
    if (lower === 'localhost' || lower === '[::1]' || lower === '::1') {
      return true;
    }
    const parts = lower.split('.');
    if (parts.length !== 4) {
      return false;
    }
    const nums: number[] = [];
    for (let i = 0; i < 4; i++) {
      const p = parts[i];
      if (!/^[0-9]{1,3}$/.test(p)) {
        return false;
      }
      const n = Number(p);
      if (n < 0 || n > 255 || String(n) !== p) {
        return false;
      }
      nums.push(n);
    }
    return nums[0] === 10 || nums[0] === 127 || (nums[0] === 192 && nums[1] === 168) ||
      (nums[0] === 172 && nums[1] >= 16 && nums[1] <= 31);
  }

  static origin(value: string): string {
    const trimmed = value.trim();
    const match = /^(https?):\/\/([^\/?#]+)(\/[^?#]*)?(\?[^#]*)?(#.*)?$/i.exec(trimmed);
    if (!match) {
      throw new Error('请填写完整网关地址，例如 https://... 或 http://192.168.x.x:9999');
    }
    const scheme = match[1].toLowerCase();
    const authority = match[2];
    if (authority.includes('@')) {
      throw new Error('网关地址不应包含账号信息');
    }
    const hostPortMatch = /^(\[[^\]]+\]|[^:]+)(?::([0-9]+))?$/.exec(authority);
    if (!hostPortMatch) {
      throw new Error('网关主机格式不正确');
    }
    const host = hostPortMatch[1];
    if (hostPortMatch[2] !== undefined) {
      const port = Number(hostPortMatch[2]);
      if (port <= 0 || port > 65535) {
        throw new Error('网关端口无效');
      }
    }
    if (scheme !== 'https' && !(scheme === 'http' && GatewayUrl.isLocalHost(host))) {
      throw new Error('公网地址必须使用 HTTPS；HTTP 仅限局域网');
    }
    let path = match[3] ?? '';
    if (path !== '' && path !== '/' && !/^\/d\/[a-f0-9]{32}\/?$/i.test(path)) {
      throw new Error('网关地址不应包含多余路径');
    }
    if (path.startsWith('/d/')) {
      path = path.replace(/\/$/, '');
    } else {
      path = '';
    }
    return `${scheme}://${authority.toLowerCase()}${path}`;
  }

  static connection(value: string): string {
    const trimmed = value.trim();
    const hashIdx = trimmed.indexOf('#');
    const basePart = hashIdx >= 0 ? trimmed.slice(0, hashIdx) : trimmed;
    const fragment = hashIdx >= 0 ? trimmed.slice(hashIdx + 1) : '';
    const base = GatewayUrl.origin(basePart);
    if (fragment.length > 0 && !/^pair=[A-Za-z0-9_-]{43}$/.test(fragment)) {
      throw new Error('请使用电脑网关提供的地址或二维码');
    }
    return fragment.length > 0 ? `${base}/#${fragment}` : `${base}/`;
  }

  static httpOrigin(base: string): string {
    const match = /^(https?:\/\/[^\/?#]+)/i.exec(base.trim());
    return match ? match[1].toLowerCase() : '';
  }

  static cookiePath(base: string): string {
    const httpOrg = GatewayUrl.httpOrigin(base);
    if (!httpOrg || base.length < httpOrg.length) {
      return '/';
    }
    return base.slice(httpOrg.length) + '/';
  }

  static sameOrigin(url: string, origin: string): boolean {
    if (!url || !origin) {
      return false;
    }
    const urlHttp = GatewayUrl.httpOrigin(url);
    const orgHttp = GatewayUrl.httpOrigin(origin);
    if (!urlHttp || urlHttp !== orgHttp) {
      return false;
    }
    const rest = url.slice(urlHttp.length);
    const pathOnly = rest.split(/[?#]/)[0] || '/';
    const prefix = GatewayUrl.cookiePath(origin);
    return pathOnly.startsWith(prefix) && !pathOnly.includes('%') && !pathOnly.includes('/../');
  }

  static chatHash(thread: string, host: string): string {
    const safeThread = encodeURIComponent(thread).replace(/\*/g, '%2A');
    const safeHost = encodeURIComponent(host);
    return `#${safeThread}~${safeHost}`;
  }

  static chatUrl(origin: string, thread: string, host: string): string {
    const base = GatewayUrl.origin(origin);
    return `${base}/${GatewayUrl.chatHash(thread, host)}`;
  }

  static isChatPage(url: string, base: string): boolean {
    if (!url || !base) {
      return false;
    }
    try {
      const noHash = url.split('#')[0];
      const qIdx = noHash.indexOf('?');
      const pathPart = qIdx >= 0 ? noHash.slice(0, qIdx) : noHash;
      return GatewayUrl.origin(pathPart) === base;
    } catch {
      return false;
    }
  }
}
