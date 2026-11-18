'use strict';
const {test,expect}=require('@playwright/test');
const path=require('node:path');
const root=path.resolve(__dirname,'../..');

test.beforeEach(async({context,baseURL})=>{
  await context.route('**/*',route=>new URL(route.request().url()).origin===baseURL?route.continue():route.abort());
});

test('brand images, favicon and install links follow the resolved theme without replacing the page',async({page})=>{
  await page.emulateMedia({colorScheme:'dark'});
  // Use the isolated fixture origin so actual generated assets can be decoded.
  await page.goto('/__fixture_health');
  await page.setContent(`<html><head>
    <link rel="icon" href="/icon-favicon.png" data-icon-light="/icon-favicon.png" data-icon-dark="/icon-favicon-dark.png">
    <link rel="manifest" href="/manifest.webmanifest" data-icon-light="/manifest.webmanifest" data-icon-dark="/manifest-dark.webmanifest">
    <link rel="apple-touch-icon" href="/icon-touch.png" data-icon-light="/icon-touch.png" data-icon-dark="/icon-touch-dark.png">
    </head><body><img id="brand" src="/icon-ui.png" data-icon-light="/icon-ui.png" data-icon-dark="/icon-ui-dark.png"><textarea id="draft">keep this draft</textarea></body></html>`);
  await page.route('**/__fixture_brand_icons.js',route=>route.fulfill({path:path.join(root,'web/shared/brand-icons.js'),contentType:'text/javascript'}));
  await page.addScriptTag({url:'/__fixture_brand_icons.js'});
  const image=page.locator('#brand');
  await expect(image).toHaveAttribute('src','/icon-ui-dark.png');
  await expect(page.locator('link[rel=icon]')).toHaveAttribute('href','/icon-favicon-dark.png');
  await expect(page.locator('link[rel=manifest]')).toHaveAttribute('href','/manifest-dark.webmanifest');
  await expect(page.locator('link[rel=apple-touch-icon]')).toHaveAttribute('href','/icon-touch-dark.png');
  await expect.poll(()=>image.evaluate(image=>image.complete&&image.naturalWidth)).toBe(144);

  // Explicit light wins over a dark OS and stays light as the OS changes.
  await page.evaluate(()=>{window.originalBrand=document.getElementById('brand');document.documentElement.dataset.theme='light';});
  await expect(image).toHaveAttribute('src','/icon-ui.png');
  await page.emulateMedia({colorScheme:'light'});await page.emulateMedia({colorScheme:'dark'});
  await expect(image).toHaveAttribute('src','/icon-ui.png');

  // This is the same final attribute written by the existing manual/native theme handlers.
  await page.evaluate(()=>{document.documentElement.dataset.theme='dark';});
  await expect(image).toHaveAttribute('src','/icon-ui-dark.png');
  await page.evaluate(()=>{
    const logo=document.createElement('img');logo.id='late-brand';
    logo.dataset.iconLight='/icon-ui.png';logo.dataset.iconDark='/icon-ui-dark.png';
    window.BridgeBrandIcons.apply(logo);document.body.append(logo);
  });
  await expect(page.locator('#late-brand')).toHaveAttribute('src','/icon-ui-dark.png');
  await page.evaluate(()=>delete document.documentElement.dataset.theme);
  await page.emulateMedia({colorScheme:'light'});
  await expect(image).toHaveAttribute('src','/icon-ui.png');
  await expect(page.locator('#late-brand')).toHaveAttribute('src','/icon-ui.png');
  await expect(page.locator('#draft')).toHaveValue('keep this draft');
  expect(await page.evaluate(()=>window.originalBrand===document.getElementById('brand'))).toBe(true);
});

test('production page startup and appearance controls switch the real versioned icons',async({page},testInfo)=>{
  await page.emulateMedia({colorScheme:'dark'});
  await page.addInitScript(()=>localStorage.setItem('bridge-appearance',JSON.stringify({theme:'light'})));
  await page.goto('/?case=session-menu');
  await page.waitForFunction(()=>window.BridgePresentation&&document.getElementById('bootstrap-status').hidden);
  await expect(page.locator('html')).toHaveAttribute('data-theme','light');
  const favicon=page.locator('link[rel=icon]');
  await expect(favicon).toHaveAttribute('href',/^\/icon-favicon\.png\?v=[a-f0-9]{64}$/);
  await page.evaluate(()=>{
    document.getElementById('message').value='unsent theme test';
    document.getElementById('appearance-dialog').showModal();
  });
  await page.locator('#appearance-theme').selectOption('dark');
  await expect(favicon).toHaveAttribute('href',/^\/icon-favicon-dark\.png\?v=[a-f0-9]{64}$/);
  await expect(page.locator('link[rel=manifest]')).toHaveAttribute('href',/^\/manifest-dark\.webmanifest\?v=/);
  const brands=page.locator('img[data-icon-light]');
  expect(await brands.count()).toBeGreaterThan(1);
  for(const logo of await brands.all()){
    await expect(logo).toHaveAttribute('src',/\/icon-ui-dark\.png\?v=/);
    await expect.poll(()=>logo.evaluate(image=>image.complete&&image.naturalWidth)).toBe(144);
  }
  await page.screenshot({path:testInfo.outputPath('production-dark.png')});
  await page.locator('#appearance-theme').selectOption('system');
  await page.emulateMedia({colorScheme:'light'});
  await expect(page.locator('html')).toHaveAttribute('data-theme','light');
  await expect(favicon).toHaveAttribute('href',/^\/icon-favicon\.png\?v=/);
  await page.emulateMedia({colorScheme:'dark'});
  await expect(favicon).toHaveAttribute('href',/^\/icon-favicon-dark\.png\?v=/);
  await page.evaluate(()=>{
    window.BridgeNativeTheme={theme:'light',dark:false};
    document.dispatchEvent(new Event('bridge-native-theme'));
  });
  await expect(favicon).toHaveAttribute('href',/^\/icon-favicon\.png\?v=/);
  await expect(page.locator('#message')).toHaveValue('unsent theme test');
  await page.screenshot({path:testInfo.outputPath('production-light.png')});
});
