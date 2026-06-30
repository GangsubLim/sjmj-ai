# Receiving-Code-Review — OCR 큐레이션 Phase A (파이프라인 3단계)

- **대상 plan:** `docs/superpowers/plans/2026-06-30-ocr-curation-phase-a.md` (1819줄)
- **검증 대상 리포트:** `docs/superpowers/reviews/2026-06-30-ocr-curation-phase-a-eng-review.md` (2단계 eng-review, 종합판정 Go-with-fixes)
- **방법론:** `superpowers:receiving-code-review` — 맹목 수용·맹목 거부 금지. 각 지적을 실제 코드/Pydantic 동작으로 검증.
- **불변식 기준:** `AGENTS.md`(외부 계약 불변식) · `.claude/rules/api-conventions.md`(details=`{필드:메시지}`, 400 status)
- **검증 수단:** `errors.py`·`ocr_repository._parse_job` 실 Read, 기존 Validator 슬라이스 grep, **Pydantic v2 + FastAPI 실 TestClient 실행으로 `loc` 동작 측정**.

## 판정 요약

| 이슈                                                         | 심각도 | 판정        | 처리                                                                     |
| ------------------------------------------------------------ | ------ | ----------- | ------------------------------------------------------------------------ |
| 1. 빈 body(model_validator) 실패 시 `details` 키 = `"body"`  | MEDIUM | **ACCEPT**  | 키 규약 문서화 + unit/contract 테스트로 고정(invented-key 정규화는 거부) |
| 2. result_json 파싱 중복(`find_job_detail` vs `_parse_job`)  | MEDIUM | **PARTIAL** | 추출 거부(2줄·repo격리·YAGNI), 의도적 중복 WHY 주석만                    |
| 3. Task9 invalid-kind 400이 Task1 핸들러 의존(문서 미기재)   | LOW    | **ACCEPT**  | Task9에 의존성 메모 1줄                                                  |
| 4. `update_pair` 데드 분기 + `_data_dir` RuntimeError 미커버 | LOW    | **PARTIAL** | 방어코드 WHY 주석만(강제 테스트 거부)                                    |
| 5. `build_training_pairs` row_index 파싱 취약                | LOW    | **REJECT**  | 변경 없음(신뢰 입력 + 기존 테스트 커버)                                  |
| 6. status DB enum 미강제(VARCHAR)                            | LOW    | **REJECT**  | 변경 없음(spec §3.1과 일치)                                              |
| 7. 라인 번호 ±1 드리프트                                     | LOW    | **ACCEPT**  | Global Constraints에 앵커 우선 메모 1줄                                  |

**확정 plan 수정안: 8개 edit** (아래 §확정 수정안). CRITICAL/HIGH 없음 — eng-review의 Go-with-fixes 판정 유지. 모든 edit은 외부 계약 불변식·TDD 절차·기존 plan 포맷을 보존한다.

---

## 지적별 검증 근거

### MEDIUM 1 — 빈 body 실패 시 details 키 = `"body"` → **ACCEPT**

**검증.** Task 7의 `CurationPairPatch`를 실제로 띄워 Pydantic v2 + FastAPI TestClient로 `RequestValidationError.errors()`의 `loc`을 측정했다:

| 요청                                    | `loc`                        | `loc[-1]`(=details 키) |
| --------------------------------------- | ---------------------------- | ---------------------- |
| `json={}` (model_validator: 둘 다 None) | `('body',)`                  | **`"body"`**           |
| `json={"status":"garbage"}`             | `('body','status')`          | `"status"` ✓           |
| `json={"canonical_label":""}`           | `('body','canonical_label')` | `"canonical_label"` ✓  |
| path `/p/abc` (int 파싱 실패)           | `('path','id')`              | `"id"` ✓               |

