# hermes-skills — sjmj-ai가 소유하는 hermes agent 스킬

macmini의 hermes agent(v0.21.0)가 `skills.external_dirs`로 읽는 스킬 디렉토리. 구조는 hermes 요구대로 `<카테고리>/<이름>/SKILL.md` 3단

| 스킬 | 역할 | spec |
| --- | --- | --- |
| `sjmj/invoice-entry` | 텔레그램 사진 → `POST /api/invoices` → `/edit/{id}` 링크 회신, 사진·초안 보관 | `docs/work/2026-09/2026-09-05-hermes-invoice-entry/spec.md`(로컬 전용) |

## 등록

`~/.hermes/config.yaml`의 `skills:` 블록에 이 디렉토리의 **절대경로** 추가 후 게이트웨이 재시작

```yaml
skills:
  external_dirs:
    - /Users/submini/.herdr/worktrees/sjmj-ai/feat-new/apps/invoice-ocr/agent/hermes-skills
```

```bash
launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway
hermes skills list | grep -i invoice-entry     # 등록 확인
```

external_dirs 스킬은 hermes 입장에서 읽기 전용이며 curator 자동 정리 대상에서 제외. 같은 이름의 로컬 스킬(`~/.hermes/skills/**`)이 있으면 로컬이 우선

## 해제

config.yaml에서 해당 줄 삭제 후 게이트웨이 재시작. hermes 설치본에는 흔적이 남지 않음

## 자기개선 루프(야간 배치)

spec `docs/work/2026-09/2026-09-06-hermes-self-improvement-pipeline/spec.md`(로컬 전용). 사용자가 `/edit/{id}`에서 고친 최종본을 매일 03:00 회수해 `/Users/submini/sjmj-ai-data/agent_knowledge/active.md`로 누적 — SKILL.md 0단계가 읽는다. 스크립트는 `apps/invoice-ocr/ml/tools/agent_learn.py`(extract·publish·report)

| 파일(`agent_knowledge/`) | 소유 | 내용 |
| --- | --- | --- |
| `corrections.jsonl` | extract | 교정 1건 1줄(append-only) |
| `ledger.json` | extract | `{invoice_id: 최종본 해시}` — 멱등·재수정 감지 |
| `vocab_snapshot.json` | extract | 사전 스냅샷(publish 재생성용) |
| `proposed.md` | extract 작성 · LLM 편집 | 다음 버전 스테이징 — LLM은 `## 거래처 프로필`·`## 일반화 규칙`만 편집 |
| `knowledge/v{N}.md` · `active.md` · `versions.jsonl` | publish | 버전 보관 · 현재본 · 발행/거부 기록 |

### 래퍼 스크립트 `~/.hermes/scripts/sjmj_agent_learn.sh`

```bash
#!/bin/bash
# ML_DIR: 릴리스 전 검증은 워크트리 ml/, 릴리스 후 운영 체크아웃 ml/
ML_DIR=/Users/submini/sjmj-ai/apps/invoice-ocr/ml
PYTHON_BIN=/Users/submini/sjmj-ai/apps/invoice-ocr/ml/.venv/bin/python
set -a; . "$HOME/.sjmj-ai/backend.env"; set +a
cd "$ML_DIR" || { echo '{"wakeAgent": false}'; exit 0; }
exec "$PYTHON_BIN" -m tools.agent_learn extract --data-dir "$SJMJ_DATA_DIR"
```

`chmod +x` 후 단독 실행해 stdout 마지막 줄이 요약 JSON(신규 있음) 또는 `{"wakeAgent": false}`인지 확인. 인터프리터는 운영 venv(sqlalchemy·pymysql 포함)이고 코드는 `ML_DIR`에서 로드 — 운영 `ml/`에서 `uv run`·`uv sync` 금지

### cron 등록

```bash
hermes cron create "0 3 * * *" --name "sjmj agent learn" --script sjmj_agent_learn.sh \
  --deliver telegram --reasoning-effort medium "$(cat <<'EOF'
sjmj 판독 지식 야간 갱신. 무인 실행 — 질문 금지, HTTP API 호출 금지, 초안·DB 수정 금지.
1. 위에 주입된 extract 요약 JSON의 proposed 경로 파일을 읽는다(/Users/submini/sjmj-ai-data/agent_knowledge/proposed.md).
2. 같은 디렉토리의 corrections.jsonl에서 요약의 new 건수만큼 마지막 레코드를 읽어 근거로 삼는다.
3. proposed.md에서 `## 거래처 프로필`과 `## 일반화 규칙` 두 절만 갱신한다. 다른 절·헤딩·제목은 한 글자도 바꾸지 않는다. 각 불릿은 `- 내용 (#invoice_id)` 형식으로 근거 id를 반드시 단다. 확신 없는 규칙은 쓰지 않는다. 거래처 프로필 30줄·일반화 규칙 20줄 이하. 비어 있으면 `(없음)` 한 줄.
4. 실행: cd /Users/submini/sjmj-ai/apps/invoice-ocr/ml && set -a && . ~/.sjmj-ai/backend.env && set +a && /Users/submini/sjmj-ai/apps/invoice-ocr/ml/.venv/bin/python -m tools.agent_learn publish --data-dir "$SJMJ_DATA_DIR"
5. 실행: 같은 환경에서 python -m tools.agent_learn report --data-dir "$SJMJ_DATA_DIR" --out /tmp/agent_report 후 /tmp/agent_report/report.md의 `## 지식 버전별 일치율` 표를 읽는다.
6. 회신 1건(한국어, 5줄 이내): publish 출력 1줄 그대로 · 추가 교정 n건(kind별) · 버전별 일치율 표의 마지막 두 행. rejected면 사유를 그대로 인용한다.
EOF
)"
hermes cron list | grep -A6 "sjmj agent learn"     # id·다음 실행 확인
hermes cron run <id>                                # 즉시 1회 실행(다음 tick)
```

잡별 toolsets는 지정하지 않는다 — CLI 생성 잡은 플랫폼 기본 toolset(terminal·file 포함)을 쓴다. 4·5의 `cd` 경로도 래퍼의 `ML_DIR`과 같이 릴리스 전엔 워크트리로 바꿔 등록

### 롤백·수동 조작

- 잘못 학습된 버전 되돌리기: `cp agent_knowledge/knowledge/v{N}.md agent_knowledge/active.md` (versions.jsonl은 그대로 — report의 버전 매핑은 발행 시각 기준이라 롤백 이후 초안은 최신 버전으로 집계됨, 필요 시 report.md에 메모)
- 학습 일시 중지: `hermes cron pause <id>` / 재개 `hermes cron resume <id>`
- 처음부터 다시: `agent_knowledge/` 디렉토리 삭제 → 다음 extract가 원장 없이 전량 재추출

## 주의

- 등록 경로가 **워크트리**면 워크트리 삭제 시 스킬 소실 — 승격 시 배포 체크아웃(`/Users/submini/sjmj-ai`, 배포 후) 경로로 이전
- SKILL.md 수정 후에는 게이트웨이 재시작으로 반영 확인(스캔 시그니처는 디렉토리 mtime 기반)
- 게이트웨이 cwd가 `/Users/submini`라 repo-local `.hermes/skills` + `hermes skills trust` 방식은 부적합
- 보관 파일: `/Users/submini/sjmj-ai-data/agent_uploads/{invoice_id}.{jpg|png}` + `{invoice_id}.draft.json`. 리포트는 `apps/invoice-ocr/ml/tools/agent_report.py`
