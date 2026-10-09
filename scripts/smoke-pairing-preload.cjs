// Isolated test bridge. Production uses desktop/preload.cjs and main IPC checks.
'use strict';
const {contextBridge}=require('electron');
const {runWorker}=require('../desktop/shared/controller.cjs');
const {pairingImage}=require('../desktop/features/connections/qr.cjs');
const path=require('node:path');
const worker=process.env.CMB_TEST_RUNTIME?{executable:process.env.CMB_TEST_RUNTIME,dataDir:process.env.CMB_DATA_DIR}:
  {executable:process.env.CMB_PYTHON||(process.platform==='win32'?'python':'python3'),prefix:['-B',path.resolve(__dirname,'../desktop.py')],dataDir:process.env.CMB_DATA_DIR};
const api={language:async()=> 'zh-CN',setLanguage:async value=>value};
for(const action of ['snapshot','save','start','stop','logs'])api[action]=payload=>runWorker(worker,action,payload);
api.pairing=async payload=>{const value=await runWorker(worker,'pairing',payload);return payload.action==='create'?pairingImage(value):value;};
contextBridge.exposeInMainWorld('bridgeDesktop',api);
