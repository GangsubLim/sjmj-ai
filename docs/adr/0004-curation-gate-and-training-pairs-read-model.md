# 큐레이션 게이트와 training_pairs read-model로 라이브 교정을 재학습에 잇는다

라이브 HITL 교정(`ocr_corrections`)을 품목 인식기 재학습으로 흘려보내기 위해, confirm된 모든 행을 `(crop → 라벨)` 학습 후보로 머티리얼라이즈한 `training_pairs` 테이블을 두고, 사람이 잡 단위로 검수(배제·정식명 정규화)한 뒤 "검수 완료"로 표시한 잡의 `included` 쌍만 학습 export로 푼다. 즉 큐레이션은 재학습 직전의 **2차 관문**이고, `training_pairs`는 학습 데이터의 SSoT read-model이다.

이유: confirm 시점의 1차 검수는 명세서를 빠르게 입력하는 흐름이라, 그 라벨을 그대로 학습 정답지로 쓰면 오타·표기흔들림("생삼겹살/삼겹살")이 학습셋을 파편화시킨다. 자유 텍스트 `invoice_items.name`과 학습용 정식 라벨을 분리하고, 학습 전 사람이 한 번 더 거르는 관문이 필요하다.

## Considered Options

- **`ocr_corrections` 위 얇은 결정 오버레이**(crop_ref별 status·canonical만 저장, 원천은 JSON 조인): DRY하지만 "전 잡 통틀어 라벨별 그룹핑·상태 필터·페이지네이션"이 JSON 배열 스캔이 되어 큐레이션 화면의 주 질의가 껄끄럽다. 행-단위 머티리얼라이즈 테이블을 택했다(읽기 모델로서의 중복은 정당).
- **검수 없이 `included` 전부 흘림**: 관문이 무력화되어 사용자가 원한 "재학습 전 검토"가 성립 안 한다. 잡 단위 `curation_reviewed` 게이트를 둔다.
- **행마다 명시 승인**: confirm이 이미 1차 답이라 행별 재승인은 중복 노동. 기본 `included` + 배제만.

## Consequences

- `canonical_label`(학습용 정규화 라벨)은 `invoice_items.name`(청구 사실)과 **의도적으로 갈라질 수 있다**. invoice는 confirm 후 불변이며, 큐레이션은 학습 데이터에만 영향을 준다.
- 큐레이션 페이지가 기존 `grouping_corrections.json` 손편집을 **계승**한다 — 라벨 병합/정규화를 JSON 파일이 아니라 잡 드릴다운의 행 인라인에서 수행한다. 라벨 그룹 단위 일괄 병합 뷰는 파편화가 실제 문제가 될 때 2차 렌즈로 추가한다(YAGNI).
- `excluded`는 두 가지를 함께 담는다 — 크롭 불량(파이프라인 개선 신호)과 **원본에 정답이 없는 행**. 후자는 타이어처럼 관례상 품목명을 생략하고 `단가 × 수량`만 적는 전표에서 나오며, 크롭·인식이 정상이어도 학습쌍이 성립하지 않는다. 판별은 사람이 한다(오크롭된 숫자에도 유사도가 높게 나와 미확신 신호로는 구분되지 않는다). 기준은 `docs/runbooks/ocr-curation-analysis.md`.
- 재학습 진입점은 하나로 유지한다(라이브 교정용 평행 학습 경로를 만들지 않는다). 단 그 진입점의 실제 입력은 디렉터리가 아니라 `train_contrastive`가 import하는 `build_rows()` 동일-walk + 교정 JSON이다 — `training_pairs` crop을 이 walk에 합류시킨다. 현재 그 학습 의존 체인은 gitignore된 `report/sp2_spike/item/`에 있고 하드코딩 절대경로를 쓰므로, 브리지에 앞서 production(git-track·env 주입)으로 끌어올린다(spec §7).
- ADR 0003대로 재학습 *실행*은 지금은 macmini 수동 CLI다. 페이지는 큐레이션 결정만 영속화하며, 향후 페이지-주도 학습 실행·모니터링이 이 read-model 위에 얹힌다.
- 금액(`Qwen3-VL`)은 학습 대상이 아니므로(ADR 0002·CONTEXT) 학습 후보를 이루지 않는다. 큐레이션 화면에서 금액은 행 식별용 읽기전용 맥락으로만 보인다.

