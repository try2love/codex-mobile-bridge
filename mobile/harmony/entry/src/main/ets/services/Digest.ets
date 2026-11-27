import { cryptoFramework } from '@kit.CryptoArchitectureKit';

export class Digest {
  static hex(data: Uint8Array): string { return Array.from(data).map(value => value.toString(16).padStart(2, '0')).join(''); }
  static async sha256(data: Uint8Array): Promise<string> {
    const digest = cryptoFramework.createMd('SHA256'); await digest.update({ data }); return Digest.hex((await digest.digest()).data);
  }
}
