'use strict';
const {dictionary,translateEnglish}=require('../web/i18n.js');
// Only native-shell messages live here; all shared UI text comes from web/i18n.
const english={...dictionary,
  '正在读取状态':'Loading status','打开控制面板':'Open control panel',
  '网关运行中':'Gateway running','网关未启动':'Gateway stopped',
  '打开手机访问地址':'Open phone URL','复制手机访问地址':'Copy phone URL',
  '停止网关并退出':'Stop gateway and quit',
  '退出控制面板（保留网关）':'Quit control panel (keep gateway running)',
  '网关操作未完成':'Gateway action failed'
};
function normalize(value){return typeof value==='string'&&/^en(?:-|$)/i.test(value)?'en':'zh-CN';}
function translate(text,language){
  const source=String(text).replace(/^Error invoking remote method '[^']+': (?:Error: )?/,'');
  return normalize(language)==='en'?(english[source]??translateEnglish(source)):source;
}
module.exports={normalize,translate,english};
