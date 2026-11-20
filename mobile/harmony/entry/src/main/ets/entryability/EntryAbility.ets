import { AbilityConstant, Configuration, ConfigurationConstant, UIAbility, Want } from '@kit.AbilityKit';
import { hilog } from '@kit.PerformanceAnalysisKit';
import { window } from '@kit.ArkUI';
import { ComputerStore } from '../services/ComputerStore';
import { PushManager } from '../services/PushManager';
import { PendingDeepLink } from '../services/PendingDeepLink';

const DOMAIN = 0x0000;
const TAG = 'CodexBridgeAbility';

export const sharedStore = new ComputerStore();

export default class EntryAbility extends UIAbility {
  private mainWindow: window.Window | undefined = undefined;

  onCreate(want: Want, _launchParam: AbilityConstant.LaunchParam): void {
    hilog.info(DOMAIN, TAG, 'Ability onCreate');
    try {
      this.context.getApplicationContext().setColorMode(ConfigurationConstant.ColorMode.COLOR_MODE_NOT_SET);
    } catch (err) {
      hilog.warn(DOMAIN, TAG, 'setColorMode failed: %{public}s', JSON.stringify(err));
    }
    this.handleWant(want);
    this.bootstrapServices();
  }

  onNewWant(want: Want, _launchParam: AbilityConstant.LaunchParam): void {
    hilog.info(DOMAIN, TAG, 'Ability onNewWant');
    this.handleWant(want);
  }

  private handleWant(want: Want): void {
    const target = PushManager.parseNotificationWant(want);
    if (target !== undefined) {
      hilog.info(DOMAIN, TAG, 'DeepLink target: %{public}s thread=%{public}s', target.origin, target.thread);
      PendingDeepLink.push(target);
    }
  }

  private bootstrapServices(): void {
    sharedStore.init(this.context).then(async () => {
      await PushManager.ensurePermission(this.context);
      const token = await PushManager.initAndFetchToken(this, sharedStore);
      if (token.length > 0) {
        await PushManager.registerAllComputers(sharedStore);
      }
    }).catch((err: Error) => {
      hilog.warn(DOMAIN, TAG, 'bootstrapServices error: %{public}s', err.message);
    });
  }

  onWindowStageCreate(windowStage: window.WindowStage): void {
    this.mainWindow = windowStage.getMainWindowSync();
    this.applyWindowTheme();
    windowStage.loadContent('pages/Index', (err) => {
      if (err.code) {
        hilog.error(DOMAIN, TAG, 'Failed to load Index: %{public}s', JSON.stringify(err));
        return;
      }
      this.applyWindowTheme();
    });
  }

  onConfigurationUpdate(_newConfig: Configuration): void {
    this.applyWindowTheme();
  }

  private applyWindowTheme(): void {
    const win = this.mainWindow;
    if (win === undefined) {
      return;
    }
    const isDark = this.context.config.colorMode === ConfigurationConstant.ColorMode.COLOR_MODE_DARK;
    const bg = isDark ? '#0B0F17' : '#F6F7F9';
    const content = isDark ? '#F2F4FA' : '#1B2437';
    try {
      win.setWindowBackgroundColor(bg);
      win.setWindowSystemBarProperties({
        statusBarColor: bg,
        statusBarContentColor: content,
        navigationBarColor: bg,
        navigationBarContentColor: content
      }).catch(() => {});
    } catch {
      // ignore
    }
  }

  onForeground(): void {
    PushManager.initAndFetchToken(this, sharedStore).then((token) => {
      if (token.length > 0) {
        PushManager.registerAllComputers(sharedStore).catch(() => {});
      }
    }).catch(() => {});
  }
}
