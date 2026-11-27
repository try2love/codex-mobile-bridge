import { common } from '@kit.AbilityKit';
import { window } from '@kit.ArkUI';

export class SystemBars {
  static async apply(context: common.Context, top: string, current: () => boolean): Promise<void> {
    const target = await window.getLastWindow(context);
    if (!current()) { return; }
    const channels = [1, 3, 5].map(index => parseInt(top.slice(index, index + 2), 16));
    const bright = channels[0] * 0.299 + channels[1] * 0.587 + channels[2] * 0.114 > 150;
    // HarmonyOS does not support the three-key navigation-bar color setters.
    await target.setWindowSystemBarProperties({ statusBarColor: top, statusBarContentColor: bright ? '#000000' : '#FFFFFF' });
  }
}
