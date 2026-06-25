# SP2 품목·금액 결합 그룹핑 라벨링 — 설계 (2026-06-26)

> 워프 crop 점검 이후, **품목·금액 라벨링 품질**(박스 위치/크기 + 그룹핑)을 개선하는 설계.
> 입력 spec: `2026-06-25-sp2-handwriting-recognizer-findings.md`(§3 그룹핑 규칙, §8-B 라벨셋).
> 대상 코드: `apps/invoice-ocr/ml/report/sp2_spike/item/`(gitignore·로컬 실험).

---

## 0. 배경 — 현재 메커니즘과 두 결함

`labelset.py:select_items`는 **품목칸 stroke 하나만**(`ITEM_X=(100,392)`) 본다. 헤더 이후
"`DBn`개 연속 + 전부 `stroke≥TRUST_MIN`" 윈도우의 최상단을 품목으로 선택한다. 즉
**그룹핑이 없는 전표(손글씨 행수 == DBn)만** 표현 가능하고, 그룹핑 전표는 `no_window`로
skip된다. 여기서 두 결함이 발생한다:

1. **박스가 글자 없는 곳에서 시작** — crop을 `w[a-4:b+4, …]`로 **고정피치 그리드 셀**(한 칸
   = 피치 P)을 그대로 자른다. deskew 후에도 위상 φ가 어긋나거나 글씨가 칸 안에서 쏠리면
   셀 상단이 빈 공간이 된다. **박스가 실제 획 범위에 스냅되지 않는다.**
2. **빈 그룹핑칸이 정상 박스로 오인식** — 금액칸을 전혀 보지 않는다. 품목칸 stroke만 보는데,
   그림자·격자선·인접 번짐으로 빈 품목칸이 임계를 넘으면 새 항목으로 오선택된다.
   **"빈 품목칸 + 금액 있음 = 위 항목 합산"(§3)이 알고리즘에 없다.**

## 1. 목표 / 성공기준

- **박스**: 손글씨 실제 획에 세로 스냅 → 빈 곳 시작 제거(결함1).
- **그룹핑**: §3 규칙을 알고리즘에 내장 → 빈 continuation칸이 새 항목으로 안 잡힘(결함2).
- **단계적 판정**: 1차 구조(금액척추 + 품목 ink) → 2차 금액값 연속합 정합(보조).
- **교정 에디터**: 사람이 **행 타입만** 클릭 교정, 박스 자동, 교정값을 GT로 export.

성공 = 그룹핑 전표 포함 자동 라벨 정확도↑(현재 trusted 68/74 대비), 박스 육안 타이트,
교정 GT가 few-shot 셋 + 임계 튜닝/평가셋으로 재사용.

## 2. 접근 선택 — 금액칸을 행의 척추로

세 후보 중 채택안:

- **(기각) 고정그리드 + 이중신호** — 기존 `canon` 고정피치 그리드 행에 두 신호만 추가.
  변경 최소이나 **위상 φ에 인질** → 결함1이 남는다.
- **(채택) 금액칸 척추** — **금액칸**(가장 또박또박 쓰이는 열, 숫자 84.8%의 근거)을 행 검출
  1차 기준으로 삼는다. 금액칸 stroke 세로 프로파일/연결요소로 "금액 한 덩어리 = 데이터 행
  1개"를 **실제 글씨 위치**에서 찾고, 고정피치 P로 정규화한다. 각 금액행의 같은 y밴드에서
  품목칸 ink를 본다 → 있으면 새 항목, 없으면 위에 합산(§3 직역). 박스 = 그 밴드 안 ink bbox.
  행이 내용에 고정돼 결함1 소멸, 위상 드리프트에 강함.
- **(기각) 전면 CC 레이아웃** — 데이터 영역 전체 stroke 연결요소 군집화. 고정양식엔 과설계(YAGNI).

**고정피치 P는 정규화·sanity check로 병용**한다(금액척추 + P scaffold).

## 3. 컴포넌트 (모두 `item/`)

| 파일 | 역할 |
|---|---|
| `rows.py` (신규) | **금액칸 척추 행검출** — `AMOUNT_X` stroke 세로 프로파일/연결요소로 데이터 행 y밴드 검출, 피치 P로 정규화. |
| `group.py` (신규·코어) | 행별 품목ink·금액ink 분류 {new/cont/empty} → 블록 형성 → 품목칸 **ink bbox 세로스냅** → 블록↔DB 위치매핑 → proposal 산출. **순수함수 핵심.** |
| `group_amounts.py` (신규·2차) | 금액칸 값 1회 VLM 판독 → 행 정렬 → 블록 합 vs DB 항목합 → ok/mismatch 플래그(보조). |
| `group_editor.py` (신규) | `grouping_editor.html` 생성 — 인터랙티브 행타입 교정·박스 자동·금액/합검증 표시·DB라벨, export → `grouping_corrections.json` 다운로드. |
| `dataset_build.py` (수정) | corrections 우선 ingest(없으면 auto) → `dataset_v2/<DB명>/` 크롭·라벨 생성. |

재사용: `rectify`(워프·deskew), `canon`(`global_pitch`·`fit_phase`를 정규화 scaffold로),
`grid_v4`(상수 `WARP_W/H=900/2100`·`ITEM_X=(100,392)`·`AMOUNT_X=(612,896)`·`DATA_Y=(612,1948)`),
DB GT(`manifest.json` / `item_gt.json`).

## 4. 데이터 흐름

