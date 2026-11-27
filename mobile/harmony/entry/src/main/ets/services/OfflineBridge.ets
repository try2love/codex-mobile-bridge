import { webview } from '@kit.ArkWeb';
import { DocumentGuard } from './DocumentGuard';
import { GatewayHttp } from './GatewayHttp';
import { OfflineArchive, ArchiveObject, ArchiveAccount } from './OfflineArchive';
import { GatewayUrl } from './GatewayUrl';
import { DeviceBinding } from './DeviceBinding';
import { HistoryTransfer } from './HistoryTransfer';
import { NetworkPolicy } from './NetworkPolicy';
import { common } from '@kit.AbilityKit';

interface SyncIdentity { version: number; gatewayId: string; identity: string; }
interface ComputerName { name?: string; }
interface AuthProof { authenticated?: boolean; sessionSync?: SyncIdentity; computer?: ComputerName; cookieName?: string; }

export class OfflineBridge {
  private archive: OfflineArchive;
  private guard: DocumentGuard;
  private controller: webview.WebviewController;
  private token: string;
  private http: GatewayHttp = new GatewayHttp();
  private account: ArchiveAccount | null = null;
  private cookie: string = '';
  private proof: Promise<ArchiveAccount> | null = null;
  private authorizationRevision: number = 0;
  private transfers: HistoryTransfer | null = null;
  private network: NetworkPolicy | null = null;
  private context: common.UIAbilityContext | null = null;
  authorized: ((account: ArchiveAccount, base: string, cookie: string) => void) | undefined = undefined;
  unauthorized: (() => void) | undefined = undefined;
  constructor(archive: OfflineArchive, guard: DocumentGuard, controller: webview.WebviewController, token: string) {
    this.archive = archive; this.guard = guard; this.controller = controller; this.token = token;
  }
  enableTransfers(transfers: HistoryTransfer, network: NetworkPolicy, context: common.UIAbilityContext): void {
    this.transfers = transfers; this.network = network; this.context = context;
  }
  detach(): void { this.authorizationRevision++; this.account = null; this.cookie = ''; this.proof = null; this.http.cancel(); this.transfers?.cancel(); }
  private current(generation: number, cookie: string): boolean {
    return this.guard.accepts(this.controller.getUrl(), this.controller.getUrl(), generation) &&
      DeviceBinding.cookies(this.guard.base) === cookie;
  }
  enqueue(value: string): string | null {
    if (value.length > 2000000) { return null; }
    const event = JSON.parse(value) as ArchiveObject, id = event['requestId'] as string;
    if (!/^[0-9]{1,16}$/.test(id)) { return null; }
    const generation = this.guard.generation;
    const cookie = DeviceBinding.cookies(this.guard.base);
    this.handle(event, generation, cookie).then(result => {
      if (this.current(generation, cookie)) { this.reply(id, true, result); }
    }).catch(() => { if (this.current(generation, cookie)) { this.reply(id, false, null); } });
    return 'queued';
  }
  private reply(id: string, ok: boolean, value: Object | null): void {
    this.controller.runJavaScript('window.CodexBridgeNativeOffline?.receive(' + JSON.stringify(this.token) + ',' +
      JSON.stringify({ id, ok, value }) + ')').catch(() => {});
  }
  private async authorize(event: ArchiveObject, generation: number, cookie: string): Promise<ArchiveAccount> {
    if (!cookie || !this.current(generation, cookie)) { throw new Error('登录已变化'); }
    if (this.proof) { await this.proof; }
    if (!this.account || this.cookie !== cookie || event['type'] === 'auth') {
      const base = this.guard.base;
      const revision = this.authorizationRevision;
      const proof = (async () => {
        const auth = await this.http.json(base, '/api/auth', cookie) as AuthProof;
        const sync = auth.sessionSync;
        if (revision !== this.authorizationRevision || !auth.authenticated || sync?.version !== 1 || !this.current(generation, cookie) ||
          sync.gatewayId !== event['gatewayId'] || sync.identity !== event['identity']) { throw new Error('离线身份核验失败'); }
        const account = await this.archive.serial(() => {
          if (revision !== this.authorizationRevision || !this.current(generation, cookie)) { throw new Error('登录已变化'); }
          return this.archive.bind(base, auth.computer?.name || base, sync.gatewayId, sync.identity);
        }) as ArchiveAccount;
        if (revision !== this.authorizationRevision || !this.current(generation, cookie)) { throw new Error('登录已变化'); }
        this.account = account; this.cookie = cookie;
        this.authorized?.(account, base, cookie);
        DeviceBinding.capture(base, cookie, auth.cookieName || 'codex_mobile_session').catch(() => {});
        return account;
      })();
      this.proof = proof;
      try { await proof; } finally { if (this.proof === proof) { this.proof = null; } }
    }
    if (!this.account || this.account.gateway !== event['gatewayId'] || this.account.identity !== event['identity'] || !this.current(generation, cookie)) {
      throw new Error('离线身份不匹配');
    }
    return this.archive.current(this.account);
  }
  private async handle(event: ArchiveObject, generation: number, cookie: string): Promise<Object | null> {
    const type = event['type'] as string;
    if (type === 'auth' && event['status'] !== 'valid') {
      this.authorizationRevision++;
      this.unauthorized?.();
      const base = this.guard.base;
      await this.archive.serial(async () => {
        const previous = await this.archive.forAddress(base);
        if (previous && event['gatewayId'] === previous.gateway && event['identity'] === previous.identity) {
          await this.archive.status(previous, event['status'] as string);
        }
        return null;
      });
      DeviceBinding.clear(base).catch(() => {});
      this.account = null; this.cookie = ''; return this.facts();
    }
    const account = await this.authorize(event, generation, cookie);
    const current = (): boolean => this.current(generation, cookie);
    if (this.transfers && type === 'transferChunkFetch') { return this.transfers.fetch(account, event, this.guard.base, cookie, current); }
    if (this.transfers && (type === 'transferRecordCommit' || type === 'transferAttachmentCommit')) {
      return this.transfers.commit(account, event, type === 'transferAttachmentCommit', current);
    }
    if (this.transfers && this.context && type === 'transferAttachmentOpen') { return this.transfers.exportAttachment(this.context, account, event, current); }
    return this.archive.serial(async () => {
      if (!this.current(generation, cookie)) { throw new Error('登录已变化'); }
      const current = await this.archive.current(account);
      if (OfflineArchive.composerAction(type)) { return this.archive.composer(current, type, event); }
      if (type === 'merge') {
        if (!['manual', 'live'].includes(event['source'] as string) && !(event['source'] === 'prefetch' && this.network?.unmetered)) { throw new Error('不允许后台预取'); }
        await this.archive.merge(current, event); return { saved: true };
      }
      if (type === 'appearance' || type === 'auth') {
        if (event['appearance']) { await this.archive.db.run('INSERT OR REPLACE INTO appearance VALUES(?,?)', [current.id, JSON.stringify(event['appearance'])]); }
        return this.state(current, event['sessionId'] as string || '');
      }
      if (type === 'read') {
        const session = event['sessionId'] as string;
        if (event['page']) { return { page: await this.archive.page(current, session, event) }; }
        if (event['body']) { return { body: await this.archive.body(current, session, event['body'] as ArchiveObject) }; }
        return { turns: [] };
      }
      if (type === 'list') { return this.archive.list(current); }
      if (type === 'session') { return this.archive.page(current, event['sessionId'] as string, event); }
      if (type === 'state') { return this.state(current, event['sessionId'] as string || ''); }
      if (this.transfers && type.startsWith('transfer')) { return this.transfers.command(current, type, event); }
      throw new Error('不支持的离线命令');
    });
  }
  private facts(): ArchiveObject {
    return { recordPages: 1, foreground: this.guard.foreground,
      allowPrefetch: this.guard.foreground && this.network?.unmetered === true,
      allowTransfer: !!this.transfers && this.guard.foreground && this.network?.unmetered === true,
      networkAvailable: this.network?.available === true, networkEpoch: this.network?.epoch || '' };
  }
  private async state(account: ArchiveAccount, session: string): Promise<ArchiveObject> {
    const state = await this.archive.state(account, session);
    for (const key of Object.keys(this.facts())) { state[key] = this.facts()[key]; } return state;
  }
  publish(): void {
    if (!this.guard.base || !GatewayUrl.sameOrigin(this.controller.getUrl(), this.guard.base)) { return; }
    this.controller.runJavaScript('window.CodexBridgeNativeOffline?.receive(' + JSON.stringify(this.token) + ',' +
      JSON.stringify({ state: this.facts() }) + ')').catch(() => {});
  }
}
