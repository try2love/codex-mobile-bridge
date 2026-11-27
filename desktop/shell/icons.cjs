'use strict';
const path=require('node:path');

function createIconTheme({app,nativeTheme,nativeImage,assetsDir,getWindow,getTray,platform=process.platform}){
  const images=new Map();
  let listening=false;
  function dark(){
    return platform==='win32'?(nativeTheme.shouldUseDarkColorsForSystemIntegratedUI??nativeTheme.shouldUseDarkColors):nativeTheme.shouldUseDarkColors;
  }
  function file(extension='png'){return path.join(assetsDir,`icon${dark()?'-dark':''}.${extension}`);}
  function image(filename){
    if(!images.has(filename))images.set(filename,nativeImage.createFromPath(filename));
    return images.get(filename);
  }
  function windowIcon(){return file(platform==='win32'?'ico':'png');}
  function trayIcon(){
    if(platform!=='darwin')return windowIcon();
    const template=image(path.join(assetsDir,'trayTemplate.png'));
    template.setTemplateImage(true);
    return template;
  }
  function update(){
    const window=getWindow();
    if(platform==='darwin')app.dock?.setIcon(image(file()));
    else if(window&&!window.isDestroyed())window.setIcon(windowIcon());
    getTray()?.setIcon(trayIcon());
  }
  return {windowIcon,trayIcon,update,
    start(){if(!listening){nativeTheme.on('updated',update);listening=true;}update();},
    dispose(){if(listening)nativeTheme.removeListener('updated',update);listening=false;},
  };
}
module.exports={createIconTheme};
