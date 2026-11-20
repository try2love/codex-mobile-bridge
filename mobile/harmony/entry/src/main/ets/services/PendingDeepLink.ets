import { DeepLinkTarget } from './GatewayUrl';

type DeepLinkListener = (target: DeepLinkTarget) => void;

export class PendingDeepLink {
  private static current: DeepLinkTarget | undefined = undefined;
  private static listener: DeepLinkListener | undefined = undefined;

  static push(target: DeepLinkTarget): void {
    PendingDeepLink.current = target;
    if (PendingDeepLink.listener !== undefined) {
      const cb = PendingDeepLink.listener;
      cb(target);
    }
  }

  static consume(): DeepLinkTarget | undefined {
    const target = PendingDeepLink.current;
    PendingDeepLink.current = undefined;
    return target;
  }

  static setListener(listener: DeepLinkListener | undefined): void {
    PendingDeepLink.listener = listener;
  }
}
