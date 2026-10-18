"use client";

import React, { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import {
  AlertOctagon,
  Clock,
  CheckCircle2,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  TrendingUp,
  RefreshCw,
  Wrench,
  Check,
} from "lucide-react";
import { api } from "@/lib/api";
import { useAuditReports } from "@/hooks/useTradingData";
import type { LiveSummary } from "@/types/trading";

interface LedgerProps {
  summary: LiveSummary | undefined;
}

export function TradeLedgerAndAuditPanel({ summary }: LedgerProps) {
  const qc = useQueryClient();
  const [tab, setTab] = useState<"ACTIVE" | "HISTORY" | "AUDIT">("ACTIVE");

  const activePositions = summary?.active_positions || [];
  const closedTrades = summary?.closed_trades || [];
  const auditQ = useAuditReports();
  const reports = auditQ.data?.reports || [];
  const latestReport = reports[0];

  const panicCloseMutation = useMutation({
    mutationFn: (ticker: string) => api.closePosition(ticker),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["live-summary"] });
    },
  });

  const runAuditMutation = useMutation({
    mutationFn: (autoApply: boolean) => api.runCriticalAudit(autoApply),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["audit-reports"] });
      qc.invalidateQueries({ queryKey: ["scanner-status"] });
    },
  });

  const applyFixesMutation = useMutation({
    mutationFn: (auditId: string) => api.applyAuditRecommendations(auditId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["audit-reports"] });
      qc.invalidateQueries({ queryKey: ["scanner-status"] });
    },
  });

  const handlePanicClose = (symbol: string) => {
    if (
      window.confirm(
        `¿Cerrar posición INMEDIATAMENTE en ${symbol} a precio de mercado y cancelar salidas OCO?`
      )
    ) {
      panicCloseMutation.mutate(symbol);
    }
  };

  // Curva de equidad sparkline SVG simple
  const equityPoints = summary?.performance?.equity_curve || [];
  const minEq = equityPoints.length ? Math.min(...equityPoints.map((p) => p.equity)) : 5000;
  const maxEq = equityPoints.length ? Math.max(...equityPoints.map((p) => p.equity)) : 5000;
  const range = maxEq - minEq || 1;
  const sparklineCoords = equityPoints
    .map((p, idx) => {
      const x = (idx / (Math.max(1, equityPoints.length - 1))) * 100;
      const y = 28 - ((p.equity - minEq) / range) * 22;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");

  return (
    <div className="flex h-full flex-col rounded-xl border border-line bg-surface overflow-hidden shadow-sm">
      {/* Pestañas Superiores */}
      <div className="flex items-center border-b border-line bg-slate-50/50 px-2 pt-2 gap-1 text-xs font-semibold">
        <button
          onClick={() => setTab("ACTIVE")}
          className={clsx(
            "flex items-center gap-1.5 rounded-t-lg px-3 py-2 transition-colors border-b-2",
            tab === "ACTIVE"
              ? "border-scan bg-white text-scan font-bold shadow-sm"
              : "border-transparent text-ink-soft hover:text-ink"
          )}
        >
          Posiciones OCO
          <span className="rounded-full bg-slate-100 px-1.5 py-0.2 text-[10px] font-bold text-ink">
            {activePositions.length}
          </span>
        </button>

        <button
          onClick={() => setTab("HISTORY")}
          className={clsx(
            "flex items-center gap-1.5 rounded-t-lg px-3 py-2 transition-colors border-b-2",
            tab === "HISTORY"
              ? "border-scan bg-white text-scan font-bold shadow-sm"
              : "border-transparent text-ink-soft hover:text-ink"
          )}
        >
          Historial Cerrado
          <span className="rounded-full bg-slate-100 px-1.5 py-0.2 text-[10px] font-bold text-ink">
            {closedTrades.length}
          </span>
        </button>

        <button
          onClick={() => setTab("AUDIT")}
          className={clsx(
            "flex items-center gap-1.5 rounded-t-lg px-3 py-2 transition-colors border-b-2",
            tab === "AUDIT"
              ? "border-scan bg-white text-scan font-bold shadow-sm"
              : "border-transparent text-ink-soft hover:text-ink"
          )}
        >
          Auditor Crítico
          {latestReport && (
            <span
              className={clsx(
                "h-2 w-2 rounded-full",
                latestReport.critical_findings?.some((f: any) => f.severity === "CRITICAL")
                  ? "bg-sell"
                  : "bg-buy"
              )}
            />
          )}
        </button>
      </div>

      {/* Contenido de la Pestaña */}
      <div className="flex-1 overflow-auto p-3">
        {tab === "ACTIVE" && (
          activePositions.length > 0 ? (
            <div className="flex flex-col gap-2.5">
              {activePositions.map((p) => {
                const isProfitable = p.unrealized_pl >= 0;
                const progress = p.sl_to_tp_progress_pct ?? 50;

                return (
                  <div
                    key={p.symbol}
                    className="flex flex-col gap-2 rounded-lg border border-line bg-slate-50/50 p-3 text-xs"
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-1.5">
                        <span className="font-bold text-sm text-ink">{p.symbol}</span>
                        <span className="num text-ink-soft">
                          {p.qty} acc @ ${p.avg_entry_price.toFixed(2)}
                        </span>
                      </div>

                      <div className="flex items-center gap-2">
                        <span
                          className={clsx(
                            "num font-bold",
                            isProfitable ? "text-buy" : "text-sell"
                          )}
                        >
                          {isProfitable ? "+" : ""}${p.unrealized_pl.toFixed(2)} ({p.unrealized_plpc.toFixed(2)}%)
                        </span>

                        <button
                          onClick={() => handlePanicClose(p.symbol)}
                          disabled={panicCloseMutation.isPending}
                          className="flex items-center gap-1 rounded bg-rose-50 px-2 py-1 text-[11px] font-bold text-sell ring-1 ring-rose-200 hover:bg-rose-100 transition-colors disabled:opacity-50"
                          title="Cierre de Pánico Inmediato a Mercado"
                        >
                          <AlertOctagon className="h-3 w-3" /> Panic Close
                        </button>
                      </div>
                    </div>

                    {/* Termómetro Horizontal: SL [Rojo] <--- ● ---> TP [Verde] */}
                    <div className="flex flex-col gap-1">
                      <div className="flex items-center justify-between text-[10px] text-ink-soft font-medium">
                        <span className="text-sell font-semibold">SL ${p.stop_loss_price?.toFixed(2) ?? "—"}</span>
                        <span className="num font-bold text-ink">${p.current_price.toFixed(2)}</span>
                        <span className="text-buy font-semibold">TP ${p.take_profit_price?.toFixed(2) ?? "—"}</span>
                      </div>
                      <div className="h-2 w-full overflow-hidden rounded-full bg-slate-200 relative">
                        <div
                          className="h-full bg-gradient-to-r from-rose-400 via-amber-300 to-emerald-400 transition-all rounded-full"
                          style={{ width: `${progress}%` }}
                        />
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="flex h-full flex-col items-center justify-center text-center text-xs text-ink-soft gap-1">
              <CheckCircle2 className="h-8 w-8 text-slate-300" />
              <p className="font-semibold text-ink">Sin posiciones abiertas</p>
              <p className="text-[11px]">
                Las órdenes Bracket ejecutadas aparecerán aquí con seguimiento en vivo de Stop Loss y Take Profit.
              </p>
            </div>
          )
        )}

        {tab === "HISTORY" && (
          closedTrades.length > 0 ? (
            <div className="flex flex-col divide-y divide-line text-xs">
              {closedTrades.map((t) => {
                const isWin = t.realized_pnl_usd >= 0;

                return (
                  <div key={t.trade_id} className="flex items-center justify-between py-2">
                    <div className="flex flex-col">
                      <div className="flex items-center gap-1.5">
                        <span className="font-bold text-ink">{t.ticker}</span>
                        <span className="text-[10px] uppercase rounded bg-slate-100 px-1 text-ink-soft">
                          {t.execution_mode}
                        </span>
                      </div>
                      <span className="num text-[11px] text-ink-soft">
                        ${t.entry_price.toFixed(2)} → ${t.exit_price.toFixed(2)} · {t.exit_reason}
                      </span>
                    </div>

                    <div className="flex flex-col text-right">
                      <span
                        className={clsx(
                          "num font-bold",
                          isWin ? "text-buy" : "text-sell"
                        )}
                      >
                        {isWin ? "+" : ""}${t.realized_pnl_usd.toFixed(2)} ({t.realized_pnl_pct.toFixed(2)}%)
                      </span>
                      {t.duration_seconds && (
                        <span className="num text-[10px] text-ink-soft flex items-center justify-end gap-0.5">
                          <Clock className="h-2.5 w-2.5" />
                          {Math.floor(t.duration_seconds / 60)}m {t.duration_seconds % 60}s
                        </span>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="flex h-full flex-col items-center justify-center text-center text-xs text-ink-soft gap-1">
              <Clock className="h-8 w-8 text-slate-300" />
              <p className="font-semibold text-ink">Sin operaciones cerradas en esta sesión</p>
              <p className="text-[11px]">El historial auditado con PnL neto se registrará aquí automáticamente.</p>
            </div>
          )
        )}

        {tab === "AUDIT" && (
          <div className="flex flex-col gap-3 text-xs">
            {/* Cabecera del Agente Auditor */}
            <div className="flex items-center justify-between border-b border-line pb-2">
              <div className="flex items-center gap-1.5">
                <Sparkles className="h-4 w-4 text-scan" />
                <span className="font-bold text-ink">Agente Auditor Cuantitativo</span>
              </div>
              <button
                onClick={() => runAuditMutation.mutate(false)}
                disabled={runAuditMutation.isPending}
                className="flex items-center gap-1 rounded bg-scan-bg px-2.5 py-1 text-[11px] font-semibold text-scan hover:bg-blue-100 transition-colors disabled:opacity-50"
              >
                <RefreshCw className={clsx("h-3 w-3", runAuditMutation.isPending && "animate-spin")} />
                {runAuditMutation.isPending ? "Auditando..." : "Auditar Ahora"}
              </button>
            </div>

            {latestReport ? (
              <div className="flex flex-col gap-2.5">
                <div className="flex items-center justify-between text-[11px] text-ink-soft">
                  <span>ID: {latestReport.audit_id}</span>
                  <span className="num">
                    {new Date(latestReport.created_at).toLocaleTimeString("es-ES", {
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </span>
                </div>

                {/* Hallazgos Críticos */}
                {latestReport.critical_findings?.map((finding: any, idx: number) => {
                  const isCrit = finding.severity === "CRITICAL";
                  const isWarn = finding.severity === "WARNING";

                  return (
                    <div
                      key={idx}
                      className={clsx(
                        "flex flex-col gap-1 rounded-lg border p-2.5",
                        isCrit && "border-rose-200 bg-rose-50/60 text-sell",
                        isWarn && "border-amber-200 bg-amber-50/60 text-warn",
                        !isCrit && !isWarn && "border-line bg-slate-50 text-ink"
                      )}
                    >
                      <div className="flex items-center gap-1.5 font-bold">
                        <ShieldAlert className="h-3.5 w-3.5 shrink-0" />
                        <span>{finding.title}</span>
                      </div>
                      <p className="text-[11px] text-ink-soft leading-snug">{finding.analysis}</p>
                      <p className="text-[11px] font-semibold text-ink leading-snug">
                        💡 {finding.recommendation}
                      </p>
                    </div>
                  );
                })}

                {/* Propuestas de Corrección de Parámetros */}
                {Object.keys(latestReport.recommended_parameter_adjustments || {}).length > 0 && (
                  <div className="flex flex-col gap-2 rounded-lg border border-line bg-white p-2.5 shadow-sm">
                    <span className="text-[11px] font-bold text-ink flex items-center gap-1">
                      <Wrench className="h-3.5 w-3.5 text-scan" /> Mejoras de Parámetros Recomendadas:
                    </span>
                    <pre className="text-[10px] text-ink-soft bg-slate-50 p-2 rounded num overflow-x-auto">
                      {JSON.stringify(latestReport.recommended_parameter_adjustments, null, 2)}
                    </pre>

                    <button
                      onClick={() => applyFixesMutation.mutate(latestReport.audit_id)}
                      disabled={applyFixesMutation.isPending || latestReport.auto_applied}
                      className="flex items-center justify-center gap-1.5 rounded-lg bg-buy-bg py-1.5 text-xs font-bold text-buy ring-1 ring-emerald-200 hover:bg-emerald-100 transition-colors disabled:opacity-50"
                    >
                      <Check className="h-3.5 w-3.5" />
                      {latestReport.auto_applied ? "Mejoras ya aplicadas" : "Aplicar Mejoras en 1 Clic"}
                    </button>
                  </div>
                )}
              </div>
            ) : (
              <div className="flex h-36 flex-col items-center justify-center text-center text-xs text-ink-soft gap-1">
                <ShieldCheck className="h-8 w-8 text-slate-300" />
                <p className="font-semibold text-ink">Sin informes de auditoría todavía</p>
                <p className="text-[11px]">
                  Pulsa "Auditar Ahora" o finaliza una sesión para que el Agente evalúe la operativa y proponga mejoras.
                </p>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Footer Fijo de Métricas Profesionales + Sparkline */}
      <div className="border-t border-line bg-slate-50/70 p-3 flex flex-col gap-2">
        <div className="grid grid-cols-3 gap-2 text-center text-xs">
          <div className="flex flex-col rounded bg-white p-1.5 border border-line/60">
            <span className="text-[10px] uppercase font-bold text-ink-soft">Win Rate</span>
            <span className="num font-bold text-ink">
              {summary?.performance?.win_rate_pct != null ? `${summary.performance.win_rate_pct}%` : "—"}
            </span>
          </div>
          <div className="flex flex-col rounded bg-white p-1.5 border border-line/60">
            <span className="text-[10px] uppercase font-bold text-ink-soft">Profit Factor</span>
            <span className="num font-bold text-ink">
              {summary?.performance?.profit_factor != null ? summary.performance.profit_factor : "—"}
            </span>
          </div>
          <div className="flex flex-col rounded bg-white p-1.5 border border-line/60">
            <span className="text-[10px] uppercase font-bold text-ink-soft">Ratio R:R Medio</span>
            <span className="num font-bold text-buy">
              {summary?.performance?.avg_rr_ratio != null ? `1:${summary.performance.avg_rr_ratio}` : "1:2.0"}
            </span>
          </div>
        </div>

        {/* Sparkline de Curva de Equidad */}
        <div className="flex items-center justify-between text-[10px] text-ink-soft px-1">
          <span>Curva de Equidad</span>
          <span className="num font-semibold text-ink">
            ${summary?.effective_portfolio_value?.toFixed(2) ?? "5,000.00"}
          </span>
        </div>
        <div className="h-7 w-full overflow-hidden rounded bg-white border border-line/40 px-1 py-0.5">
          {equityPoints.length > 1 ? (
            <svg className="h-full w-full overflow-visible" preserveAspectRatio="none" viewBox="0 0 100 30">
              <polyline
                fill="none"
                stroke="#059669"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                points={sparklineCoords}
              />
            </svg>
          ) : (
            <div className="flex h-full items-center justify-center text-[10px] text-slate-400">
              Base $5,000.00 · Se trazará tras cerrar operaciones
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
