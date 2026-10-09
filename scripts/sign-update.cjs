'use strict';
// Usage: CMB_UPDATE_SIGNING_KEY=<PEM> node scripts/sign-update.cjs <artifact-dir> <notes-file>
// The private key belongs in a release secret, never in the repository or app.
const fs=require('node:fs'),path=require('node:path');
const {createHash,createPublicKey,sign}=require('node:crypto');
const {assetName}=require('../desktop/features/updates/updater.cjs');
const root=path.resolve(__dirname,'..');
function buildManifest(directory,notes,key){
  const version=require('../package.json').version;
  const trusted=fs.readFileSync(path.join(root,'desktop/features/updates/update-public-key.pem'),'utf8');
  if(createPublicKey(key).export({type:'spki',format:'pem'})!==trusted)throw Error('Signing key does not match the public key shipped in this release.');
  const assets={};
  for(const [platform,arch] of [['darwin','arm64'],['darwin','x64'],['win32','x64']]){
    const name=assetName(version,platform,arch),bytes=fs.readFileSync(path.join(directory,name));
    assets[`${platform}-${arch}`]={name,size:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex')};
  }
  const payload=Buffer.from(JSON.stringify({schema:1,version,notes,assets}));
  return JSON.stringify({payload:payload.toString('base64'),signature:sign(null,payload,key).toString('base64')})+'\n';
}
if(require.main===module){
  const [directory,notesFile]=process.argv.slice(2),key=process.env.CMB_UPDATE_SIGNING_KEY;
  if(!directory||!notesFile||!key)throw Error('Artifact directory, notes file and CMB_UPDATE_SIGNING_KEY are required.');
  const manifest=buildManifest(directory,fs.readFileSync(notesFile,'utf8'),key);
  fs.writeFileSync(path.join(directory,'bridge-update.json'),manifest);
  const sums=fs.readdirSync(directory).filter(name=>/\.(dmg|zip|exe|deb|AppImage)$/.test(name)).sort().map(name=>createHash('sha256').update(fs.readFileSync(path.join(directory,name))).digest('hex')+'  '+name);
  fs.writeFileSync(path.join(directory,'SHA256SUMS.txt'),sums.join('\n')+'\n');
  console.log('Signed update manifest and checksums created.');
}
module.exports={buildManifest};
