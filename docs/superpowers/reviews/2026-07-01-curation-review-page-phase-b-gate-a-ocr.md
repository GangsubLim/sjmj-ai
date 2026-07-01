# Gate A (룰셋 리뷰) — OCR 큐레이션 검수 페이지 Phase B

- **날짜**: 2026-07-01
- **대상 브랜치**: `feat/curation-review-page-phase-b` (base `origin/devel`)
- **범위**: OCR 큐레이션 검수 페이지 Phase B — 순수 프론트엔드(`apps/invoice-ocr/frontend/`)
- **게이트**: 표준 구현 파이프라인 7단계 게이트 A(룰셋 리뷰) — 독립 러너 소유
- **리뷰 도구**: `/ocr-code-review:review --from origin/devel --to HEAD` (커밋 범위 모드)
- **판정 방법론**: `superpowers:receiving-code-review` (findings = 평가 대상 신호, 맹목 수용 금지)

## 1. 리뷰 실행 결과

- 커밋 범위 모드로 실행(작업트리 비어 있음 — 8 task 커밋 전부 커밋됨).
- 결정론적 collector가 **18개 리뷰 대상 파일**을 산출(테스트/스펙 파일은 결정론적으로 제외됨).
  - 17개 TS/TSX → `ts_js_tsx_jsx.md` 룰셋
  - `.gitignore` 1개 → `default.md` 룰셋(playwright 산출물 무시 추가 — 자명·안전, 무결함)
- 파일별 read-only 리뷰어 8개 팬아웃(대형 파일 개별, 소형 파일 그룹).
- **매칭 없음 아님** — 룰셋이 정상 매칭됨. 아래는 '통과(findings 있음, 판정 후 부분 수정)'.

## 2. Findings 집계

- **High: 0**
- **Medium: 6** (아래)
- **Low: 다수 — review-method에 따라 침묵 폐기**
- 무결함 판정 그룹: `types/curation.ts` · `utils/curation.ts` · `utils/placeholder.ts` · `lib/pagination.ts` (엣지케이스 전수 추적, 무결함) / `autocomplete.tsx` · `main.tsx` · `app/list/page.tsx` · `app/settings/page.tsx` (additive·회귀 없음, 무결함)

## 3. Receiving 판정 (Medium 6건)

| #   | 파일:위치                                    | Finding 요지                                                                                                                                                    | 판정             | 근거                                                                                                                                                                                                                                 |
| --- | -------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 1   | `hooks/use-curation-job.ts:59`               | 같은 pair에 대한 동시 PATCH 시, `prevPair` 스냅샷이 passive effect flush 이전이면 롤백이 먼저 성공한 쓰기를 덮어씀                                              | **REJECT(보류)** | 실재하나 매우 좁은 엣지(같은 pair 두 PATCH가 effect flush 전에 발행). 명시 불변식 "per-pair 롤백(다른 pair 보존)"은 충족됨. 올바른 수정은 옵티미스틱 동시성 재설계로 게이트의 surgical 범위를 넘고 회귀 위험. **후속 과제로 문서화** |
| 2   | `hooks/use-curation-jobs.ts:39`              | fetch effect에 stale-response/unmount 가드 없음. 이 훅은 `page` state·`setPage`를 소유해 빠른 페이지네이션 시 in-flight 경쟁 → 오래된 응답이 최신 데이터 덮어씀 | **ACCEPT**       | 현실적 경쟁(페이지 소유). 표준·저위험 수정. `refetch: fetch` 공개 API 보존하며 reqId ref 가드로 해결                                                                                                                                 |
| 3   | `components/curation/CurationPairRow.tsx:32` | 행마다 `useItems` 즉시 페칭 → N행 = N개 `/items` 요청(사용자가 안 열 수 있는 제안까지)                                                                          | **REJECT(보류)** | 유효한 성능 관찰이나 수정은 Autocomplete/useItems API 표면 확장을 요구. 의도적 최적화로 게이트 touch가 아닌 **후속 과제**. 자명한 무해 수정 없음                                                                                     |
| 4   | `mocks/api.ts:509`                           | mock `reviewJob`이 없는 jobId에도 성공 위조 반환(형제 `patchPair`는 throw). mock 모드에서 UI 실패 경로가 절대 실행 안 됨                                        | **ACCEPT**       | mock 충실도 수정. 형제 `patchPair` 패턴과 일치. 자명·저위험                                                                                                                                                                          |
| 5   | `app/curation/page.tsx:105`                  | 페이지네이션이 `totalPages>1`로만 게이팅(table/empty/skeleton은 `!loading && !error`). fetch 에러 후 totalPages 잔존 → 에러 메시지 아래 컨트롤만 렌더           | **ACCEPT**       | 자명한 일관성 수정. `!loading && !error && totalPages>1`                                                                                                                                                                             |
| 6   | `app/curation/[jobId]/page.tsx:80`           | Warp 이미지 인라인 `onError`가 공용 `handleImageError`의 재진입 가드를 누락(DRY 이탈 + onError 무한 루프 위험)                                                  | **ACCEPT**       | 자명·안전. 바로 위 원본 이미지가 쓰는 `handleImageError` 재사용                                                                                                                                                                      |

