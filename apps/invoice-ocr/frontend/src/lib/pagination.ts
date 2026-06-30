/**
 * 현재 페이지를 중심으로 한 윈도잉된 페이지 번호 목록을 만든다.
 * 전체 페이지가 maxVisible보다 많을 때 일부만 노출해 무한 링크 렌더를 막는다.
 */
export function getVisiblePages(
  current: number,
  total: number,
  maxVisible = 5,
): number[] {
  let start = Math.max(1, current - Math.floor(maxVisible / 2));
  const end = Math.min(total, start + maxVisible - 1);
  start = Math.max(1, end - maxVisible + 1);
  return Array.from({ length: end - start + 1 }, (_, i) => start + i);
}
