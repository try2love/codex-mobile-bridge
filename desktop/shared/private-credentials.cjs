'use strict';
// Profile keys survive application rebuilds. No OS credential API is used here.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const MAGIC=Buffer.from('CMB1');
class PrivateCredentials{
  constructor(profile,purpose){this.directory=path.join(profile,'.credential-vault');this.purpose=purpose;this.keyImages=new WeakMap();}
  folder(){
    fs.mkdirSync(this.directory,{recursive:true,mode:0o700});const info=fs.lstatSync(this.directory);
    if(!info.isDirectory()||info.isSymbolicLink()||info.uid!==process.getuid()||(info.mode&0o077))throw Error('网关凭据目录权限不正确');
    return this.directory;
  }
  readKey(file){
    const fd=fs.openSync(file,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW);
    try{const info=fs.fstatSync(fd);if(!info.isFile()||info.uid!==process.getuid()||(info.mode&0o077))throw Error('网关凭据文件权限不正确');const key=fs.readFileSync(fd);if(key.length!==32)throw Error('网关凭据密钥无法读取');const stamp=fs.fstatSync(fd,{bigint:true});this.keyImage=[stamp.dev,stamp.ino,stamp.size,stamp.mtimeNs,stamp.ctimeNs,crypto.createHash('sha256').update(key).digest('hex')].join(':');return key;}
    finally{fs.closeSync(fd);}
  }
  key(create=false){
    const directory=this.folder(),file=path.join(directory,'key');
    if(create&&!fs.existsSync(file)){
      const auth=path.join(path.dirname(directory),'zcode-official-auth.enc');
      const index=path.join(path.dirname(directory),'connection-vault.json'),indexed=fs.existsSync(index)&&Object.values(JSON.parse(fs.readFileSync(index,'utf8'))).includes('gateway-v1');
      if(fs.readdirSync(directory).some(name=>name.endsWith('.enc'))||indexed||fs.existsSync(auth)&&!['v10','v11'].includes(fs.readFileSync(auth).subarray(0,3).toString()))throw Error('网关凭据密钥缺失，已保留原密文');
      const temporary=path.join(directory,'key.'+crypto.randomUUID()+'.tmp');let fd;
      try{fd=fs.openSync(temporary,'wx',0o600);fs.writeFileSync(fd,crypto.randomBytes(32));fs.fsyncSync(fd);fs.closeSync(fd);fd=undefined;try{fs.linkSync(temporary,file);}catch(error){if(error.code!=='EEXIST')throw error;}}
      finally{if(fd!==undefined)fs.closeSync(fd);fs.rmSync(temporary,{force:true});}
    }
    return this.readKey(file);
  }
  async isAsyncEncryptionAvailable(){try{this.folder();return true;}catch{return false;}}
  async encryptStringAsync(value){
    const nonce=crypto.randomBytes(12),cipher=crypto.createCipheriv('aes-256-gcm',this.key(true),nonce);cipher.setAAD(Buffer.from('CMB1:'+this.purpose));
    const encrypted=Buffer.concat([MAGIC,nonce,cipher.update(value,'utf8'),cipher.final(),cipher.getAuthTag()]);this.keyImages.set(encrypted,this.keyImage);return encrypted;
  }
  async decryptStringAsync(value){
    if(!PrivateCredentials.encrypted(value))throw Error('网关凭据格式无法确认');
    const cipher=crypto.createDecipheriv('aes-256-gcm',this.key(),value.subarray(4,16));cipher.setAAD(Buffer.from('CMB1:'+this.purpose));cipher.setAuthTag(value.subarray(-16));
    const decrypted={result:Buffer.concat([cipher.update(value.subarray(16,-16)),cipher.final()]).toString('utf8'),shouldReEncrypt:false};this.keyImages.set(decrypted,this.keyImage);return decrypted;
  }
  assertCurrent(value){const expected=this.keyImages.get(value);this.readKey(path.join(this.directory,'key'));if(!expected||expected!==this.keyImage)throw Error('网关凭据密钥已变化，已停止覆盖');}
  static encrypted(value){return Buffer.isBuffer(value)&&value.length>=32&&value.subarray(0,4).equals(MAGIC);}
}
module.exports={PrivateCredentials};
