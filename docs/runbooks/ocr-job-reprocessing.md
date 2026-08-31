# 런북 — OCR 잡 재처리 (엔진 개선분을 과거 데이터에 반영)

엔진(warp·크롭·행검출)이 개선된 뒤, 확정된 과거 잡을 현재 엔진으로 다시 판정하고 개선된
크롭을 뱅크에 반영하는 절차. `ocr-bank-update.md`로 이어진다.

**순서가 절차의 핵심이다 — 재검수와 크롭 교체 완료 확인이 재임베딩보다 먼저다.**

## 0. 전제

- 재처리는 되돌릴 수 없다. 초안·크롭·좌표가 모두 덮인다(ADR 0010).
  복구는 옛 산출물 복원이 아니라 **엔진을 되돌려 다시 재처리하는 방향**이며, 실제로 복원해야
  하는 것은 오염이 학습에 닿는 유일한 통로인 뱅크뿐이다.
- 확정된 거래명세서(invoice)는 재처리가 건드리지 않는다.
- **재처리 이전부터 그 쌍이 이미 `blank_crop` 등 기계 사유로 배제·검수 완료돼 있었더라도**,
  승계에 실패하면 `exclusion_reason`이 `relink_failed`로 갈아치워지고 `reviewed_at`이 NULL이
  되어 검수 큐에 다시 뜬다. **이것은 의도된 동작이다** — 재처리가 크롭을 통째 교체하므로 옛
  사유가 가리키던 그림은 이미 없다. 버그로 오인하지 않는다.
  **사람이 배제한 쌍(사유 NULL)만은 예외로 사유가 그대로 NULL로 남는다** — 아래 항목의
  판별자가 성립하려면 이 표식이 미결 전환에서도 보존돼야 하기 때문이다.
- 반대 방향도 자동이다. **사유가 `relink_failed`로 남아 있는 쌍이 다음 재처리에서 승계에
  성공하면 `included`로 되돌아간다**(`ml/worker/db.py`의 `commit_job` ②단계) — 엔진을 고쳐
  다시 돌리는 것이 곧 데이터 회수가 되도록 한 것이다. 판별자는 사유다: 사람이 배제하면
  사유가 NULL로 지워지므로(ADR 0006 §6) **사람이 배제한 쌍은 승계돼도 배제로 남는다**.
  위 항목과 맞물려 원래 `blank_crop`이던 쌍이 `included`로 돌아올 수 있는데, 크롭이 여전히
  비었으면 빈 크롭 가드가 다음 실행에서 다시 배제한다. `reviewed_at`은 건드리지 않는다 —
  아직 재검수 전인 쌍은 NULL인 채 검수 큐에 남고, 4단계에서 이미 검수 완료를 누른 잡의 쌍은
  (`mark_reviewed`가 그때 미처리 쌍 전량에 `reviewed_at`을 찍으므로) **검수 완료 상태 그대로
  복원된다** — 그 복원은 사람 눈을 다시 거치지 않는다.
- 백엔드는 PATCH·검수완료 양쪽에서 `job_token`을 **필수**로 요구한다(spec §12). 배포 순서는
  신경 쓸 필요가 없다 — CD(`deploy.yml`)는 태그 하나를 체크아웃해 프론트 빌드와 백엔드
  재시작을 한 런에서 하므로 "백엔드만 나간 상태"가 성립하지 않는다(롤백도 양쪽을 함께
  되돌린다). **주의할 것은 배포 시점에 이미 열려 있던 검수 탭이다** — 옛 번들은 토큰을 아예
  보내지 않아 그 탭의 모든 쓰기가 400이 되므로, 배포 후 검수 화면은 새로고침해야 한다.
  macmini에서 백엔드만 손으로 재시작하면 같은 증상이 전면적으로 나타난다.
- macmini에서 `uv run`/`uv sync`를 **절대 실행하지 않는다** — worker venv가 파괴된다.
  `"$PYTHON_BIN" -m tools.bank_update` 관용구를 쓴다(`~/.sjmj-ai/ml-worker.env`).
- **실행 위치는 macmini** — 대상 DB·크롭 원본·뱅크가 전부 거기 있다(ADR 0001). 아래 모든
  단계(SQL 조회 포함)는 다음 세션 안에서 실행한다.

```bash
ssh macmini
export PATH=/opt/homebrew/bin:$PATH              # ssh 비대화형 셸에는 homebrew가 없다 — 아래 참조
cd ~/sjmj-ai/apps/invoice-ocr/ml
set -a; source ~/.sjmj-ai/ml-worker.env; set +a   # SJMJ_ML_MODELS_DIR·SJMJ_DATA_DIR·PYTHON_BIN·DB_*
```

**PATH 보정을 빠뜨리면 1단계 백업이 `mysqldump: command not found`로 실패하고, 이후 모든
SQL 조회 단계도 `mysql`을 찾지 못한다.** `ssh macmini <명령>` 형태의 비대화형 셸은 로그인
셸의 PATH를 상속하지 않아 `/opt/homebrew/bin`이 빠진다 — **되돌릴 수 없는 작업의 유일한
안전망이 첫 명령에서 실패하는 경로이므로 세션을 열 때 가장 먼저 넣는다.**

이 런북의 SQL은 모두 아래 관용구로 실행한다. 비밀번호는 `MYSQL_PWD`로 넘긴다 —
`-p"$DB_PASS"`는 프로세스 목록에 노출되는 데다, 출력을 파일로 리다이렉트하면 프롬프트가
보이지 않는 채 멈춘다(`scripts/backup-db.sh`도 같은 방식이다). 접속값과 대상 DB명은 env에서만
읽는다(하드코딩 금지 — 런타임/백업 DB 발산 방지).

