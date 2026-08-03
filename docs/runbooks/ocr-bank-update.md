# 뱅크 증분 갱신 (bank.npz)

큐레이션에서 검수 완료된 학습쌍의 품목 크롭을 운영 뱅크(`bank.npz`)에 증분 반영하는 절차.
`out_of_bank`(뱅크에 정답 라벨이 없어 retrieval이 구조적으로 못 맞추는 케이스)를 해소한다.

도구: `apps/invoice-ocr/ml/tools/bank_update.py` (`plan` / `apply` / `score`).
**실행 위치는 macmini** — 모델(`ft_prod.pt`)·뱅크·크롭 원본이 전부 거기 있다(ADR 0001).
병합은 append가 아니라 desired 상태로 수렴하는 **멱등 sync**다. 같은 입력으로 재실행하면
diff가 공집합이 되어 아무것도 바뀌지 않는다.

## 0. 선행 조건 — 큐레이션 검수 완료

ADR 0004 게이트에 따라 **`ocr_jobs.curation_reviewed=TRUE`인 잡의 `status='included'` 쌍만**
대상이 된다. `training_pairs.status` 기본값이 `included`라, 검수 완료 처리를 하지 않으면
미검수 쌍이 전부 통과해 버린다. 반영 전에 큐레이션 페이지에서 대상 잡을 "검수 완료"로 표시한다
(API: `POST /api/curation/jobs/{job_id}/review`).

> [!IMPORTANT]
> **이 절은 `BLANK_INK_MAX`가 확정되기 전까지 비활성이다.** 미확정 상태에서 `apply`는
> DB를 건드리기 전에 `RuntimeError`로 즉시 멈춘다(의도된 fail-fast). 임계 확정 전에는
> 이 절을 건너뛰고 기존 절차대로 진행한다 — 빈 크롭은 그때까지 **사람이 큐레이션에서
> 직접 제외**한다. 확정 절차는 Issue #38의 캘리브레이션 PR에 있다.

### 0-1. 빈 크롭 자동 배제 반영 (bank_update 앞 단계)

