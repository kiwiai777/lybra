const path = require('path');
const { defineConfig, devices } = require('@playwright/test');

const repoRoot = path.resolve(__dirname, '../..');

// The WSL shell used in this workspace may export proxy settings that should not
// apply to localhost test traffic. Playwright inherits the parent environment,
// so explicitly bypass local addresses for the browser and web server process.
for (const key of ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy']) {
  delete process.env[key];
}
process.env.NO_PROXY = '127.0.0.1,localhost';
process.env.no_proxy = '127.0.0.1,localhost';

module.exports = defineConfig({
  testDir: __dirname,
  fullyParallel: true,
  retries: 0,
  use: {
    baseURL: 'http://127.0.0.1:8765',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: `python3 -m web.board.app --repo-root ${repoRoot} --host 127.0.0.1 --port 8765`,
    cwd: repoRoot,
    url: 'http://127.0.0.1:8765/',
    reuseExistingServer: !process.env.CI,
    stdout: 'pipe',
    stderr: 'pipe',
    timeout: 120000,
  },
  projects: [
    {
      name: 'desktop-chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'mobile-chromium',
      use: { ...devices['Pixel 7'] },
    },
  ],
});
