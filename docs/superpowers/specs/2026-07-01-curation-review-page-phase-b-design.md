# OCR 큐레이션 검수 페이지 (Phase B) 설계

수기 거래명세서 OCR 큐레이션의 **검수 UI**. Phase A가 깔아둔
`/api/curation/*` 6개 엔드포인트 위에 관리자성 검토 화면을 올린다 —
재학습 직전 사람이 단계별 인식 결과를 보고 학습쌍을 정리하는 2차 관문.

정본 설계는 `2026-06-30-ocr-curation-retraining-design.md` §6. 이 문서는 그 §6을
구현 가능한 수준으로 좁힌 Phase B 전용 설계다. 외부 계약(엔드포인트·응답 형태)은
Phase A에서 확정됐으므로 여기서는 프론트만 다룬다.

## 1. 확정 결정 (brainstorming)

| 결정          | 내용                                                                                             |
| ------------- | ------------------------------------------------------------------------------------------------ |
| 폼팩터        | **데스크톱 우선 관리자 도구.** 메인 nav 미포함. 모바일은 반응형 degrade(별도 분기 없음)          |
| 진입점        | `/curation` URL 직접 + `설정` 페이지 하단 보조 링크 1개(데스크톱 노출)                           |
| 라벨 자동완성 | 기존 `/items`(itemSuggestionsAPI)만. "유사 기존 학습 라벨 힌트"는 Phase C로 연기(신규 EP 불필요) |
| 저장 시점     | **즉시 옵티미스틱 PATCH.** 라벨 blur·제외 토글마다 pair PATCH, 실패 시 롤백 + toast              |
| mock          | JSON 4종만 mock(VITE_USE_MOCK), 이미지 2종은 실서버/placeholder 404 degrade                      |

## 2. 아키텍처 · 라우팅

`main.tsx`에 lazy 라우트 2개 추가(기존 `AppShell` children):

- `/curation` → `CurationQueuePage` (검수 큐 목록)
- `/curation/:jobId` → `CurationJobPage` (잡 드릴다운)

메인 nav(`TopNav`/`BottomNav`)에는 넣지 않는다 — 영업사원 일상 동선과 분리.
`CurationJobPage`는 넓은 2단 레이아웃을 전제로 하고, 모바일에선 반응형 그리드로
세로 스택 degrade(별도 모바일 컴포넌트 없음).

## 3. 데이터 레이어

### 3.1 `types/curation.ts` (신규)

api-spec 스키마를 TS로 미러한다(드리프트 시 api-spec이 SSoT).

```ts
interface CurationTop5Item {
  label: string;
  sim: number;
}

interface CurationJobSummary {
  job_id: number;
  invoice_id: number | null;
  curation_reviewed: boolean;
  pair_count: number;
  unreviewed_count: number;
  created_at: string;
}

interface CurationPair {
  id: number;
  crop_ref: string;
  row_index: number;
  draft_label: string | null;
  final_label: string | null;
  canonical_label: string | null;
  supply: number | null;
  status: "included" | "excluded";
  reviewed_at: string | null;
  top5: CurationTop5Item[];
}

interface CurationJobDetail {
  job_id: number;
  invoice_id: number | null;
  curation_reviewed: boolean;
  warp_ok: boolean;
  created_at: string;
  pairs: CurationPair[];
}

type CurationPairPatch = {
  status?: "included" | "excluded";
  canonical_label?: string;
};
```

### 3.2 `services/api.ts` — `curationAPI` 추가

API 호출 단일 진입점 규약 준수.

- `getJobs(params)` → `ListResponse<CurationJobSummary>` (페이지네이션)
- `getJob(jobId)` → `SingleResponse<CurationJobDetail>`
- `patchPair(id, patch)` → `SingleResponse<CurationPair>`
- `reviewJob(jobId)` → `SingleResponse<{ ... }>` (ack)
- `imageUrl(jobId, kind)` / `cropUrl(jobId, row)` → **순수 URL 문자열 빌더**
  (axios 호출 아님, `getApiBaseUrl()` 기반으로 경로 조립해 `<img src>`에 직결)

mock proxy: JSON 4종(`getJobs`/`getJob`/`patchPair`/`reviewJob`)만
`createMockProxy`로 감싼다. URL 빌더는 real만 — mock 모드에선 실서버/placeholder
이미지로 떨어진다(이미지는 raw FileResponse라 mock 부적합).

### 3.3 훅 (`hooks/`, 코로케이트 테스트)

- `use-curation-jobs.ts` — 큐 목록 페칭 + 페이지네이션 상태
- `use-curation-job.ts` — 잡 상세 페칭 + 옵티미스틱 PATCH + `reviewJob`

