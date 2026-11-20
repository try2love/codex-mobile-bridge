import { preferences } from '@kit.ArkData';
import { common } from '@kit.AbilityKit';
import { util } from '@kit.ArkTS';

export interface ComputerRecord {
  origin: string;
  name: string;
  endpoints: string[];
  activeUrl: string;
  deviceId: string;
  lastLatencyMs: number;
  online: boolean;
}

export interface LastWorkspace {
  origin: string;
  name: string;
  url: string;
}

const STORE_NAME = 'codex_bridge_store';
const KEY_COMPUTERS = 'computers_json';
const KEY_PUSH_ENABLED = 'native_push_enabled';
const KEY_PUSH_DEVICE_ID = 'push_device_id';
const KEY_HMS_TOKEN = 'hms_push_token';
const KEY_THEME = 'settings_theme';
const KEY_LANGUAGE = 'settings_language';
const KEY_LAST_WORKSPACE = 'last_workspace_json';

export class ComputerStore {
  private prefs: preferences.Preferences | undefined = undefined;

  async init(context: common.Context): Promise<void> {
    if (this.prefs !== undefined) {
      return;
    }
    this.prefs = await preferences.getPreferences(context, STORE_NAME);
  }

  private ensurePrefs(): preferences.Preferences {
    if (this.prefs === undefined) {
      throw new Error('ComputerStore not initialized');
    }
    return this.prefs;
  }

  listComputers(): ComputerRecord[] {
    const raw = this.ensurePrefs().getSync(KEY_COMPUTERS, '[]') as string;
    try {
      const parsed = JSON.parse(raw) as ComputerRecord[];
      if (Array.isArray(parsed)) {
        return parsed;
      }
    } catch {
      // ignore corrupted list
    }
    return [];
  }

  async saveComputers(list: ComputerRecord[]): Promise<void> {
    const prefs = this.ensurePrefs();
    prefs.putSync(KEY_COMPUTERS, JSON.stringify(list));
    await prefs.flush();
  }

