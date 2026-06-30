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
- 재학습 진입점은 하나로 유지한다(라이브 교정용 평행 학습 경로를 만들지 않는다). 단 그 진입점의 실제 입력은 디렉터리가 아니라 `train_contrastive`가 import하는 `build_rows()` 동일-walk + 교정 JSON이다 — `training_pairs` crop을 이 walk에 합류시킨다. 현재 그 학습 의존 체인은 gitignore된 `report/sp2_spike/item/`에 있고 하드코딩 절대경로를 쓰므로, 브리지에 앞서 production(git-track·env 주입)으로 끌어올린다(spec §7).
- ADR 0003대로 재학습 *실행*은 지금은 macmini 수동 CLI다. 페이지는 큐레이션 결정만 영속화하며, 향후 페이지-주도 학습 실행·모니터링이 이 read-model 위에 얹힌다.
- 금액(`Qwen3-VL`)은 학습 대상이 아니므로(ADR 0002·CONTEXT) 학습 후보를 이루지 않는다. 큐레이션 화면에서 금액은 행 식별용 읽기전용 맥락으로만 보인다.
