import { asset } from '@kit.AssetStoreKit';
import { util } from '@kit.ArkTS';
import { webview } from '@kit.ArkWeb';
import { Digest } from './Digest';
import { GatewayHttp } from './GatewayHttp';
import { GatewayUrl } from './GatewayUrl';

interface BindingValue { name: string; token: string; }
interface BindingAuth { authenticated?: boolean; cookieName?: string; }

/** Stores only a server-issued, revocable session token; never a gateway password. */
export class DeviceBinding {
  private static encoder: util.TextEncoder = new util.TextEncoder();
  private static async alias(base: string): Promise<Uint8Array> {
    return DeviceBinding.encoder.encodeInto('bridge-binding-' + await Digest.sha256(DeviceBinding.encoder.encodeInto(GatewayUrl.origin(base))));
  }
  static cookies(base: string): string { return webview.WebCookieManager.fetchCookieSync(base + '/', false, true); }
  static credential(cookie: string, name: string): BindingValue | null {
    if (!/^codex_mobile_session(?:_[a-f0-9]{24})?$/.test(name)) { return null; }
    const matches = cookie.split(';').map(part => part.trim()).filter(part => part.startsWith(name + '='));
    if (matches.length !== 1) { return null; }
    const token = matches[0].slice(name.length + 1);
    return /^[A-Za-z0-9_-]{32,128}$/.test(token) ? { name, token } : null;
  }
  static async capture(base: string, cookie: string, name: string): Promise<void> {
    const saved = DeviceBinding.credential(cookie, name); if (!saved) { return; }
    const attributes: asset.AssetMap = new Map();
    attributes.set(asset.Tag.ALIAS, await DeviceBinding.alias(base));
    attributes.set(asset.Tag.SECRET, DeviceBinding.encoder.encodeInto(JSON.stringify(saved)));
    attributes.set(asset.Tag.ACCESSIBILITY, asset.Accessibility.DEVICE_UNLOCKED);
    attributes.set(asset.Tag.CONFLICT_RESOLUTION, asset.ConflictResolution.OVERWRITE);
    await asset.add(attributes);
  }
  static async clear(base: string): Promise<void> {
    const query: asset.AssetMap = new Map(); query.set(asset.Tag.ALIAS, await DeviceBinding.alias(base)); await asset.remove(query);
  }
  static async restore(base: string, current: () => boolean): Promise<boolean> {
    if (!current() || /(?:^|;\s*)codex_mobile_session(?:_[a-f0-9]{24})?=/.test(DeviceBinding.cookies(base))) { return false; }
    const query: asset.AssetMap = new Map(); query.set(asset.Tag.ALIAS, await DeviceBinding.alias(base)); query.set(asset.Tag.RETURN_TYPE, asset.ReturnType.ALL);
    let entries: asset.AssetMap[];
    try { entries = await asset.query(query); } catch { return false; }
    if (entries.length !== 1 || !current()) { return false; }
    const raw = entries[0].get(asset.Tag.SECRET) as Uint8Array;
    const saved = JSON.parse(util.TextDecoder.create('utf-8', { ignoreBOM: true }).decodeToString(raw)) as BindingValue;
    if (!DeviceBinding.credential(saved.name + '=' + saved.token, saved.name)) { return false; }
    const cookie = saved.name + '=' + saved.token;
    let proof: BindingAuth;
    try { proof = await new GatewayHttp(4000).json(base, '/api/auth', cookie) as BindingAuth; } catch { return false; }
    if (proof.authenticated !== true || (proof.cookieName || 'codex_mobile_session') !== saved.name) {
      if (current()) { await DeviceBinding.clear(base); } return false;
    }
    if (!current() || /(?:^|;\s*)codex_mobile_session(?:_[a-f0-9]{24})?=/.test(DeviceBinding.cookies(base))) { return false; }
    const prefix = base.slice(GatewayUrl.httpOrigin(base).length) || '/';
    webview.WebCookieManager.configCookieSync(base + '/', cookie + '; Path=' + prefix + '; HttpOnly; SameSite=Strict' +
      (base.startsWith('https://') ? '; Secure' : ''), false, true);
    await webview.WebCookieManager.saveCookieAsync(); return true;
  }
}