빈 크롭(품목 크롭에 손글씨 획이 사실상 없는 행)이 학습쌍에 섞이면 그대로 뱅크 오염원이 된다.
뱅크 갱신 **전에** 자동 배제를 반영한다(ADR 0006 · Issue #38).

```bash
# 로컬 개발 머신에서 (apps/invoice-ocr/ml)
uv run python -m tools.blank_crop_report fetch             # training_pairs + 품목 크롭 동기화
uv run python -m tools.blank_crop_report report             # 잉크율 분포·판정 확인 (눈으로 본다)
uv run python -m tools.blank_crop_report apply --dry-run    # 계획만 확인 (ssh 없이 종료) — 첫 실전 회차 전 필수
uv run python -m tools.blank_crop_report apply              # 운영 DB 반영 (미검수 잡만)
```

- **첫 실전 회차 전에는 반드시 `apply --dry-run`으로 계획(대상·보호·불변·변경 예정·보류
  건수)을 먼저 확인한다.** `--dry-run`은 ssh로 아무것도 쏘지 않고 계획 요약만 출력하고 끝낸다.
- `apply`는 **캐시를 만든 호스트와 쓰기 대상(`--host`) 호스트가 다르면 즉시 거부**한다 —
  조건부 UPDATE의 WHERE에 실리는 seen 상태는 fetch한 DB에서 본 값이라, 다른 DB에서 본 근거로
  운영 행을 뒤집을 수 있기 때문이다. `--host`를 바꿨다면 `fetch`부터 다시 실행한다.
- `apply`는 **보류가 1건이라도 있으면 비-0으로 종료**한다. 보류(`crop_missing`/`crop_unreadable`)를
  해소(크롭 재생성 또는 사람이 배제)하지 않으면 이 단계를 넘어갈 수 없다. 의도적으로 무시하려면
  `--allow-holds`.
- 로직이 개선돼 과거 잡을 전수 재판정하려면 `apply --recheck-reviewed`(검수 완료 잡까지
  대상에 포함해 재판정). 사람 판정은 §불변식(사유가 비어 있으면 사람 소유)이 보호한다.
- `apply`로 판정이 바뀐 쌍은 `reviewed_at`이 지워지고 그 잡의 검수 표식이 풀린다 —
  **큐레이션 화면에서 재검수한 뒤** 이 런북의 나머지 단계로 넘어간다.
- `--cache`가 기존 디렉터리인데 이 도구의 산출물(`pairs.json`/`meta.json`/`crops`/
  `blank_crop_report.md`)이 하나도 없으면 거부된다 — 남의 디렉터리를 캐시로 잘못 지정하는
  사고를 막는다.
- `fetch`는 시작하자마자 캐시 매니페스트를 무효화한다 — 중단되면 `report`/`apply`가 그 캐시로
  돌 수 없다. 재-`fetch`로 복구한다.
- 쌍 수가 많을 때(약 1,200쌍 초과) `apply`는 ssh argv 상한(macOS `ARG_MAX`)에 걸려
  fail-fast하며, 청크 분할 대신 stdin 경유 전환이 필요하다는 안내 메시지가 나온다.
- 선행: 운영 DB에 `db/migration_009_training_pairs_exclusion_reason.sql`이 적용돼 있어야 한다.

> [!WARNING]
> **macmini에서 `uv run` / `uv sync`를 절대 실행하지 않는다.**
> `~/sjmj-ai/apps/invoice-ocr/ml/.venv`는 **운영 ml-worker가 쓰는 바로 그 venv**다
> (`deploy/env/ml-worker.env.example`의 `PYTHON_BIN`, `scripts/run-ml-worker.sh`).
> `uv run`은 lock에 있는 패키지를 lock 버전으로 강제 교체하고(numpy가 dev 그룹에 있다),
> `uv sync`는 lock에 없는 패키지(torch·cv2·mlx — 전부 수동 설치분)를 **삭제**한다.
> 둘 다 워커를 죽이며, 이 런북의 롤백(7단계)은 `bank.npz`만 되돌리므로 복구되지 않는다.
> macmini에서는 항상 `"$PYTHON_BIN" -m ...`으로 실행한다
> (CD도 같은 이유로 ml venv를 sync하지 않는다 — `.github/workflows/deploy.yml`의 Restart ml-worker).

> [!WARNING]
> **`apply`를 동시에 두 번 실행하지 않는다.** `apply`는 운영 뱅크(`bank.npz`)를 고치는
> 유일한 경로지만 락이 없다. 같은 뱅크 파일에 대해 두 `apply`가 겹치면 백업 파일명 충돌
> (같은 초일 때) 또는 서로의 tmp 파일 rename을 밟는 레이스가 날 수 있다. 한 번에 한 사람만,
> 순차로 실행한다.

> [!WARNING]
> **신뢰 경계 밖에서 받은 `.npz` 파일을 이 도구에 넣지 않는다.** `load_bank`는
> `np.load(path, allow_pickle=True)`로 읽는다 — pickle은 임의 코드 실행이 가능한 포맷이라,
> 출처를 모르는 npz를 `--before`/`--after`/뱅크 경로에 넘기면 임의 코드가 실행될 수 있다.
> 항상 이 절차로 만든 백업(`bank.<stamp>.npz.bak`)이나 운영 뱅크(`bank.npz`) 자신만 사용한다.

## 1. plan — 무엇이 바뀌는지 먼저 본다

macmini에서:

```bash
ssh macmini
cd ~/sjmj-ai/apps/invoice-ocr/ml
set -a; source ~/.sjmj-ai/ml-worker.env; set +a   # SJMJ_ML_MODELS_DIR · SJMJ_DATA_DIR
"$PYTHON_BIN" -m tools.bank_update plan
```

출력: 추가/교체/제거/불변 건수 + 제외 목록(crop_ref 형식 불량·빈 canonical_label) +
보류 목록(크롭 파일 누락 — 추가·교체만 보류, 기존 뱅크 항목은 유지).
산출물은 `results/bank_update/plan.jsonl`(로컬 전용, gitignore).
DB 접속값은 기본적으로 `~/.sjmj-ai/backend.env`(`--backend-env`로 재지정 가능)에서 읽는다.

## 2. 크롭 품질 검수 (선택이지만 권장)

큐레이션 UI 썸네일은 `object-cover`로 크롭 가로 일부만 보여주므로, 검수자가 크롭 품질까지
확인했다고 볼 수 없다. **로컬 개발 머신**에서 크롭을 회수해 눈으로 확인한다.

```bash
# ↓ 여기부터는 macmini가 아니라 로컬 개발 머신에서 실행한다(uv 사용 가능)
cd apps/invoice-ocr/ml
uv run python -m tools.curation_report fetch
uv run python -m tools.curation_report pull-images --jobs <검수 완료 잡 id...>
open results/curation/images_index.md   # ref → 파일 → 라벨 인덱스
```

품질 탈락 크롭은 **큐레이션 UI(또는 `PATCH /api/curation/pairs/{id}` with `{"status":"excluded"}`)로
영속 처리**한다. 별도 제외 파일은 두지 않는다 — DB가 유일한 SSoT이고, 일회성 파일은 다음
실행에서 멱등성을 깨뜨린다. 처리 후 macmini에서 `plan`을 재실행해 diff에서 빠졌는지 확인한다.

## 3. apply — 뱅크 sync (백업 자동)

```bash
"$PYTHON_BIN" -m tools.bank_update apply --plan results/bank_update/plan.jsonl
```

- **`plan` 산출 후 DB(큐레이션 검수·제외 상태)를 건드렸다면 `apply` 전에 `plan`을 다시 실행한다.** `plan.jsonl`은 실행 시점의 스냅샷이며 `apply`는 그 파일만 신뢰한다.
- **제거가 포함된 plan은 `--yes` 없이는 거부된다.** 되돌릴 수 없는 삭제이기 때문이다. 붙이기 전에
  1단계 `plan` 출력의 제거 건수·대상이 의도한 것인지 확인한다 — 잘못된 `--backend-env`로 만든 plan은
  검수 완료 잡이 0건으로 조회돼 **뱅크의 crop_ref 항목 전량이 제거 대상**으로 나온다.

  ```bash
  "$PYTHON_BIN" -m tools.bank_update apply --plan results/bank_update/plan.jsonl --yes
  ```

- 임베딩은 운영 추론과 동일 경로(`square` → `EVAL_TF` → `ItemEncoder` projection, CPU).
- 임베딩 성공을 확인한 뒤 `$SJMJ_ML_MODELS_DIR/bank.{YYYYMMDD-HHMMSS}.npz.bak` 백업을 만든다(백업 실패 시 중단).
- 저장 전 정합 검증(4배열 길이·`emb` 128차원·NaN/inf 없음)을 통과해야 하고, tmp 파일에 쓴 뒤
  rename한다(부분 쓰기 방지).
- 출력의 백업 경로를 **기록해 둔다** — 4단계 채점과 7단계 롤백에 쓴다.

## 4. score — 갱신 전/후 비교

```bash
"$PYTHON_BIN" -m tools.bank_update score \
  --before "$SJMJ_ML_MODELS_DIR/bank.<stamp>.npz.bak" \
  --after  "$SJMJ_ML_MODELS_DIR/bank.npz" \
  --scope all      # 생략 시 reviewed(검수 완료만)
```

`--scope`는 **채점 모집단**이다(위 제외 축 `AXES`와 다른 축이다).

- `reviewed`(기본) — 검수 완료 잡의 `included` 쌍만. 뱅크 갱신 판단용 기존 기준이다.
- `all` — 검수 여부와 무관하게 크롭이 있고 `canonical_label`이 있는 `included` 쌍 전부.
  큐레이션 리포트의 era-aware 재평가(Issue #49)가 소비하는 산출물을 만들 때 쓴다. 검수 전
  잡이 쌓일수록 증분이 커진다(2026-07-30 실측 증분 5쌍).

**`--scope`는 `score`에만 있다.** `plan`의 모집단은 `reviewed` 고정이며 이는 ADR 0004 검수
게이트다 — `plan`이 미검수 쌍을 뱅크 갱신 대상으로 삼으면 되돌릴 수 없는 뱅크 오염이 된다.

산출물에 `score_meta.json`이 추가된다 — before/after retrieval 지문 · 산출 시각 · scope ·
`axes` · 표본 수 · `score.jsonl`의 sha256. **큐레이션 리포트는 이 파일을 재평가 유효성의 단일
게이트로 쓴다**(없으면 `score.jsonl`이 있어도 재평가 없음으로 취급). 쓰기 순서는 jsonl →
meta이고 둘 다 원자 교체이므로, 중단된 재실행이 남긴 반쪽 산출물은 다이제스트·레코드 수
불일치로 걸러진다. 또한 **릴리스 배포 후에는 이 단계를 다시 돌린다** — 지문에 배포 코드
SHA가 들어가므로 배포가 기존 재평가를 stale로 만든다.

> [!NOTE]
> **재평가 도구를 고쳤다면 머지만으로는 이 단계에 반영되지 않는다.** 서버 레포는 `v*` 태그로
> checkout된 detached HEAD이므로 순서는 **머지 → 릴리스 태그 → 배포 → macmini
> 재평가(`score --scope all`) → 로컬 `fetch`/`report`**다. 러너 워크스페이스를 수동 checkout해
> 앞당기지 않는다(다음 배포와 충돌한다). 이 배포 순서는 여기가 정본이며
> `docs/runbooks/ocr-curation-analysis.md`에 중복 서술하지 않는다.

`score.md`는 **제외 축별로 표 2개**를 낸다. 축은 "채점할 때 뱅크에서 무엇을 빼는가"다.
같은 쿼리 쌍을 축만 바꿔 채점하므로 표본 수(`n`)는 두 축이 같다 — 축마다 달라지는 것은
제외 후 후보로 남는 뱅크 항목이며, 그 여파가 아래 `peer_n` 분모에 드러난다.

- **`crop_ref` 축** — 쿼리 자신(동일 `crop_ref`)만 뺀다. 이전 릴리스까지의 기준이라 과거
  리포트와 직접 비교할 수 있다. 같은 전표(같은 사진·같은 필기 세션)의 다른 크롭이 뱅크에
  남으므로, 그 크롭이 답을 알려주는 만큼 수치가 낙관적이다.
- **`invoice` 축** — 뱅크 `inv` 열이 쿼리와 같은 항목 전체를 뺀다. 학습 쪽 평가 기준
  (`apps/invoice-ocr/ml/handwriting/train_contrastive.py`의 전표 단위 hold-out)과 같은 축이며,
  새 전표를 처음 볼 때의 실제 성능에 가깝다. 단 `inv` 값은 네임스페이스가 둘이다 —
  부트스트랩은 `2025-08-18_inv011.jpg`, 큐레이션은 `job-42`. 큐레이션 쿼리의 `inv`는
  부트스트랩 항목과 절대 일치하지 않으므로 **같은 종이 전표를 다시 촬영해 잡으로 올린
  경우의 누수는 이 축으로도 남는다**(받아들이는 전제). 즉 이 축은 누수 0의 보장이 아니라
  동일 잡 누수를 제거한 값이다.

두 축의 차이가 곧 **동일 전표 누수의 크기**다. 차이가 크면 뱅크가 전표 중복에 기대고 있다는
신호이므로 개선 판단은 `invoice` 축으로 한다. 이 축간 비교는 분모가 `n`으로 두 축이 동일한
아래 **제외 후 top-1/top-5** 행에서 읽는다 — peer 존재 한정 행은 분모(`peer_n`)가 축마다
달라지므로, 그 행의 두 축 값을 그대로 빼면 누수 크기가 아니라 "누수 + 분모 표본 변화"가
섞인 값이 된다. peer 행은 같은 축 안에서 before↔after를 비교하는 용도로만 쓴다.

각 표 안의 지표는 2종으로 분리해 읽는다.

- **커버리지(제외 무관)** — 정답 라벨이 뱅크에 존재하는가. 제외 축과 무관하게(제외 대상
  항목까지 포함해) 같은 값이며, `out_of_bank` 해소는 **이 지표로** 판단한다.
- **제외 후 top-1/top-5** — 위 축대로 뺀 뒤의 retrieval 정확도. 단일 샘플 라벨은 제외 후
  후보가 0이라 구조적으로 미스다. 그래서 리포트는 "동일 라벨의 다른 크롭이 (제외 후에도)
  존재하는 쌍 한정" 수치(peer 존재 한정 top-1/top-5)를 함께 낸다 — retrieval 개선은 같은 축
  안에서 그 행의 before↔after로 판단한다(peer 행은 축간 비교에 쓰지 않는다, 위 참조). 이
  분모(`peer_n`)는 축마다 달라진다(`invoice` 축이 더 작거나 같다).

산출물: `results/bank_update/score.md`, `score.jsonl`, `score_meta.json`.
`score.jsonl` 레코드의 유일키는 `(side, axis, crop_ref)`다 — `crop_ref`만으로 map을 만들면
축·전후 4벌 중 하나가 조용히 이긴다.

**임계 캘리브레이션(`ITEM_CONF_THRESHOLD`)은 `crop_ref` 축으로 한다.** 현행 0.75가 그 축에서
산정됐고(2026-07-28, 35쌍), 축을 바꾸면 과거 분포와 비교가 끊긴다. 과거 리포트·주석의
**`leave-self-out`은 이 `crop_ref` 축의 `제외 후`와 같은 것**이다(#53에서 표현만 중립화). `invoice`
축은 더 엄격한 수치이므로 임계를 다시 산정할 때만 근거를 명시하고 바꾼다.
`score.jsonl`을 직접 소비하는 스니펫을 작성할 때는 `side`뿐 아니라 **`axis`도 반드시 필터**해야
한다 — 안 하면 표본이 2배가 되고 서로 다른 채점규칙이 한 분포에 섞인다.

## 5. ml-worker 재시작 (반영 시점)

워커는 시작 시에만 뱅크를 적재하므로 재시작해야 반영된다.

```bash
launchctl kickstart -k gui/$(id -u)/ai.sjmj.ml-worker
tail -n 40 ~/.sjmj-ai/logs/ml-worker.err.log  # SJMJ_LOG_DIR로 재정의하면 경로가 달라진다
```

## 6. 헬스 확인

신규 사진 1건을 업로드해 잡이 `done`으로 끝나고 `item_top5`에 새 라벨이 등장하는지 본다
(큐레이션 페이지의 해당 잡 드릴다운으로 확인).

## 7. 롤백

```bash
cp "$SJMJ_ML_MODELS_DIR/bank.<stamp>.npz.bak" "$SJMJ_ML_MODELS_DIR/bank.npz"
launchctl kickstart -k gui/$(id -u)/ai.sjmj.ml-worker
```

## 실패 시 참고

| 증상                        | 원인/조치                                                                                                                                                                                                                               |
| --------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `SJMJ_ML_MODELS_DIR 미설정` | `set -a; source ~/.sjmj-ai/ml-worker.env; set +a`를 먼저 실행                                                                                                                                                                           |
| `뱅크 npz 키 구조 불일치`   | 대상 파일이 운영 뱅크가 아님(`emb/lab/inv/keys` 4배열 필요) — 경로 확인                                                                                                                                                                 |
| `plan`은 나오는데 대상 0건  | 잡이 검수 완료(0단계)되지 않았을 가능성이 가장 높다                                                                                                                                                                                     |
| `보류 ... missing_crop`     | 크롭 PNG가 지워졌거나 재처리로 경로가 어긋남 — 해당 잡 재처리 여부 확인(추가·교체만 보류되며 기존 뱅크 항목은 유지된다)                                                                                                                 |
| `--yes` 없이 거부           | plan에 제거가 있다 — 대상이 의도한 것인지 확인 후 `--yes` 추가. 예상 밖의 대량 제거면 `plan`을 올바른 `--backend-env`로 재산출한다                                                                                                      |
| `임베딩 shape 불일치`       | 크롭 임베딩 결과 shape가 기대와 다름 — 뱅크는 쓰이지 않았으므로 원인 해결 후 재실행. 백업은 임베딩 성공 확인 후에만 만들어지므로 이 실패에서는 `.npz.bak`도 생성되지 않는다(고아 `.bak`은 저장 직전 정합 검증 실패 시에만 남을 수 있다) |

## 참고

- 설계 근거: 이슈 [#17](https://github.com/GangsubLim/sjmj-ai/issues/17)
- 선행 분석 절차: `docs/runbooks/ocr-curation-analysis.md`
- 관련 ADR: `docs/adr/0001-ml-model-artifacts-live-and-train-on-macmini.md`,
  `docs/adr/0004-curation-gate-and-training-pairs-read-model.md`