```bash
mysql_q() { MYSQL_PWD="$DB_PASS" mysql -h"$DB_HOST" -P"$DB_PORT" -u"$DB_USER" "$DB_NAME" -e "$1"; }

mysql_q "SELECT COUNT(*) FROM ocr_jobs;"        # 접속 확인
```

**이 기능 배포 후 최초 1회만** 아래를 확인한다. 검수 화면의 크롭·top5 노출은 `crop_ref`가
`job-N/row-M` 형식인지의 정규식 판정에 걸려 있어(`backend/app/services/curation_service.py`의
`_ROW_CROP_REF_RE`), 비표준 형식을 가진 레거시 쌍이 운영 DB에 있으면 그 쌍이 미결로
오분류돼 그림과 후보가 조용히 사라진다. 현재 생성 경로는 항상 표준 형식이므로 **0이 나오는
것이 정상**이고, 확인 비용도 쿼리 한 줄이다. 0이 아니면 그 쌍부터 조사한 뒤 배치를 시작한다.

`_ROW_CROP_REF_RE`를 그대로 부정하면 **미결 쌍까지 함께 걸린다** — 승계 실패 쌍은
`job-N/orphan-{pair_id}` 좌표를 들고 있고(`ml/handwriting/relink.py`의 `orphan_ref`),
그 형식이 곧 "행과 끊어졌다"는 의도된 표식이다. 미결이 하나라도 있는 DB에서는 부정 쿼리가
구조적으로 0이 될 수 없어, 그대로 쓰면 매 회차 정상 미결 건수를 레거시 오염으로 오독하게
된다. 그래서 아래 쿼리는 `orphan-` 네임스페이스를 모수에서 뺀다.

```sql
SELECT COUNT(*) FROM training_pairs
WHERE crop_ref NOT REGEXP '^job-[0-9]+/row-[0-9]+$'
  AND crop_ref NOT REGEXP '^job-[0-9]+/orphan-[0-9]+$';
```

미결 건수 자체를 보려면 4단계의 사유 기준 쿼리를 쓴다 — 사람이 배제한 쌍은 사유가 NULL로
지워지므로(ADR 0006 §6) 좌표 형식과 사유 어느 쪽도 단독으로는 미결 전량을 세지 못한다.

### 창 B 재백필 (`draft_supply` 도입 배포 직후 1회 — Issue #106)

`migration_012`가 CD에서 컬럼과 백필을 함께 적용하지만, **마이그레이션과 백엔드 재시작 사이
수 분 동안 구 백엔드가 계속 확정을 받는다.** 그 사이 만들어진 쌍은 `draft_supply`가 NULL로
남아 다음 재처리에서 ② 앵커를 잃는다. health check 통과(= 신버전 서빙) 확인 후 아래를 1회
실행해 닫는다. 재처리 배치보다 **먼저** 한다.

```bash
mysql_q "SELECT COUNT(*) FROM training_pairs WHERE draft_supply IS NOT NULL;"   # 전
MYSQL_PWD="$DB_PASS" mysql -h"$DB_HOST" -P"$DB_PORT" -u"$DB_USER" "$DB_NAME" \
  < ~/sjmj-ai/db/migration_012_training_pairs_draft_supply.sql
mysql_q "SELECT COUNT(*) FROM training_pairs WHERE draft_supply IS NOT NULL;"   # 후
```

증가분은 창 B에서 확정된 쌍 중 정합·타입·범위 가드를 통과한 것의 상한 근사다 — "전" 측정
시점과 재실행 사이에 신버전 백엔드가 확정한 쌍도 INSERT 시점에 이미 값이 있어 증가분에 섞인다.
파일 전체를 다시 먹여도 안전하다 — 컬럼 추가는
`information_schema` 가드로 `DO 0`이 되고, 백필은 `WHERE draft_supply IS NULL`이라 이미 채워진
값(특히 신버전 백엔드가 직접 쓴 값)을 덮지 않는다. `scripts/migrate-db.sh`는 `schema_migrations`
원장에 012가 있으면 파일을 아예 실행하지 않으므로 **러너로는 이 재실행이 되지 않는다.**

## 1. 배치 전 백업

```bash
~/sjmj-ai/scripts/backup-db.sh               # 0단계에서 cd한 ml/ 밑에 scripts/는 없다 — 레포 루트를 홈 기준으로 명시
ls -l "$SJMJ_ML_MODELS_DIR"/bank*.npz*       # 현재 뱅크와 백업 존재 확인(백업은 bank.<stamp>.npz.bak)
```

**마지막 줄에 `backup ok: <경로> (kept<=10)`이 찍혔는지 눈으로 확인하고 다음 단계로 간다.**
이 출력이 없으면 백업이 없는 것이다 — 재처리는 되돌릴 수 없으므로(0단계) 원인을 해결하기
전에는 2단계로 넘어가지 않는다. `mysqldump: command not found`면 0단계의 PATH 보정을
빠뜨린 것이고, `missing env file`이면 `~/.sjmj-ai/backend.env`가 없는 것이다
(`backup-db.sh`는 ml-worker.env가 아니라 backend.env를 읽는다).

검수자가 화면을 열어두지 않은 시간대를 고른다. 낙관적 잠금이 오염은 막지만(spec §12),
사람이 409를 만나면 편집이 버려진다.

