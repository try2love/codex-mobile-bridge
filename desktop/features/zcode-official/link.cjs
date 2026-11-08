'use strict';
const ORIGIN='https://zcode.z.ai';
function parseLink(value){
  let url;try{url=new URL(value);}catch{throw Error('请粘贴 ZCode 原 App 的官方远控链接。');}
  if(url.origin!==ORIGIN||url.pathname!=='/remote/v4'||url.username||url.password||url.hash||
    [...url.searchParams.keys()].some(key=>!['sid','mid','name','app_version'].includes(key)||url.searchParams.getAll(key).length!==1))
    throw Error('请使用 ZCode 原 App 提供的官方远控链接。');
  const sid=url.searchParams.get('sid'),mid=url.searchParams.get('mid');
  if([sid,mid].some(value=>!value||value.trim()!==value||Buffer.byteLength(value)>128))throw Error('官方远控链接缺少设备信息。');
  return {url:url.href,sid,mid};
}
module.exports={ORIGIN,parseLink};
