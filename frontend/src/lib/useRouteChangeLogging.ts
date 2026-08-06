import { useEffect, useRef } from "react";
import { useLocation } from "react-router-dom";
import { formatDuration, logger } from "./logger";

/**
 * Her rota (route) degisiminde, yeni sayfanin render edilip DOM'a
 * commit edilmesine kadar gecen sureyi `page` kategorisinde loglar.
 *
 * Zamanlayici, `location.pathname` degistigi anda (render fazinda, henuz
 * bir efekt calismadan) baslatilir - boylece navigasyon ile yeni sayfanin
 * ekrana yansimasi arasindaki gecikme olculur. Bu, `App` icinde TEK bir
 * yerde cagrilir; her sayfa bileseninin kendi baslangic/bitis logunu
 * yazmasina gerek kalmaz.
 */
export function useRouteChangeLogging(): void {
  const location = useLocation();
  const previousPathRef = useRef<string | null>(null);
  const startRef = useRef<number>(performance.now());

  if (previousPathRef.current !== location.pathname) {
    previousPathRef.current = location.pathname;
    startRef.current = performance.now();
  }

  useEffect(() => {
    const elapsed = performance.now() - startRef.current;
    logger.info("page", `page load (${location.pathname}) took ${formatDuration(elapsed)}`);
  }, [location.pathname]);
}
