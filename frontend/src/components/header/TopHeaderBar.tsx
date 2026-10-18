"use client";

import { Activity, ShieldAlert, ShieldCheck, AlertTriangle, WifiOff } from "lucide-react";
import clsx from "clsx";
import { EngineSessionController } from "./EngineSessionController";
import { GlobalMarketClocks } from "./GlobalMarketClocks";
import { LivePortfolioStrip } from "./LivePortfolioStrip";
import { useLiveSummary, useScannerStatus } from "@/hooks/useTradingData";

export function TopHeaderBar() {
  const statusQ = useScannerStatus();
  const summaryQ = useLiveSummary();
  const offline = statusQ.isError && summaryQ.isError;
  const status = statusQ.data;
  const macro = status?.macro_regime || summaryQ.data?.macro_regime;

  const isLockdown = macro?.status === "RISK_OFF_LOCKDOWN";
  const isCaution = macro?.status === "CAUTION";
  const spyChg = macro?.spy?.daily_change_pct ?? 0;
  const qqqChg = macro?.qqq?.daily_change_pct ?? 0;
  const breadthPct = macro?.market_breadth?.advancing_pct ?? 50;

  return (
    <header className="flex h-[88px] shrink-0 items-center justify-between gap-3 border-b border-line bg-surface px-4">
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-scan-bg text-scan">
            {offline ? <WifiOff className="h-4 w-4" /> : <Activity className="h-4 w-4" />}
          </div>
          <div className="flex flex-col leading-tight">
            <span className="text-sm font-bold">Quant Center</span>
            <span className="flex items-center gap-1 text-[10px] font-medium text-ink-soft">
              <span
                className={clsx(
                  "h-1.5 w-1.5 rounded-full",
                  offline ? "bg-sell" : status?.is_scanning_now ? "scan-dot bg-scan" : status?.running ? "bg-buy" : "bg-slate-300",
                )}
              />
              {offline ? "motor sin conexión" : status?.running ? `escaneando cada ${status.interval_seconds}s` : "motor detenido"}
            </span>
          </div>
        </div>
        <EngineSessionController status={status} updatedAt={statusQ.dataUpdatedAt} />

        {macro && (
          <div
            title={macro.reason}
            className={clsx(
              "hidden xl:flex flex-col justify-center rounded-lg border px-2.5 py-1 text-[10px] transition-colors",
              isLockdown
                ? "border-rose-300 bg-rose-50/90 text-rose-800"
                : isCaution
                  ? "border-amber-300 bg-amber-50/90 text-amber-800"
                  : "border-emerald-200 bg-emerald-50/70 text-emerald-800",
            )}
          >
            <div className="flex items-center gap-1.5 font-bold">
              {isLockdown ? (
                <ShieldAlert className="h-3.5 w-3.5 text-rose-600" />
              ) : isCaution ? (
                <AlertTriangle className="h-3.5 w-3.5 text-amber-600" />
              ) : (
                <ShieldCheck className="h-3.5 w-3.5 text-emerald-600" />
              )}
              <span>
                {isLockdown
                  ? "RÉGIMEN: VETO MACRO"
                  : isCaution
                    ? "RÉGIMEN: PRECAUCIÓN"
                    : "RÉGIMEN: RISK-ON"}
              </span>
              {macro.vetoed_opportunities_count > 0 && (
                <span className="rounded bg-rose-200/80 px-1 py-0.2 text-[9px] font-extrabold text-rose-900">
                  {macro.vetoed_opportunities_count} vetadas
                </span>
              )}
            </div>
            <div className="mt-0.5 flex items-center gap-2 font-mono text-[9.5px] opacity-90">
              <span>
                SPY {spyChg >= 0 ? `+${spyChg.toFixed(2)}%` : `${spyChg.toFixed(2)}%`}
              </span>
              <span>·</span>
              <span>
                QQQ {qqqChg >= 0 ? `+${qqqChg.toFixed(2)}%` : `${qqqChg.toFixed(2)}%`}
              </span>
              <span>·</span>
              <span>Amp {breadthPct.toFixed(0)}%</span>
            </div>
          </div>
        )}
      </div>
      <GlobalMarketClocks />
      <LivePortfolioStrip summary={summaryQ.data} status={status} error={summaryQ.isError} />
    </header>
  );
}
