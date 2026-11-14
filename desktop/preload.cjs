'use strict';
const {contextBridge,ipcRenderer}=require('electron');

const developmentUI=process.argv.includes('--cmb-dev-ui');
let invocations=0;
function invoke(channel,...args){
  if(!developmentUI)return ipcRenderer.invoke(channel,...args);
  invocations++;
  return ipcRenderer.invoke(channel,...args).finally(()=>invocations--);
}

const api={
  platform:process.platform,
  checkUpdate:()=>invoke('bridge:check-update'),
  skipUpdate:()=>invoke('bridge:skip-update'),
  installUpdate:()=>invoke('bridge:install-update'),
  language:()=>invoke('bridge:language'),
  setLanguage:value=>invoke('bridge:set-language',value),
  notificationWatches:value=>invoke('bridge:notification-watches',value),
  transferSettings:value=>invoke('bridge:transfer-settings',value),
  devices:value=>invoke('bridge:devices',value),
  pairing:value=>invoke('bridge:pairing',value),
  cloud:value=>invoke('bridge:cloud',value),
  installCloudflared:()=>invoke('bridge:install-cloudflared'),
  checkCloudflared:value=>invoke('bridge:check-cloudflared',value),
  copyServerDiagnostics:value=>invoke('bridge:copy-server-diagnostics',value),
  serverSetup:value=>invoke('bridge:server-setup',value),
  tailscaleSetup:value=>invoke('bridge:tailscale-setup',value),
  tailscaleAuthorize:value=>invoke('bridge:tailscale-authorize',value),
  connectionCredentials:value=>invoke('bridge:connection-credentials',value),
  readCredentials:value=>invoke('bridge:read-credentials',value),
  webhookEndpoint:value=>invoke('bridge:webhook-endpoint',value),
  networkAdaptation:value=>invoke('bridge:network-adaptation',value),
  snapshot:()=>invoke('bridge:snapshot'),
  accounts:value=>invoke('bridge:accounts',value),
  cliAccess:value=>invoke('bridge:cli-access',value),
  desktopSessions:value=>invoke('bridge:desktop-sessions',value),
  harness:value=>invoke('bridge:harness',value),
  account:value=>invoke('bridge:account',value),
  save:value=>invoke('bridge:save',value),
  start:()=>invoke('bridge:start'),
  stop:()=>invoke('bridge:stop'),
  logs:()=>invoke('bridge:logs'),
  testNotification:value=>invoke('bridge:test-notification',value),
  exportDeployment:value=>invoke('bridge:export-deployment',value),
  copyDeployment:value=>invoke('bridge:copy-deployment',value),
  checkEntry:value=>invoke('bridge:check-entry',value),
  choose:kind=>invoke('bridge:choose',kind),
  open:target=>invoke('bridge:open',target),
  copy:target=>invoke('bridge:copy',target)
};
if(developmentUI)api.development={
  busy:()=>invocations>0,
  state:()=>invoke('bridge:dev-state'),
  reload:value=>invoke('bridge:dev-reload',value),
  onChange:callback=>{
    if(typeof callback!=='function')throw TypeError('Expected development callback');
    const listener=(_event,value)=>callback(value);
    ipcRenderer.on('bridge:dev-change',listener);
    return ()=>ipcRenderer.removeListener('bridge:dev-change',listener);
  }
};
contextBridge.exposeInMainWorld('bridgeDesktop',api);