**옵티미스틱 PATCH 흐름**: 토글/라벨 blur → 로컬 `pairs` 즉시 갱신(불변 업데이트)
→ `patchPair` 발행 → 성공이면 서버 응답으로 확정, 실패면 이전 값 롤백 + sonner
에러 토스트. invoice는 절대 건드리지 않는다(분리 불변식).

## 4. UI 페이지

### 4.1 `CurationQueuePage` (`/curation`)

confirmed 잡 목록. 미검수 잡 우선, 페이지네이션(`ui/pagination` 재사용).

```
┌─ OCR 학습 큐레이션 ──────────────────────────────┐
│  [ 미검수만 보기 ☑ ]                  총 42건     │
├──────────────────────────────────────────────────┤
│  잡    명세서   행수  미처리   상태      생성일    │
│  #128  inv·341   7     7      ● 미검수  06-30 →   │
│  #127  inv·340   5     0      ✓ 검수됨  06-30 →   │
├──────────────────────────────────────────────────┤
│                    ‹ 1 2 3 ›                      │
└──────────────────────────────────────────────────┘
```

- 행 클릭 → `/curation/:jobId`.
- 상태 배지: `curation_reviewed`(● 미검수 / ✓ 검수됨), 미처리수 = `unreviewed_count`.
- 빈 상태 `ui/empty-state` 재사용.

### 4.2 `CurationJobPage` (`/curation/:jobId`) — 핵심

데스크톱 2단(좌 단계 이미지 / 우 행 테이블), 모바일 세로 스택 degrade.

```
┌─ 잡 #128  (inv·341)            [ 검수 완료 ] ─────────────────┐
├──────────────────────────┬───────────────────────────────────┤
│  단계 이미지              │  행별 학습쌍                       │
│  ① 원본                  │  #0  [crop🖼]  배추                │
│   [원본 전표 🖼]          │      top5: 배추·무·파...          │
│  ② Warp                  │      라벨[ 배추        ▼] [제외]   │
│   [warped.png 🖼]         │      금액 12,000 ⟨학습 비대상⟩     │
│   (없으면 placeholder)    │  ─────────────────────────────    │
│                          │  #1  [crop🖼]  무 ✎변경           │
│                          │      라벨[ 무          ▼] [제외]   │
└──────────────────────────┴───────────────────────────────────┘
```

**행(pair) 카드**

- crop 이미지: `cropUrl(jobId, row_index)` 직결, `onError` → placeholder.
- `top5` 칩: 최초 인식(`label·sim`), 읽기 전용 참고.
- 라벨 입력: `canonical_label` 편집 — `/items` 자동완성(itemSuggestionsAPI,
  Combobox 패턴). blur 시 옵티미스틱 PATCH.
- 제외 토글: `status` included↔excluded, 즉시 옵티미스틱 PATCH. 제외 행은 흐리게.
- 금액: `supply` 읽기 전용 + "학습 비대상" 배지.
- 변경 강조: `draft_label !== final_label`(인식 교정) 또는
  `final_label !== canonical_label`(재정규화) 시 ✎ 표시.

**단계 이미지**: `imageUrl(jobId,'original')` / `imageUrl(jobId,'warped')`.
`warp_ok=false`이거나 404면 워프 칸은 placeholder("워프 산출 없음").

**검수 완료 버튼**: `reviewJob(jobId)` → 성공 시 toast + `navigate('/curation')`.
잡이 큐에서 ✓로 내려간다.

### 4.3 보조 진입 링크

`설정` 페이지(`app/settings/page.tsx`) 하단에 "OCR 학습 큐레이션" 링크 1개 추가
(데스크톱 한정 노출). URL만으로는 발견 불가하므로.

## 5. 테스트 (정본 §9 준수)

- **vitest 단위**: `use-curation-jobs`/`use-curation-job`(옵티미스틱 PATCH 성공·롤백),
  `curationAPI` URL 빌더, 변경 강조 판정 로직.
- **playwright e2e**: 업로드→confirm→`/curation` 큐→드릴다운(라벨편집·제외)
  →검수완료→큐에서 ✓ 확인. 라이브 백엔드 필요.

## 6. 비범위 (Phase B)

- "유사 기존 학습 라벨 힌트"(신규 distinct-canonical EP 필요) → Phase C.
- 라벨 그룹 단위 일괄 병합 뷰 → 정본 §10 그대로 2차 렌즈.
- 학습 브리지·재학습 실행 → Phase C.
- 모바일 전용 레이아웃 분기(반응형 degrade로 충분).
