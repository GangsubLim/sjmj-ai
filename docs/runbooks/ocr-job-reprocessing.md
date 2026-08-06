# 런북 — OCR 잡 재처리 (엔진 개선분을 과거 데이터에 반영)

엔진(warp·크롭·행검출)이 개선된 뒤, 확정된 과거 잡을 현재 엔진으로 다시 판정하고 개선된
크롭을 뱅크에 반영하는 절차. `ocr-bank-update.md`로 이어진다.

**순서가 절차의 핵심이다 — 재검수와 크롭 교체 완료 확인이 재임베딩보다 먼저다.**

## 0. 전제

- 재처리는 되돌릴 수 없다. 초안·크롭·좌표가 모두 덮인다(ADR 0010).
  복구는 옛 산출물 복원이 아니라 **엔진을 되돌려 다시 재처리하는 방향**이며, 실제로 복원해야
  하는 것은 오염이 학습에 닿는 유일한 통로인 뱅크뿐이다.
- 확정된 거래명세서(invoice)는 재처리가 건드리지 않는다.
- **재처리 이전부터 그 쌍이 이미 `blank_crop` 등으로 배제·검수 완료돼 있었더라도**, 승계에
  실패하면 `exclusion_reason`이 `relink_failed`로 갈아치워지고 `reviewed_at`이 NULL이 되어
  검수 큐에 다시 뜬다. **이것은 의도된 동작이다** — 재처리가 크롭을 통째 교체하므로 옛 사유가
  가리키던 그림은 이미 없다. 버그로 오인하지 않는다.
- 백엔드는 PATCH·검수완료 양쪽에서 `job_token`을 **필수**로 요구한다(spec §12). **이 기능은
  백엔드·프론트가 함께 배포돼야 한다** — 프론트 없이 백엔드만 나가면 검수 화면의 모든 쓰기가
  400이 된다.
- macmini에서 `uv run`/`uv sync`를 **절대 실행하지 않는다** — worker venv가 파괴된다.
  `"$PYTHON_BIN" -m tools.bank_update` 관용구를 쓴다(`~/.sjmj-ai/ml-worker.env`).
- **실행 위치는 macmini** — 대상 DB·크롭 원본·뱅크가 전부 거기 있다(ADR 0001). 아래 모든
  단계(SQL 조회 포함)는 다음 세션 안에서 실행한다.

```bash
ssh macmini
cd ~/sjmj-ai/apps/invoice-ocr/ml
set -a; source ~/.sjmj-ai/ml-worker.env; set +a   # SJMJ_ML_MODELS_DIR·SJMJ_DATA_DIR·PYTHON_BIN·DB_*
```

## 1. 배치 전 백업

```bash
~/sjmj-ai/scripts/backup-db.sh               # 0단계에서 cd한 ml/ 밑에 scripts/는 없다 — 레포 루트를 홈 기준으로 명시
ls -l "$SJMJ_ML_MODELS_DIR"/bank*.npz*       # 현재 뱅크와 백업 존재 확인(백업은 bank.<stamp>.npz.bak)
```

검수자가 화면을 열어두지 않은 시간대를 고른다. 낙관적 잠금이 오염은 막지만(spec §12),
사람이 409를 만나면 편집이 버려진다.

또한 **워커가 승계 계획을 세운 뒤 커밋하기 전에** 사람이 같은 잡의 **거래명세서를
확정**(`POST /api/ocr/jobs/{id}/confirm`)하면 `crop_ref` UNIQUE 충돌로 **그 잡의 재처리가
실패한다**(데이터 오염은 없고 잡만 실패). 검수 완료(`POST /api/curation/jobs/{id}/review`)는
`reviewed_at`만 갱신하고 `crop_ref`를 쓰지 않으므로 이 충돌을 일으키지 않는다 — 배치
재처리 중 잠가야 할 화면은 **검수 화면이 아니라 전표 확정(confirm) 화면**이다.

## 2. 대상 선정

**확정된 잡 전량.** 선별 기준을 만들면 그 기준 자체가 판단이고, 부분 집합으로 재면
before/after 분모가 갈린다.

