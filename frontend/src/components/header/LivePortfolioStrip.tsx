"use client";

import { useEffect, useRef, useState } from "react";
import clsx from "clsx";
import type { LiveSummary, ScannerStatus } from "@/types/trading";

const usd = (v: number | null | undefined, digits = 2) =>
  v == null
    ? "—"
    : v.toLocaleString("en-US", {
        style: "currency",
        currency: "USD",
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      });

const signed = (v: number | null | undefined, suffix = "") =>
  v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}${suffix}`;

/** Parpadeo suave verde/rojo cuando el valor sube o baja. */
function useFlash(value: number | null | undefined) {
  const prev = useRef<number | null | undefined>(value);
  const [cls, setCls] = useState("");
  useEffect(() => {
    if (value != null && prev.current != null && value !== prev.current) {
      setCls(value > prev.current ? "flash-up" : "flash-down");
      const t = setTimeout(() => setCls(""), 950);
      prev.current = value;
      return () => clearTimeout(t);
    }
    prev.current = value;
  }, [value]);
  return cls;
}

function Kpi({
  label,
  value,
  sub,
  tone,
  flashValue,
  big,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "buy" | "sell" | "neutral";
  flashValue?: number | null;
  big?: boolean;
}) {
  const flash = useFlash(flashValue);
  return (
    <div className={clsx("flex flex-col rounded-lg px-2.5 py-1", flash)}>
      <span className="text-[10px] font-semibold uppercase tracking-wider text-ink-soft">{label}</span>
      <span
        className={clsx(
          "num font-semibold leading-tight",
          big ? "text-xl" : "text-base",
          tone === "buy" && "text-buy",
          tone === "sell" && "text-sell",
        )}
      >
        {value}
      </span>
      {sub && <span className="num text-[10px] text-ink-soft">{sub}</span>}
    </div>
  );
}

export function LivePortfolioStrip({
  summary,
  status,
  error,
}: {
  summary: LiveSummary | undefined;
  status: ScannerStatus | undefined;
  error: boolean;
}) {
  const pnl = summary?.daily_pnl_usd ?? null;
  const pnlTone = pnl == null || pnl === 0 ? "neutral" : pnl > 0 ? "buy" : "sell";
  const dd = status?.current_session_drawdown_pct ?? summary?.current_drawdown_pct ?? 0;
  const limit = status?.max_session_drawdown_pct ?? 4;
  const ddUsed = Math.min(100, Math.max(0, (Math.abs(Math.min(dd, 0)) / limit) * 100));

  return (
    <div className="flex items-center gap-1 divide-x divide-line">
      <Kpi
        big
        label="Valor de cartera"
        value={usd(summary?.effective_portfolio_value)}
        sub={error ? "sin conexión" : summary?.alpaca_mode === "LIVE_PAPER" ? "Alpaca Paper · base virtual" : summary?.alpaca_mode}
        flashValue={summary?.effective_portfolio_value}
      />
      <Kpi label="Buying power" value={usd(summary?.buying_power, 0)} sub={`efectivo ${usd(summary?.cash, 0)}`} />
      <Kpi
        label="PnL diario"
        value={usd(pnl)}
        sub={signed(summary?.daily_pnl_pct, "%")}
        tone={pnlTone}
        flashValue={pnl}
      />
      <Kpi
        label="Riesgo abierto"
        value={`${(summary?.open_risk_exposure_pct ?? 0).toFixed(2)}%`}
        sub={usd(summary?.open_risk_exposure_usd)}
      />
      <div className="flex flex-col px-3 py-1">
        <span className="text-[10px] font-semibold uppercase tracking-wider text-ink-soft">
          Cortafuegos −{limit}%
        </span>
        <div className="mt-1 h-1.5 w-28 overflow-hidden rounded-full bg-slate-100">
          <div
            className={clsx("h-full rounded-full transition-all", ddUsed > 66 ? "bg-sell" : "bg-scan")}
            style={{ width: `${ddUsed}%` }}
          />
        </div>
        <span className="num mt-0.5 text-[10px] text-ink-soft">
          suelo {usd(summary?.hard_stop_4pct_price, 0)} · {dd.toFixed(2)}%
        </span>
      </div>
    </div>
  );
}