또한 **워커가 승계 계획을 세운 뒤 커밋하기 전에** 사람이 같은 잡의 **거래명세서를
확정**(`POST /api/ocr/jobs/{id}/confirm`)하면 위험이 갈린다 — **갈림길은 그 잡에 학습쌍이
이미 있는가**다.

- **쌍이 있는 잡**: confirm은 `invoice_id`가 비어 있어야 통과하므로
  (`backend/app/services/ocr_service.py`의 `confirm`), 이 갈래는 과거 확정 뒤 명세서 삭제로
  FK가 풀린 잡에서 성립한다. confirm이 넣는 새 쌍의 `crop_ref`가 재처리가 기입하는 최종
  좌표와 겹쳐 UNIQUE 충돌이 나고 **그 잡의 재처리가 실패한다**(데이터 오염은 없고 잡만
  실패). 실패 시나리오 표의 "배치 재처리 도중 특정 잡만 실패"가 이것이다.
- **쌍이 없는 잡**(한 번도 확정된 적 없는 `done` 잡, 그리고 확정됐어도 `crop_ref` 있는 행이
  0개여서 쌍이 안 만들어진 잡 — 6단계의 "쌍 0건" 잡): 재처리가 쓸 `crop_ref`가 없어 충돌이
  성립하지 않는다. 옛 초안을 띄워 둔 확정 화면이 그대로 confirm하면 **새 크롭에 옛 라벨이
  붙은 학습쌍이 조용히 만들어진다** — 이 기능이 막으려는 행 오프셋 그 자체이고, 실패도 뜨지
  않는다. 한 번도 확정된 적 없는 잡은 2단계 술어가 빼지만, **확정 뒤 명세서가 삭제된 쌍 0건
  잡은 `ocr_corrections`가 남아 2단계 대상에 들어온다** — 이 갈래를 막는 것은 아래의 확정
  화면 잠금뿐이다. **대상 목록을 2단계 SQL로만 뽑고 임의로 잡 id를 더하지 않는 이유가
  이것이다.**

검수 완료(`POST /api/curation/jobs/{id}/review`)는 `reviewed_at`만 갱신하고 `crop_ref`를
쓰지 않으므로 이 충돌을 일으키지 않는다 — 배치 재처리 중 잠가야 할 화면은 **검수 화면이
아니라 전표 확정(confirm) 화면**이다.

## 2. 대상 선정

**확정된 잡 전량.** 선별 기준을 만들면 그 기준 자체가 판단이고, 부분 집합으로 재면
before/after 분모가 갈린다.

