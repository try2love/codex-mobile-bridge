import userAuth from '@ohos.userIAM.userAuth';
import { util } from '@kit.ArkTS';

export class DeviceAuthentication {
  static verify(): Promise<boolean> {
    return new Promise((resolve) => {
      try {
        const challenge = new util.TextEncoder().encodeInto(util.generateRandomUUID().replace(/-/g, ''));
        const instance = userAuth.getUserAuthInstance({ challenge,
          authType: [userAuth.UserAuthType.PIN, userAuth.UserAuthType.FINGERPRINT, userAuth.UserAuthType.FACE],
          authTrustLevel: userAuth.AuthTrustLevel.ATL1 }, { title: '查看离线聊天' });
        instance.on('result', { onResult: result => { instance.off('result'); resolve(result.result === 12500000); } });
        instance.start();
      } catch { resolve(false); }
    });
  }
}
