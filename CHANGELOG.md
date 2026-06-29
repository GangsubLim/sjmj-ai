# Changelog

이 프로젝트의 주요 변경 사항을 기록한다. 형식은 [Keep a Changelog](https://keepachangelog.com/ko/1.0.0/),
버전 체계는 [Semantic Versioning](https://semver.org/lang/ko/)을 따른다.

릴리스 항목은 `scripts/release.sh`가 `## [vX.Y.Z] — YYYY-MM-DD` 헤더를 추가하면 my-release 스킬 Step 4에서 본문을 작성한다.

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
