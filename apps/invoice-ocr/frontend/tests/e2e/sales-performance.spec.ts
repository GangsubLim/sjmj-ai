import { test, expect } from "@playwright/test";

test("영업사원 추가 → 실적 입력 → PNG 다운로드", async ({ page }) => {
  await page.goto("/settings");
  await page.getByPlaceholder("영업사원 이름").fill("E2E사원");
  await page.getByRole("button", { name: "추가" }).click();
  await expect(page.getByText("E2E사원")).toBeVisible();

  await page.goto("/sales-performance");
  await expect(page.getByText(/실적/)).toBeVisible();

  const firstCell = page.getByRole("button", { name: /^\d+$/ }).first();
  await firstCell.click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();

  const input = dialog.locator('input[inputmode="numeric"]').first();
  await input.fill("1234567");
  await dialog.getByRole("button", { name: "저장" }).click();

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: /PNG 저장/ }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/sales-performance-\d{4}-\d{2}\.png/);
});

test("모바일 viewport → MobileBlockedPage", async ({ browser }) => {
  const context = await browser.newContext({
    viewport: { width: 800, height: 600 },
  });
  const page = await context.newPage();
  await page.goto("/sales-performance");
  await expect(page.getByText("데스크탑에서 열어주세요")).toBeVisible();
  await page.getByRole("link", { name: "홈으로 돌아가기" }).click();
  await expect(page).toHaveURL("/");
  await context.close();
});