확정 증거는 셋이다 — `invoice_id`는 명세서 삭제로 FK가 `ON DELETE SET NULL` 되면 풀리므로
그것만 보면 과거 확정 잡이 조용히 빠진다. 아래 WHERE는 백엔드
`app/repositories/ocr_repository.py`의 `_UNCONFIRMED_WHERE`(미확정 판정)의 **부정**이며,
`ml/tools/curation_enrich.py`의 확정 잡 모집단과 같은 술어다. 세 곳이 갈라지면 재처리
대상과 리포트 분모가 어긋난다 — 한 곳을 고치면 나머지도 함께 고친다.

```sql
SELECT id FROM ocr_jobs j
WHERE j.status = 'done'
  AND (
    j.invoice_id IS NOT NULL
    OR EXISTS (SELECT 1 FROM ocr_corrections c WHERE c.job_id = j.id)
    OR EXISTS (SELECT 1 FROM training_pairs tp WHERE tp.job_id = j.id)
  )
ORDER BY id;
```

## 3. 재처리 실행

잡 id 목록으로 엔드포인트를 반복 호출한다(배치 전용 엔드포인트는 없다 — 부분 실패 시
어디까지 걸렸는지가 오히려 명확하다).

```bash
for id in 12 13 14; do
  curl -s -X POST "http://127.0.0.1:8400/api/curation/jobs/$id/reprocess" | jq -c .
done
```

- `409 CONFLICT` = 그 잡이 `done` 상태가 아니다(이미 재처리 큐에 있거나 실패 상태). 건너뛴다.
- 워커는 신규 업로드를 먼저 집는다(spec §2) — 사무실 업로드가 밀리지 않는다.
- 워커 로그는 진행 상황판이 아니라 **예외 신호**다. `[warp-gate]`는 warp 게이트가
  구제(`rescued-by-enh`)되거나 강등(`demoted`)되거나 격자를 못 찾았을 때만(`quad_missing`)
  찍힌다 — 게이트를 정상 통과한 잡은 어떤 줄도 남기지 않는다. **정상 배치는 조용한 것이
  맞다.** 빈 화면을 "멈췄다"로 오독하지 않는다. `[warp-gate]`는 stdout(`ml-worker.out.log`)
  으로, 부팅 지문과 크롭 교체 실패는 `file=sys.stderr`로 명시돼 stderr(`ml-worker.err.log`)
  로 간다 — 창구가 하나가 아니므로 둘 다 본다:

  ```bash
  tail -f ~/.sjmj-ai/logs/ml-worker.out.log ~/.sjmj-ai/logs/ml-worker.err.log
  ```

  (`SJMJ_LOG_DIR`로 재정의하면 경로가 달라진다.) 잡이 **끝났는지**는 로그가 아니라
  `SELECT status FROM ocr_jobs WHERE id=...`로 확인한다.

## 4. 미결 확인 (재임베딩의 선행 조건)

승계에 실패한 미결 쌍이 나온 잡만 게이트가 해제돼 검수 큐에 뜬다(ADR 0011).
미결 쌍은 `relink_failed` 배지와 "그림 없음"으로 표시되고 목록 뒤에 모인다.

```sql
SELECT job_id, COUNT(*) FROM training_pairs
WHERE exclusion_reason = 'relink_failed' GROUP BY job_id ORDER BY job_id;
```

**이 잡들의 재검수를 끝내기 전에는 다음 단계로 넘어가지 않는다** — `--reembed-job`이 거부한다.

## 5. 크롭 교체 완료 확인 (재임베딩의 두 번째 선행 조건)

워커는 크롭을 `job-N.tmp/`에 쓰고 DB 커밋이 성공한 뒤에만 `job-N/`으로 교체한다. 그 사이에
프로세스가 죽으면 **DB는 새 좌표인데 파일은 옛 그림**인 상태가 `done`인 채 남고 재큐잉조차
없다 — 옛 PNG가 제자리에 있어 크롭 존재 검사를 통과하므로, 그대로 재임베딩하면 옛 그림이
새 라벨로 뱅크에 정식 등록된다. 잔여 디렉터리(`job-N.tmp` / `job-N.old`, 크롭 루트의 형제
디렉터리)가 그 상태의 유일한 신호다.

