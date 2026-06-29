// 거래처 자동완성 데이터
export const companySuggestions = ["한국강화"];

// 품목 자동완성 데이터
export const itemSuggestions = [
  "엔진오일",
  "파워오일",
  "파워오일휠터",
  "브레이크오일",
  "브레이크호수",
  "드라이",
  "드라이휠터",
  "라이닝",
  "라이트",
  "에어",
  "타이어",
  "중고타이어",
  "차압센서",
  "항균필터",
  "브러쉬",
  "부동액",
  "번호등",
  "삼.디.스.센",
  "얼라이먼트",
  "볼트",
  "너트",
  "핸들조인트",
  "탑부싱",
  "벨트교환",
  "쇼바",
  "공임",
  "링크대",
  "엔도대",
  "킹핀",
  "베어링",
  "도리까이",
  "앗세이",
  "구리스",
  "구리스리데나",
  "원터치",
  "깔깔이",
  "하부",
];

// 자동완성 필터링 함수
export const getSuggestions = (
  searchText: string,
  suggestions: string[],
): string[] => {
  if (!searchText) return [];

  const lowercasedSearch = searchText.toLowerCase();
  return suggestions.filter((item) =>
    item.toLowerCase().startsWith(lowercasedSearch),
  );
};
