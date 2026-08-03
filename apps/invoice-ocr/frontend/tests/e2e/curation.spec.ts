import { test, expect } from "@playwright/test";

test("큐 → 드릴다운(라벨/제외) → 검수완료 → 큐에서 검수됨 확인", async ({
  page,
}) => {
  await page.goto("/curation");
  // 미검수-우선 정렬: #128이 먼저, 미검수 배지 노출.
  await expect(page.getByText("#128")).toBeVisible();
  await expect(page.getByText("● 미검수")).toBeVisible();

  // 드릴다운.
  await page.getByText("#128").click();
  await expect(page).toHaveURL(/\/curation\/128$/);
  await expect(page.getByRole("heading", { name: /잡 #128/ })).toBeVisible();

  // 라벨 편집 → blur로 옵티미스틱 PATCH.
  // mock 시드 #128은 row_index 0·1 두 pair를 가진다(mocks/curation.ts). 시드 변경 시 이 셀렉터도 동반 수정.
  const labelInput = page.getByLabel("행 1 라벨");
  await labelInput.fill("배추");
  await labelInput.blur();
  // 편집이 조용히 실패/롤백하지 않았음을 명시 단언한다. 옵티미스틱 PATCH가
  // 성공하면 canonical_label이 "배추"로 merge돼 입력 표시값이 유지되고,
  // 실패해 롤백되면 value 동기 effect가 시드값("무")으로 되돌린다(web-first가 포착).
  await expect(labelInput).toHaveValue("배추");

  // 제외 토글 → 옵티미스틱 PATCH.
  await page.getByRole("button", { name: "제외" }).first().click();
  await expect(
    page.getByRole("button", { name: "포함" }).first(),
  ).toBeVisible();

  // 검수 완료 → 큐로 복귀.
  await page.getByRole("button", { name: "검수 완료" }).click();
  await expect(page).toHaveURL(/\/curation$/);

  // 잡 #128이 이제 검수됨으로 내려간다.
  // 행은 시맨틱 table row로 노출되고, 첫 셀의 접근성 버튼("잡 #128 상세")이 키보드·SR 진입점.
  await expect(
    page
      .getByRole("row")
      .filter({ has: page.getByRole("button", { name: "잡 #128 상세" }) })
      .getByText("✓ 검수됨"),
  ).toBeVisible();
});

test("워프 미산출 잡 → 워프 이미지 404 시 placeholder로 폴백", async ({
  page,
}) => {
  await page.goto("/curation/127"); // warp_ok=false 시드 — 워프 산출 파일이 없다.
  // JobImagePanel은 warp_ok로 분기하지 않고 항상 img를 렌더한 뒤, 로드 실패(404) 시
  // onError로 src를 data:image/svg+xml placeholder로 교체한다(코로케이트 단위
  // 테스트: components/curation/JobImagePanel.test.tsx).
  const warped = page.getByAltText("워프 전표");
  await expect(warped).toBeVisible();
  await expect(warped).toHaveAttribute("src", /^data:image\/svg\+xml/);
});
