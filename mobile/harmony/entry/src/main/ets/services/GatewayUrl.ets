import { url } from '@kit.ArkTS';

export class GatewayUrl {
  private static parse(value: string): url.URL {
    if (!value || /[\u0000-\u0020\u007f\\]/.test(value) || !/^https?:\/\//i.test(value)) {
      throw new Error('请填写完整的 HTTP 或 HTTPS 网关地址');
    }
    const parsed = url.URL.parseURL(value);
    if (parsed.username || parsed.password || !parsed.hostname) {
      throw new Error('网关地址不应包含账号信息');
    }
    if (parsed.protocol !== 'https:' && !(parsed.protocol === 'http:' && GatewayUrl.isLocalHost(parsed.hostname))) {
      throw new Error('公网地址必须使用 HTTPS；HTTP 仅限局域网');
    }
    return parsed;
  }

  static isLocalHost(host: string): boolean {
    if (host === 'localhost' || host === '[::1]' || host === '::1') { return true; }
    const parts = host.split('.');
    if (parts.length !== 4 || parts.some((p: string) => !/^\d{1,3}$/.test(p) || String(Number(p)) !== p || Number(p) > 255)) { return false; }
    const nums = parts.map((p: string) => Number(p));
    return nums[0] === 10 || nums[0] === 127 || (nums[0] === 192 && nums[1] === 168) ||
      (nums[0] === 172 && nums[1] >= 16 && nums[1] <= 31);
  }

  static origin(value: string): string {
    const parsed = GatewayUrl.parse(value);
    const rawPath = value.replace(/^https?:\/\/[^/]+/i, '').split(/[?#]/)[0];
    if (/%|(?:^|\/)\.{1,2}(?:\/|$)/.test(rawPath) || parsed.search || parsed.hash ||
        (parsed.pathname !== '/' && !/^\/d\/[a-f0-9]{32}\/?$/.test(parsed.pathname))) {
      throw new Error('请使用网关根地址，不要附加查询参数或其他路径');
    }
    return parsed.origin + parsed.pathname.replace(/\/$/, '');
  }

  static httpOrigin(value: string): string { return GatewayUrl.parse(value).origin; }

  static apiPath(value: string, base: string): string {
    if (!GatewayUrl.sameOrigin(value, base)) { throw new Error('地址不属于当前网关'); }
    return GatewayUrl.parse(value).pathname.slice(GatewayUrl.parse(base).pathname.replace(/\/$/, '').length);
  }

  static connection(value: string): string {
    const trimmed = value.trim();
    const hash = trimmed.indexOf('#');
    const base = GatewayUrl.origin(hash < 0 ? trimmed : trimmed.slice(0, hash));
    const fragment = hash < 0 ? '' : trimmed.slice(hash);
    if (fragment && !/^#pair=[A-Za-z0-9_-]{43}$/.test(fragment)) {
      throw new Error('请使用电脑网关提供的配对二维码');
    }
    return base + '/' + fragment;
  }

  static sameOrigin(value: string, base: string): boolean {
    try {
      const parsed = GatewayUrl.parse(value);
      const expected = GatewayUrl.parse(base);
      const rawPath = value.replace(/^https?:\/\/[^/]+/i, '').split(/[?#]/)[0];
      if (/%|(?:^|\/)\.{1,2}(?:\/|$)/.test(rawPath)) { return false; }
      const prefix = expected.pathname.replace(/\/$/, '');
      return parsed.origin === expected.origin && (parsed.pathname === prefix || parsed.pathname.startsWith(prefix + '/'));
    } catch { return false; }
  }
}
