import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './e2e', fullyParallel: false, workers: 1, timeout: 20000,
  use: { baseURL: 'http://127.0.0.1:4177', viewport: { width: 1160, height: 820 }, screenshot: 'only-on-failure' },
  webServer: { command: 'python3 -m http.server 4177 --bind 127.0.0.1 --directory ../app/web', url: 'http://127.0.0.1:4177', reuseExistingServer: false },
});
