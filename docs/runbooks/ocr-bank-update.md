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

출력: 추가/교체/제거/불변 건수 + 제외 목록(크롭 파일 누락·빈 canonical_label).
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
- 실행 전 `$SJMJ_ML_MODELS_DIR/bank.{YYYYMMDD-HHMMSS}.npz.bak` 백업을 만든다(백업 실패 시 중단).
- 임베딩은 운영 추론과 동일 경로(`square` → `EVAL_TF` → `ItemEncoder` projection, CPU).
- 저장 전 정합 검증(4배열 길이·`emb` 128차원·NaN/inf 없음)을 통과해야 하고, tmp 파일에 쓴 뒤
  rename한다(부분 쓰기 방지).
- 출력의 백업 경로를 **기록해 둔다** — 4단계 채점과 7단계 롤백에 쓴다.

## 4. score — 갱신 전/후 비교

```bash
"$PYTHON_BIN" -m tools.bank_update score \
  --before "$SJMJ_ML_MODELS_DIR/bank.<stamp>.npz.bak" \
  --after  "$SJMJ_ML_MODELS_DIR/bank.npz"
```

지표는 2종으로 분리해 읽는다.

- **커버리지(self 포함)** — 정답 라벨이 뱅크에 존재하는가. `out_of_bank` 해소는 **이 지표로**
  판단한다.
- **leave-self-out top-1/top-5** — 쿼리 자신(동일 `crop_ref`)만 제외한 retrieval 정확도.
  단일 샘플 라벨은 자기 제외 시 후보가 0이라 구조적으로 미스다. 그래서 리포트는 "동일 라벨의
  다른 크롭이 존재하는 쌍 한정" 수치를 함께 낸다 — retrieval 개선은 **그 행으로** 판단한다.

산출물: `results/bank_update/score.md`, `score.jsonl`.

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

| 증상                        | 원인/조치                                                                                                                                                   |
| --------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `SJMJ_ML_MODELS_DIR 미설정` | `set -a; source ~/.sjmj-ai/ml-worker.env; set +a`를 먼저 실행                                                                                               |
| `뱅크 npz 키 구조 불일치`   | 대상 파일이 운영 뱅크가 아님(`emb/lab/inv/keys` 4배열 필요) — 경로 확인                                                                                     |
| `plan`은 나오는데 대상 0건  | 잡이 검수 완료(0단계)되지 않았을 가능성이 가장 높다                                                                                                         |
| `제외 ... missing_crop`     | 크롭 PNG가 지워졌거나 재처리로 경로가 어긋남 — 해당 잡 재처리 여부 확인                                                                                     |
| `임베딩 개수 불일치`        | 크롭 읽기 실패 — 뱅크는 쓰이지 않았으므로 원인 해결 후 재실행. 백업(`.npz.bak`)은 임베딩보다 먼저 만들어지므로 사용되지 않은 백업 파일이 남을 수 있다(무해) |

## 참고

- 설계 근거: 이슈 [#17](https://github.com/nxnsystems/sjmj-ai/issues/17)
- 선행 분석 절차: `docs/runbooks/ocr-curation-analysis.md`
- 관련 ADR: `docs/adr/0001-ml-model-artifacts-live-and-train-on-macmini.md`,
  `docs/adr/0004-curation-gate-and-training-pairs-read-model.md`
