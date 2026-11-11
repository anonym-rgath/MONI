import { defineConfig } from '@playwright/test'

// Browser-Smoke gegen den echten Manager-Container (CI). Zertifikat selbstsigniert, nur dort.
export default defineConfig({
  testDir: 'e2e',
  timeout: 120_000,
  workers: 1,
  reporter: 'list',
  use: {
    baseURL: process.env.MONI_URL ?? 'https://127.0.0.1:8443',
    ignoreHTTPSErrors: true,
    browserName: 'chromium',
  },
})
