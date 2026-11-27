'use strict';
const {dictionary,translateEnglish}=require('../../web/shared/i18n.js');
// Only native-shell messages live here; all shared UI text comes from web/shared/i18n.js.
const english={...dictionary,
  '正在读取状态':'Loading status','打开控制面板':'Open control panel',
  '网关运行中':'Gateway running','网关未启动':'Gateway stopped',
  '打开手机访问地址':'Open phone URL','复制手机访问地址':'Copy phone URL',
  '停止网关并退出':'Stop gateway and quit',
  '退出控制面板（保留网关）':'Quit control panel (keep gateway running)',
  '网关操作未完成':'Gateway action failed',
  '更新下载缓存':'Update download cache',
  '确认删除所选缓存？':'Delete selected downloaded packages?',
  '将删除 {count} 个更新包，释放 {size}。':'Delete {count} update packages and free up {size}.',
  '删除所选缓存':'Delete selected packages',
  '请等待应用更新完成后再删除缓存。':'Wait for the application update to finish before deleting cached packages.',
  '更新缓存请求无效。':'Invalid update cache request.',
  '更新缓存已变化，请刷新后重试。':'The update cache has changed. Refresh and try again.',
  '更新版本请求无效。':'Invalid release version request.',
  '确认切换到所选版本？':'Switch to the selected version?',
  '安装包校验后会备份网关配置，并交由系统安装器或你手动完成替换。旧版可能无法识别较新的配置功能；安装完成前，当前版本继续运行。':'After verifying the package, your gateway configuration will be backed up. Complete the replacement with the system installer or manually. Older versions may not recognize newer configuration features. The current version keeps running until installation is complete.',
  '切换版本会备份网关配置、替换当前应用并重启，继续使用现有资料。预览版可能存在未解决的问题。':'Switching versions backs up your gateway configuration, replaces the current app and restarts it with your existing data. Preview versions may have unresolved issues.'
};
function normalize(value){return typeof value==='string'&&/^en(?:-|$)/i.test(value)?'en':'zh-CN';}
function translate(text,language){
  const source=String(text).replace(/^Error invoking remote method '[^']+': (?:Error: )?/,'');
  return normalize(language)==='en'?(english[source]??translateEnglish(source)):source;
}
module.exports={normalize,translate,english};
