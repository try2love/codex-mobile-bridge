'use strict';
// Assemble locally signed mobile files alongside the CI desktop artifacts, then:
// node scripts/release-assets.cjs verify <artifact-dir> --write-checksums
// Download the complete draft and run verify again without --write-checksums.
const fs=require('node:fs'),path=require('node:path');
const {createHash}=require('node:crypto');
const {manifest}=require('../desktop/features/updates/updater.cjs');
const version=require('../package.json').version;
const publicKey=fs.readFileSync(path.join(__dirname,'../desktop/features/updates/update-public-key.pem'),'utf8');
const isAsset=name=>/\.(dmg|zip|exe|deb|AppImage|apk|ipa)$/.test(name)||name==='bridge-update.json';
const digest=file=>createHash('sha256').update(fs.readFileSync(file)).digest('hex');
function releaseAssetNames(v=version){
  const prefix=`Codex-Mobile-Bridge-${v}-`;
  return [
    ...['arm64','x64'].flatMap(arch=>['dmg','zip'].map(ext=>`${prefix}macOS-${arch}.${ext}`)),
    `${prefix}Windows-x64-Setup.exe`,`${prefix}Windows-x64.zip`,
    `${prefix}Linux-amd64.deb`,`${prefix}Linux-x86_64.AppImage`,
    `${prefix}Linux-arm64.deb`,`${prefix}Linux-arm64.AppImage`,
    `${prefix}Android.apk`,`${prefix}iOS-unsigned.ipa`,`${prefix}iOS-source.zip`,
    'bridge-update.json',
  ].sort();
}
function currentReleaseNotes(text,v=version){
  const escaped=v.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
  const heading=new RegExp(`^## v${escaped}(?:[ \\t].*)?$`,'m').exec(text);
  if(!heading)throw Error(`Missing release notes for v${v}.`);
  const remainder=text.slice(heading.index),next=remainder.slice(heading[0].length).search(/^## /m);
  return (next<0?remainder:remainder.slice(0,heading[0].length+next)).replace(/\s*---\s*$/,'').trim()+'\n';
}
function writeChecksums(directory){
  const names=fs.readdirSync(directory).filter(isAsset).sort();
  const sums=names.map(name=>digest(path.join(directory,name))+'  '+name);
  fs.writeFileSync(path.join(directory,'SHA256SUMS.txt'),sums.join('\n')+'\n');
}
function verifyRelease(directory,{write=false,key=publicKey}={}){
  const names=releaseAssetNames(),actual=fs.readdirSync(directory).filter(isAsset).sort();
  if(JSON.stringify(actual)!==JSON.stringify(names))throw Error('Release assets are incomplete or unexpected. Required: '+names.join(', '));
  for(const name of names){
    const stat=fs.lstatSync(path.join(directory,name));
    if(!stat.isFile()||stat.size===0)throw Error('Release asset must be a nonempty regular file: '+name);
  }
  const bytes=fs.readFileSync(path.join(directory,'bridge-update.json'));
  for(const [platform,arch] of [['darwin','arm64'],['darwin','x64'],['win32','x64']]){
    const {asset}=manifest(bytes,key,{current:'0.0.0-preview.0',platform,arch,expected:version});
    const file=path.join(directory,asset.name);
    if(fs.statSync(file).size!==asset.size||digest(file)!==asset.sha256)throw Error('Signed update asset mismatch: '+asset.name);
  }
  if(write)writeChecksums(directory);
  const lines=fs.readFileSync(path.join(directory,'SHA256SUMS.txt'),'utf8').trim().split('\n');
  const checked=new Set();
  for(const line of lines){
    const match=/^([a-f0-9]{64})  ([^/\\\r\n]+)$/.exec(line);
    if(!match||!names.includes(match[2])||checked.has(match[2]))throw Error('Unexpected or duplicate checksum entry.');
    if(digest(path.join(directory,match[2]))!==match[1])throw Error('Checksum mismatch: '+match[2]);
    checked.add(match[2]);
  }
  if(checked.size!==names.length)throw Error('Checksums do not cover every release asset.');
  return {version,assets:names.length,checksums:checked.size};
}
if(require.main===module){
  const [command,input,output]=process.argv.slice(2);
  if(command==='notes'&&input&&output){
    const notes=currentReleaseNotes(fs.readFileSync(input,'utf8'));
    fs.mkdirSync(path.dirname(output),{recursive:true});fs.writeFileSync(output,notes);
  }else if(command==='verify'&&input&&(!output||output==='--write-checksums')){
    console.log(JSON.stringify(verifyRelease(input,{write:output==='--write-checksums'})));
  }else throw Error('Usage: release-assets.cjs notes <notes-file> <output-file> | verify <artifact-dir> [--write-checksums]');
}
module.exports={releaseAssetNames,currentReleaseNotes,writeChecksums,verifyRelease};
