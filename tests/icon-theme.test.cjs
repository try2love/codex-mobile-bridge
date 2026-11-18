'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {EventEmitter}=require('node:events');
const path=require('node:path');
const {createIconTheme}=require('../desktop/shell/icons.cjs');

function fixture(platform){
  const nativeTheme=new EventEmitter();
  nativeTheme.shouldUseDarkColors=false;
  nativeTheme.shouldUseDarkColorsForSystemIntegratedUI=false;
  const windows=[],trays=[],docks=[];
  let window={isDestroyed:()=>false,setIcon:icon=>windows.push(icon)};
  const nativeImage={createFromPath:filename=>({filename,template:false,setTemplateImage(value){this.template=value;}})};
  const theme=createIconTheme({platform,nativeTheme,nativeImage,assetsDir:'assets',
    app:{dock:{setIcon:icon=>docks.push(icon)}},getWindow:()=>window,getTray:()=>({setIcon:icon=>trays.push(icon)})});
  return {nativeTheme,theme,windows,trays,docks,close(){window=null;}};
}

test('Windows taskbar and tray follow system appearance independently of app appearance',()=>{
  const f=fixture('win32');f.nativeTheme.shouldUseDarkColors=true;
  f.theme.start();assert.equal(path.basename(f.windows.at(-1)),'icon.ico');
  f.nativeTheme.shouldUseDarkColorsForSystemIntegratedUI=true;f.nativeTheme.emit('updated');
  assert.equal(path.basename(f.windows.at(-1)),'icon-dark.ico');
  assert.equal(f.windows.at(-1),f.trays.at(-1));
  f.nativeTheme.shouldUseDarkColors=false;f.nativeTheme.emit('updated');
  assert.equal(path.basename(f.windows.at(-1)),'icon-dark.ico');
  f.nativeTheme.shouldUseDarkColorsForSystemIntegratedUI=false;f.nativeTheme.emit('updated');
  assert.equal(path.basename(f.windows.at(-1)),'icon.ico');
  f.theme.dispose();
});

test('macOS updates the Dock while retaining a system-tinted menu bar template',()=>{
  const f=fixture('darwin');f.theme.start();
  assert.equal(path.basename(f.docks.at(-1).filename),'icon.png');
  const template=f.trays.at(-1);assert.equal(template.template,true);
  f.nativeTheme.shouldUseDarkColors=true;f.nativeTheme.emit('updated');
  assert.equal(path.basename(f.docks.at(-1).filename),'icon-dark.png');
  assert.equal(f.trays.at(-1),template);assert.equal(f.windows.length,0);
  f.nativeTheme.shouldUseDarkColors=false;f.nativeTheme.emit('updated');
  assert.equal(path.basename(f.docks.at(-1).filename),'icon.png');
  f.theme.dispose();
});

test('older system-theme APIs fall back and late events after disposal do not touch native UI',()=>{
  const f=fixture('win32');delete f.nativeTheme.shouldUseDarkColorsForSystemIntegratedUI;
  f.nativeTheme.shouldUseDarkColors=true;f.theme.start();f.theme.start();
  assert.equal(f.nativeTheme.listenerCount('updated'),1);
  assert.equal(path.basename(f.theme.windowIcon()),'icon-dark.ico');
  f.close();f.nativeTheme.shouldUseDarkColors=false;f.nativeTheme.emit('updated');
  assert.equal(path.basename(f.trays.at(-1)),'icon.ico');
  const count=f.trays.length;f.theme.dispose();f.nativeTheme.emit('updated');
  assert.equal(f.nativeTheme.listenerCount('updated'),0);assert.equal(f.trays.length,count);
});
