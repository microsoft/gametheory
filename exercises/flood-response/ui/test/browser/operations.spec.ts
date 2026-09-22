import { expect, test } from '@playwright/test';

test('TEST ONLY: keyboard-accessible independent acknowledgement/allocation flow', async ({ page }, testInfo) => {
  await page.goto('/test/fixture.html');
  await expect(page.getByRole('note')).toContainText('TEST ONLY');
  await page.getByRole('button', { name: /baseline blanket request/ }).click();
  await page.getByRole('button', { name: 'Acknowledge request' }).click();
  await expect(page.getByRole('heading', { name: 'Allocate resources' })).toBeVisible();
  await page.getByLabel('Quantity', { exact: true }).fill('40');
  await page.getByLabel('Available at (UTC)').fill('2030-01-01T10:15');
  await page.getByRole('button', { name: 'Record allocation' }).click();
  await expect(page.getByText(/requested quantity is fully allocated/)).toBeVisible();
  await page.getByRole('button', { name: 'Read events' }).click();
  await expect(page.getByText('request.allocate', { exact: true })).toBeVisible();
  await expect(page.getByText(/Notification sending is disabled/)).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath(`${testInfo.project.name}.png`), fullPage: true });
});

test('normal entry point has no test identity fallback', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Setup required' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Acknowledge request' })).toHaveCount(0);
});