→ 리뷰 지적이 **정확**하다. `model_validator(mode="after")`가 raise하는 cross-field 에러는 단일 `loc=('body',)`라서 Task1 핸들러(`field=str(loc[-1])`, plan line 129)가 details 키를 `"body"`로 만든다. 필드/경로 에러는 정상적으로 실제 필드명을 산출하므로 문제는 **whole-object(model_validator) 케이스에 한정**된다.

**계약 영향 판단.** `api-conventions.md`·plan line 15는 `details`를 `{필드: 메시지}` 문자열 맵으로 규정한다. `"body"`는 요청 모델의 필드가 아니므로 규약의 문언적 정신과 어긋난다. 기존 Validator 슬라이스의 whole-body 에러 처리를 grep으로 대조하면:

- `settings.py:62` `bad_request("수정할 설정값이 필요합니다.")` → **details 생략**(`_error_body`가 None이면 details 키 자체를 안 넣음, `errors.py:36-37`).
- `sales_records.py:56` → `{"year":..,"month":..}` 실제 필드 맵.

즉 기존 코드에는 "body" 같은 합성 키 선례가 없다. 다만 Task7의 `test_patch_pair_empty_body_is_400`(plan 1092-1097)은 `code`만 검사하고 details 키는 미검사 → **회귀 시 조용히 깨질 수 있음**(리뷰 지적과 일치).

**처리 결정.**

- 리뷰가 제시한 두 옵션 중 **invented-key 정규화(`"_"`/`"non_field"`)는 거부**한다 — 코드베이스에 선례가 없고, `"body"`는 오히려 "본문 레벨 폼 에러"라는 의미를 담아 클라이언트(향후 큐레이션 UI)가 폼-레벨 에러로 표시하기에 더 정직하다. 새 합성 키를 발명하면 프론트가 또 다른 비-필드 키 규약을 알아야 한다(YAGNI·선례 부재).
- **채택: 키 규약을 명시적 계약으로 승격** — (A) Task1 Interfaces에 "단일 `loc`(model_validator/whole-body)는 `"body"`로 키잉" 규약을 명문화, (B) Task1 단위테스트에 model-level 케이스를 추가해 **제네릭 핸들러**의 동작을 고정(향후 모든 Pydantic 슬라이스가 이 핸들러를 재사용), (C) Task7 contract 테스트에 `"body"` 키 존재를 단언해 통합 지점에서 고정. 이는 코드베이스 철학("details 형태를 contract 테스트로 문자 단위 고정")과 정확히 일치한다.

### MEDIUM 2 — result_json 파싱 중복 → **PARTIAL**

**검증.** 두 지점을 실제 Read:

- `ocr_repository.py:10-16 _parse_job` — `row`(Result Row)를 받아 `dict(row._mapping)` 후 `d["result_json"] = json.loads(raw) if isinstance(raw,str) else raw`.
- plan Task6 `find_job_detail`(950-973) — 이미 `dict(job_row)`를 가진 상태에서 동일한 2줄(`raw = job.get(...); job["result_json"] = json.loads(raw) if isinstance(raw,str) else raw`)을 인라인.

중복의 실체는 **딱 2줄의 관용구**다. `_parse_job`은 `row._mapping`까지 처리하지만 `find_job_detail`은 이미 dict라 그 부분이 다르다. 두 함수는 **서로 다른 repository 모듈**(ocr vs curation)에 있고, 코드베이스 규약은 "repository 격리 — 각 repo가 자기 데이터 접근을 캡슐화"다.

**처리 결정.**

- **공유 모듈 추출 거부.** 2줄 관용구를 위해 공유 util 모듈을 만들면 두 repo가 한 모듈에 결합되고, coding-style의 DRY 규약("추상화는 반복이 실제일 때, 투기적이지 않게")·KISS·surgical 원칙에 역행한다. 추출은 과설계다.
- **채택: 의도적 중복 WHY 주석 1줄**(리뷰의 lighter 옵션). 미래 리뷰어의 재플래깅을 막고 "왜 repo 격리상 중복인지"를 코드에 남긴다(coding-style: 주석은 WHY).