```bash
ls -d "$SJMJ_DATA_DIR"/ocr_crops/job-*.tmp "$SJMJ_DATA_DIR"/ocr_crops/job-*.old 2>/dev/null
```

- 아무것도 안 나오면 정상이다.
- 나오면 **그 잡을 다시 재처리해서**(3단계) 교체를 끝낸다. 재처리는 멱등이라 같은 사진·같은
  엔진이면 좌표가 제자리에 남는다. 워커가 처리 중일 때도 `.tmp`가 보이므로, 먼저 잡 상태를
  확인한다(`SELECT status FROM ocr_jobs WHERE id=...` — `running`이면 처리 중, `done`이면
  아래 잔여 판정으로 넘어간다). 정상 처리 중인 잡은 로그를 남기지 않으므로 로그로는
  판단하지 않는다(3단계 참고).
- **잔여는 "교체 절차가 완주했다고 보장할 수 없다"는 신호일 뿐이다.** `job-N`의 내용이 DB와
  정합한지는 잔여 마커만으로 판정할 수 없다 — `.old` 삭제 단계만 실패한 경우 `job-N`이 이미
  새 그림이고 DB와 완전히 정합한데도 `.old`가 남는다. 그래서 이 확인은 **거부(재임베딩
  보류)만 하고, `.old`를 `job-N`으로 되돌리는 등의 파일 복구를 절대 시도하지 않는다** —
  복구는 `job-N`이 정직하게 비어 있는(404) 상태를 "새 좌표 + 그럴싸한 옛 그림"이라는 금지된
  오염 상태로 되돌린다.
- 잔여를 남긴 잡의 검수 화면은 크롭이 404로 뜬다(옛 그림을 새 좌표에 되돌리지 않는다 —
  그럴싸한 옛 그림이 사람 눈을 통과하는 것보다 그림 없음이 정직하다).
- 이 확인을 건너뛰어도 `--reembed-job`이 같은 검사를 하고 거부한다. 여기서 먼저 보는 이유는
  배치 도중이 아니라 시작 전에 알기 위해서다.
- **추론 도중(커밋 전)에 프로세스가 죽으면 잡은 `running`에서 멈춘다** — `pending → running`
  전이만 있고(`worker/db.py`의 `claim_next_pending`), 죽은 `running` 잡을 되돌리는 워치독은
  없다. `.tmp`도 함께 남는다. 이 잡은 `done`이 아니므로 3단계의 `reprocess` 호출이 409로
  거부돼 그대로는 3단계로 돌아갈 수 없다.
  **"처리 중이 아님"을 로그로 확인하지 않는다** — 정상 처리 중인 잡은 `[warp-gate]` 같은
  예외 신호가 없는 한 어떤 줄도 남기지 않으므로(3단계 참고), 로그가 비어 있다는 것이
  "처리 중이 아니다"의 증거가 되지 못한다. 지금 처리 중인 잡일수록 오히려 로그에 안
  보이는 게 정상이라, 로그로 판단하면 처리 중인 잡을 "아니다"로 오판해 `commit_job`이
  방금 넣은 재처리 요청을 `done`으로 조용히 덮어써 버릴 수 있다(`worker/db.py`).
  대신 **워커를 먼저 재시작해 처리 중을 강제로 끊는다.** 재시작 순간 in-flight 추론은
  전부 중단되고, `claim_next_pending`은 `status='pending'`인 잡만 다시 집으므로
  `running`으로 굳은 잡은 재시작 후에도 재시도되지 않는다 — 이 시점부터 그 잡이 "처리
  중이 아님"이 보장된다. 순서를 지켜 진행한다:

  ```bash
  launchctl kickstart -k gui/$(id -u)/ai.sjmj.ml-worker   # 1) in-flight 추론을 강제로 끊는다
  ```

  ```sql
  UPDATE ocr_jobs SET status='done' WHERE id=<job_id>;    -- 2) running을 되돌린다
  ```

  이어서 3단계로 그 잡의 재처리를 다시 넣는다. 남은 `.tmp`는 워커가 그 잡을 다시 집을 때
  `process_one_job`이 "앞선 실패가 남긴 잔여"로 간주해 먼저 지운다(`worker/poll.py`) —
  따로 지울 필요는 없다.