**전량 원칙이 정본이다.** PR #96이 권고했던 "10~20건 파일럿 후 전량"은 드라이런 도입으로
폐기한다 — 2.5단계가 전량을 미리 재므로 "소규모로 먼저 떠본다"의 존재 이유가 사라진다
(Issue #101 AC ③).

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

## 2.5 드라이런 (되돌릴 수 없는 커밋 전에 결과를 먼저 본다)

재처리는 되돌릴 수 없다(0단계). 드라이런은 같은 앵커 조회·같은 계획 함수로 **커밋 없이**
승계·미결 예상치만 낸다 — DB 쓰기 경로를 아예 부르지 않고 크롭은 임시 디렉터리 안에만
만든다(Issue #100).

- **워커를 내릴 필요는 없다**(쓰기 경로가 없다). 단 모델이 두 벌 뜨므로 **업로드가 없는
  시간대**에 한다.
- 추론 비용이 2배가 되는 것은 수용한 비용이다 — 대신 파일럿 분할을 폐기했다(2단계).

대상 목록은 2단계 SQL을 헤더 없이(`-N -B`) 파일로 뽑는다.

```bash
MYSQL_PWD="$DB_PASS" mysql -N -B -h"$DB_HOST" -P"$DB_PORT" -u"$DB_USER" "$DB_NAME" -e "
SELECT id FROM ocr_jobs j
WHERE j.status = 'done'
  AND (
    j.invoice_id IS NOT NULL
    OR EXISTS (SELECT 1 FROM ocr_corrections c WHERE c.job_id = j.id)
    OR EXISTS (SELECT 1 FROM training_pairs tp WHERE tp.job_id = j.id)
  )
ORDER BY id;" > /tmp/jobs.txt
```

붕괴(3단계 참조)가 배치 도중 1~3잡마다 관측되므로 셸 재시작 루프로 감싼다 — CLI에는
launchd `KeepAlive`가 없고, 이 장치 없이는 29잡 드라이런이 완주하지 못한다. 잡 1건을
끝낼 때마다 `--out`에 append하므로 재실행은 남은 잡부터 이어 간다.

```bash
until "$PYTHON_BIN" -m tools.reprocess_dryrun --jobs-file /tmp/jobs.txt \
        --out results/dryrun/forecast.jsonl; do
  code=$?
  [ "$code" -eq 75 ] || { echo "재시도 불가 종료(code=$code) — 메시지를 읽고 사람이 조치"; break; }
  sleep 2
done
```

- 종료 코드 `75`만 재시도 대상(붕괴)이다. `2`는 잡 목록 형식 오류·**재개 귀속 거부**(`--out`에
  기록된 대상 잡 집합·코드 SHA가 이번 실행과 다름)·`code_version` 취득 실패이며, 재실행해도
  같은 결과라 루프가 수렴하지 않는다 — `--out`에 새 경로를 주거나 기존 파일을 치운다. 그 밖의
  비0(`1` 등)은 DB env 누락·모델 적재 실패 같은 미처리 예외라 재시도로 풀리지 않는다.
- 잡이 그 프로세스의 첫 Qwen 잡으로 붕괴하면 `예측 불가: degenerate`로 즉시 확정 기록,
  다음 실행부터 제외 — 첫 잡이 아닌 붕괴는 미기록이며 다음 새 프로세스가 첫 잡으로
  한 번 더 재시도.

완주한 실행이 요약을 찍는다.

```
 job   new_rows  pairs  relink  orphan   orphan%
  27         14     11      11       0      0.0%
  40          0      9       0       9    100.0%
────────────────────────────────────────────────
 합계        14     20      11       9     45.0%   (잡 2건)
```

**판정.**

- **선행조건: 판정 대상 집합의 `예측 불가 == 0`.** 하나라도 남아 있으면 임계 판정을 하지
  않는다 — 실패 잡은 분모에서 빠지므로 pair가 많은 잡이 실패하면 남은 잡의 비율만 낮게
  보여 배치가 안전하다고 오판된다. 여는 길은 둘이다.
  - **원인 해결 후 재예측** — `--out`에 `error`로 확정 기록된 잡은 같은 파일로 재실행해도
    건너뛰어진다(재개는 기록된 잡을 다시 추론하지 않는다). 그 잡들만 담은 목록을 **새
    `--out`** 으로 돌리고 두 실행의 `합계` 행을 합산해 판정한다 —
    `배치 미결 = (orphan₁ + orphan₂) / (pairs₁ + pairs₂)`. 원인 해결이 **배포를 수반하면
    전체 배치를 다시 돈다**(코드가 바뀌면 앞선 예측이 무효다).
  - **3단계 대상에서 제외** — 그 잡을 3단계 목록에서 빼고 나머지로 판정한다. 실패 잡은 이미
    분모 밖이라 남은 비율이 곧 축소 배치의 비율이고, 뺀 잡은 재처리하지 않으므로 예측 없이
    커밋되는 경로가 생기지 않는다.
  - 실패 잡을 되살리는 재시도 플래그는 두지 않는다 — `until` 루프 안에서 그 플래그가
    `degenerate` 확정 기록을 매 회차 되돌려 위의 크래시루프 수렴이 깨진다.
- **중단 임계: 배치 미결 예상 ≥ 20%.** 근거는 `ml/handwriting/relink.py` ②단계 주석 —
  정상 재처리의 미결은 _행 검출 변화율_ 을 따라가야 하고, _금액 인식 오류율_ 을 따라가는
  순간 재처리가 곧 전량 재검수가 된다. 2026-08-07 파일럿은 78%였다. **첫 드라이런 실측 후
  재조정한다**(현재 이 20%를 뒷받침하는 정상 배치 실측은 없다).
- 잡 단위 미결 100%인 잡이 하나라도 있으면 그 잡은 원인 조사 대상이다(warp 실패 또는
  #99 계열).
- **드라이런과 3단계 사이에 배포(CD)가 지나가면 드라이런을 다시 돈다.** 예측을 무효화하는
  것은 추론 엔진 코드와 금액 모델이지 뱅크가 아니다 — `--out` 첫 줄의 `code_version`이 그
  시점의 SHA이며, 배포 후 재개하면 귀속 거부(code=2)로 스스로 막힌다.
- 예측과 실측이 **정확히** 같다는 보장은 없다(워커 재기동·MLX 상태 차이). 도구가 보장하는
  것은 "같은 `result_json`이면 같은 승계 계획"까지다.

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

- 배치 도중 `[degenerate] job=N raw=...`가 stderr에 찍히고 **워커가 재기동되는 것은 정상
  방어 동작이다**(이슈 #99). 금액 판독 모델(MLX Qwen)이 한 프로세스에서 금액칸을 수십 회
  연속 처리하다 출력이 `!` 스팸으로 붕괴하면, 그 잡은 커밋되지 않고 되돌려진다 — 신규·재처리
  잡 모두 `pending`으로 돌아간다(재처리 잡은 `result_json`이 남아 있어 다음 점유에서 스스로
  재처리로 재분류된다). 이어서 워커가 비0 종료하고 launchd `KeepAlive`가 새 프로세스를
  띄우며, 되돌려진 잡은 모델이 재적재된 깨끗한 상태에서 자동으로 다시 처리된다. 붕괴는
  프로세스가 살아 있는 동안 지속되므로(sticky) 재기동 말고는 복구 수단이 없고, 사람이 손댈
  일도 없다 — **단, 예외가 하나 있다(아래 "부팅 직후 첫 잡" 참조).**

  붕괴는 한 프로세스가 금액칸 ~28~42회를 처리할 때쯤 일어나므로(잡당 13~26회) 대규모
  배치에서는 **1~3잡마다 한 번** 재기동이 보인다. 매 재기동마다 품목 인코더·뱅크·Qwen
  모델을 다시 적재하므로 배치가 눈에 띄게 느려지는데, 이것이 "느려짐"이지 "고장"이 아니다 —
  고장은 PID가 바뀌는데 잡 상태가 전진하지 않는 것이다. 재기동을 줄이는 예방적 주기
  재활용(N칸마다 선제 재기동)은 이번 범위 밖이며 후속 이슈로 다룬다.

  ```bash
  grep '\[degenerate\]' ~/.sjmj-ai/logs/ml-worker.err.log   # 감지 이력
  launchctl list | grep ai.sjmj.ml-worker                   # 첫 열이 PID — 재기동 시 바뀐다
  ```

  **판정은 로그가 아니라 PID 변경 + 잡 상태 전이로 한다.** 예외가 하나 있다 — 부팅 직후
  **첫** 금액 판독 잡이 곧바로 degenerate면 재시도하지 않고 그 잡을 강등한 뒤 종료한다
  (재기동해도 같은 붕괴가 반복되므로) — 신규 잡은 `failed`, 재처리 잡은 `done`으로 남는다.
  이때도 워커는 재기동하지만 되돌려진 잡이 없어 자동 재시도 대상이 아니다 — 사람이
  `[degenerate]` 로그의 job id를 확인해 그 잡만 다시 큐에 넣는다. **같은 잡이 두 번 연속
  부팅 직후 강등되면 재큐를 반복하지 말고 그 잡을 개별 조사 대상으로 뺀다**(금액칸 수가
  프로세스 임계에 가까울 수 있다).

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
  확인한다(`SELECT status FROM ocr_jobs WHERE id=...`). `done`이면 아래 잔여 판정으로
  넘어간다. **`running`이면 두 가지 중 하나다** — 지금 정말 처리 중이거나, 워치독 없이
  죽어서 굳은 것이다(아래 마지막 항목 참고). 이 둘을 로그나 상태 조회만으로 구분할 방법은
  없으므로(정상 처리 중인 잡은 로그를 남기지 않는다, 3단계 참고) 판단하려 들지 말고 아래
  마지막 항목의 절차로 간다.
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
- **같은 잡이 반복 처리되면(재처리 → 또 `.old` 잔여 → 재처리) 위 처방이 수렴하지 않는다.**
  `.old`가 권한·점유 등으로 지워지지 않으면 `_swap_crop_dir`의
  `rmtree(..., ignore_errors=True)`가 조용히 실패하고 뒤이은 `rename`이 `ENOTEMPTY`로 죽어
  잡이 다시 재처리 큐로 돌아간다(`worker/poll.py`). 연속 3회(`SWAP_RETRY_LIMIT`)까지만
  재시도하고, 상한에 닿으면 워커가 그 잡을 **초안 보존 failed**로 전이하고
  (`mark_failed_keep_result` — 커밋된 새 좌표가 정본으로 남는다) 다음 잡으로 넘어간다 —
  루프는 저절로 끊기고 다른 잡을 굶기지 않는다(로그 `크롭 교체 실패 3회 — 재시도 상한 도달`).
  남는 일은 **`.old` 제거 → 재큐잉** 두 가지다. failed 잡은 워커가 집지 않으므로 워커를
  내릴 필요가 없다 — `status='failed'`를 확인한 뒤 제거한다(상한 도달 전이면 워커가 그 잡을
  다시 처리하며 `.old`를 되만들 수 있다).

  ```bash
  # status='failed' 확인 후에만 만진다 — 상한 도달 전이면 지우는 사이 워커가 다시 만든다.
  rm -rf "$SJMJ_DATA_DIR"/ocr_crops/job-<job_id>.old
  # 그래도 안 지워지면 크롭 루트 밖으로 옮겨 원인 조사용으로 남긴다(루트 안은 안 된다).
  mv "$SJMJ_DATA_DIR"/ocr_crops/job-<job_id>.old ~/job-<job_id>.old.stuck
  ```

  **이것은 위에서 금지한 "파일 복구"가 아니다** — 금지된 것은 `.old`(옛 그림)를 `job-N`으로
  **되돌리는** 방향이고, 여기서는 옛 그림을 치우기만 한다. 옮길 때 **크롭 루트 밖으로**
  보내는 것이 중요하다. 루트 안에 다른 이름으로 두면 잔여 점검(위 `ls`)과 `--reembed-job`
  가드(`require_settled_crops`)의 시야에서 사라져 마커만 조용히 없어진다. 제거해도 `job-N`이
  DB와 정합하다는 보장은 여전히 없으므로, 반드시 그 잡을 **다시 재처리해** 교체를
  완주시킨다 — failed 잡은 3단계의 `reprocess` 호출이 409로 거부하므로
  `UPDATE ocr_jobs SET status='pending' WHERE id=<job_id>;`로 되돌린다(새 좌표가 담긴
  `result_json`의 rows를 워커 판별자가 보고 재처리로 재분류한다).

- **추론 도중(커밋 전)에 프로세스가 죽으면 잡은 `running`에서 멈춘다** — `pending → running`
  전이만 있고(`worker/db.py`의 `claim_next_pending`), 죽은 프로세스는 그 잡을 되돌리지
  못한다. `.tmp`도 함께 남는다. 이 잡은 `done`이 아니므로 3단계의 `reprocess` 호출이 409로
  거부돼 그대로는 3단계로 돌아갈 수 없다.
  **워커를 다시 기동하면 부팅 워치독이 자동 복구한다**(#85, `worker/db.py`의
  `requeue_stale_running`) — 기동 시 `running` 전량을 `pending`으로 되돌려 순서대로 재처리한다
  (`[watchdog]` 로그로 확인). 아래 수동 절차는 **워커를 기동하지 않은 채** 상태를 판정·정리해야
  할 때(예: 좌초분을 `pending`이 아닌 `done`으로 되돌려 재처리 없이 마감하고 싶을 때)만 필요.
  **"처리 중이 아님"을 로그로 확인하지 않는다** — 정상 처리 중인 잡은 `[warp-gate]` 같은
  예외 신호가 없는 한 어떤 줄도 남기지 않으므로(3단계 참고), 로그가 비어 있다는 것이
  "처리 중이 아니다"의 증거가 되지 못한다. 지금 처리 중인 잡일수록 오히려 로그에 안
  보이는 게 정상이라, 로그로 판단하면 처리 중인 잡을 "아니다"로 오판해 `commit_job`이
  방금 넣은 재처리 요청을 `done`으로 조용히 덮어써 버릴 수 있다(`worker/db.py`).
  대신 **워커를 정지시킨 뒤에 상태를 판정한다.**

  **`launchctl kickstart -k`(재시작)로는 안 된다.** plist는 `RunAtLoad`·`KeepAlive`가
  모두 `true`라(`deploy/launchd/ai.sjmj.ml-worker.plist.template`) kickstart는 정지가
  아니라 재시작이고, 새로 뜬 워커는 모델을 적재한 뒤 곧바로 무한 폴링으로 돌아가
  `pending` 잡을 다시 집는다(`worker/main.py`의 `load_models()` → `while True`). 배치
  재처리 도중이면 큐에 `pending`이 가득하므로 재시작 몇십 초 뒤부터 `status='running'`에는
  **지금 정말 처리 중인 잡**이 섞인다 — 그 잡에 아래 UPDATE를 걸면 위에서 금지한 사고가
  그대로 일어난다.

  **정지 자체가 새 피해자를 만든다.** 워커는 단일 직렬이라 정지 순간 in-flight였던 잡은
  커밋 전에 끊겨 똑같이 `running`으로 남는다. 워커는 신규 업로드를 먼저 집으므로(3단계)
  배치 재처리 도중이면 그 피해자는 **사무실이 방금 올린 신규 잡**일 확률이 높다 — 사람이
  지켜보지 않고, `.tmp` 점검(위 `ls`)의 대상도 아니라(신규 잡은 `job-N.tmp` 자체가 아직
  없거나 다른 잡 id라 이 배치의 점검망 밖이다) 조용히 방치되기 쉽다. 그래서 2)의 조회는
  대상 잡 하나가 아니라 `running` **전량**을 본다. **정지해 둔 동안 사무실 업로드는
  처리되지 않는다** — 잡은 `pending`으로 쌓였다가 기동 후 순서대로 처리되므로 유실은
  없지만, 가능하면 업로드가 없는 시간대에 한다. 순서를 지켜 진행한다:

  ```bash
  # 1) 워커를 gui 도메인에서 내린다(kickstart와 달리 KeepAlive가 되살리지 않는다).
  #    bootout은 비동기라 완전히 내려갈 때까지 기다린다 —
  #    scripts/install-launchagent-ml-worker.sh가 같은 이유로 launchctl print를 폴링한다.
  #    이미 내려가 있으면 bootout이 비정상 종료 코드를 내지만 무해하다.
  launchctl bootout gui/$(id -u)/ai.sjmj.ml-worker
  until ! launchctl print gui/$(id -u)/ai.sjmj.ml-worker >/dev/null 2>&1; do sleep 1; done
  ```

  ```sql
  -- 2) 이제 잡을 집는 주체가 없으므로, status='running'인 잡은 전부 좌초된 것이다.
  --    is_reprocess=1(rows 있는 옛 초안 보유 — 워커 판별자와 동일)이면 재처리 잡,
  --    0이면 신규 업로드(또는 에러 JSON만 남은 실패 잡)다 —
  --    되돌리는 상태가 다르다(아래).
  SELECT id, status, (JSON_EXTRACT(result_json, '$.rows') IS NOT NULL) AS is_reprocess
  FROM ocr_jobs WHERE status='running';
  ```

  걸린 잡마다(대상 잡 포함) `is_reprocess`에 따라 되돌린다:

  ```sql
  -- 3-a) 재처리 잡(is_reprocess=1) — 옛 초안·크롭이 여전히 정합이므로 done으로.
  UPDATE ocr_jobs SET status='done' WHERE id=<job_id>;
  -- 3-b) 신규 잡(is_reprocess=0) — result_json이 없어 done은 무효 상태다.
  --      pending으로 되돌리면 워커가 기동 후 신규 경로로 다시 추론한다.
  UPDATE ocr_jobs SET status='pending' WHERE id=<job_id>;
  ```

  ```bash
  # 4) 워커를 다시 올린다. plist가 RunAtLoad=true라 bootstrap만으로 기동한다.
  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.sjmj.ml-worker.plist
  tail -f ~/.sjmj-ai/logs/ml-worker.err.log   # [retrieval-version] 부팅 지문=... 이 기동 완료 신호
  ```

  기동한 워커는 모델을 1회 적재한 뒤에야 폴링을 시작하므로 곧바로 잡을 집지 않는다 —
  위 부팅 지문 한 줄(`worker/main.py`, stderr)이 적재가 끝났다는 신호다.

  재처리 잡은 그 뒤 3단계로 다시 `reprocess`를 넣는다. 신규 잡은 `pending`으로
  되돌린 것만으로 워커가 다음 순번에 다시 집는다(별도 호출 불필요). 남은 `.tmp`는
  워커가 그 잡을 다시 집을 때 `process_one_job`이 "앞선 실패가 남긴 잔여"로 간주해
  먼저 지운다(`worker/poll.py`) — 따로 지울 필요는 없다.

## 6. 뱅크 재임베딩

`--reembed-job`에는 **3단계에서 재처리한 잡 중 `curation_reviewed = 1`인 것**을 넣는다
(아래 예시의 `12 13 14`는 지면상 축약이다). 목록은 손으로 고르지 말고 아래로 뽑는다:

```sql
SELECT id FROM ocr_jobs WHERE curation_reviewed = 1 AND id IN (<3단계에서 재처리한 잡>);
```

**"재처리한 잡 전량"이 아니라 교집합인 이유.** 2단계 대상에는 확정됐지만 **학습쌍이 0개인
잡**(강등·전량 수기 입력)이 섞인다. 큐레이션 목록은 `ocr_jobs JOIN training_pairs`라
(`backend/app/repositories/curation_repository.py`의 `list_jobs`) 그런 잡은 검수 화면에 아예
뜨지 않아 사람이 검수 완료를 누를 수단이 없다 — 전량을 넣으면 아래 미검수 가드가 **반드시**
걸리고, 4단계로 돌아가도 해소되지 않는다(그 잡엔 미결도, 검수할 화면도 없다).

**게이트는 재처리 말고 제외로도 풀린다.** `ocr-bank-update.md` 2절의 크롭 육안 검수에서 쌍을
하나라도 `excluded`로 바꾸면 `patch_pair`가 그 잡의 `curation_reviewed`를 0으로 내리므로, 위
교집합을 **제외 처리 전에 뽑아 두면 그 잡이 실제로는 빠진 채 `apply`가 돈다**. 교집합 SQL은
제외 처리를 모두 끝내고 재검수 완료까지 누른 뒤에 뽑는다(절차는 그 런북의 경고 블록 참조).

**교집합에서 빠지는 잡이 누락 위험을 만들지 않는 근거.** 재임베딩 대상은 정의상 뱅크에
항목이 있는 잡뿐이고, 뱅크에는 `curation_reviewed = 1`인 잡의 쌍만 들어간다(ADR 0004,
`select_desired`). 그러므로 빠지는 잡은 ① 쌍이 0개라 뱅크에 항목이 없거나, ② 애초에
미검수라 뱅크에 들어간 적이 없거나 — 어느 쪽이든 제외가 no-op이다. ③ 미결이 나와 게이트가
해제된 잡은 4단계에서 재검수를 끝내면 다시 `1`이 되어 이 교집합에 들어온다. **그래서 4단계를
먼저 끝내는 것이 이 절의 전제다.** 그 상태에서 이 교집합은 "뱅크에 항목이 있는 재처리 잡
전량"과 같아진다.

거꾸로 **이 교집합에 드는 잡을 빠뜨리면** 안 된다. 누락된 잡은 좌표가 그대로라 diff에서
`unchanged`로 분류되고, `force_replace`는 지정한 잡만 승격하므로 — 크롭 PNG는 새 그림인데
뱅크 임베딩은 옛 그림인 채로 조용히 남는다.

```bash
"$PYTHON_BIN" -m tools.bank_update plan --reembed-job 12 13 14
```

- `remove 건수 = 좌표 이동 수 + 미결 수`를 대조한다. 재처리 이후엔 정상 상태에서도 remove가
  뜨므로 `--yes`에 무뎌지지 않게 하는 절차다.
- 미검수 잡을 지정하면 CLI가 잡 id를 담아 즉시 실패한다(§11-1). 그 잡에 미결이 있으면
  4단계로 돌아가고, 애초에 검수 대상이 아닌 잡(쌍 0건 등)이면 위 교집합 SQL대로 목록에서
  빼면 된다.
- 크롭 교체가 끝나지 않은 잡을 지정하면 잔여 디렉터리 이름을 담아 실패한다. 5단계로 돌아간다.

```bash
"$PYTHON_BIN" -m tools.bank_update apply --plan results/bank_update/plan.jsonl --yes
```

## 7. 워커 다시 띄우기 (뱅크 재적재)

워커는 기동 시 1회만 뱅크를 적재하므로 프로세스를 다시 띄워야 새 뱅크가 반영된다.

여기서도 `kickstart -k`(재시작)가 아니라 **정지 → 확인 → 기동**을 쓴다. 이 전환도 그 순간
in-flight였던 잡을 `running`으로 좌초시키는데(5단계와 같은 이유), 재시작으로는 그 피해자를
확인할 창이 열리지 않기 때문이다 — 워커가 곧바로 다시 잡을 집어 `running` 조회가 좌초된
잡과 정상 처리 중인 잡을 섞어 보여준다. 정지 상태에서 보면 조회 결과가 곧 좌초 목록이다.
정지 동안 사무실 업로드가 `pending`으로 쌓이는 것도 5단계와 같다.

```bash
launchctl bootout gui/$(id -u)/ai.sjmj.ml-worker
until ! launchctl print gui/$(id -u)/ai.sjmj.ml-worker >/dev/null 2>&1; do sleep 1; done
```

```sql
-- 좌초된 잡이 나오면 5단계의 3-a/3-b로 되돌린다.
SELECT id, status, (JSON_EXTRACT(result_json, '$.rows') IS NOT NULL) AS is_reprocess
FROM ocr_jobs WHERE status='running';
```

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.sjmj.ml-worker.plist
```

## 8. 오염 발견 시

```bash
cp "$SJMJ_ML_MODELS_DIR"/bank.<stamp>.npz.bak "$SJMJ_ML_MODELS_DIR"/bank.npz
```

뱅크는 기동 시 1회만 적재되므로, 7단계와 같은 정지 → 확인 → 기동으로 워커를 다시 띄워야
복원한 뱅크가 반영된다.

## 9. 잡 단위 롤백은 지원하지 않는다

원본 사진은 보존되므로 복구는 엔진을 되돌려 다시 재처리하는 방향이다. 재처리는 멱등이라
같은 사진·같은 엔진이면 매칭이 항등이 되어 좌표가 제자리에 남는다.

## 실패 시나리오 대응

| 증상                                                                           | 원인·대응                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| ------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 재처리 후에도 잡이 `done`이고 초안이 그대로                                    | 재추론 실패 → done 롤백(정상 동작). 워커 로그의 `[warp-gate]` 라인 확인                                                                                                                                                                                                                                                                                                                                                                                                        |
| 그 잡의 쌍이 전부 미결                                                         | warp 실패로 행이 0개 검출됨. 원본 사진 품질 확인 후 재시도                                                                                                                                                                                                                                                                                                                                                                                                                     |
| 검수 화면에서 PATCH가 409                                                      | 그 사이 재처리가 지나갔다. 새로고침 후 다시 편집                                                                                                                                                                                                                                                                                                                                                                                                                               |
| 검수 완료 버튼이 409                                                           | 재처리가 게이트를 다시 열었다. 새로고침해 새 미결 쌍을 확인한 뒤 다시 완료 처리                                                                                                                                                                                                                                                                                                                                                                                                |
| 이미 검수 완료된 쌍이 재검수 큐에 다시 뜸                                      | 승계 실패로 사유가 `relink_failed`로 갈아치워졌다(0. 전제 참조) — 버그가 아니라 의도된 동작                                                                                                                                                                                                                                                                                                                                                                                    |
| 배제돼 있던 쌍이 사유 없이 `included`로 돌아옴                                 | 그 쌍이 이번 재처리에서 승계에 성공했다(기계 소유 배제의 자동 복원, 0. 전제 참조) — 의도된 동작                                                                                                                                                                                                                                                                                                                                                                                |
| 워커가 같은 잡을 반복 처리                                                     | `.old`가 안 지워져 교체가 실패한다(권한·디스크). 연속 3회에서 워커가 초안 보존 failed로 스스로 끊는다 — 로그 `크롭 교체 실패` 확인 후 5단계의 "같은 잡이 반복 처리되면" 절차로 `.old` 제거·재큐잉                                                                                                                                                                                                                                                                              |
| 배치 재처리 도중 특정 잡만 실패                                                | 그 사이 사람이 같은 잡의 거래명세서를 확정했다(1단계의 TOCTOU). 해당 잡만 다시 재처리                                                                                                                                                                                                                                                                                                                                                                                          |
| 검수 화면 크롭이 전부 404이고 `.old`가 남아 있음                               | 커밋 후 교체가 끝나지 않았다. 그 잡을 다시 재처리한다(5단계) — 옛 그림은 복원하지 않는다                                                                                                                                                                                                                                                                                                                                                                                       |
| `--reembed-job`이 교체 미완을 이유로 거부                                      | 5단계를 건너뛴 것이다. 잔여를 남긴 잡을 재처리한 뒤 다시 실행                                                                                                                                                                                                                                                                                                                                                                                                                  |
| 잡이 `running`에서 멈춘 채 진행이 없음                                         | 워커가 추론 도중 죽었다(워치독 없음). 워커를 `bootout`으로 정지시킨 상태에서 좌초된 잡 전부(대상 잡 포함)를 되돌린 뒤 다시 띄운다(5단계 참조)                                                                                                                                                                                                                                                                                                                                  |
| 배치 도중 워커 PID가 바뀌고 `[degenerate]` 로그가 있음                         | MLX 금액 판독이 `!` 스팸으로 붕괴해 워커가 자가 재기동했다(이슈 #99) — 되돌려진 잡(신규·재처리 모두)은 자동 재시도된다. 개입 불필요                                                                                                                                                                                                                                                                                                                                            |
| 부팅 직후 첫 잡이 곧바로 `failed`이고 `[degenerate]` 로그가 있음               | 크래시루프 가드가 그 잡을 자동 재시도하는 대신 강등(retire)했다(3단계 참조) — 재기동해도 같은 붕괴가 반복되므로 첫 잡은 재시도시키지 않는다. 워커는 이미 재기동해 다음 잡을 처리하고 있다. `reprocess` API는 `done` 잡만 받으므로 `failed` 상태에서는 409다 — `UPDATE ocr_jobs SET status='pending', result_json=NULL WHERE id=<job_id>;`로 그 잡만 되돌리면 실행 중인 워커가 다음 폴링에서 집는다. `result_json`을 함께 비우지 않으면 워커가 그 잡을 재처리 잡으로 오분류한다 |
| 부팅 직후 첫 잡(재처리 잡)이 곧바로 도로 `done`인데 `[degenerate]` 로그가 있음 | 위와 같은 크래시루프 가드 강등이지만 이 잡은 **재처리 잡**이라 `failed`가 아니라 `done`으로 남는다(3단계 참조) — 정상 완료와 상태만으로 구별되지 않으므로 `[degenerate]` 로그로 확인한다. 이미 `done`이므로 `reprocess` API가 409 없이 통과한다 — raw SQL 없이 평범한 `POST /api/curation/jobs/{id}/reprocess`만 다시 호출하면 된다                                                                                                                                            |