### LOW 3 — Task9 invalid-kind 400이 Task1 핸들러 의존 → **ACCEPT**

**검증.** Task9 `image(job_id, kind: ImageKind)`(plan 1506-1512)에서 `kind=garbage`는 enum path param 검증 실패 → `RequestValidationError`. 실측에서 path param 실패는 `loc=('path','id')` 형태로 동일 핸들러를 경유함을 확인(위 표). `kind`도 동일하게 `loc=('path','kind')` → 400 VALIDATION_ERROR + details 키 `"kind"`. Task9의 `test_image_invalid_kind_is_400`(1402-1406)이 이 경로에 의존한다. 그러나 plan은 Task1을 "body 검증 선결"로만 서술 → 의존이 암묵적. Task1→...→Task9 순차 실행이라 **동작엔 문제 없음**.

**처리 결정.** Task9에 "Task1 핸들러가 path/query 검증 실패(enum kind 포함)도 422→400으로 변환한다"는 의존성 메모 1줄 추가. 비용 0, 추적성 향상.

### LOW 4 — `update_pair` 데드 분기 + `_data_dir` RuntimeError 미커버 → **PARTIAL**

**검증.**

- `update_pair`(plan 1160-1170)의 `if not cols: return` — 라우터가 `patch.model_dump(exclude_unset=True)`(1206)를 넘기고 `model_validator`가 "둘 다 None"을 차단하므로 `cols`가 빈 경우는 **API 경로로 도달 불가**. 순수 방어코드.
- `_data_dir`(plan 1455-1459)의 `RuntimeError` — 테스트가 `_data_dir` 픽스처(1368-1371)로 `SJMJ_DATA_DIR`을 항상 설정하므로 미커버. 단 이는 **운영 오설정 가드**(env 누락 시 명확 실패)로 의미가 있다.

**처리 결정.**

- **강제 테스트 추가 거부.** API로 도달 불가한 분기에 contrived 단위테스트를 다는 것은 "테스트를 위한 테스트"이며, eng-review도 "집계 커버리지 80%에 영향 미미"·"과하지 않게"라고 명시. YAGNI.
- **채택: 두 방어코드에 WHY 주석**으로 "의도적 방어/오설정 가드"임을 명시 → 커버리지 검토자·미래 리뷰어의 재플래깅 차단. 80% 게이트는 집계라 영향 없음.

### LOW 5 — row_index 파싱 취약 → **REJECT(변경 없음)**

**검증.** `build_training_pairs`(plan 387, 456-)의 `row_index = int(crop_ref의 "/row-" 뒤)`. crop_ref는 **항상 서버 생성**(`infer_job.py`의 `job-{id}/row-{i}`). plan Task3에 `test_build_training_pairs_skips_lines_without_crop_ref`(431-433, crop_ref 없으면 제외)와 `test_build_training_pairs_parses_multidigit_row_index`(436-440, 멀티자리)가 이미 존재. crop_ref가 있으면서 `/row-`가 없는 케이스는 신뢰 입력 경로상 발생 불가.

**근거 있는 거부.** 신뢰 입력 + 기존 테스트 2건 커버 + 방어 강화는 발생 불가 시나리오에 대한 에러 핸들링(전역 규칙 "impossible scenario 에러 핸들링 금지"에 해당). eng-review 자신도 "그대로 진행 가능"으로 결론. plan 변경 불요.

### LOW 6 — status DB enum 미강제(VARCHAR) → **REJECT(변경 없음)**

**검증.** spec §3.1이 `status VARCHAR`로 규정(eng-review 코드표 검증 완료). API 경계에서 Pydantic `Literal["included","excluded"]`가 강제하고, confirm/백필은 항상 `'included'`. 직접 SQL 우회만이 위험인데 그건 내부망 단일 사용자 운영 범위 밖.

