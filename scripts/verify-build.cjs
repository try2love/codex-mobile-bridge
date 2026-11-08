'use strict';
const fs=require('node:fs'),path=require('node:path');
const {Arch}=require('builder-util');

module.exports=async context=>{
  const root=context.packager.projectDir;
  const metadata=JSON.parse(fs.readFileSync(path.join(root,'dist/update-version.json'),'utf8'));
  const arch=Arch[context.arch],platform=context.electronPlatformName;
  if(metadata.platform!==platform||metadata.arch!==arch||metadata.version!==context.packager.appInfo.version){
    throw Error(`Gateway does not match ${platform}/${arch}/${context.packager.appInfo.version}. Run scripts/build-desktop.py on the target OS and architecture first.`);
  }
  const executable=path.join(root,'dist/gateway',platform==='win32'?'codex-mobile-gateway.exe':'codex-mobile-gateway');
  fs.accessSync(executable,fs.constants.R_OK);
  const helpers={darwin:['claude-bridge-helper','zcode-bridge-helper'],win32:['claude-bridge-helper.exe','codex-quit-helper.exe']};
  for(const name of helpers[platform]||[]){
    fs.accessSync(path.join(root,'dist/gateway/_internal/client-helpers',name),fs.constants.R_OK);
  }
  const bundled=path.join(root,'dist/gateway/_internal/cloudflared');
  const tunnel=JSON.parse(fs.readFileSync(path.join(bundled,'version.json'),'utf8'));
  const lock=JSON.parse(fs.readFileSync(path.join(root,'scripts/cloudflared-lock.json'),'utf8'));
  if(tunnel.platform!==platform||tunnel.arch!==({x64:'amd64',arm64:'arm64'}[arch])||tunnel.version!==lock.version)throw Error('Bundled cloudflared does not match the target; rebuild the gateway.');
  fs.accessSync(path.join(bundled,platform==='win32'?'cloudflared.exe':'cloudflared'),fs.constants.R_OK);
  fs.accessSync(path.join(bundled,'LICENSE'),fs.constants.R_OK);
  if(platform==='win32'){
    const conpty=path.join(root,'dist/gateway/_internal/conpty');
    const version=JSON.parse(fs.readFileSync(path.join(conpty,'version.json'),'utf8'));
    const lock=JSON.parse(fs.readFileSync(path.join(root,'scripts/conpty-lock.json'),'utf8'));
    if(version.version!==lock.version||version.arch!==arch||version.packageSha256!==lock.sha256)
      throw Error('Bundled ConPTY does not match the target; rebuild the gateway.');
    for(const name of ['conpty.dll','x86/OpenConsole.exe','x64/OpenConsole.exe','arm64/OpenConsole.exe','LICENSE'])
      fs.accessSync(path.join(conpty,name),fs.constants.R_OK);
    // Nsis7z in electron-builder 26.15.3 silently skips ARM64-filtered executables.
    process.env.ELECTRON_BUILDER_7Z_FILTER='BCJ';
  }
};
