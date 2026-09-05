---
name: sjmj-invoice-entry
description: "텔레그램으로 받은 수기 거래명세서 사진을 읽어 sjmj-ai에 거래명세서를 생성하고 수정 링크을 회신. 거래명세서·명세서·영수증 사진이 오면 사용"
version: 0.1.0
author: sjmj-ai
license: MIT
platforms: [macos]
metadata:
  hermes:
    tags: [sjmj, invoice, ocr, telegram]
---

# sjmj 거래명세서 입력

사용자가 텔레그램으로 보낸 수기 거래명세서 사진 1장을 읽어 sjmj-ai 백엔드(`http://127.0.0.1:8400`)에 거래명세서 1건을 생성하고, 사진·초안을 보관한 뒤, 요약과 수정 링크를 회신하는 절차

## 단위와 트리거

- 사진 1장 = 거래명세서 1건 = `POST /api/invoices` 1회
- 사진 여러 장이 한 메시지에 오면 장마다 독립 처리, 실패한 장은 건너뛰고 사유만 회신
- 캡션은 힌트(발행일·거래처 지정 등)로만 사용, 사진 내용과 충돌하면 캡션 우선
- 거래명세서로 보이지 않는 사진은 생성하지 않고 "거래명세서로 보이지 않음" 한 줄 회신
- 인바운드 메시지의 `[Image attached at: <경로>]`가 원본 파일 경로 — 3단계에서 그대로 사용. 이 노트가 없으면 `ls -t ~/.hermes/cache/images | head -1`의 파일을 사용

## 1단계 — 판독

사진에서 읽을 것

| 필드 | 규칙 |
| --- | --- |
| `issue_date` | `YYYY-MM-DD`, 사진에 없으면 오늘 |
| `recipient` | 수신처(거래처명), 100자 이내 |
| `recipient2` | 두 번째 수신처 표기가 있을 때만 |
| `vehicle_no` | 차량번호가 있을 때만 |
| `memo` | 비고란이 있을 때만 |
| `items[]` | 품목 행 — `name`(200자) · `quantity`(정수) · `unit` · `unit_price`(정수) · `deduction`(차감 행이면 true) |

도메인 규칙 2종

- **약식 분해**: 품목명은 첫 줄에만 쓰고 아래 줄은 수량·단가·금액만 이어지는 관행 — 아래 줄(연속행)은 품목이 아니므로 위 품목에 금액을 합산해 한 항목으로 만듦. 수량·단가가 줄마다 다르면 합산 금액을 `unit_price`, `quantity`=1로 기재
- **빈 줄**: 품목칸과 금액칸이 모두 빈 줄은 항목 아님

## 2단계 — 이름 정규화

- `GET http://127.0.0.1:8400/api/companies?q=<수신처 앞 2~3자>` → 응답 `data[].company_name` 중 손글씨와 사실상 같은 것이 있으면 그 문자열 사용
- 품목마다 `GET http://127.0.0.1:8400/api/items?q=<품목명 앞 2~3자>` → `data[].item_name` 중 사실상 같은 것이 있으면 그 문자열 사용, `unit` 판독 불가 시 그 항목의 `default_unit`, 그것도 없으면 `EA`
- 없으면 읽은 대로 기재 — **`POST /api/companies`·`POST /api/items` 호출 금지**(자동완성 사전은 마스터가 아님)
- 기존 이름으로 맞춘 경우 회신 ⚠️ 줄에 "기존 이름 X로 맞춤" 표기

## 3단계 — 계산(합계는 UI(프론트)가 계산해 보내는 값 — API를 직접 호출하는 이 스킬이 프론트 대신 계산)

```
supply = unit_price * quantity
vat    = round(supply * 0.1)      # 사사오입, 정수
total  = supply + vat
deduction 행은 supply/vat/total 모두 음수
total_supply = Σ supply
total_vat    = Σ vat
grand_total  = Σ total
```

사진에 적힌 합계와 계산값이 다르면 계산값을 저장하고 회신 ⚠️ 줄에 "사진 합계 X ≠ 계산 Y" 표기

## 4단계 — 저장

```bash
# 1) 생성
curl -s -X POST http://127.0.0.1:8400/api/invoices \
  -H 'Content-Type: application/json' \
  -d @/tmp/sjmj-draft.json
# 응답 {"success":true,"data":{"id":123,...}} → ID=123. success가 false면 error.message를 회신하고 종료(재시도 1회만)

# 2) 보관 — 캐시는 24시간 뒤 삭제되므로 생성 직후 즉시
mkdir -p /Users/submini/sjmj-ai-data/agent_uploads
cp "<Image attached at 경로>" /Users/submini/sjmj-ai-data/agent_uploads/${ID}.jpg   # 원본이 png면 .png
cp /tmp/sjmj-draft.json /Users/submini/sjmj-ai-data/agent_uploads/${ID}.draft.json
```

`/tmp/sjmj-draft.json` 형식(요청 바디 그대로):

```json
{
  "issue_date": "2026-09-05",
  "recipient": "○○상사",
  "document_title": "거 래 명 세 서",
  "show_stamp": true,
  "total_supply": 150000,
  "total_vat": 15000,
  "grand_total": 165000,
  "items": [
    {"name": "각파이프 50x50", "quantity": 10, "unit": "EA", "unit_price": 15000,
     "supply": 150000, "vat": 15000, "total": 165000, "deduction": false}
  ]
}
```

`recipient2`·`vehicle_no`·`memo`는 값이 있을 때만 키를 넣음. 보관(2)이 실패해도 생성은 유효 — 회신에 "사진 보관 실패" 표기하고 링크는 정상 회신

## 5단계 — 회신(텍스트 1건)

```
✅ 거래명세서 #{ID} 등록
수신처 {recipient} · {issue_date} · 품목 {n}건 · 합계 {grand_total:,}원
확인·수정: https://macmini.tail99e9f1.ts.net:8443/edit/{ID}
⚠️ 불확실: {판독 확신이 낮은 항목·정규화·합계 불일치 0~3줄, 없으면 이 줄 생략}
```

## 금지

- `PUT /api/invoices/*`, `DELETE /api/invoices/*`, `POST /api/invoices/{id}/duplicate` 호출 금지 — 수정은 사람이 링크에서
- `POST /api/ocr/jobs` 호출 금지 — ML 경로 오염 방지
- 사진 1장당 invoice 1건, POST 재시도는 비 2xx일 때 1회만
- 사진 내용을 묻는 되질문 금지 — 불확실은 ⚠️ 줄로 표기하고 생성은 진행
