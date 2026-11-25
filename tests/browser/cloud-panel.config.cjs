'use strict';
const path=require('node:path');
const {defineConfig}=require('@playwright/test');
const upstream=require('./playwright.config.cjs');
module.exports=defineConfig(upstream,{
  testMatch:['cloud-i18n.spec.cjs'],
  outputDir:path.resolve(__dirname,'../../.tmp/cloud-panel-browser'),
  reporter:[['list'],['json',{outputFile:path.resolve(__dirname,'../../.tmp/cloud-panel-browser/report.json')}]],
});
