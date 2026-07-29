# Changelog

이 프로젝트의 주요 변경 사항을 기록한다. 형식은 [Keep a Changelog](https://keepachangelog.com/ko/1.0.0/),
버전 체계는 [Semantic Versioning](https://semver.org/lang/ko/)을 따른다.

릴리스 항목은 `scripts/release.sh`가 `## [vX.Y.Z] — YYYY-MM-DD` 헤더를 추가하면 my-release 스킬 Step 4에서 본문을 작성한다.

## [v0.5.0] — 2026-07-29

품목 인식이 헷갈릴 때 이를 숨기지 않고 알려주고, 후보 중에서 바로 고를 수 있게 한다 ([#42](https://github.com/GangsubLim/sjmj-ai/pull/42)).

### Added

- 품목 인식이 불확실한 행에 미확신 배지를 표시하고, 상위 5개 후보를 칩으로 제시해 클릭 한 번으로 확정 ([#42](https://github.com/GangsubLim/sjmj-ai/pull/42))
- 등록 화면에 손글씨 크롭 썸네일과 OCR 후보 칩을 함께 표시해, 원본을 다시 열지 않고 확인 ([#42](https://github.com/GangsubLim/sjmj-ai/pull/42))
- 라벨을 무엇으로 확정했는지(후보 선택·직접 입력·신규 등록) 검수 결과에 기록 — 이후 인식 개선의 근거로 쓰인다 ([#42](https://github.com/GangsubLim/sjmj-ai/pull/42))

### Changed

- 크롭 이미지 조회 경로를 `/ocr` 네임스페이스로 정리 ([#42](https://github.com/GangsubLim/sjmj-ai/pull/42))

### Fixed

- 손글씨 크롭 썸네일이 가로 폭의 절반 이상을 잘라먹어 글씨가 안 보이던 문제 (bb873bf)
- 신규 품목 등록 버튼이 동작하지 않던 문제와, 등록 시 기존 품목 단가를 덮어쓰던 문제 ([#42](https://github.com/GangsubLim/sjmj-ai/pull/42))
- 검수 중 빠르게 연속 수정하면 이전 응답이 나중에 도착해 화면이 되돌아가던 문제 ([#42](https://github.com/GangsubLim/sjmj-ai/pull/42))
- 후보 목록 펼치기가 키보드·스크린리더에서 인식되지 않던 접근성 문제 ([#42](https://github.com/GangsubLim/sjmj-ai/pull/42))

## [v0.4.0] — 2026-07-28

수기 명세서 인식 정확도를 끌어올린다 — 잘못 펴진 사진을 걸러내고, 금액 유실을 메우고, 검수 결과를 품목 인식에 되먹인다 ([#35](https://github.com/GangsubLim/sjmj-ai/pull/35), [#34](https://github.com/GangsubLim/sjmj-ai/pull/34), [#25](https://github.com/GangsubLim/sjmj-ai/pull/25), [#24](https://github.com/GangsubLim/sjmj-ai/pull/24)).

### Added

- 사진의 표 정합이 깨진 경우를 자동으로 판정해, 잘못 읽은 행을 저장하지 않고 재촬영을 안내 ([#35](https://github.com/GangsubLim/sjmj-ai/pull/35))
- 검수 완료된 큐레이션 결과를 품목 인식 뱅크에 반영하는 증분 갱신 도구 — 검수할수록 품목 인식이 정확해진다 ([#34](https://github.com/GangsubLim/sjmj-ai/pull/34))
- 인식 정확도를 수치로 확인하는 큐레이션 분석 리포트와 품목 크롭·행검출 시각 진단 도구 (b8055a2, 86413d7)
- 배포 시 DB 마이그레이션을 자동·멱등 적용 — 스키마 변경이 배포에 함께 반영된다 (c5ae2bf)

### Fixed

- 여러 줄에 걸쳐 적힌 품목의 금액이 누락되던 문제 해소 ([#25](https://github.com/GangsubLim/sjmj-ai/pull/25))
- 금액칸 판독이 비정상 출력으로 무너질 때 한 번 더 시도해 인식률 회복 ([#24](https://github.com/GangsubLim/sjmj-ai/pull/24))

### Security

- 업로드 파일 확장자를 허용 목록으로 제한 (b7be85c)

## [v0.3.0] — 2026-07-01

수기 명세서 OCR 학습 데이터를 사람이 검수·정리하는 큐레이션 파이프라인을 추가한다 ([#12](https://github.com/GangsubLim/sjmj-ai/pull/12), [#13](https://github.com/GangsubLim/sjmj-ai/pull/13)).

### Added

- OCR 학습 큐레이션 검수 페이지 — `/curation` 큐에서 잡을 골라 행별 인식 결과를 보고 라벨 교정·제외를 즉시 저장한다 ([#13](https://github.com/GangsubLim/sjmj-ai/pull/13))
- OCR 큐레이션 백엔드 — training_pairs read-model과 큐레이션 API 6종(잡 목록·상세·pair 수정·검수 완료·이미지) ([#12](https://github.com/GangsubLim/sjmj-ai/pull/12))

### Changed

- 배포 시 ml-worker를 함께 재시작해 ML 코드 수정이 즉시 반영되도록 개선한다 ([#8](https://github.com/GangsubLim/sjmj-ai/pull/8))
- 프론트 API 계층을 nested pagination 단일 경로로 정리한다(legacy 분기 제거) ([#9](https://github.com/GangsubLim/sjmj-ai/pull/9))

## [v0.2.1] — 2026-06-29

OCR로 인식한 공급가액을 실제 원 단위 금액으로 바로잡는다 ([#6](https://github.com/GangsubLim/sjmj-ai/pull/6)).

### Fixed

- 수기 거래명세서가 천 단위를 생략해 적는 점을 반영해, 인식한 공급가액을 ×1000 보정한 실제 원 단위 금액으로 저장 — 인식에 실패한 행은 합산에서 제외

## [v0.2.0] — 2026-06-29

수기 거래명세서 사진 한 장으로 작성 폼을 자동 채우는 OCR 자동입력 슬라이스를 추가한다 ([#4](https://github.com/GangsubLim/sjmj-ai/pull/4)).

### Added

- 손글씨 거래명세서 사진을 업로드하면 OCR이 품목·공급가를 추론해 작성 폼에 자동 입력하는 기능 제공 — 업로드 → 추론 → 사람 검수 → 확정(거래명세서 생성)까지 한 번에 관통
- 작성 폼에서 인식 품목 top-5 후보를 미리 채우고, 사람이 교정한 결과로 거래명세서를 확정 — 확정 시 초안 대비 교정 내역을 함께 기록
- 업로드된 OCR 잡을 백그라운드에서 폴링·추론하는 ml-worker 서비스(launchd) 추가

## [v0.1.1] — 2026-06-29

SJMJ 업무 AI 자동화 플랫폼 첫 배포 — 수기 거래명세서 OCR 자동입력 모듈의 백엔드·프론트엔드·ML 파이프라인을 macmini 운영 환경에 올린다 ([#1](https://github.com/GangsubLim/sjmj-ai/pull/1)).

### Added

- 거래명세서·거래처·품목·설정·영업사원·매출집계 6개 도메인을 다루는 FastAPI 백엔드 제공 — 기존 PHP(SJMJ-Web) 백엔드를 동형 포팅해 검증 메시지·응답 포맷·부수효과까지 동일하게 보존
- 거래명세서 관리 7개 화면 프론트엔드 제공 (React 19 + Vite + Tailwind)
- 수기 거래명세서 이미지에서 셀 단위로 숫자를 인식하고 산술 검산까지 수행하는 OCR 파이프라인 제공
- 거래명세서 CSV 내보내기(UTF-8 BOM)·복제 기능 제공
- 운영 MySQL 스키마 정본화 및 ML 결과 연동용 마이그레이션 제공
- `vX.Y.Z` 태그 push 시 macmini로 자동 배포되는 CD 파이프라인 구축 — 배포 전 DB 백업, 헬스 체크 실패 시 자동 롤백
- launchd 기반 단일 서비스(:8400) 운영 — 프론트 빌드 산출물을 동일 출처로 서빙