## 6. 뱅크 재임베딩

`--reembed-job`에는 **2단계에서 재처리한 잡 전량**을 넣는다(아래 예시의 `12 13 14`는 지면상
축약이다). 누락된 잡은 좌표가 그대로라 diff에서 `unchanged`로 분류되고, `force_replace`는
지정한 잡만 승격하므로 — 크롭 PNG는 새 그림인데 뱅크 임베딩은 옛 그림인 채로 조용히 남는다.

```bash
"$PYTHON_BIN" -m tools.bank_update plan --reembed-job 12 13 14
```

- `remove 건수 = 좌표 이동 수 + 미결 수`를 대조한다. 재처리 이후엔 정상 상태에서도 remove가
  뜨므로 `--yes`에 무뎌지지 않게 하는 절차다.
- 미검수 잡을 지정하면 CLI가 잡 id를 담아 즉시 실패한다(§11-1). 4단계로 돌아간다.
- 크롭 교체가 끝나지 않은 잡을 지정하면 잔여 디렉터리 이름을 담아 실패한다. 5단계로 돌아간다.

```bash
"$PYTHON_BIN" -m tools.bank_update apply --plan results/bank_update/plan.jsonl --yes
```

## 7. 워커 재시작

워커는 기동 시 1회만 뱅크를 적재한다.

```bash
launchctl kickstart -k gui/$(id -u)/ai.sjmj.ml-worker
```

## 8. 오염 발견 시

```bash
cp "$SJMJ_ML_MODELS_DIR"/bank.<stamp>.npz.bak "$SJMJ_ML_MODELS_DIR"/bank.npz
launchctl kickstart -k gui/$(id -u)/ai.sjmj.ml-worker
```

## 9. 잡 단위 롤백은 지원하지 않는다

원본 사진은 보존되므로 복구는 엔진을 되돌려 다시 재처리하는 방향이다. 재처리는 멱등이라
같은 사진·같은 엔진이면 매칭이 항등이 되어 좌표가 제자리에 남는다.

## 실패 시나리오 대응

| 증상                                             | 원인·대응                                                                                                                              |
| ------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------- |
| 재처리 후에도 잡이 `done`이고 초안이 그대로      | 재추론 실패 → done 롤백(정상 동작). 워커 로그의 `[warp-gate]` 라인 확인                                                                |
| 그 잡의 쌍이 전부 미결                           | warp 실패로 행이 0개 검출됨. 원본 사진 품질 확인 후 재시도                                                                             |
| 검수 화면에서 PATCH가 409                        | 그 사이 재처리가 지나갔다. 새로고침 후 다시 편집                                                                                       |
| 검수 완료 버튼이 409                             | 재처리가 게이트를 다시 열었다. 새로고침해 새 미결 쌍을 확인한 뒤 다시 완료 처리                                                        |
| 이미 검수 완료된 쌍이 재검수 큐에 다시 뜸        | 승계 실패로 사유가 `relink_failed`로 갈아치워졌다(0. 전제 참조) — 버그가 아니라 의도된 동작                                            |
| 워커가 같은 잡을 반복 처리                       | 크롭 디렉터리 교체 실패(권한·디스크). 로그 `크롭 교체 실패` 확인                                                                       |
| 배치 재처리 도중 특정 잡만 실패                  | 그 사이 사람이 같은 잡의 거래명세서를 확정했다(1단계의 TOCTOU). 해당 잡만 다시 재처리                                                  |
| 검수 화면 크롭이 전부 404이고 `.old`가 남아 있음 | 커밋 후 교체가 끝나지 않았다. 그 잡을 다시 재처리한다(5단계) — 옛 그림은 복원하지 않는다                                               |
| `--reembed-job`이 교체 미완을 이유로 거부        | 5단계를 건너뛴 것이다. 잔여를 남긴 잡을 재처리한 뒤 다시 실행                                                                          |
| 잡이 `running`에서 멈춘 채 진행이 없음           | 워커가 추론 도중 죽었다(워치독 없음). 워커를 재시작해 처리 중을 강제로 끊은 뒤 상태를 `done`으로 되돌리고 3단계부터 재시도(5단계 참조) |
