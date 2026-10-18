"use client";

import React from "react";
import clsx from "clsx";
import { ArrowLeft, Loader2, AlertTriangle, CheckCircle2 } from "lucide-react";
import { CandlestickBracketChart } from "./CandlestickBracketChart";
import { OneClickExecutionCard } from "./OneClickExecutionCard";
import type { CandleResponse, OpportunityItem } from "@/types/trading";

interface InstantActionViewProps {
  ticker: string;
  candleData: CandleResponse | null | undefined;
  isLoadingCandles: boolean;
  opportunity: OpportunityItem | undefined;
  onBackToRadar: () => void;
  onApprove: (ticker: string) => void;
  onDismiss: (ticker: string) => void;
  onRequestAdvice: (ticker: string) => void;
  isApproving: boolean;
  isDismissing: boolean;
}

export function InstantActionView({
  ticker,
  candleData,
  isLoadingCandles,
  opportunity,
  onBackToRadar,
  onApprove,
  onDismiss,
  onRequestAdvice,
  isApproving,
  isDismissing,
}: InstantActionViewProps) {
  const entryPrice = opportunity?.entry_price ?? candleData?.bracket_preview?.entry_price;
  const stopLossPrice =
    opportunity?.order_sizing?.stop_loss_price ?? candleData?.bracket_preview?.stop_loss_price;
  const takeProfitPrice =
    opportunity?.order_sizing?.take_profit_price ?? candleData?.bracket_preview?.take_profit_price;
  const ind = candleData?.indicators;
  const score = opportunity?.quant_score ?? ind?.quant_score ?? 50;
  const fallingKnife = opportunity?.falling_knife ?? ind?.falling_knife ?? false;
  const regime = opportunity?.market_regime ?? ind?.regime ?? "NEUTRAL";

  return (
    <div className="flex h-full flex-col gap-3">
      {/* Botón Volver y Cabecera Táctica del Gráfico */}
      <div className="flex items-center justify-between">
        <button
          onClick={onBackToRadar}
          className="flex items-center gap-1.5 text-xs font-semibold text-ink-soft hover:text-ink transition-colors"
        >
          <ArrowLeft className="h-4 w-4" /> Volver al Radar (14 Activos)
        </button>

        <div className="flex items-center gap-2">
          <span className="text-xs font-bold text-ink">{ticker} · Estructura Corto Plazo</span>
          <span
            className={clsx(
              "num rounded-md px-2 py-0.5 text-[10px] font-bold",
              score >= 70 && "bg-buy-bg text-buy border border-emerald-200",
              score >= 50 && score < 70 && "bg-amber-50 text-amber-700 border border-amber-200",
              score < 50 && "bg-sell-bg text-sell border border-rose-200",
            )}
          >
            Score {score}/100
          </span>
          {fallingKnife && (
            <span className="flex items-center gap-1 rounded-full bg-sell-bg px-2.5 py-0.5 text-[10px] font-bold text-sell border border-rose-200">
              <AlertTriangle className="h-3 w-3" /> ROTURA BAJISTA / CUCHILLO CAYENDO
            </span>
          )}
          {opportunity && !fallingKnife && (
            <span className="animate-pulse rounded-full bg-buy-bg px-2 py-0.5 text-[10px] font-bold text-buy border border-emerald-200">
              ⚡ OPORTUNIDAD ACTIVA
            </span>
          )}
        </div>
      </div>

      {/* Gráfico TradingView Lightweight Charts con EMA20, SMA50 y SMA200 */}
      <div className="flex-1 min-h-[290px] rounded-xl border border-line bg-surface overflow-hidden shadow-sm relative">
        {isLoadingCandles && (
          <div className="absolute inset-0 flex items-center justify-center bg-white/80 z-10">
            <Loader2 className="h-6 w-6 animate-spin text-scan" />
          </div>
        )}
        {candleData?.candles && candleData.candles.length > 0 ? (
          <CandlestickBracketChart
            candles={candleData.candles}
            ema20Series={candleData.ema20_series || []}
            sma50Series={candleData.sma50_series || []}
            smaSeries={candleData.sma200_series || []}
            entryPrice={entryPrice}
            stopLossPrice={stopLossPrice}
            takeProfitPrice={takeProfitPrice}
          />
        ) : (
          <div className="flex h-full items-center justify-center text-xs text-ink-soft">
            Cargando velas históricas de {ticker}...
          </div>
        )}
      </div>

      {/* Tarjeta Ejecutiva de 1 Clic con IA Gemini o Diagnóstico Experto de Inspección */}
      {opportunity ? (
        <OneClickExecutionCard
          opp={opportunity}
          onApprove={onApprove}
          onDismiss={onDismiss}
          onRequestAdvice={onRequestAdvice}
          isApproving={isApproving}
          isDismissing={isDismissing}
        />
      ) : (
        <div
          className={clsx(
            "rounded-xl border p-3 text-xs flex flex-col gap-2",
            fallingKnife || regime.includes("BEARISH")
              ? "border-rose-200 bg-rose-50/50 text-ink"
              : "border-line bg-surface text-ink-soft",
          )}
        >
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              {fallingKnife || regime.includes("BEARISH") ? (
                <AlertTriangle className="h-4 w-4 text-sell shrink-0" />
              ) : (
                <CheckCircle2 className="h-4 w-4 text-scan shrink-0" />
              )}
              <span className="font-bold text-ink">
                Diagnóstico Experto Corto Plazo ({regime}):
              </span>
              <span className="text-ink">
                {ind?.expert_summary ||
                  `Entrada ref $${entryPrice?.toFixed(2)} · SL $${stopLossPrice?.toFixed(2)} · TP $${takeProfitPrice?.toFixed(2)}`}
              </span>
            </div>
            <span
              className={clsx(
                "shrink-0 rounded px-2 py-0.5 text-[10px] font-bold uppercase",
                fallingKnife || regime.includes("BEARISH")
                  ? "bg-sell-bg text-sell border border-rose-200"
                  : "bg-slate-100 text-ink-soft",
              )}
            >
              {fallingKnife
                ? "COMPRA BLOQUEADA (CUCHILLO CAYENDO)"
                : "SIN DISPARO ACTIVO"}
            </span>
          </div>

          {ind?.expert_criteria && ind.expert_criteria.length > 0 && (
            <div className="grid grid-cols-2 gap-1.5 pt-1 text-[11px] md:grid-cols-3">
              {ind.expert_criteria.map((c, idx) => (
                <div
                  key={idx}
                  className={clsx(
                    "flex items-start gap-1.5 rounded border px-2 py-1",
                    c.passed
                      ? "border-emerald-200/80 bg-emerald-50/40 text-ink"
                      : "border-rose-200/80 bg-white/80 text-sell",
                  )}
                >
                  <span className="font-bold">{c.passed ? "✓" : "✗"}</span>
                  <div className="flex flex-col leading-tight">
                    <span className="font-semibold text-[10px]">{c.label}</span>
                    <span className="text-[10px] opacity-85">{c.detail}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