  async upsertComputer(origin: string, name: string, endpoints?: string[], deviceId?: string): Promise<ComputerRecord[]> {
    const list = this.listComputers();
    const mergedEndpoints = endpoints && endpoints.length > 0 ? [...endpoints] : [origin];
    if (!mergedEndpoints.includes(origin)) {
      mergedEndpoints.unshift(origin);
    }
    const idx = list.findIndex((item) =>
      item.origin === origin ||
      item.endpoints.some((ep) => mergedEndpoints.includes(ep)) ||
      (deviceId && deviceId.length > 0 && item.deviceId === deviceId)
    );
    if (idx >= 0) {
      const existing = list[idx];
      const nextList = [...list];
      const combinedEndpoints: string[] = [...existing.endpoints];
      for (let i = 0; i < mergedEndpoints.length; i++) {
        if (!combinedEndpoints.includes(mergedEndpoints[i])) {
          combinedEndpoints.push(mergedEndpoints[i]);
        }
      }
      nextList[idx] = {
        origin: existing.origin,
        name: name.length > 0 ? name : existing.name,
        endpoints: combinedEndpoints,
        activeUrl: origin,
        deviceId: deviceId && deviceId.length > 0 ? deviceId : existing.deviceId,
        lastLatencyMs: existing.lastLatencyMs,
        online: existing.online
      };
      await this.saveComputers(nextList);
      return nextList;
    }
    const next: ComputerRecord = {
      origin: origin,
      name: name.length > 0 ? name : origin.replace(/^https?:\/\//, ''),
      endpoints: mergedEndpoints,
      activeUrl: origin,
      deviceId: deviceId ?? '',
      lastLatencyMs: -1,
      online: false
    };
    const nextList = [next, ...list];
    await this.saveComputers(nextList);
    return nextList;
  }

  async removeComputer(origin: string): Promise<ComputerRecord[]> {
    const nextList = this.listComputers().filter((item) => item.origin !== origin);
    await this.saveComputers(nextList);
    return nextList;
  }

  async addEndpoint(cardOrigin: string, newEndpoint: string): Promise<ComputerRecord[]> {
    const list = this.listComputers();
    const nextList = list.map((item) => {
      if (item.origin !== cardOrigin) {
        return item;
      }
      const nextEndpoints = item.endpoints.includes(newEndpoint) ? item.endpoints : [newEndpoint, ...item.endpoints];
      return {
        origin: item.origin,
        name: item.name,
        endpoints: nextEndpoints,
        activeUrl: item.activeUrl || item.origin,
        deviceId: item.deviceId,
        lastLatencyMs: item.lastLatencyMs,
        online: item.online
      };
    });
    await this.saveComputers(nextList);
    return nextList;
  }

  async removeEndpoint(cardOrigin: string, targetEndpoint: string): Promise<ComputerRecord[]> {
    const list = this.listComputers();
    const nextList = list.map((item) => {
      if (item.origin !== cardOrigin) {
        return item;
      }
      const remaining = item.endpoints.filter((ep) => ep !== targetEndpoint);
      if (remaining.length === 0) {
        return item;
      }
      const nextActive = item.activeUrl === targetEndpoint ? remaining[0] : (item.activeUrl || remaining[0]);
      return {
        origin: item.origin,
        name: item.name,
        endpoints: remaining,
        activeUrl: nextActive,
        deviceId: item.deviceId,
        lastLatencyMs: item.lastLatencyMs,
        online: item.online
      };
    });
    await this.saveComputers(nextList);
    return nextList;
  }

  async mergeMultiHomeCards(knownGroup: string[]): Promise<ComputerRecord[]> {
    const list = this.listComputers();
    const matches = list.filter((item) =>
      knownGroup.includes(item.origin) || item.endpoints.some((ep) => knownGroup.includes(ep))
    );
    if (matches.length === 0) {
      return list;
    }
    const primary = matches.find((m) => m.name && !/^https?:\/\//.test(m.name) && !/^[0-9.]+:/.test(m.name)) ?? matches[0];
    const combined: string[] = [];
    for (let i = 0; i < knownGroup.length; i++) {
      if (!combined.includes(knownGroup[i])) {
        combined.push(knownGroup[i]);
      }
    }
    for (let i = 0; i < matches.length; i++) {
      const m = matches[i];
      if (!combined.includes(m.origin)) {
        combined.push(m.origin);
      }
      for (let j = 0; j < m.endpoints.length; j++) {
        if (!combined.includes(m.endpoints[j])) {
          combined.push(m.endpoints[j]);
        }
      }
    }
    const mergedCard: ComputerRecord = {
      origin: primary.origin,
      name: primary.name,
      endpoints: combined,
      activeUrl: combined[0],
      deviceId: primary.deviceId,
      lastLatencyMs: primary.lastLatencyMs,
      online: primary.online
    };
    const rest = list.filter((item) => !matches.includes(item));
    const nextList = [mergedCard, ...rest];
    await this.saveComputers(nextList);
    return nextList;
  }

  isPushEnabled(): boolean {
    return this.ensurePrefs().getSync(KEY_PUSH_ENABLED, true) as boolean;
  }

  async setPushEnabled(enabled: boolean): Promise<void> {
    const prefs = this.ensurePrefs();
    prefs.putSync(KEY_PUSH_ENABLED, enabled);
    await prefs.flush();
  }

  getOrCreatePushDeviceId(): string {
    const prefs = this.ensurePrefs();
    const existing = prefs.getSync(KEY_PUSH_DEVICE_ID, '') as string;
    if (existing.length > 0) {
      return existing;
    }
    const generated = util.generateRandomUUID(true);
    prefs.putSync(KEY_PUSH_DEVICE_ID, generated);
    prefs.flush();
    return generated;
  }

  getHmsToken(): string {
    return this.ensurePrefs().getSync(KEY_HMS_TOKEN, '') as string;
  }

  async setHmsToken(token: string): Promise<void> {
    const prefs = this.ensurePrefs();
    prefs.putSync(KEY_HMS_TOKEN, token);
    await prefs.flush();
  }

  getTheme(): string {
    return this.ensurePrefs().getSync(KEY_THEME, 'system') as string;
  }

  async setTheme(theme: string): Promise<void> {
    const prefs = this.ensurePrefs();
    prefs.putSync(KEY_THEME, theme);
    await prefs.flush();
  }

  getLanguage(): string {
    return this.ensurePrefs().getSync(KEY_LANGUAGE, 'zh') as string;
  }

  async setLanguage(lang: string): Promise<void> {
    const prefs = this.ensurePrefs();
    prefs.putSync(KEY_LANGUAGE, lang);
    await prefs.flush();
  }

  getLastWorkspace(): LastWorkspace | undefined {
    const raw = this.ensurePrefs().getSync(KEY_LAST_WORKSPACE, '') as string;
    if (raw.length === 0) {
      return undefined;
    }
    try {
      const parsed = JSON.parse(raw) as LastWorkspace;
      if (parsed && typeof parsed.origin === 'string' && parsed.origin.length > 0 &&
          typeof parsed.url === 'string' && parsed.url.length > 0) {
        return {
          origin: parsed.origin,
          name: typeof parsed.name === 'string' ? parsed.name : '',
          url: parsed.url
        };
      }
    } catch {
      // ignore corrupted workspace record
    }
    return undefined;
  }

  async setLastWorkspace(origin: string, name: string, url: string): Promise<void> {
    const prefs = this.ensurePrefs();
    const record: LastWorkspace = { origin: origin, name: name, url: url };
    prefs.putSync(KEY_LAST_WORKSPACE, JSON.stringify(record));
    await prefs.flush();
  }

  async clearLastWorkspace(): Promise<void> {
    const prefs = this.ensurePrefs();
    prefs.putSync(KEY_LAST_WORKSPACE, '');
    await prefs.flush();
  }
}
