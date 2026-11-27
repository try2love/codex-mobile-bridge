import { GatewayUrl } from './GatewayUrl';

/** Every asynchronous native result is bound to the current document. */
export class DocumentGuard {
  base: string = '';
  generation: number = 0;
  foreground: boolean = true;
  private touchedAt: number = 0;

  navigate(base: string): number {
    this.base = GatewayUrl.origin(base); this.touchedAt = 0;
    return ++this.generation;
  }
  touch(now: number = Date.now()): void { this.touchedAt = now; }
  accepts(url: string, current: string, generation: number = this.generation): boolean {
    return this.foreground && generation === this.generation && !!this.base &&
      GatewayUrl.sameOrigin(url, this.base) && GatewayUrl.sameOrigin(current, this.base);
  }
  consumeTouch(now: number = Date.now()): boolean {
    const touched = this.touchedAt; this.touchedAt = 0;
    return this.foreground && touched > 0 && now >= touched && now - touched <= 10000;
  }
}
