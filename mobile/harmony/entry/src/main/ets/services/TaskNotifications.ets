import { common, wantAgent, bundleManager } from '@kit.AbilityKit';
import { notificationManager } from '@kit.NotificationKit';
import { preferences } from '@kit.ArkData';
import { util } from '@kit.ArkTS';
import { ArchiveAccount, ArchiveObject } from './OfflineArchive';
import { GatewayHttp } from './GatewayHttp';
import { DeviceBinding } from './DeviceBinding';
import { Digest } from './Digest';

/** A foreground-only receiver. It never registers a cloud push or starts a task. */
export class TaskNotifications {
  private prefs: preferences.Preferences | null = null;
  private context: common.UIAbilityContext | null = null;
  private http: GatewayHttp = new GatewayHttp(75000);
  private generation: number = 0;
  private timer: number = -1;
  private foreground: boolean = true;
  private base: string = '';
  private cookie: string = '';
  private identity: string = '';
  private gateway: string = '';
  async init(context: common.UIAbilityContext): Promise<void> {
    this.context = context; this.prefs = await preferences.getPreferences(context, 'bridge_task_notifications');
  }
  enabled(): boolean { return !!this.base && this.prefs?.getSync('enabled:' + this.base, false) === true; }
  async enable(value: boolean): Promise<void> {
    if (!this.context || !this.prefs || !this.base) { throw new Error('请先登录电脑网关'); }
    if (value) { await notificationManager.requestEnableNotification(this.context); }
    this.prefs.putSync('enabled:' + this.base, value); await this.prefs.flush(); this.restart();
  }
  configure(account: ArchiveAccount, base: string, cookie: string): void {
    if (this.base === base && this.cookie === cookie && this.identity === account.identity) { return; }
    this.base = base; this.cookie = cookie; this.identity = account.identity; this.gateway = account.gateway; this.restart();
  }
  detach(): void { this.stop(); this.base = ''; this.cookie = ''; this.identity = ''; }
  visibility(foreground: boolean): void { this.foreground = foreground; this.restart(); }
  private stop(): void { this.generation++; clearTimeout(this.timer); this.timer = -1; this.http.cancel(); }
  private current(generation: number): boolean {
    return generation === this.generation && this.foreground && this.enabled() && DeviceBinding.cookies(this.base) === this.cookie;
  }
  private restart(): void {
    this.stop(); const generation = this.generation;
    if (this.current(generation)) { this.receive(generation, true).catch(() => {}); }
  }
  private async checkpoint(binding: string): Promise<ArchiveObject> {
    try {
      const raw = this.prefs?.getSync('checkpoint:' + this.base, '') as string;
      const saved = JSON.parse(raw) as ArchiveObject;
      if (saved['binding'] === binding && typeof saved['streamId'] === 'string' && (saved['streamId'] as string).length <= 128 &&
        Number.isSafeInteger(saved['cursor']) && (saved['cursor'] as number) >= 0) { return saved; }
    } catch {}
    return { binding, streamId: '', cursor: 0 };
  }
  private async save(checkpoint: ArchiveObject, generation: number): Promise<void> {
    if (!this.current(generation) || !this.prefs) { throw new Error('通知连接已变化'); }
    this.prefs.putSync('checkpoint:' + this.base, JSON.stringify(checkpoint)); await this.prefs.flush();
  }
  private async post(stream: string, event: ArchiveObject, generation: number): Promise<void> {
    if (!this.context || !this.current(generation)) { throw new Error('通知连接已变化'); }
    const messageId = await Digest.sha256(new util.TextEncoder().encodeInto(JSON.stringify([this.gateway, this.identity, stream, event['sequence']])));
    const info = bundleManager.getBundleInfoForSelfSync(bundleManager.BundleFlag.GET_BUNDLE_INFO_DEFAULT);
    const agent = await wantAgent.getWantAgent({ wants: [{ bundleName: info.name, abilityName: 'EntryAbility' }],
      operationType: wantAgent.OperationType.START_ABILITY, requestCode: parseInt(messageId.slice(0, 7), 16), wantAgentFlags: [wantAgent.WantAgentFlags.UPDATE_PRESENT_FLAG] });
    if (!this.current(generation)) { throw new Error('通知连接已变化'); }
    await notificationManager.publish({ id: parseInt(messageId.slice(0, 7), 16), label: messageId, appMessageId: messageId,
      notificationSlotType: notificationManager.SlotType.SOCIAL_COMMUNICATION, tapDismissed: true, isAlertOnce: true, wantAgent: agent,
      content: { notificationContentType: notificationManager.ContentType.NOTIFICATION_CONTENT_BASIC_TEXT,
        normal: { title: String(event['title'] || '任务提醒').slice(0, 160), text: String(event['body'] || '电脑任务有新进展').slice(0, 600) } } });
  }
  private async receive(generation: number, verify: boolean): Promise<void> {
    let delay = 3000;
    try {
      if (!this.current(generation)) { return; }
      if (verify) {
        const proof = await this.http.json(this.base, '/api/auth', this.cookie) as ArchiveObject;
        const sync = proof['sessionSync'] as ArchiveObject;
        if (!this.current(generation) || proof['authenticated'] !== true || sync?.['version'] !== 1 || sync['gatewayId'] !== this.gateway || sync['identity'] !== this.identity) { return; }
      }
      const binding = await Digest.sha256(new util.TextEncoder().encodeInto(this.cookie));
      if (!this.current(generation)) { return; }
      const checkpoint = await this.checkpoint(binding), stream = checkpoint['streamId'] as string;
      if (!this.current(generation)) { return; }
      const revision = checkpoint['revision'] as string;
      const waiting = !!stream && /^[a-f0-9]{32}$/.test(revision || '');
      const query = '?after=' + checkpoint['cursor'] + (waiting ? '&wait=60&streamId=' + encodeURIComponent(stream) + '&revision=' + revision : '');
      const started = Date.now(), data = await this.http.json(this.base, '/api/mobile/events' + query, this.cookie) as ArchiveObject;
      if (!this.current(generation)) { return; }
      if (typeof data['streamId'] !== 'string' || !(data['streamId'] as string).length || (data['streamId'] as string).length > 128 ||
        !Number.isSafeInteger(data['cursor']) || (data['cursor'] as number) < 0 || !Array.isArray(data['events'])) { throw new Error('无效通知快照'); }
      const same = stream === data['streamId']; let previous = checkpoint['cursor'] as number;
      if (same && (data['cursor'] as number) < previous) { throw new Error('通知游标倒退'); }
      if (same && data['enabled'] === true) {
        for (const event of data['events'] as ArchiveObject[]) {
          const sequence = event['sequence'] as number;
          if (!Number.isSafeInteger(sequence) || sequence <= previous || sequence > (data['cursor'] as number)) { throw new Error('无效通知顺序'); }
          if (event['unread'] !== false) { await this.post(stream, event, generation); }
          previous = sequence; await this.save({ binding, streamId: stream, cursor: sequence }, generation);
        }
      }
      // First use and stream replacement establish a baseline, never replay old alerts.
      await this.save({ binding, streamId: data['streamId'], cursor: data['cursor'],
        revision: data['enabled'] === true && data['longPoll'] === 60 ? data['revision'] || '' : '' }, generation);
      delay = waiting ? Math.max(250, 1000 - (Date.now() - started)) : 3000;
    } catch {}
    if (this.current(generation)) { this.timer = setTimeout(() => { this.receive(generation, false).catch(() => {}); }, delay); }
  }
}
