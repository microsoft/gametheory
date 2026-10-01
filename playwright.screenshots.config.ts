import { defineConfig } from '@playwright/test'
import base from './playwright.config'

/**
 * Refreshes the README images in docs/images from the synthetic Riverwatch test scenario. An
 * image is rewritten only when it visibly differs, so rendering noise leaves tracked files alone.
 */
export default defineConfig<{ readmeImages: boolean }>({
  ...base,
  testMatch: 'readme-screenshots.spec.ts',
  snapshotPathTemplate: '{testDir}/../docs/images/{arg}{ext}',
  updateSnapshots: 'changed',
  use: { ...base.use, readmeImages: true },
})
