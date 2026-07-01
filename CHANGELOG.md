# Changelog

이 프로젝트의 주요 변경 사항을 기록한다. 형식은 [Keep a Changelog](https://keepachangelog.com/ko/1.0.0/),
버전 체계는 [Semantic Versioning](https://semver.org/lang/ko/)을 따른다.

릴리스 항목은 `scripts/release.sh`가 `## [vX.Y.Z] — YYYY-MM-DD` 헤더를 추가하면 my-release 스킬 Step 4에서 본문을 작성한다.

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