**요약: ACCEPT 4 · PARTIAL 0 · REJECT(보류) 2**

## 4. 적용한 수정 (ACCEPT 4건 — 별도 `fix:` 커밋)

수정자는 리뷰어와 분리된 편집 권한 서브에이전트가 수행("자기 코드 자기 합리화" 차단).

- **A (finding 6)** `[jobId]/page.tsx`: Warp `onError` 인라인 → 공용 `handleImageError` 재사용(루프 가드 확보)
- **B (finding 5)** `curation/page.tsx`: 페이지네이션 게이트를 `!loading && !error && totalPages > 1`로 통일
- **C (finding 4)** `mocks/api.ts`: `reviewJob`에 존재하지 않는 jobId throw 가드 추가(형제 `patchPair`와 일치)
- **D (finding 2)** `use-curation-jobs.ts`: `reqId` ref로 stale-response/unmount 가드. `refetch: fetch` 공개 API 보존

## 5. 계약 불변식 검증

수정·판정 전 과정에서 아래 외부 계약 불변식 보존을 확인:

- 성공 envelope `{success, data, pagination?}` — 불변
- 계약 비대칭(GET pair = top5 有 / job_id 無, PATCH = top5 無 / job_id 有) — 리뷰어가 types·mocks에서 정확 모델링 확인, 수정으로 미변경
- 옵티미스틱 PATCH top5 보존 · per-pair 롤백 — `use-curation-job` 훅 소유, 미변경(finding 1은 보류)
- invoice 절대 미변경 — `reviewJob`은 `curation/jobs/{id}/review`만 호출, 확인됨
- api-spec이 타입 SSoT — 수정은 계약 스키마를 건드리지 않음

## 6. 보류 항목(후속 과제 트래킹 권고)

- **finding 1**: `use-curation-job` 같은-pair 동시 PATCH 롤백 — 필드 단위 롤백 또는 pair별 in-flight 스냅샷 키잉으로 개선 가능. 옵티미스틱 동시성 설계 결정 필요.
- **finding 3**: `CurationPairRow` 행별 제안 페칭 fan-out — Autocomplete 포커스/오픈 시로 게이팅(useItems `enabled` 플래그 등). 성능 최적화.

## 7. clean-before-PR 후속 (fix 커밋 amend)

- finding 2 수정(`use-curation-jobs.ts` reqId 가드)이 남긴 `react-hooks/exhaustive-deps` warning 1건을 해소.
- cleanup은 '가장 최근' 발행 요청(refetch로 시작된 in-flight 포함)까지 무효화해야 하므로 스냅샷 지역변수 복사는 의도를 깬다 → 최신 `reqId.current`를 그대로 증가시키고 `eslint-disable-next-line react-hooks/exhaustive-deps` + WHY 주석으로 명시 억제(기존 `use-invoices.ts` 관례와 일치). `npm run lint` warning 0 확인.
