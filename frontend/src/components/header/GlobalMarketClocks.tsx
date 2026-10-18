"use client";

import clsx from "clsx";
import { EXCHANGES, formatCountdown, getExchangeState } from "@/lib/marketHours";
import { useNow } from "@/hooks/useNow";

export function GlobalMarketClocks() {
  const now = useNow();
  return (
    <div className="flex items-stretch gap-2">
      {EXCHANGES.map((ex) => {
        const st = getExchangeState(ex, now);
        return (
          <div
            key={ex.id}
            className={clsx(
              "flex min-w-[148px] flex-col rounded-lg border px-2.5 py-1.5",
              st.isOpen ? "border-emerald-200 bg-buy-bg" : "border-line bg-slate-50",
            )}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-[10px] font-semibold uppercase tracking-wide text-ink-soft">
                {ex.label}
              </span>
              <span className="num text-[11px] font-medium text-ink">{st.localTime.slice(0, 5)}</span>
            </div>
            <span className={clsx("text-[11px] font-semibold", st.isOpen ? "text-buy" : "text-sell")}>
              {st.isOpen ? "🟢 ABIERTO" : "🔴 CERRADO"}
              <span className="num ml-1 font-medium text-ink-soft">
                {st.isOpen ? "· cierra en " : "· abre en "}
                {formatCountdown(st.secondsToChange)}
              </span>
            </span>
          </div>
        );
      })}
    </div>
  );
}
