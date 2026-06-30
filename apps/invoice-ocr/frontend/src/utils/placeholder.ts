import type { SyntheticEvent } from "react";

// 회색 사각형 SVG placeholder를 data URI로 생성한다(외부 자원 없이 즉시 표시).
export function placeholderSvg(width: number, height: number): string {
  return (
    "data:image/svg+xml;utf8," +
    encodeURIComponent(
      `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}">` +
        `<rect width="${width}" height="${height}" fill="#e5e7eb"/></svg>`,
    )
  );
}

// <img onError> 공용 핸들러. 이미 placeholder면 재설정하지 않아 onError 무한 재진입을 막는다.
export function fallbackToPlaceholder(placeholder: string) {
  return (e: SyntheticEvent<HTMLImageElement>): void => {
    const img = e.currentTarget;
    if (img.src !== placeholder) {
      img.src = placeholder;
    }
  };
}