**근거 있는 거부.** 설계 정본(spec)과 일치 = 의도된 설계. DB CHECK 추가는 spec 변경을 요하는 별도 결정이며 Phase A 범위 밖. plan 변경 불요.

### LOW 7 — 라인 번호 ±1 드리프트 → **ACCEPT**

**검증.** eng-review 코드표가 cv2 import 50(plan 51), ocr_jobs CREATE 끝 157(plan 158), errors 52-55 등 다수 ±1 드리프트를 실측. 전부 "다음 줄 삽입" 기준 앵커라 무해하나, 구현자가 라인 번호를 맹신하면 미세 오삽입 위험.

**처리 결정.** Global Constraints에 "라인 번호는 참고용 — 삽입 위치는 함수명/주석 등 앵커 문자열로 잡는다" 메모 1줄 추가. plan은 이미 "list_jobs 다음", "\_unhandled_handler 아래" 등 앵커를 병기하므로 메모는 그 원칙을 명문화할 뿐.

---

## 확정 plan 수정안 (4단계 plan-editor용 — 8 edit)

> 모든 edit은 외부 계약 불변식·TDD(RED→GREEN→검증→commit)·기존 포맷을 보존한다. 위치는 앵커 문자열 기준(라인 번호는 참고).

### Edit A — [MEDIUM 1] Task 1 Interfaces에 "body" 키 규약 명문화

- **위치:** Task 1 "Interfaces" 블록, plan line 63 문장 끝(`...각 에러 \`loc\`의 마지막 요소(\`str\`).`) 뒤에 이어 붙임.
- **추가 텍스트:**
  > 단, `model_validator(mode="after")`처럼 특정 필드에 매이지 않는 whole-object 검증 실패는 Pydantic이 단일 `loc=("body",)`를 산출하므로 details 키가 `"body"`가 된다(필드명 아님). 이는 **의도된 폼-레벨 에러 키 규약**으로 고정한다 — 합성 키(`"_"`/`"non_field"`)로 정규화하지 않는다. 이 동작은 Step 1 단위테스트로 고정한다.

### Edit B — [MEDIUM 1] Task 1 Step 1 단위테스트에 model-level 케이스 추가

- **위치:** Task 1 Step 1 `tests/unit/test_errors_validation.py` 코드블록(plan 69-107) 끝(`test_validation_details_key_is_field_name` 뒤)에 클래스+테스트 추가. import에 `model_validator`가 필요하므로 상단 `from pydantic import BaseModel`을 `from pydantic import BaseModel, model_validator`로 교체.
- **추가 코드:**

  ```python
  class _ModelBody(BaseModel):
      a: str | None = None

      @model_validator(mode="after")
      def _need_a(self):
          if self.a is None:
              raise ValueError("a가 필요합니다.")
          return self


  def _model_app() -> TestClient:
      app = FastAPI()
      register_error_handlers(app)

      @app.post("/m")
      def m(body: _ModelBody):  # noqa: D103
          return {"ok": True}

      return TestClient(app)


  def test_model_level_validation_keys_under_body():
      res = _model_app().post("/m", json={})  # model_validator 실패 → loc=("body",)
      assert res.status_code == 400
      details = res.json()["error"]["details"]
      assert "body" in details  # whole-object 에러는 "body" 키로 고정
      assert details["body"]  # 비어있지 않은 메시지
  ```

- **Step 4 기대값 갱신:** `Expected: PASS (2 passed).` → `Expected: PASS (3 passed).`

### Edit C — [MEDIUM 1] Task 7 contract 테스트에 "body" details 키 고정

- **위치:** Task 7 `test_patch_pair_empty_body_is_400`(plan 1092-1097), `assert res.json()["error"]["code"] == "VALIDATION_ERROR"`(1097) 다음 줄에 단언 추가.
- **추가 코드:**
  ```python
      assert "body" in res.json()["error"]["details"]  # model_validator 실패는 "body" 키(계약 고정)
  ```

