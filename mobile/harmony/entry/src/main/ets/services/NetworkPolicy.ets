import { connection } from '@kit.NetworkKit';
import { util } from '@kit.ArkTS';

export class NetworkPolicy {
  available: boolean = false;
  unmetered: boolean = false;
  epoch: string = util.generateRandomUUID();
  private fingerprint: string = '';
  private monitor: connection.NetConnection | null = null;
  private revision: number = 0;
  private changed: () => void;
  constructor(changed: () => void) { this.changed = changed; }
  async start(): Promise<void> {
    if (this.monitor) { return; }
    const monitor = connection.createNetConnection(); this.monitor = monitor;
    const update = (): void => { if (this.monitor === monitor) { this.refresh().catch(() => {}); } };
    monitor.on('netAvailable', update); monitor.on('netLost', update); monitor.on('netCapabilitiesChange', update);
    try { await monitor.register(); if (this.monitor === monitor) { await this.refresh(); } }
    catch { if (this.monitor === monitor) { this.monitor = null; this.available = false; this.unmetered = false; this.changed(); } }
  }
  stop(): void {
    const previous = this.monitor; this.monitor = null; this.revision++;
    if (previous) { previous.unregister().catch(() => {}); }
    this.available = false; this.unmetered = false; this.fingerprint = ''; this.epoch = util.generateRandomUUID(); this.changed();
  }
  private async refresh(): Promise<void> {
    const monitor = this.monitor, revision = ++this.revision; let fingerprint = 'offline', available = false, unmetered = false;
    try {
      const handle = await connection.getDefaultNet();
      if (handle.netId > 0) {
        const caps = await connection.getNetCapabilities(handle); available = true;
        unmetered = caps.networkCap.includes(connection.NetCap.NET_CAPABILITY_NOT_METERED) &&
          (caps.bearerTypes.includes(connection.NetBearType.BEARER_WIFI) || caps.bearerTypes.includes(connection.NetBearType.BEARER_ETHERNET));
        fingerprint = JSON.stringify([handle.netId, caps.networkCap, caps.bearerTypes]);
      }
    } catch {}
    if (this.monitor !== monitor || !monitor || revision !== this.revision) { return; }
    const changed = this.available !== available || this.unmetered !== unmetered || fingerprint !== this.fingerprint;
    if (fingerprint !== this.fingerprint) { this.fingerprint = fingerprint; this.epoch = util.generateRandomUUID(); }
    this.available = available; this.unmetered = unmetered;
    if (changed) { this.changed(); }
  }
}
