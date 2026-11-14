'use strict';
const path=require('node:path');
const {defineConfig}=require('@playwright/test');
process.env.PLAYWRIGHT_BROWSERS_PATH ||= path.resolve(__dirname,'../../.local/tools/playwright');
module.exports=defineConfig({testDir:__dirname,testMatch:'cloud-web.spec.cjs',workers:1,timeout:90000,
 outputDir:path.resolve(__dirname,'../../.tmp/cloud-web-browser'),reporter:'list',
 use:{baseURL:'http://127.0.0.1:18879',screenshot:'only-on-failure',trace:'retain-on-failure'},
 projects:['chromium','webkit'].flatMap(browserName=>[
  {name:browserName+'-wide',use:{browserName,...(browserName==='chromium'&&process.env.CMB_CHROMIUM_CHANNEL?{channel:process.env.CMB_CHROMIUM_CHANNEL}:{}),viewport:{width:1280,height:900}}},
  {name:browserName+'-narrow',use:{browserName,...(browserName==='chromium'&&process.env.CMB_CHROMIUM_CHANNEL?{channel:process.env.CMB_CHROMIUM_CHANNEL}:{}),viewport:{width:390,height:844}}}])});