### Edit D — [MEDIUM 2] Task 6 find_job_detail에 의도적 중복 WHY 주석

- **위치:** Task 6 Step 3-1 `find_job_detail` 코드블록(plan 950-973), `job = dict(job_row)` 직전 줄(line 970 부근).
- **추가 코드(주석 1줄):**
  ```python
          # result_json 파싱은 ocr_repository._parse_job와 동일 관용구 — repo 격리상 의도적 중복(공유 추출 안 함).
  ```

### Edit E — [LOW 3] Task 9에 Task1 핸들러 의존성 메모

- **위치:** Task 9 "Interfaces" 블록(plan 1357-1361)의 마지막 bullet 뒤, 또는 Task 9 서두 문단(1349) 끝에 메모 1줄.
- **추가 텍스트:**
  > 의존: `kind` enum 검증 실패는 path-param `RequestValidationError`로 Task 1 핸들러가 422→400 `VALIDATION_ERROR`(details 키 `"kind"`)로 변환한다 — `test_image_invalid_kind_is_400`이 이에 의존(Task 1 선행 필수).

### Edit F — [LOW 4] Task 7 update_pair 방어코드 WHY 주석

- **위치:** Task 7 Step 3-3 `update_pair` 코드블록(plan 1160-1170), `if not cols:` 줄(1164)에 인라인/직전 주석.
- **추가 코드:**
  ```python
          # 방어: 라우터는 model_validator로 검증된 비어있지 않은 fields만 전달(API 경로로는 도달 불가).
          if not cols:
              return
  ```

### Edit G — [LOW 4] Task 9 \_data_dir 오설정 가드 WHY 주석

- **위치:** Task 9 Step 3-2 `_data_dir` 코드블록(plan 1455-1459), `raise RuntimeError(...)` 직전.
- **추가 코드:**
  ```python
          # 오설정 가드: SJMJ_DATA_DIR 누락 시 명확 실패(운영 전용 — 테스트는 항상 설정).
  ```

### Edit H — [LOW 7] Global Constraints에 앵커 우선 메모

- **위치:** "Global Constraints" 섹션(plan 11-25) bullet 목록 끝(line 25 `Pydantic v2...` 뒤)에 bullet 추가.
- **추가 텍스트:**
  > - **라인 번호는 참고용:** plan의 라인 번호는 ±1 드리프트할 수 있다. 삽입/수정 위치는 함수명·주석·기존 코드 문자열 등 **앵커**로 잡는다(예: "`_unhandled_handler` 아래", "`list_jobs` 다음").

---

## 적용 후 검증 포인트 (plan-editor 반영 뒤 구현 단계에서 확인)

- Edit B/C 반영 후 Task1·Task7 테스트가 `"body"` 키를 단언 → 향후 핸들러 회귀 차단.
- Edit A의 규약과 Global Constraints line 15(`details=\{필드:메시지\}`)는 충돌하지 않는다 — "body"는 whole-object 에러의 명시적 예외로 문서에 함께 남는다(불변식 위반 아님, 예외 명문화).
- REJECT 2건(LOW 5/6)·PARTIAL의 거부 항목(MEDIUM2 추출, LOW4 강제테스트)은 plan 무변경 — surgical 유지.

## 메서드 노트

- MEDIUM 1은 추정이 아니라 실제 Pydantic v2 + FastAPI TestClient 실행으로 `loc`을 측정해 확정했다.
- 맹목 수용 회피: MEDIUM 2의 추출 옵션·MEDIUM 1의 invented-key 정규화·LOW 4의 강제 테스트는 코드 근거(2줄 관용구·선례 부재·도달 불가 분기)로 거부했다.
- 맹목 거부 회피: 동일 이슈의 lighter 옵션(주석·테스트 고정·메모)은 비용 대비 추적성 가치가 있어 채택했다.
- plan 파일은 직접 수정하지 않았다(3단계 책임 범위) — 수정안만 산출. 반영은 4단계 plan-editor.
