import { preferences } from '@kit.ArkData';
import { common } from '@kit.AbilityKit';
import { GatewayUrl } from './GatewayUrl';

export class ComputerStore {
  private prefs: preferences.Preferences | undefined = undefined;

  async init(context: common.Context): Promise<void> {
    this.prefs = await preferences.getPreferences(context, 'bridge_experimental_addresses');
  }

  list(): string[] {
    if (!this.prefs) { return []; }
    try {
      const values = JSON.parse(this.prefs.getSync('origins', '[]') as string) as string[];
      if (!Array.isArray(values)) { return []; }
      const result: string[] = [];
      for (const value of values) {
        if (typeof value !== 'string') { continue; }
        try {
          const origin = GatewayUrl.origin(value);
          if (!result.includes(origin)) { result.push(origin); }
        } catch { /* Malformed addresses never become bindings. */ }
      }
      return result;
    } catch { return []; }
  }

  async save(values: string[]): Promise<void> {
    if (!this.prefs) { throw new Error('地址存储尚未就绪'); }
    const origins = values.map((value: string) => GatewayUrl.origin(value));
    this.prefs.putSync('origins', JSON.stringify(origins));
    await this.prefs.flush();
  }

  name(origin: string): string {
    return this.prefs?.getSync('name:' + GatewayUrl.origin(origin), '') as string || origin;
  }
  async rename(origin: string, value: string): Promise<void> {
    if (!this.prefs || !this.list().includes(GatewayUrl.origin(origin)) || value.trim().length > 80) {
      throw new Error('无法保存电脑名称');
    }
    this.prefs.putSync('name:' + GatewayUrl.origin(origin), value.trim()); await this.prefs.flush();
  }
}
