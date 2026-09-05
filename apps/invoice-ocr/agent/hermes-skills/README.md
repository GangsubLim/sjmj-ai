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

## 주의

- 등록 경로가 **워크트리**면 워크트리 삭제 시 스킬 소실 — 승격 시 배포 체크아웃(`/Users/submini/sjmj-ai`, 배포 후) 경로로 이전
- SKILL.md 수정 후에는 게이트웨이 재시작으로 반영 확인(스캔 시그니처는 디렉토리 mtime 기반)
- 게이트웨이 cwd가 `/Users/submini`라 repo-local `.hermes/skills` + `hermes skills trust` 방식은 부적합
- 보관 파일: `/Users/submini/sjmj-ai-data/agent_uploads/{invoice_id}.{jpg|png}` + `{invoice_id}.draft.json`. 리포트는 `apps/invoice-ocr/ml/tools/agent_report.py`