```
warp ─► rows.detect(warp, P) ─► 정렬된 y밴드
                                   │
        group.classify(warp, bands, DBn):
          밴드별 item_ink·amt_ink → type{new/cont/empty}
          → 블록(new + 후속 cont) → 품목 ink bbox 세로스냅 → 블록↔DB 위치매핑
          → proposal{rows:[y0,y1,item_ink,amt_ink,type,box,block,db_idx,db_name], status}
                                   │ (2차)
        group_amounts.validate ─► 행별 금액값·블록합 vs DB → ok/mismatch
                                   │
        group_editor ─► grouping_editor.html (행 클릭→타입순환, 블록·DB라벨 live 재계산)
                                   │ export
        grouping_corrections.json ─► dataset_build ─► dataset_v2 크롭·라벨
```

## 5. 핵심 규칙 (`group.classify` — 순수함수)

- `amt_ink` 있음 = **데이터 행**. 없음 = 빈행(데이터 밖) → `empty`.
- 데이터 행 중 `item_ink` 있음 = **`new`**, 없음 = **`cont`**(위 블록 합산).
- 블록 = `new` + 뒤따르는 `cont`들. **블록 수 == DBn**이면 `status=ok`(자동 trusted 후보),
  아니면 `needs_review`(에디터 플래그, 자동 trusted 제외).
- `item_ink`/`amt_ink`는 `labelset.stroke_frac`(국소대비 — 전역 그림자·인쇄 격자선 배제)
  재사용. 임계는 별도 상수(`ITEM_MIN`/`AMT_MIN`)로 두어 튜닝셋으로 조정.
- 박스 = 그 밴드 안 품목칸 국소대비 stroke의 [첫,끝] 행 → 패딩. 가로는 `ITEM_X` 유지
  (열 구조 고정), 선행·후행 빈 행만 트림.

## 6. 2차 — 금액값 연속합 정합 (`group_amounts`, 보조)

- 금액칸(`AMOUNT_X`) strip 1회 VLM 판독(기존 숫자 파이프라인 경로 재사용) → 위→아래 정수 리스트.
- 데이터 행(`amt_ink` 있는 밴드)에 위치순 정렬. **판독수 == 데이터행수면** 행별 값 배정,
  블록별 합 = DB 항목합 비교 → `ok`/`mismatch` 플래그.
- **하드 게이트 아님(보조 신호).** 판독수≠데이터행수면 부분표시(정렬 불확실 명시). §6 spec의
  "같은 금액 반복·복수해에서 틀린 그룹핑이 합검증 통과"(false confidence)를 인지하고,
  에디터에서 사람 확정의 참고로만 쓴다.

## 7. 교정 에디터 UX (`group_editor`)

- 전표 카드: 좌측 워프+오버레이(밴드 타입색·박스), 우측 행 테이블 = 각 데이터행 칩
  (품목썸네일 | 금액썸네일·값 | 타입뱃지 | `new`면 DB명·DB금액), 블록은 들여쓰기/브래킷.
- 행 클릭 → `new→cont→empty→new` 순환 → JS가 블록·DB매핑·합검증 즉시 재계산·재렌더.
- 헤더: status(블록수 vs DBn, 합검증), 필터(mismatch만 / skip만 / 전체).
- **export** 버튼 → Blob 다운로드 `grouping_corrections.json` =
  `{<cname>: {"bands": [[y0,y1],…], "types": ["new","cont",…]}}`. 박스는 타입에서
  알고리즘이 재스냅(타입만 교정 원칙).

## 8. dataset_build 통합

- `grouping_corrections.json` 있으면 그 전표는 **교정 타입 우선**, 없으면 auto proposal.
- `status==ok`(또는 교정 후 블록수==DBn)인 전표만 `new` 행 박스를 크롭 → `dataset_v2/<DB명>/<cname>_<k>.png`.
- 기존 `twoup_split.json` 제외(2-up inv042), 가림 4건은 워프 실패로 자연 needs_review.

## 9. 알려진 한계 (v1 범위 — 정직하게)

- **타입만 교정 = 밴드 검출 오류는 못 고침.** 금액척추가 행을 놓치거나 과분할하면 그 전표는
  `needs_review`로 빠지고 **오늘처럼 사람이 외부 처리**(2-up·가림 4건과 동일). 금액은 항상
  존재해 누락은 드물지만 v1에서 명시. (밴드 split/merge 교정은 v2 후보.)
- **2차 금액합은 보조**(하드 게이트 아님) — false confidence·판독수 불일치 가능.
- few-shot 정확도 향상은 **부수효과**이지 직접 목표가 아니다(목표 = 라벨 품질·수량).

## 10. 테스트

sp2_spike는 gitignore 실험물이라 production 테스트 규약 비적용(CLAUDE.md). 단
`group.classify`는 **순수함수**라 합성 시퀀스(`item_ink`/`amt_ink` 패턴 → 기대 타입/블록)
경량 단위테스트를 동반한다. 대표 케이스:

- 그룹핑 없음: `[(new,…),(new,…),…]` DBn개 → 블록 DBn, status=ok.
- 그룹핑 있음(inv_001식): `new,cont,cont,cont,new,cont,cont,…` → 블록이 DBn으로 축약.
- 노이즈 하단행: 데이터 블록 뒤 빈행→`amt_ink` 없는 행은 empty로 배제.
- 빈 품목칸 오선택 회귀: `amt_ink` 있고 `item_ink` 없는 행 = `cont`(새 항목 아님).

## 11. 검증 (사용자 리뷰 루프)

1. 파이프라인 실행 → `grouping_editor.html` 열기.
2. 74장 검토, 행 타입 교정, export → `grouping_corrections.json`.
3. `dataset_build` 재실행 → 라벨셋 통계(trusted/crop/라벨)를 현재(68/314/142)와 비교.
4. 박스 육안 타이트(빈 시작 없음) + 그룹핑 전표 자동 정확도 확인.