## 게이트 해제 규칙 (Issue #52, 2026-08-05 추가)

검수 완료된 잡의 학습쌍을 수정하면 게이트를 **무조건 해제**하고(`curation_reviewed = 0`) 사람이 "검수 완료"를 다시 눌러야 학습셋에 들어간다. 검수 전에는 제외/포함 토글이 초안이고 "검수 완료"가 확정 행위인데, 검수 후에는 제외 버튼 자체가 확정 행위가 되어 오클릭 한 번이 다음 뱅크 갱신에 그대로 들어가던 비대칭을 없앤다.

- **수정된 쌍의 `reviewed_at`은 NULL로 되돌린다.** `ml/tools/blank_crop_report.py`의 `--recheck-reviewed` 기계 경로가 이미 쓰던 관례를 사람 경로에 맞춘 것이다 — 두 경로가 같은 컬럼의 의미를 다르게 쓰지 않기 위해서다. `reviewed_at`은 "이 쌍이 사람 확인을 통과했다"는 표식이지 감사 로그가 아니다. 덕분에 목록의 `unreviewed_count`가 그대로 "재확인해야 할 행 수"가 된다.
- **첫 검수 시각은 `ocr_jobs.curation_reviewed_at`이 잡 단위로 보존한다**(`migration_011`). `mark_reviewed`가 `COALESCE`로 첫 값만 채우고 해제 시에는 지우지 않으므로, `(curation_reviewed, curation_reviewed_at)` 2필드로 미검수 / 재검수 필요 / 검수됨 3-state가 갈린다. 마이그레이션 이전에 기계 경로가 잡의 **모든** 쌍을 수정해 스탬프가 전부 사라진 잡은 과거 검수 여부를 DB만으로 복원할 수 없다 — legacy unknown으로 인정하고 사람이 다시 검수한다.
- **해제는 조건부가 아니다.** 값이 실제로 바뀌었는지 판별하지 않는다. 이미 미검수면 no-op이고, 제외했다 되돌린 경우도 재확인 대상으로 본다.
- **정식 라벨의 자동완성 사전 등록 트리거는 `mark_reviewed` 하나뿐이다.** 과거에는 "검수완료 잡의 쌍이 included면 즉시 등록"하는 우회 경로가 있었는데, 그 존재 이유("검수완료 버튼이 disabled라 트리거를 다시 걸 수 없다")를 이 변경이 없앴다. 게이트가 풀린 상태에서 학습용 라벨만 먼저 사전에 새는 모순을 막는다(ADR 0008).
- **잠금 순서는 `ocr_jobs` → `training_pairs`로 고정한다.** `mark_reviewed`와 `patch_pair` 두 **사람 경로**가 같은 순서를 쓰므로 둘 사이의 순환 대기는 성립하지 않는다. 반면 기계 apply 스크립트(`ml/tools/blank_crop_report.py`의 `build_apply_script`)는 한 트랜잭션에서 `training_pairs` → `ocr_jobs`, 즉 **자식→부모**라 사람 경로와의 순환 대기는 **여전히 가능하다**. 이 위험은 #52가 만든 것이 아니다 — `mark_reviewed`가 이미 부모→자식이었으므로 이전부터 존재했다. 기계 경로는 오퍼레이터 단발 실행이고 InnoDB 교착 감지가 한쪽을 롤백하므로 수용하며, 기계 경로 정렬은 후속 이슈 [#76](https://github.com/GangsubLim/sjmj-ai/issues/76)으로 분리했다.
