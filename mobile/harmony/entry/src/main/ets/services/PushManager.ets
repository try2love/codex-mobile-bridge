import { pushService } from '@kit.PushKit';
import { notificationManager } from '@kit.NotificationKit';
import { common, UIAbility, Want } from '@kit.AbilityKit';
import { hilog } from '@kit.PerformanceAnalysisKit';
import { ComputerStore } from './ComputerStore';
import { ChannelManager } from './ChannelManager';
import { DeepLinkTarget } from './GatewayUrl';

const TAG = 'CodexBridgePush';
const DOMAIN = 0x0000;

interface PushRegisterBody {
  kind: string;
  token: string;
  deviceId: string;
  sourceIcon: boolean;
  enabled: boolean;
}

export interface PushStatusInfo {
  notificationEnabled: boolean;
  hmsToken: string;
  hmsError: string;
}

export class PushManager {
  private static cachedToken = '';
  private static lastError = '';
  private static tokenListenerBound = false;

  static async ensurePermission(context: common.UIAbilityContext): Promise<boolean> {
    try {
      const enabled = await notificationManager.isNotificationEnabled();
      if (enabled) {
        return true;
      }
      await notificationManager.requestEnableNotification(context);
      return await notificationManager.isNotificationEnabled();
    } catch (err) {
      hilog.warn(DOMAIN, TAG, 'requestEnableNotification failed: %{public}s', JSON.stringify(err));
      return false;
    }
  }

  static async initAndFetchToken(ability: UIAbility, store: ComputerStore): Promise<string> {
    if (!PushManager.tokenListenerBound) {
      PushManager.tokenListenerBound = true;
      try {
        pushService.on('tokenUpdate', ability, (token: string) => {
          if (token && token.length > 0) {
            PushManager.cachedToken = token;
            PushManager.lastError = '';
            store.setHmsToken(token).catch(() => {});
            PushManager.registerAllComputers(store).catch(() => {});
          }
        });
      } catch (err) {
        hilog.warn(DOMAIN, TAG, 'pushService.on tokenUpdate failed: %{public}s', JSON.stringify(err));
      }
    }
    try {
      const token = await pushService.getToken();
      if (token && token.length > 0) {
        PushManager.cachedToken = token;
        PushManager.lastError = '';
        await store.setHmsToken(token);
        return token;
      }
    } catch (err) {
      PushManager.lastError = JSON.stringify(err);
      hilog.warn(DOMAIN, TAG, 'pushService.getToken failed: %{public}s', PushManager.lastError);
    }
    const saved = store.getHmsToken();
    if (saved.length > 0) {
      PushManager.cachedToken = saved;
    }
    return PushManager.cachedToken;
  }

  static getStatus(): PushStatusInfo {
    return {
      notificationEnabled: true,
      hmsToken: PushManager.cachedToken,
      hmsError: PushManager.lastError
    };
  }

  static async registerOrigin(store: ComputerStore, origin: string): Promise<boolean> {
    const token = PushManager.cachedToken || store.getHmsToken();
    if (!token || !store.isPushEnabled()) {
      return false;
    }
    const auth = await ChannelManager.fetchAuth(origin);
    if (!auth.authenticated || !auth.csrf) {
      return false;
    }
    const deviceId = store.getOrCreatePushDeviceId();
    const payload: PushRegisterBody = {
      kind: 'huawei',
      token: token,
      deviceId: deviceId,
      sourceIcon: true,
      enabled: true
    };
    const status = await ChannelManager.postJson(origin, '/api/mobile/push', JSON.stringify(payload), auth.csrf);
    hilog.info(DOMAIN, TAG, 'registerOrigin %{public}s -> HTTP %{public}d', origin, status);
    return status >= 200 && status < 300;
  }

  static async registerAllComputers(store: ComputerStore): Promise<void> {
    const list = store.listComputers();
    for (let i = 0; i < list.length; i++) {
      const comp = list[i];
      const target = comp.activeUrl || comp.origin;
      try {
        await PushManager.registerOrigin(store, target);
      } catch {
        // ignore offline computers
      }
    }
  }

  static parseNotificationWant(want: Want): DeepLinkTarget | undefined {
    const params = want.parameters;
    if (!params) {
      return undefined;
    }
    let originVal = params['origin'];
    let threadVal = params['thread'];
    let hostVal = params['host'];

    // Huawei Push clickAction.data may arrive nested under 'data' or as a JSON string
    if (typeof originVal !== 'string' && params['data'] !== undefined) {
      const rawData = params['data'];
      if (typeof rawData === 'string') {
        try {
          const parsed = JSON.parse(rawData) as Record<string, Object>;
          originVal = parsed['origin'];
          threadVal = parsed['thread'];
          hostVal = parsed['host'];
        } catch {
          // ignore
        }
      } else if (typeof rawData === 'object' && rawData !== null) {
        const obj = rawData as Record<string, Object>;
        originVal = obj['origin'];
        threadVal = obj['thread'];
        hostVal = obj['host'];
      }
    }

    if (typeof originVal === 'string' && originVal.length > 0 &&
      typeof threadVal === 'string' && threadVal.length > 0) {
      return {
        origin: originVal,
        thread: threadVal,
        host: typeof hostVal === 'string' && hostVal.length > 0 ? hostVal : 'local'
      };
    }
    return undefined;
  }
}
