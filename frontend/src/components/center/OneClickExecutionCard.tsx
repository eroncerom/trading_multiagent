"use client";

import React from "react";
import clsx from "clsx";
import { CheckCircle2, XCircle, Sparkles, AlertTriangle, ShieldCheck, ExternalLink, RefreshCw } from "lucide-react";
import type { OpportunityItem } from "@/types/trading";

interface ExecutionCardProps {
  opp: OpportunityItem;
  onApprove: (ticker: string) => void;
  onDismiss: (ticker: string) => void;
  onRequestAdvice: (ticker: string) => void;
  isApproving: boolean;
  isDismissing: boolean;
}

export function OneClickExecutionCard({
  opp,
  onApprove,
  onDismiss,
  onRequestAdvice,
  isApproving,
  isDismissing,
}: ExecutionCardProps) {
  const sizing = opp.order_sizing;
  const metrics = opp.financial_metrics;
  const advice = opp.ai_advice;

  const verdict = advice?.verdict;
  const isApprovedByAi = verdict === "APPROVE";
  const isCaution = verdict === "CAUTION";
  const isReject = verdict === "REJECT";

  return (
    <div className="flex flex-col gap-3 rounded-xl border border-line bg-surface p-4 shadow-sm">
      {/* Cabecera Ticker + Score Experto Corto Plazo + Sizing Rápido */}
      <div className="flex flex-col gap-1.5 border-b border-line pb-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xl font-bold tracking-tight text-ink">{opp.ticker}</span>
            <span className="rounded-md bg-scan-bg px-2 py-0.5 text-xs font-semibold text-scan">
              {opp.market_regime}
            </span>
            {opp.quant_score != null && (
              <span
                className={clsx(
                  "num rounded-md px-2 py-0.5 text-xs font-bold",
                  opp.quant_score >= 70
                    ? "bg-buy-bg text-buy border border-emerald-200"
                    : "bg-amber-50 text-amber-700 border border-amber-200",
                )}
              >
                Score {opp.quant_score}/100
              </span>
            )}
            <span className="num text-xs font-semibold text-ink">
              Entrada ${opp.entry_price.toFixed(2)}
            </span>
            {opp.plus_di != null && opp.minus_di != null && (
              <span className="num text-[11px] text-ink-soft">
                · +DI {opp.plus_di.toFixed(0)} / -DI {opp.minus_di.toFixed(0)} · RSI{" "}
                {(opp.rsi_14 ?? 50).toFixed(0)} · RVOL {(opp.rvol_20 ?? 1).toFixed(2)}x
              </span>
            )}
          </div>

          <div className="flex items-center gap-3">
            <div className="flex flex-col text-right">
              <span className="text-[10px] uppercase font-bold text-ink-soft">Posición</span>
              <span className="num text-xs font-bold text-ink">
                {sizing.shares_to_buy} acc · ${sizing.total_capital_allocated.toFixed(2)} ({sizing.portfolio_allocation_pct}%)
              </span>
            </div>
            <div className="flex flex-col text-right">
              <span className="text-[10px] uppercase font-bold text-ink-soft">Riesgo / Beneficio</span>
              <span className="num text-xs font-semibold text-buy">
                1 : {metrics.net_reward_risk_ratio.toFixed(2)} (+${metrics.expected_net_profit.toFixed(2)})
              </span>
            </div>
          </div>
        </div>
        {opp.expert_summary && (
          <p className="text-[11px] font-medium text-ink-soft">{opp.expert_summary}</p>
        )}
      </div>

      {/* Dictamen del Asesor IA Gemini */}
      <div className="flex flex-col gap-1.5 rounded-lg border border-line/60 bg-slate-50/70 p-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-1.5">
            <Sparkles className="h-4 w-4 text-scan" />
            <span className="text-xs font-bold text-ink">Asesor Financiero IA (Gemini 2.5 Flash)</span>
            {advice?.status === "DONE" && (
              <span
                className={clsx(
                  "rounded-full px-2 py-0.5 text-[10px] font-bold uppercase",
                  isApprovedByAi && "bg-buy-bg text-buy border border-emerald-200",
                  isCaution && "bg-warn-bg text-warn border border-amber-200",
                  isReject && "bg-sell-bg text-sell border border-rose-200"
                )}
              >
                {advice.verdict} · {advice.confidence}% Confianza
              </span>
            )}
            {advice?.status === "PENDING" && (
              <span className="flex items-center gap-1 text-[11px] text-scan animate-pulse font-medium">
                <RefreshCw className="h-3 w-3 animate-spin" /> Analizando noticias y contexto...
              </span>
            )}
          </div>

          <button
            onClick={() => onRequestAdvice(opp.ticker)}
            className="flex items-center gap-1 text-[11px] font-medium text-scan hover:underline"
          >
            <RefreshCw className="h-3 w-3" /> Reanalizar
          </button>
        </div>

        {advice?.status === "DONE" && (
          <div className="flex flex-col gap-1 text-xs">
            <p className="font-semibold text-ink leading-snug">{advice.headline}</p>
            {advice.rationale && advice.rationale.length > 0 && (
              <ul className="list-inside list-disc text-ink-soft space-y-0.5 text-[11px]">
                {advice.rationale.map((r, i) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>
            )}
            {advice.key_risks && advice.key_risks.length > 0 && (
              <div className="mt-1 flex items-start gap-1 rounded bg-rose-50/60 p-1.5 text-[11px] text-sell">
                <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
                <span><strong>Riesgos detectados:</strong> {advice.key_risks.join(" · ")}</span>
              </div>
            )}
            {advice.sources && advice.sources.length > 0 && (
              <div className="mt-0.5 flex flex-wrap gap-2 text-[10px] text-ink-soft">
                <span className="font-semibold">Fuentes:</span>
                {advice.sources.slice(0, 3).map((s, idx) => (
                  <a
                    key={idx}
                    href={s.uri}
                    target="_blank"
                    rel="noreferrer"
                    className="flex items-center gap-0.5 text-scan hover:underline"
                  >
                    {s.title || "Noticia"} <ExternalLink className="h-2.5 w-2.5" />
                  </a>
                ))}
              </div>
            )}
          </div>
        )}

        {(!advice || advice.status === "UNAVAILABLE") && (
          <div className="text-xs text-ink-soft">
            {opp.ai_thesis?.lines ? (
              <div className="space-y-0.5 text-[11px]">
                {opp.ai_thesis.lines.map((l, i) => (
                  <p key={i}>• {l}</p>
                ))}
              </div>
            ) : (
              <p>Esperando análisis cualitativo...</p>
            )}
          </div>
        )}
      </div>

      {/* Sizing y Botonera 1-Clic (< 3 Segundos) */}
      <div className="flex items-center justify-between gap-3 pt-1">
        <div className="flex items-center gap-4 text-xs">
          <div className="flex flex-col">
            <span className="text-[10px] uppercase font-bold text-ink-soft">Stop Loss (-1.5xATR)</span>
            <span className="num font-bold text-sell">
              ${sizing.stop_loss_price.toFixed(2)} (-${metrics.max_loss_with_commissions.toFixed(2)})
            </span>
          </div>
          <div className="flex flex-col">
            <span className="text-[10px] uppercase font-bold text-ink-soft">Take Profit (+2R)</span>
            <span className="num font-bold text-buy">
              ${sizing.take_profit_price.toFixed(2)} (+${metrics.expected_net_profit.toFixed(2)})
            </span>
          </div>
          <div className="flex flex-col">
            <span className="text-[10px] uppercase font-bold text-ink-soft">Comisión Est.</span>
            <span className="num font-medium text-ink-soft">
              ${metrics.round_trip_commission.toFixed(2)} ({metrics.commission_to_gross_profit_pct.toFixed(1)}%)
            </span>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => onDismiss(opp.ticker)}
            disabled={isDismissing || isApproving}
            className="flex h-10 items-center gap-1.5 rounded-lg border border-line bg-white px-3 text-xs font-semibold text-ink-soft transition-colors hover:bg-slate-50 hover:text-sell disabled:opacity-50"
          >
            <XCircle className="h-4 w-4" /> Descartar
          </button>

          <button
            onClick={() => onApprove(opp.ticker)}
            disabled={isApproving || isDismissing}
            className="flex h-10 items-center gap-2 rounded-lg bg-buy-bg px-5 text-sm font-bold text-buy ring-1 ring-emerald-200 transition-all hover:bg-emerald-100 hover:shadow-sm active:scale-95 disabled:opacity-50"
          >
            <CheckCircle2 className="h-4 w-4" /> EJECUTAR ORDEN BRACKET
          </button>
        </div>
      </div>
    </div>
  );
}
