import { useEffect, useRef } from "react";
import { useLocation } from "react-router-dom";
import { fetchServerVersion } from "@/services/api";

/**
 * 라우트 전환 시 서버 배포 버전을 1회 확인해, 다르면 현재 화면을 리로드한다.
 *
 * SPA라 메뉴 이동은 서버에 문서를 요청하지 않으므로 배포된 새 버전이 열린 탭에
 * 반영되지 않는다. effect가 전환 "후"에 돌기 때문에 이미 목적지 경로이고,
 * reload()만으로 목적지가 유지된다.
 *
 * 게이트는 불리언 마운트 플래그가 아니라 "마지막으로 확인한 경로"를 ref에 담아
 * 비교한다. 초기값이 현재 경로라서 최초 마운트는 건너뛰고, effect 재실행이 멱등해져
 * StrictMode(dev)의 mount → cleanup → mount 이중 실행에서도 같은 경로를 두 번
 * 확인하지 않는다 — 프로덕션·dev 양쪽에서 "로드 → 즉시 리로드"가 차단된다.
 * 단 서버·프론트 버전 스큐가 지속되면 경로가 바뀔 때마다 1회 리로드가 반복된다 —
 * 무한 루프는 아니지만 자기치유도 아니다(배포 창 스큐는 수초, 재시작으로 해소).
 *
 * reload()는 진행 중인 네트워크 작업(사진 업로드·OCR 잡 제출 등)을 중단시킨다 —
 * 라우트 전환은 in-flight 요청을 취소하지 않지만 리로드는 취소한다. 도달 창이
 * 전환 직후 health 왕복 1회로 좁아 수용한 리스크다.
 *
 * 미저장 입력 보호(dirty 판정)는 두지 않는다(확정 설계). 라우트 전환이 이미 로컬
 * useState 폼 값을 버리기 때문이다(useBlocker 부재). 단 reload()는 라우트 전환과
 * 달리 beforeunload 대상이므로, 목적지에서 입력을 시작한 직후 리로드가 겹치면
 * 브라우저 네이티브 이탈 확인창이 뜰 수 있다(도달 창 = health 왕복 1회, 수용).
 */
export function useVersionReload(): void {
  const { pathname } = useLocation();
  const lastCheckedPathRef = useRef(pathname);

  useEffect(() => {
    if (lastCheckedPathRef.current === pathname) {
      return;
    }
    lastCheckedPathRef.current = pathname;

    let isStale = false;
    void (async () => {
      try {
        const serverVersion = await fetchServerVersion();
        if (!isStale && serverVersion !== __APP_VERSION__) {
          window.location.reload();
        }
      } catch (error) {
        // 화면 전환은 막지 않되(오프라인·일시 장애가 흔한 실패 원인) 조용히 삼키지도
        // 않는다 — define 누락(__APP_VERSION__ ReferenceError)이나 health 응답 계약
        // 변경처럼 기능이 영구히 죽는 원인도 이 catch로 들어오기 때문이다.
        console.warn("배포 버전 확인 실패 — 자동 리로드를 건너뛴다:", error);
      }
    })();

    return () => {
      isStale = true;
    };
  }, [pathname]);
}
