# 폰트 파일 가이드

## 현재 적용된 폰트

### Batang (명세서 전용)
✅ **설치된 폰트:**
- Batang.ttf (20MB) - 모든 가중치 지원

### KoPubWorldDotum (일반 UI)
✅ **설치된 폰트:**
- KoPubWorld-Dotum-Light.ttf (5MB)
- KoPubWorld-Dotum-Medium.ttf (5MB) 
- KoPubWorld-Dotum-Bold.ttf (5MB)

**폰트 위치:** 
- 원본: `/public/fonts/`
- 빌드용: `/src/assets/fonts/` (자동 복사됨)

## 폰트 최적화

현재 TTF 형식으로 제공되는 폰트를 WOFF2로 변환하면 파일 크기를 대폭 줄일 수 있습니다.

### 자동 최적화 스크립트 사용법:

1. **ttf2woff2 설치:**
   ```bash
   npm install -g ttf2woff2
   ```

2. **폰트 최적화 실행:**
   ```bash
   cd frontend
   node scripts/optimize-fonts.js
   ```

### 수동 변환 (선택사항):
```bash
# Light 폰트 변환
ttf2woff2 "KoPubWorld Batang Light.ttf" "KoPubWorld Batang Light.woff2"

# Medium 폰트 변환  
ttf2woff2 "KoPubWorld Batang Medium.ttf" "KoPubWorld Batang Medium.woff2"

# Bold 폰트 변환
ttf2woff2 "KoPubWorld Batang Bold.ttf" "KoPubWorld Batang Bold.woff2"
```

## 폰트 적용 현황

### InvoicePreview (거래명세서)
- **주 폰트**: 'KoPubWorldBatang' → 'Batang' (명조체)
- **fallback**: '바탕', serif
- **용도**: 공식 문서 인쇄용
- **파일**: 단일 Batang.ttf (20MB)

### 나머지 모든 UI
- **주 폰트**: 'KoPubWorldDotum' (돋움체)
- **fallback**: system fonts
- **적용 범위**: App.css, InvoiceForm.css, InvoiceHistory.css, ManageList.css, AutocompleteTextField.css

**가중치 지원** (공통):
- Light (300)
- Medium/Normal (400) 
- Bold (700)

## 적용 완료 상태

✅ **폰트 설정 완료:**
- fonts.css에 단일 Batang.ttf/KoPubWorldDotum @font-face 정의
- InvoicePreview.css에 KoPubWorldBatang(→Batang) 폰트 적용
- 모든 기타 CSS에 KoPubWorldDotum 폰트 적용
- index.css에 KoPubWorldDotum을 기본 폰트로 설정
- 빌드 성공 및 모든 폰트 파일 포함 확인
- KoPubWorld-Batang 개별 파일들 제거 완료 (36MB → 20MB)

## 성능 최적화

- `font-display: swap` 적용으로 로딩 중에도 텍스트 표시
- WOFF2 우선 로딩으로 파일 크기 최적화
- TTF fallback으로 브라우저 호환성 보장

## 라이선스

KoPubWorld Batang 폰트는 한국출판인회의 공개 폰트입니다. 
상업적 용도로 무료 사용 가능합니다.
