'use strict';
// Finalize nested identities before the distributable is created. The publisher
// proof permits exact, verified ad-hoc builds without exporting an Apple key.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {execFileSync}=require('node:child_process');
const root=path.resolve(__dirname,'..');
const product='io.github.try2love.codexmobilebridge';
const products=new Set([product,product+'.test']);
function authorize(record,key,trusted){
  if(!key||crypto.createPublicKey(key).export({type:'spki',format:'pem'})!==trusted)
    throw Error('ZCode local packaging requires the matching release signing key.');
  const payload=Buffer.from(JSON.stringify(record));
  return JSON.stringify({payload:payload.toString('base64'),signature:crypto.sign(null,payload,key).toString('base64')})+'\n';
}
module.exports=async context=>{
  if(context.electronPlatformName!=='darwin'||!products.has(context.packager.appInfo.id))return;
  const appId=context.packager.appInfo.id;
  const key=process.env.CMB_UPDATE_SIGNING_KEY;
  const trusted=fs.readFileSync(path.join(root,'desktop/features/updates/update-public-key.pem'),'utf8');
  authorize({},key,trusted); // Fail before modifying an otherwise usable package.
  const app=path.join(context.appOutDir,context.packager.appInfo.productFilename+'.app');
  const identity=context.packager.platformSpecificBuildOptions.identity;
  if(identity!=='-')throw Error('ZCode hook expects the configured ad-hoc signing flow.');
  const env={...process.env};delete env.CMB_UPDATE_SIGNING_KEY;
  const run=(file,args)=>execFileSync(file,args,{cwd:root,env,encoding:'utf8',stdio:['ignore','pipe','pipe']});
  const codesign=(target,id,preserve=false)=>run('/usr/bin/codesign',[
    '--force','--sign',identity,'--timestamp=none','--identifier',id,
    ...(preserve?['--preserve-metadata=entitlements,flags']:[]),target]);
  const bundleScript=path.join(root,'scripts/zcode_starter_bundle.py');
  const python='python3';
  const broker=path.join(app,'Contents/Resources/gateway/codex-mobile-gateway');
  codesign(broker,'codex-mobile-gateway',true);
  run(python,[bundleScript,'prepare',app]);
  codesign(path.join(app,'Contents/Helpers/CMBZCodePipeStarter.app'),product+'.zcode-pipe-starter');
  const record=JSON.parse(run(python,[bundleScript,'identity',app]));
  if(record.product!==appId||record.version!==context.packager.appInfo.version)
    throw Error('ZCode component identity does not match this release.');
  fs.writeFileSync(path.join(app,'Contents/Resources/zcode-pipe-identity.json'),authorize(record,key,trusted));
  // Do not use --deep: the nested broker hashes above must remain final.
  codesign(app,appId,true);
  run('/usr/bin/codesign',['--verify','--deep','--strict',app]);
  run(python,[bundleScript,'verify',app]);
};
module.exports.authorize=authorize;
