import { bundleManager } from '@kit.AbilityKit';
import { deviceInfo } from '@kit.BasicServicesKit';

export class MobileIdentity {
  static userAgent(): string {
    const info = bundleManager.getBundleInfoForSelfSync(bundleManager.BundleFlag.GET_BUNDLE_INFO_DEFAULT);
    return 'BridgeMobile/' + info.versionName + '-HarmonyOS BridgeBuild/' + info.versionCode +
      ' BridgeModel/' + encodeURIComponent(deviceInfo.productModel) + ' BridgeOS/' + encodeURIComponent(deviceInfo.displayVersion);
  }
}
