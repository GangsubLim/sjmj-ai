# 런북 색인

운영 절차 문서 모음. 각 런북은 **되돌릴 수 없는 조작**(뱅크 갱신·재처리)을 포함하므로,
실행 위치와 선행 조건을 먼저 확인하고 들어간다.

## 목록

| 런북                                                 | 무엇을 하나                                               | 실행 위치          | 주 도구                                                               |
| ---------------------------------------------------- | --------------------------------------------------------- | ------------------ | --------------------------------------------------------------------- |
| [ocr-curation-analysis.md](ocr-curation-analysis.md) | 큐레이션 학습쌍을 분석해 OCR 정확도 개선 방향을 도출      | 로컬 개발 머신     | `tools.curation_report` (`fetch`/`report`/`pull-images`)              |
| [ocr-bank-update.md](ocr-bank-update.md)             | 검수 완료된 품목 크롭을 운영 뱅크(`bank.npz`)에 증분 반영 | macmini            | `tools.bank_update` (`plan`/`apply`/`score`)                          |
| [ocr-job-reprocessing.md](ocr-job-reprocessing.md)   | 엔진 개선분을 확정된 과거 잡에 재적용하고 뱅크를 재임베딩 | macmini            | `POST /api/curation/jobs/{id}/reprocess`, `bank_update --reembed-job` |
| [macmini-runner.md](macmini-runner.md)               | `deploy.yml`용 self-hosted 러너를 GitHub repo에 등록      | macmini (1회 셋업) | `gh api`, `config.sh`/`svc.sh`                                        |

## 어떤 순서로 이어지나

세 OCR 런북은 서로를 호출한다. 진입점은 보통 **분석**이다.

```
[분석] ocr-curation-analysis
   │  out_of_bank 누적 발견
   ▼
[뱅크 갱신] ocr-bank-update ──→ score(전/후 비교) ──→ 다시 [분석]

[엔진 개선 후]
[재처리] ocr-job-reprocessing ──→ 6단계 재임베딩에서 bank_update 사용
   (재검수·크롭 교체 완료 확인이 재임베딩보다 반드시 앞)
```

- 분석 리포트의 `## 개선 작업으로 잇기` 표가 발견 → 다음 런북 매핑의 정본이다.
- 재처리로 크롭이 바뀐 뒤에는 `ocr-bank-update.md`를 바로 타지 않는다 —
  `ocr-job-reprocessing.md`의 4·5단계(미결 확인, 크롭 교체 완료 확인)를 먼저 통과해야 한다.

## 어느 런북에서도 공통인 것

- **macmini에서 `uv run`/`uv sync` 금지.** `ml/.venv`는 운영 ml-worker가 쓰는 그 venv라
  수동 설치분(torch·cv2·mlx)이 삭제된다. `"$PYTHON_BIN" -m tools....` 관용구를 쓴다.
- **ssh 세션 첫 명령은 PATH 보정.** `export PATH=/opt/homebrew/bin:$PATH` — 비대화형 셸에
  homebrew가 없어 `mysqldump`/`mysql`이 전부 실패한다.
- **DB 접속값·DB명은 env에서만 읽는다**(`~/.sjmj-ai/backend.env` · `~/.sjmj-ai/ml-worker.env`).
  하드코딩하면 런타임 DB와 백업 DB가 갈린다.
- **워커는 기동 시 뱅크를 1회만 적재한다.** 뱅크를 바꿨으면 워커를 다시 띄워야 반영된다.
- **릴리스 배포는 retrieval 지문을 바꾼다**(배포 코드 SHA가 지문에 들어간다). 프론트만
  바뀐 릴리스여도 과거 재평가가 stale이 되므로, 배포 후에는 `bank_update score --scope all`을
  다시 돌린 뒤 분석한다.

## 참고

- 아키텍처 결정: [../adr/](../adr/) — 특히 ADR 0001(macmini 단일 실행 위치),
  0004(검수 게이트), 0006(빈 크롭 자동 배제), 0010·0011(재처리 원자성·게이트 해제)
- 작업별 분석·결정 기록: `docs/work/{yyyy-mm}/{yyyy-mm-dd}-{job-slug}/` (로컬 전용, git 비추적)
