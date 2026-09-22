import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './test/browser',
  fullyParallel: false,
  workers: 1,
  reporter: 'list',
  use: { baseURL: 'http://127.0.0.1:4188', trace: 'off' },
  projects: [
    { name: 'desktop-light', use: { viewport: { width: 1365, height: 980 }, colorScheme: 'light' } },
    { name: 'mobile-dark', use: { viewport: { width: 390, height: 844 }, colorScheme: 'dark', isMobile: true, hasTouch: true } },
  ],
  webServer: {
    command: 'npm run test:browser-server',
    url: 'http://127.0.0.1:4188/',
    reuseExistingServer: false,
    timeout: 30000,
  },
});
