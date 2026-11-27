import { common } from '@kit.AbilityKit';
import { preferences } from '@kit.ArkData';

export class NativePreferences {
  private prefs: preferences.Preferences | undefined = undefined;
  async init(context: common.Context): Promise<void> {
    this.prefs = await preferences.getPreferences(context, 'bridge_phone_settings');
  }
  read(key: string): string {
    if (!['appearance', 'composer-preferences', 'theme', 'language'].includes(key) || !this.prefs) { return ''; }
    return this.prefs.getSync(key, '') as string;
  }
  async save(key: string, value: string): Promise<void> {
    if (!this.prefs || !['appearance', 'composer-preferences', 'theme', 'language'].includes(key) || value.length > 65536) {
      throw new Error('无法保存设置');
    }
    this.prefs.putSync(key, value); await this.prefs.flush();
  }
}
