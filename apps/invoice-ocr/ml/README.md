# invoice-ocr / ml (SP1)

수기 거래명세서 숫자 셀 OCR PoC. 오프라인 배치 CLI.

## 실행

```bash
cd apps/invoice-ocr/ml
uv sync                  # 코어(경량): pillow + dev pytest
cp .env.example .env     # 경로 채우기
# 환경/검출 스파이크 (paddle 필요 → 먼저 extra 설치)
uv sync --extra ml
uv run python -m tools.spike_ppstructure inv_003
# 본 파이프라인 (Task 13에서 완성, paddle 필요)
uv run python -m ocr_poc match-extract     # reviewed_dates.csv 생성
# (사람이 reviewed_dates.csv 검수)
uv run python -m ocr_poc run               # 38장 배치 → report/
```

데이터·DB는 레포 밖(OneDrive/타 레포). 경로는 `.env`로 주입한다.
