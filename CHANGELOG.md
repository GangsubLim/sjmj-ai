# Changelog

이 프로젝트의 주요 변경 사항을 기록한다. 형식은 [Keep a Changelog](https://keepachangelog.com/ko/1.0.0/),
버전 체계는 [Semantic Versioning](https://semver.org/lang/ko/)을 따른다.

릴리스 항목은 `scripts/release.sh`가 `## [vX.Y.Z] — YYYY-MM-DD` 헤더를 추가하면 my-release 스킬 Step 4에서 본문을 작성한다.

## [v0.14.0] — 2026-08-31

거래명세서와 OCR 잡을 화면에서 서로 이어 보게 하고, 금액 크롭 좌측 경계를 전표마다 실측해 인접 칸 숫자 흡수를 줄인 릴리스 ([#131](https://github.com/GangsubLim/sjmj-ai/pull/131), [#134](https://github.com/GangsubLim/sjmj-ai/pull/134)).

### Added

- 거래명세서 목록 카드에 OCR 잡 번호 배지 노출 — 어떤 명세서가 OCR 유래인지 목록에서 바로 식별 ([#131](https://github.com/GangsubLim/sjmj-ai/pull/131))
- 큐레이션 잡 상세에 '명세서 수정' 바로가기 추가 — 검수 중 발견한 이상을 새 탭에서 곧바로 교정, 잡 간 이동 흐름은 유지 ([#131](https://github.com/GangsubLim/sjmj-ai/pull/131))
- 품목 인코더 재학습용 학습 입력 경로 신설 — 큐레이션 학습쌍 반출(`bank_update export-pairs`)과 학습곡선 실측(`train_contrastive curve`) 가능 ([#135](https://github.com/GangsubLim/sjmj-ai/pull/135))

### Fixed

- 금액 크롭이 단가칸으로 넘쳐 인접 숫자를 흡수하던 문제 완화 — 좌측 경계를 템플릿 고정값 대신 전표별 인쇄 세로선으로 실측 ([#134](https://github.com/GangsubLim/sjmj-ai/pull/134))
- `SJMJ_DATA_DIR` 미설정 시 crop 엔드포인트가 500으로 떨어지던 배포 함정 차단 — backend env 예시에 항목 추가 ([#132](https://github.com/GangsubLim/sjmj-ai/pull/132))
- 큐레이션 기계 해제 경로의 락 순서를 부모→자식으로 정렬해 교착 가능 경로 제거 ([#132](https://github.com/GangsubLim/sjmj-ai/pull/132))
- DL 코너 검출이 삼키던 추론 예외의 타입을 진단에 노출 — 검출 실패와 예외 실패 구분 가능 ([#132](https://github.com/GangsubLim/sjmj-ai/pull/132))

### Changed

- `api-spec.json`의 `info.version`을 릴리스 버전과 동기 — `sync-version.sh`가 VERSION·APP_VERSION과 함께 갱신 ([#132](https://github.com/GangsubLim/sjmj-ai/pull/132))
- ml 의존성 pillow 12.3.0·setuptools 83.0.0 갱신 ([#130](https://github.com/GangsubLim/sjmj-ai/pull/130))
- 재처리·뱅크 갱신 런북의 오독 유발 문안 교정 (8b0a908)

### Removed

- frontend `.env.example`에서 미사용 `VITE_API_MODE` 변수 제거 ([#132](https://github.com/GangsubLim/sjmj-ai/pull/132))

## [v0.13.1] — 2026-08-27

마지막 품목행 금액이 표 하단의 빈 행·합계행까지 삼켜 과대 계상되던 경로 차단, 절단 사실은 화면과 원문에 진단으로 보존 ([#127](https://github.com/GangsubLim/sjmj-ai/pull/127)).

### Fixed

- 금액 OCR이 마지막 품목행에 표 하단 빈 행·합계행을 병합해 금액을 부풀리던 문제 수정 — 잡 27에서 937,000원으로 읽히던 행이 확정값 15,000원으로 교정 ([#127](https://github.com/GangsubLim/sjmj-ai/pull/127))

### Added

- 큐레이션 확정 전 상세에 금액 OCR 원문 노출 — 제안값이 어떤 판독에서 나왔는지 확정 전에 대조 가능 ([#127](https://github.com/GangsubLim/sjmj-ai/pull/127))
- 금액 원문 끝에 `(cont×N 절단)` 접미 표기 — 병합이 몇 행에서 잘렸는지 사후 진단 가능 ([#127](https://github.com/GangsubLim/sjmj-ai/pull/127))

## [v0.13.0] — 2026-08-25

재처리를 되돌릴 수 없게 커밋하기 전에 승계·미결 예상치를 미리 본다 — "돌려봐야 안다"를 없앤 무커밋 드라이런 ([#124](https://github.com/GangsubLim/sjmj-ai/pull/124)).

### Added

- 재처리 드라이런 CLI 추가 — DB·크롭을 건드리지 않고 잡별 승계·미결 예상치를 계산해 전량 커밋 전에 판단한다. 운영과 같은 승계 계획 함수를 쓰므로 예측이 실측을 따라간다 ([#124](https://github.com/GangsubLim/sjmj-ai/pull/124))
- 재처리 런북에 드라이런 절 추가 — 중단 임계·선행조건·재시작 루프를 절차로 고정하고, 파일럿 분할 대신 전량 원칙을 정본으로 세운다 ([#124](https://github.com/GangsubLim/sjmj-ai/pull/124))
- DL 4-코너 검출을 전표 경계 후보 1순위로 통합 — 인접 전표 병합·배경 포함으로 깨지던 유형을 회복한다. 모델 배치 전까지는 비활성이라 이번 릴리스의 인식 결과는 기존 색 경로와 동일하다 ([#117](https://github.com/GangsubLim/sjmj-ai/pull/117))

## [v0.12.0] — 2026-08-11

큐레이션 검수에서 "잡 하나 보고 목록 1페이지로 튕겨 나오는" 왕복을 없앤다 — 목록으로 돌아오면 보던 페이지 그대로이고, 상세에서 목록을 거치지 않고 앞뒤 잡으로 곧장 넘어간다 ([#112](https://github.com/GangsubLim/sjmj-ai/pull/112)).

### Added

- 큐레이션 잡 상세에 이전·다음 잡 이동 버튼 추가 — 목록을 거치지 않고 연속으로 검수한다 ([#112](https://github.com/GangsubLim/sjmj-ai/pull/112))
- 큐레이션 목록의 현재 페이지가 주소에 남는다 — 뒤로가기·새로고침·북마크가 같은 페이지를 복원한다 ([#112](https://github.com/GangsubLim/sjmj-ai/pull/112))

### Changed

- 검수 완료 후 목록으로 튕겨 나가지 않고 상세에 머무른다 — 완료 표시는 즉시 반영된다 ([#112](https://github.com/GangsubLim/sjmj-ai/pull/112))
- 잡을 여러 번 넘긴 뒤에도 뒤로가기 한 번이면 보던 목록 페이지로 돌아온다 ([#112](https://github.com/GangsubLim/sjmj-ai/pull/112))

### Fixed

- 주소창의 페이지 번호가 비정상적으로 크거나 잘못된 값일 때 목록 조회가 서버 오류로 끝나며 내부 정보를 노출하던 경로를 막는다 ([#112](https://github.com/GangsubLim/sjmj-ai/pull/112))

## [v0.11.0] — 2026-08-11

한 번 짝을 잃은 학습쌍이 재처리를 아무리 돌려도 되살아나지 못하던 막다른 길을 없앤다 — 확정 시점의 금액 초안을 따로 보관해 두므로, 다음 재처리에서 새로 잘린 그림과 다시 이어붙는다 ([#107](https://github.com/GangsubLim/sjmj-ai/pull/107)).

### Added

- 확정 시점의 금액 초안을 학습쌍에 함께 저장해, 짝을 잃은 쌍도 다음 재처리에서 회수되도록 한다 ([#107](https://github.com/GangsubLim/sjmj-ai/pull/107))
- 기존 학습쌍 전량에 초안 값을 소급 적재한다 — 이번 배포 이전에 확정된 쌍도 같은 회수 경로를 탄다 ([#107](https://github.com/GangsubLim/sjmj-ai/pull/107))

### Fixed

- 인식이 뱉은 비정상적으로 긴 숫자가 저장 단계에서 거래명세서 전체를 저장 실패로 만들 수 있던 경로를 막는다 ([#107](https://github.com/GangsubLim/sjmj-ai/pull/107))
- 재처리 런북의 첫 단계인 DB 백업이 원격 접속 환경에서 조용히 실패하던 문제 — 실패를 지나치지 않도록 확인 절차도 함께 넣었다 (007f0e7)

### Changed

- 재처리 런북에 배포 직후 재백필 절차를 추가 — 배포와 서버 재시작 사이에 확정된 쌍도 빠짐없이 초안을 갖게 한다 ([#107](https://github.com/GangsubLim/sjmj-ai/pull/107))

## [v0.10.1] — 2026-08-07

인식 모델이 무너진 채로 뱉은 헛소리가 "정상 처리된 빈칸"으로 굳어 학습쌍을 대량으로 날리던 경로를 막는다. 이제 그런 잡은 커밋되지 않고 되돌려지며, 워커가 스스로 다시 살아나 같은 잡을 정상 처리한다 ([#102](https://github.com/GangsubLim/sjmj-ai/pull/102)).

### Fixed

- 금액 인식 모델이 붕괴해 의미 없는 문자만 반복해 뱉으면, 그 결과를 저장하지 않고 잡을 처리 대기로 되돌린다 — 확정된 학습쌍이 짝을 잃고 사라지던 회귀를 막는다 ([#102](https://github.com/GangsubLim/sjmj-ai/pull/102))
- 붕괴를 감지한 워커가 스스로 종료해 자동 재기동되므로, 재처리 배치 중 사람이 주기적으로 워커를 껐다 켜지 않아도 된다 ([#102](https://github.com/GangsubLim/sjmj-ai/pull/102))
- 재기동 직후 첫 잡이 또 붕괴하면 그 잡만 되돌려 놓고 은퇴시켜, 한 잡이 워커를 무한히 껐다 켜는 crash loop를 만들지 않는다 ([#102](https://github.com/GangsubLim/sjmj-ai/pull/102))

### Changed

- 재처리 런북에 붕괴 로그 시그니처와 상태 전이·복구 절차, 종결 조건을 추가 ([#102](https://github.com/GangsubLim/sjmj-ai/pull/102))

## [v0.10.0] — 2026-08-07

이미 검수를 끝낸 잡도 지금의 인식 엔진으로 다시 돌릴 수 있다 — 사람이 확정한 라벨은 그대로 이어받으므로, 엔진이 좋아진 만큼이 과거 데이터에도 반영된다 ([#96](https://github.com/GangsubLim/sjmj-ai/pull/96)).

### Added

- 확정된 OCR 잡을 현재 엔진으로 다시 인식하고, 사람이 확정한 금액 라벨을 새로 잘린 그림에 이어붙인다 ([#96](https://github.com/GangsubLim/sjmj-ai/pull/96))
- 잘린 위치가 그대로인 잡도 강제로 다시 학습 뱅크에 넣어, 뱅크만 갱신된 개선분을 받을 수 있다 ([#96](https://github.com/GangsubLim/sjmj-ai/pull/96))
- 재처리로 짝을 잃은 쌍이 생긴 잡만 검수 상태를 풀어 재확인을 요구한다 — 짝이 온전한 잡은 검수 완료로 남는다 ([#96](https://github.com/GangsubLim/sjmj-ai/pull/96))
- 재처리 전에 열어둔 검수 화면이 옛 그림을 근거로 새 쌍을 덮어쓰지 못하도록 막는다 ([#96](https://github.com/GangsubLim/sjmj-ai/pull/96))
- 재처리 운영 절차와 파일럿 배치 권고를 담은 런북 추가 ([#96](https://github.com/GangsubLim/sjmj-ai/pull/96))

### Fixed

- 한국어를 쓰는 ml 도구가 비UTF-8 로케일에서 죽거나 깨진 글자로 굳은 캐시를 남기던 문제 ([#82](https://github.com/GangsubLim/sjmj-ai/pull/82))

## [v0.9.0] — 2026-08-05

검수를 끝낸 잡이라도 학습쌍을 다시 건드리면 확인을 한 번 더 받는다. 인식 정확도 리포트는 "모델이 사람을 얼마나 도왔는지"를 처음으로 수치로 보여준다 ([#79](https://github.com/GangsubLim/sjmj-ai/pull/79), [#77](https://github.com/GangsubLim/sjmj-ai/pull/77), [#75](https://github.com/GangsubLim/sjmj-ai/pull/75)).

### Added

- 검수 완료된 잡의 학습쌍을 수정하면 검수 상태가 자동으로 풀리고 재확인을 요구한다 — 오클릭 한 번이 확인 없이 다음 학습에 반영되던 경로를 막는다 ([#79](https://github.com/GangsubLim/sjmj-ai/pull/79))
- 큐레이션 목록·상세가 "미검수 / 재검수 필요 / 검수됨" 세 상태를 구분해 보여준다 ([#79](https://github.com/GangsubLim/sjmj-ai/pull/79))
- 인식 정확도 리포트에 조작 출처 분포가 추가된다 — 첫 후보가 틀렸어도 사람이 후보 목록에서 골랐다면 모델이 일한 것으로 집계한다 ([#77](https://github.com/GangsubLim/sjmj-ai/pull/77))
- 리포트가 교정 이력을 함께 읽어, 초안 대비 확정본에서 사람이 몇 행을 더하고 뺐는지 관측한다 ([#75](https://github.com/GangsubLim/sjmj-ai/pull/75))

### Changed

- 큐레이션 분석 런북에서 "실패"가 뜻하는 층위를 쌍 단위와 잡 단위로 분리하고, 리포트가 재지 못하는 범위를 명시한다 ([#71](https://github.com/GangsubLim/sjmj-ai/issues/71))
- 런북의 배포 완료된 일회성 안내를 걷어내고 누락 버킷 2종·개선 액션 표를 현행화 (e6780ba)

## [v0.8.0] — 2026-08-04

검수를 시작하기 전 단계의 인식 잡을 목록으로 훑어보고, 어디까지 잘 처리됐는지 확인할 수 있다 ([#68](https://github.com/GangsubLim/sjmj-ai/pull/68)).

### Added

- 아직 확정하지 않은 인식 잡을 한 화면에서 모아 보고, 각 잡이 어느 단계에서 멈췄는지 배지로 확인한다 ([#68](https://github.com/GangsubLim/sjmj-ai/pull/68))
- 개별 잡을 열어 원본·보정 이미지와 인식된 행을 읽기 전용으로 살펴본다 — 이 화면의 어떤 조작도 잡·학습쌍·명세서를 바꾸지 않는다 ([#68](https://github.com/GangsubLim/sjmj-ai/pull/68))

### Changed

- 보정 이미지가 없는 잡에서도 이미지 영역이 빈 화면으로 남지 않고 안내 자리표시를 보여준다 ([#68](https://github.com/GangsubLim/sjmj-ai/pull/68))

## [v0.7.0] — 2026-08-03

멀쩡한 전표가 "정합 실패"로 잘못 강등되던 문제를 회수하고, 새로 등록한 품목 이름이 인식 사전에 자동으로 반영되게 한다 ([#65](https://github.com/GangsubLim/sjmj-ai/pull/65), [#62](https://github.com/GangsubLim/sjmj-ai/pull/62)).

### Added

- 검수에서 확정한 정식 품목명이 자동완성 사전에 그대로 등록돼, 품목 목록과 인식 어휘가 따로 놀지 않는다 ([#62](https://github.com/GangsubLim/sjmj-ai/pull/62))
- 손글씨가 없는 빈 칸을 자동으로 걸러내 학습 대상에서 제외하고, 사람이 뺀 것과 기계가 뺀 것을 구분해 기록한다 ([#59](https://github.com/GangsubLim/sjmj-ai/pull/59))
- 인식 정확도 리포트가 각 결과가 어느 시점의 사전으로 뽑힌 것인지 함께 기록해, 개선 전후를 같은 기준으로 비교한다 ([#58](https://github.com/GangsubLim/sjmj-ai/pull/58))

### Fixed

- 인쇄가 흐리거나 청색이 옅은 정상 전표가 "표 정합 실패"로 잘못 강등되던 문제 해소 — 격자 검출에 2단 폴백을 둔다 ([#65](https://github.com/GangsubLim/sjmj-ai/pull/65))

### Changed

- 처리 관측 용어 4종과 재처리 라벨 승계·품목 어휘 정합 기준을 결정 기록으로 명문화 (fc3e7d0, 6f38779)
- ML 파이프라인 README를 배포 추론 트랙 기준으로 현행화 (1c46946)

### Removed

- 사용하지 않던 SP0 플레이스홀더 디렉터리 `packages/`·`worker/` 제거 (91073b2)

## [v0.6.0] — 2026-07-30

배포하면 열려 있던 화면이 저절로 새 버전으로 바뀌고, 인식 정확도 수치가 실제 실력을 반영하도록 바로잡았다 ([#55](https://github.com/GangsubLim/sjmj-ai/pull/55), [#54](https://github.com/GangsubLim/sjmj-ai/pull/54)).

### Added

- 메뉴를 옮길 때 서버 버전을 확인해, 새 버전이 배포됐으면 화면을 자동으로 새로 불러온다 ([#55](https://github.com/GangsubLim/sjmj-ai/pull/55))
- 인식 정확도 리포트가 "같은 전표 제외" 기준을 함께 보여줘, 같은 전표의 다른 칸이 답을 알려주는 과대평가를 걸러낸다 ([#54](https://github.com/GangsubLim/sjmj-ai/pull/54))

### Fixed

- 배포 직후 열려 있던 탭에서 아직 안 눌러본 메뉴나 PDF 저장이 실패하던 문제 해소 — 이전 버전 파일을 서버에 남긴다 ([#55](https://github.com/GangsubLim/sjmj-ai/pull/55))
- 화면 파일이 오래된 상태로 캐시되던 문제 수정 — 화면 틀은 매번 새로 받고, 내용이 고정된 파일만 길게 캐시한다 ([#55](https://github.com/GangsubLim/sjmj-ai/pull/55))

### Changed

- 학습쌍 제외 기준·부트스트랩 세트·회귀 평가셋 용어를 문서에 명문화 (f48db94, ef677fb)

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
