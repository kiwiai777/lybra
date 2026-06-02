const { test, expect } = require('@playwright/test');

test.describe('Lybra Board visual smoke', () => {
  async function openBoard(page) {
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: 'AI Task Authoring' })).toBeVisible();
    await expect(page.locator('#ai-author-requirement')).toBeVisible();
    await expect(page.locator('#ai-author-card')).toBeVisible();
    await expect(page.locator('#ai-author-live-fields')).toBeHidden();
    await page.getByText('Live BYO-LLM', { exact: true }).click();
    await expect(page.locator('#ai-author-live-fields')).toBeVisible();
    await expect(page.locator('#ai-author-credential-ref')).toHaveValue('env:LYBRA_LLM_API_KEY');
  }

  test('renders the AI Task Authoring workbench on desktop', async ({ page }, testInfo) => {
    await openBoard(page);
    await expect(page.locator('body')).not.toHaveCSS('overflow-x', 'scroll');
    await page.screenshot({ path: testInfo.outputPath('board-desktop.png'), fullPage: true });
  });

  test('renders the AI Task Authoring workbench on mobile', async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 393, height: 852 });
    await openBoard(page);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
    expect(overflow).toBe(false);
    await page.screenshot({ path: testInfo.outputPath('board-mobile.png'), fullPage: true });
  });
});
