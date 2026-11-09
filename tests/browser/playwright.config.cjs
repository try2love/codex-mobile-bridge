'use strict';
const path = require('node:path');
const {defineConfig} = require('@playwright/test');
const root = path.resolve(__dirname, '../..');
// Keep downloaded test browsers and test evidence out of system/user profiles.
process.env.PLAYWRIGHT_BROWSERS_PATH ||= path.join(root, '.local/tools/playwright');
const baseURL = 'http://127.0.0.1:18279';

module.exports = defineConfig({
  testDir: __dirname,
  testMatch: ['critical.spec.cjs', 'send-feedback.spec.cjs', 'native-side-chat.spec.cjs', 'compact-composer.spec.cjs'],
  workers: 1, // The existing synthetic fixture intentionally resets state per navigation.
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  timeout: 60000,
  outputDir: path.join(root, '.tmp/browser-results'),
  reporter: [['list'], ['json', {outputFile: path.join(root, '.tmp/browser-results/report.json')}]],
  use: {baseURL, trace: 'retain-on-failure', screenshot: 'only-on-failure'},
  projects: ['chromium', 'webkit'].flatMap(browserName => {
    // Optional local fallback when the official Chromium download is unavailable.
    const browser = {browserName, ...(browserName === 'chromium' && process.env.CMB_CHROMIUM_CHANNEL
      ? {channel: process.env.CMB_CHROMIUM_CHANNEL} : {})};
    return [
      {name: browserName + '-wide', use: {...browser, viewport: {width: 1280, height: 900}}},
      {name: browserName + '-narrow', use: {...browser, viewport: {width: 390, height: 844}}},
    ];
  }),
  webServer: {
    command: `${process.platform === 'win32' ? 'python' : 'python3'} -B tests/browser/bootstrap-fixture.py --port 18279`,
    cwd: root,
    url: baseURL + '/__fixture_health',
    reuseExistingServer: false,
    timeout: 30000,
  },
});
