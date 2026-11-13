"use client";

import React, { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import {
  Activity,
  AlertOctagon,
  ArrowRight,
  Check,
  CheckCircle2,
  Clock,
  Cpu,
  FileText,
  Layers,
  Radar,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Wrench,
  X,
  Zap,
} from "lucide-react";
import { api } from "@/lib/api";
import { useAuditReports, useLiveAuditIntelligence } from "@/hooks/useTradingData";
import type { ActivePositionDiagnostic, LiveSummary } from "@/types/trading";

interface LedgerProps {
  summary: LiveSummary | undefined;
}

export function TradeLedgerAndAuditPanel({ summary }: LedgerProps) {
  const qc = useQueryClient();
  const [tab, setTab] = useState<"ACTIVE" | "HISTORY" | "AUDIT">("ACTIVE");
  const [auditSubView, setAuditSubView] = useState<"GUARDIAN" | "CLUSTERS" | "LOG">("GUARDIAN");
  const [confirmingDiag, setConfirmingDiag] = useState<ActivePositionDiagnostic | null>(null);
  const [expandedLogId, setExpandedLogId] = useState<string | null>(null);

  const activePositions = summary?.active_positions || [];
  const closedTrades = summary?.closed_trades || [];
  const auditQ = useAuditReports();
  const liveIntelQ = useLiveAuditIntelligence();

  const liveIntel = liveIntelQ.data;
  const sub11 = liveIntel?.subagent_11_cluster_audit;
  const sub12 = liveIntel?.subagent_12_position_guardian;
  const autoAdaptEnabled = liveIntel?.auto_adapt_positions ?? false;

  const posDiagBySymbol: Record<string, ActivePositionDiagnostic> = {};
  for (const d of sub12?.positions_diagnostics || []) {
    posDiagBySymbol[d.symbol] = d;
  }

  const pendingActionsCount = sub12?.actions_pending_count ?? 0;
  const auditLogs = sub12?.recent_adjustments_audit_log || [];

  const panicCloseMutation = useMutation({
    mutationFn: (ticker: string) => api.closePosition(ticker),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["live-summary"] });
      qc.invalidateQueries({ queryKey: ["live-audit-intelligence"] });
    },
  });

  const runAuditMutation = useMutation({
    mutationFn: (autoApply: boolean) => api.runCriticalAudit(autoApply),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["audit-reports"] });
      qc.invalidateQueries({ queryKey: ["live-audit-intelligence"] });
      qc.invalidateQueries({ queryKey: ["scanner-status"] });
    },
  });

  const applyDynamicAdjMutation = useMutation({
    mutationFn: (diag: ActivePositionDiagnostic) =>
      api.applyDynamicPositionAdjustment(diag.symbol, {
        stop_loss_price: diag.recommended_levels.stop_loss_price,
        take_profit_price: diag.recommended_levels.take_profit_price,
        trigger_mode: "MANUAL_1CLICK_CONFIRMED",
      }),
    onSuccess: () => {
      setConfirmingDiag(null);
      qc.invalidateQueries({ queryKey: ["live-summary"] });
      qc.invalidateQueries({ queryKey: ["live-audit-intelligence"] });
      qc.invalidateQueries({ queryKey: ["audit-reports"] });
    },
  });

  const toggleAutoAdaptMutation = useMutation({
    mutationFn: (enabled: boolean) => api.toggleAutoAdaptPositions(enabled),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["live-audit-intelligence"] });
      qc.invalidateQueries({ queryKey: ["live-summary"] });
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
      const x = (idx / Math.max(1, equityPoints.length - 1)) * 100;
      const y = 28 - ((p.equity - minEq) / range) * 22;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");

  return (
    <div className="relative flex h-full flex-col rounded-xl border border-line bg-surface overflow-hidden shadow-sm">
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
          {pendingActionsCount > 0 ? (
            <span className="flex h-4 min-w-[16px] items-center justify-center rounded-full bg-amber-500 px-1 text-[10px] font-bold text-white animate-pulse">
              {pendingActionsCount}
            </span>
          ) : (
            <span className="h-2 w-2 rounded-full bg-buy" />
          )}
        </button>
      </div>

      {/* Contenido de la Pestaña */}
      <div className="flex-1 overflow-auto p-3">
        {tab === "ACTIVE" &&
          (activePositions.length > 0 ? (
            <div className="flex flex-col gap-2.5">
              {activePositions.map((p) => {
                const isProfitable = p.unrealized_pl >= 0;
                const progress = p.sl_to_tp_progress_pct ?? 50;
                const diag = posDiagBySymbol[p.symbol];

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
                          {isProfitable ? "+" : ""}${p.unrealized_pl.toFixed(2)} (
                          {p.unrealized_plpc.toFixed(2)}%)
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
                        <span className="text-sell font-semibold">
                          SL ${p.stop_loss_price?.toFixed(2) ?? "—"}
                        </span>
                        <span className="num font-bold text-ink">
                          ${p.current_price.toFixed(2)}
                        </span>
                        <div className="flex items-center gap-1.5">
                          {p.is_split_scale_out && p.tp1_price && p.tp2_price ? (
                            <>
                              <span
                                className="rounded bg-amber-100/70 px-1.5 py-0.5 text-[10px] font-bold text-amber-800"
                                title="40% Profit Defensivo"
                              >
                                TP1 (40%) ${p.tp1_price.toFixed(2)}
                              </span>
                              <span
                                className="rounded bg-emerald-100/70 px-1.5 py-0.5 text-[10px] font-bold text-emerald-800"
                                title="60% Techo Realista"
                              >
                                TP2 (60%) ${p.tp2_price.toFixed(2)}
                              </span>
                            </>
                          ) : (
                            <span className="text-buy font-semibold">
                              TP Realista ${p.take_profit_price?.toFixed(2) ?? "—"}
                            </span>
                          )}
                        </div>
                      </div>
                      <div className="h-2 w-full overflow-hidden rounded-full bg-slate-200 relative">
                        <div
                          className="h-full bg-gradient-to-r from-rose-400 via-amber-300 to-emerald-400 transition-all rounded-full"
                          style={{ width: `${progress}%` }}
                        />
                      </div>
                    </div>

                    {/* Insignia de Escudo Pérdida Cero ("Quedarse a 0") cuando ya se vendió el tramo defensivo */}
                    {p.net_zero_shield?.active && (
                      <div className="flex items-center justify-between rounded-md border border-emerald-200 bg-emerald-50/70 px-2.5 py-1.5 text-[10px] text-emerald-900">
                        <span className="font-bold flex items-center gap-1">
                          <ShieldCheck className="h-3.5 w-3.5 text-emerald-600 shrink-0" />
                          Escudo Pérdida Cero Activo: +${p.net_zero_shield.banked_realized_pnl_usd.toFixed(2)} ya cobrados ({p.net_zero_shield.banked_shares} acc)
                        </span>
                        <span className="num font-semibold">
                          Peor caso neto: ${p.net_zero_shield.worst_case_net_total_usd >= 0 ? "+" : ""}
                          ${p.net_zero_shield.worst_case_net_total_usd.toFixed(2)} · Objetivo neto: +$
                          {p.net_zero_shield.target_net_total_usd.toFixed(2)}
                        </span>
                      </div>
                    )}

                    {/* Alerta en vivo del Subagente #12 si detecta ajuste necesario */}
                    {diag && (
                      <div
                        className={clsx(
                          "mt-1 flex flex-col gap-1.5 rounded-md border p-2 text-[11px]",
                          diag.action_required
                            ? "border-amber-300 bg-amber-50/70 text-ink"
                            : "border-slate-200 bg-white text-ink-soft"
                        )}
                      >
                        <div className="flex items-center justify-between gap-1">
                          <span className="font-bold flex items-center gap-1 text-ink">
                            <Zap className="h-3.5 w-3.5 text-amber-600 shrink-0" />
                            Subagente #12:{" "}
                            {diag.regime_code === "NET_ZERO_SHIELD_RUNNER_MODE"
                              ? `Escudo Pérdida Cero (${p.qty} acc protegidas)`
                              : diag.regime_code === "STRUCTURAL_BREAKDOWN_RELIEF_MODE"
                              ? "Corrección Brusca (Escalonar 40% / 60%)"
                              : diag.regime_code === "EXPANSION_ACCELERATION"
                              ? "Crecimiento Brusco a Favor"
                              : "Vigilancia Activa"}
                          </span>
                          <span className="num text-[10px] font-semibold text-ink-soft">
                            Pico MFE: ${diag.excursion_telemetry.peak_price_since_entry.toFixed(2)}{" "}
                            (+{diag.excursion_telemetry.mfe_r.toFixed(2)}R)
                          </span>
                        </div>

                        {diag.action_required && (
                          <>
                            <p className="text-[10px] text-ink-soft leading-snug">
                              {diag.proposal_explanation.action_summary}
                            </p>
                            <div className="flex items-center gap-1.5 pt-0.5">
                              <button
                                onClick={() => setConfirmingDiag(diag)}
                                className="flex-1 flex items-center justify-center gap-1 rounded bg-scan px-2.5 py-1.5 text-[11px] font-bold text-white shadow-xs hover:bg-blue-700 transition-colors"
                              >
                                <Wrench className="h-3 w-3" />
                                Aplicar Ajuste Dinámico en 1 Clic (Ver Motivo)
                              </button>
                              <button
                                onClick={() => {
                                  setTab("AUDIT");
                                  setAuditSubView("GUARDIAN");
                                }}
                                className="rounded border border-line bg-white px-2 py-1.5 text-[10px] font-semibold text-ink hover:bg-slate-50"
                              >
                                Detalles
                              </button>
                            </div>
                          </>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="flex h-full flex-col items-center justify-center text-center text-xs text-ink-soft gap-1">
              <CheckCircle2 className="h-8 w-8 text-slate-300" />
              <p className="font-semibold text-ink">Sin posiciones abiertas</p>
              <p className="text-[11px]">
                Las órdenes Bracket ejecutadas aparecerán aquí con seguimiento en vivo de Stop Loss
                y Take Profit.
              </p>
            </div>
          ))}

        {tab === "HISTORY" &&
          (closedTrades.length > 0 ? (
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
                        className={clsx("num font-bold", isWin ? "text-buy" : "text-sell")}
                      >
                        {isWin ? "+" : ""}${t.realized_pnl_usd.toFixed(2)} (
                        {t.realized_pnl_pct.toFixed(2)}%)
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
              <p className="text-[11px]">
                El historial auditado con PnL neto se registrará aquí automáticamente.
              </p>
            </div>
          ))}

        {tab === "AUDIT" && (
          <div className="flex flex-col gap-2.5 text-xs">
            {/* Cabecera Dual de Subagentes #10 y #11 + Switch Auto-Adaptación */}
            <div className="flex flex-col gap-2 rounded-lg border border-line bg-slate-50/80 p-2.5">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-1.5">
                  <Sparkles className="h-4 w-4 text-scan" />
                  <span className="font-bold text-ink">
                    Arquitectura Dual de Auditoría en Vivo
                  </span>
                </div>
                <button
                  onClick={() => {
                    liveIntelQ.refetch();
                    runAuditMutation.mutate(false);
                  }}
                  disabled={liveIntelQ.isFetching || runAuditMutation.isPending}
                  className="flex items-center gap-1 rounded bg-scan-bg px-2 py-1 text-[10px] font-bold text-scan hover:bg-blue-100 transition-colors disabled:opacity-50"
                >
                  <RefreshCw
                    className={clsx(
                      "h-3 w-3",
                      (liveIntelQ.isFetching || runAuditMutation.isPending) && "animate-spin"
                    )}
                  />
                  Refrescar
                </button>
              </div>

              {/* Interruptor de Auto-Adaptación en Vivo (Subagente #12) */}
              <div className="flex items-center justify-between rounded-md bg-white px-2.5 py-1.5 border border-line/80">
                <div className="flex flex-col">
                  <span className="text-[11px] font-bold text-ink flex items-center gap-1">
                    <Zap className="h-3 w-3 text-amber-500" />
                    Auto-Adaptación de Órdenes Activas (Subagente #12)
                  </span>
                  <span className="text-[10px] text-ink-soft">
                    Ajusta SL/TP automáticamente en Alpaca ante giros bruscos y guarda auditoría.
                  </span>
                </div>
                <button
                  onClick={() => toggleAutoAdaptMutation.mutate(!autoAdaptEnabled)}
                  disabled={toggleAutoAdaptMutation.isPending}
                  className={clsx(
                    "relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out",
                    autoAdaptEnabled ? "bg-buy" : "bg-slate-300"
                  )}
                  title="Activar/Desactivar ajuste automático de órdenes activas en Alpaca"
                >
                  <span
                    className={clsx(
                      "pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out",
                      autoAdaptEnabled ? "translate-x-4" : "translate-x-0"
                    )}
                  />
                </button>
              </div>

              {/* Sub-navegación entre Subagente #12 (Posiciones/Mercado), Subagente #11 (Clústeres) y Registro Auditado */}
              <div className="grid grid-cols-3 gap-1 rounded-md bg-slate-200/70 p-0.5 text-[10px] font-bold">
                <button
                  onClick={() => setAuditSubView("GUARDIAN")}
                  className={clsx(
                    "flex items-center justify-center gap-1 rounded py-1 transition-colors",
                    auditSubView === "GUARDIAN"
                      ? "bg-white text-scan shadow-2xs"
                      : "text-ink-soft hover:text-ink"
                  )}
                >
                  <Activity className="h-3 w-3" />
                  Sub #12 · Posiciones ({sub12?.active_positions_count ?? 0})
                </button>
                <button
                  onClick={() => setAuditSubView("CLUSTERS")}
                  className={clsx(
                    "flex items-center justify-center gap-1 rounded py-1 transition-colors",
                    auditSubView === "CLUSTERS"
                      ? "bg-white text-scan shadow-2xs"
                      : "text-ink-soft hover:text-ink"
                  )}
                >
                  <Layers className="h-3 w-3" />
                  Sub #11 · 17 Clústeres ({sub11?.total_assets_audited ?? 248})
                </button>
                <button
                  onClick={() => setAuditSubView("LOG")}
                  className={clsx(
                    "flex items-center justify-center gap-1 rounded py-1 transition-colors",
                    auditSubView === "LOG"
                      ? "bg-white text-scan shadow-2xs"
                      : "text-ink-soft hover:text-ink"
                  )}
                >
                  <FileText className="h-3 w-3" />
                  Bitácora ({auditLogs.length})
                </button>
              </div>
            </div>

            {/* VISTA 1: SUBAGENTE #12 - GUARDIÁN DINÁMICO DE POSICIONES Y RÉGIMEN */}
            {auditSubView === "GUARDIAN" && (
              <div className="flex flex-col gap-2.5">
                {/* Pulso Macro y Sectorial en Tiempo Real */}
                {sub12?.global_market_pulse && (
                  <div className="flex flex-col gap-1.5 rounded-lg border border-line bg-white p-2.5">
                    <div className="flex items-center justify-between">
                      <span className="text-[10px] uppercase font-bold text-ink-soft">
                        Pulso Macro / Sectorial en Tiempo Real
                      </span>
                      <span className="num text-[10px] text-ink-soft">
                        Latencia: {sub12.execution_latency_ms} ms
                      </span>
                    </div>
                    <p className="text-[11px] font-bold text-ink">
                      {sub12.global_market_pulse.headline}
                    </p>
                    <div className="grid grid-cols-4 gap-1.5 pt-1 text-center text-[10px]">
                      <div className="rounded bg-slate-50 p-1 border border-line/50">
                        <span className="text-ink-soft block">SOXX (5d)</span>
                        <span
                          className={clsx(
                            "num font-bold",
                            sub12.global_market_pulse.soxx_ret_5d_pct >= 0
                              ? "text-buy"
                              : "text-sell"
                          )}
                        >
                          {sub12.global_market_pulse.soxx_ret_5d_pct >= 0 ? "+" : ""}
                          {sub12.global_market_pulse.soxx_ret_5d_pct}%
                        </span>
                      </div>
                      <div className="rounded bg-slate-50 p-1 border border-line/50">
                        <span className="text-ink-soft block">QQQ (5d)</span>
                        <span
                          className={clsx(
                            "num font-bold",
                            sub12.global_market_pulse.qqq_ret_5d_pct >= 0
                              ? "text-buy"
                              : "text-sell"
                          )}
                        >
                          {sub12.global_market_pulse.qqq_ret_5d_pct >= 0 ? "+" : ""}
                          {sub12.global_market_pulse.qqq_ret_5d_pct}%
                        </span>
                      </div>
                      <div className="rounded bg-slate-50 p-1 border border-line/50">
                        <span className="text-ink-soft block">^VIX Miedo</span>
                        <span
                          className={clsx(
                            "num font-bold",
                            sub12.global_market_pulse.vix_level >= 19
                              ? "text-amber-600"
                              : "text-ink"
                          )}
                        >
                          {sub12.global_market_pulse.vix_level}
                        </span>
                      </div>
                      <div className="rounded bg-slate-50 p-1 border border-line/50">
                        <span className="text-ink-soft block">Bono 10Y</span>
                        <span className="num font-bold text-ink">
                          {sub12.global_market_pulse.tnx_10y_yield}%
                        </span>
                      </div>
                    </div>
                  </div>
                )}

                {/* Diagnóstico de cada Posición Activa (ej. NVDA) */}
                {sub12?.positions_diagnostics && sub12.positions_diagnostics.length > 0 ? (
                  sub12.positions_diagnostics.map((diag) => {
                    const isHighUrgency = diag.urgency === "HIGH";
                    return (
                      <div
                        key={diag.symbol}
                        className={clsx(
                          "flex flex-col gap-2 rounded-lg border p-3",
                          diag.action_required && isHighUrgency
                            ? "border-amber-300 bg-amber-50/50"
                            : "border-line bg-white"
                        )}
                      >
                        <div className="flex items-start justify-between gap-2">
                          <div className="flex flex-col">
                            <div className="flex items-center gap-1.5">
                              <span className="font-bold text-sm text-ink">{diag.symbol}</span>
                              <span className="rounded bg-slate-200/80 px-1.5 py-0.5 text-[10px] font-bold text-ink">
                                {diag.sector_etf} · {diag.cluster_id}
                              </span>
                              {diag.action_required && (
                                <span className="rounded bg-amber-500 px-1.5 py-0.5 text-[9px] font-bold text-white uppercase">
                                  Ajuste Recomendado
                                </span>
                              )}
                            </div>
                            <span className="text-[11px] font-bold text-ink mt-1">
                              {diag.headline}
                            </span>
                          </div>
                        </div>

                        {/* Trazabilidad MFE (Crecimiento Miércoles) vs Caída (Jueves/Viernes) */}
                        <div className="grid grid-cols-3 gap-1.5 rounded-md bg-white/90 p-2 border border-line/70 text-[10px]">
                          <div>
                            <span className="text-ink-soft block">Entrada ({diag.entry_date})</span>
                            <span className="num font-bold text-ink">
                              ${diag.entry_price.toFixed(2)} ({diag.qty} acc)
                            </span>
                          </div>
                          <div>
                            <span className="text-ink-soft block">
                              Pico MFE ({diag.excursion_telemetry.peak_date})
                            </span>
                            <span className="num font-bold text-buy">
                              ${diag.excursion_telemetry.peak_price_since_entry.toFixed(2)} (+
                              {diag.excursion_telemetry.mfe_r}R / +$
                              {diag.excursion_telemetry.mfe_total_usd.toFixed(2)})
                            </span>
                          </div>
                          <div>
                            <span className="text-ink-soft block">Peor Sesión</span>
                            <span className="num font-bold text-sell">
                              {diag.excursion_telemetry.worst_single_day_drop_pct}% (
                              {diag.excursion_telemetry.worst_single_day_date})
                            </span>
                          </div>
                        </div>

                        {/* Variables en Tiempo Real del Activo */}
                        <div className="grid grid-cols-4 gap-1 text-[10px] text-center">
                          <div className="rounded bg-white px-1.5 py-1 border border-line/50">
                            <span className="text-ink-soft block">EMA20 / SMA50</span>
                            <span className="num font-semibold text-ink">
                              ${diag.realtime_variables.ema_20} / ${diag.realtime_variables.sma_50}
                            </span>
                          </div>
                          <div className="rounded bg-white px-1.5 py-1 border border-line/50">
                            <span className="text-ink-soft block">VWAP 15m</span>
                            <span className="num font-semibold text-ink">
                              ${diag.realtime_variables.vwap_15m} (
                              {diag.realtime_variables.vwap_spread_pct >= 0 ? "+" : ""}
                              {diag.realtime_variables.vwap_spread_pct}%)
                            </span>
                          </div>
                          <div className="rounded bg-white px-1.5 py-1 border border-line/50">
                            <span className="text-ink-soft block">Flujo DMI</span>
                            <span className="num font-semibold text-sell">
                              +DI {diag.realtime_variables.plus_di} / -DI{" "}
                              {diag.realtime_variables.minus_di}
                            </span>
                          </div>
                          <div className="rounded bg-white px-1.5 py-1 border border-line/50">
                            <span className="text-ink-soft block">Stoch %K / %D</span>
                            <span className="num font-semibold text-scan">
                              {diag.realtime_variables.stoch_k} / {diag.realtime_variables.stoch_d}
                            </span>
                          </div>
                        </div>

                        {/* Comparativa de Niveles: Foto Fija Actual vs Adaptación Propuesta */}
                        <div className="grid grid-cols-2 gap-2 rounded-md bg-white p-2 border border-line text-[11px]">
                          <div className="flex flex-col border-r border-line pr-2">
                            <span className="text-[10px] font-bold uppercase text-ink-soft">
                              Foto Fija Actual (Alpaca)
                            </span>
                            <div className="flex justify-between mt-1">
                              <span className="text-ink-soft">Stop Loss:</span>
                              <span className="num font-bold text-sell">
                                ${diag.current_levels.stop_loss_price?.toFixed(2) ?? "—"} (-$
                                {diag.current_levels.max_loss_usd.toFixed(2)})
                              </span>
                            </div>
                            <div className="flex justify-between">
                              <span className="text-ink-soft">Take Profit:</span>
                              <span className="num font-bold text-ink">
                                ${diag.current_levels.take_profit_price?.toFixed(2) ?? "—"} (+$
                                {diag.current_levels.target_profit_usd.toFixed(2)})
                              </span>
                            </div>
                          </div>

                          <div className="flex flex-col pl-1">
                            <span className="text-[10px] font-bold uppercase text-scan">
                              Propuesta Adaptada Sub #11
                            </span>
                            <div className="flex justify-between mt-1">
                              <span className="text-ink-soft">Nuevo SL:</span>
                              <span className="num font-bold text-buy">
                                ${diag.recommended_levels.stop_loss_price?.toFixed(2) ?? "—"} (Ahorra
                                +${diag.recommended_levels.risk_reduction_usd.toFixed(2)})
                              </span>
                            </div>
                            <div className="flex justify-between">
                              <span className="text-ink-soft">Nuevo TP:</span>
                              <span className="num font-bold text-scan">
                                ${diag.recommended_levels.take_profit_price?.toFixed(2) ?? "—"} (+$
                                {diag.recommended_levels.target_profit_usd.toFixed(2)})
                              </span>
                            </div>
                          </div>
                        </div>

                        {diag.action_required ? (
                          <button
                            onClick={() => setConfirmingDiag(diag)}
                            className="flex items-center justify-center gap-1.5 rounded-lg bg-scan py-2 px-3 text-xs font-bold text-white shadow-xs hover:bg-blue-700 transition-colors"
                          >
                            <Wrench className="h-3.5 w-3.5" />
                            Aplicar Ajuste Dinámico en 1 Clic (Revisar Explicación)
                          </button>
                        ) : (
                          <div className="flex items-center justify-center gap-1 rounded bg-emerald-50 py-1.5 text-[11px] font-semibold text-buy">
                            <Check className="h-3.5 w-3.5" /> Niveles OCO sincronizados con la
                            realidad actual del mercado
                          </div>
                        )}
                      </div>
                    );
                  })
                ) : (
                  <div className="flex h-32 flex-col items-center justify-center rounded-lg border border-line bg-slate-50 p-4 text-center">
                    <ShieldCheck className="h-7 w-7 text-slate-400 mb-1" />
                    <p className="font-bold text-ink">Sin posiciones abiertas que adaptar</p>
                    <p className="text-[10px] text-ink-soft">
                      En cuanto se abra una posición en Alpaca, el Subagente #12 vigilará su
                      evolución, sector y volatilidad cada 8s.
                    </p>
                  </div>
                )}
              </div>
            )}

            {/* VISTA 2: SUBAGENTE #11 - AUDITORÍA DE LOS 17 CLÚSTERES Y CÁLCULOS */}
            {auditSubView === "CLUSTERS" && sub11 && (
              <div className="flex flex-col gap-2.5">
                {/* Sello de Integridad Matemática */}
                <div className="flex items-center justify-between rounded-lg border border-emerald-200 bg-emerald-50/60 p-2.5">
                  <div className="flex items-center gap-2">
                    <Cpu className="h-4 w-4 text-buy shrink-0" />
                    <div className="flex flex-col">
                      <span className="font-bold text-ink text-[11px]">
                        Integridad Matemática de los 17 Subagentes:{" "}
                        {sub11.math_integrity.integrity_score_pct}%
                      </span>
                      <span className="text-[10px] text-ink-soft">
                        {sub11.math_integrity.valid_snapshots}/{sub11.total_assets_audited} activos
                        verificados · {sub11.math_integrity.anomalies_detected} anomalías · Latencia{" "}
                        {sub11.execution_latency_ms} ms
                      </span>
                    </div>
                  </div>
                  <span className="rounded bg-buy px-1.5 py-0.5 text-[9px] font-bold text-white">
                    VERIFICADO
                  </span>
                </div>

                {/* Radar de Pre-Activación (Near-Triggers %K <= 34) */}
                <div className="flex flex-col gap-1.5 rounded-lg border border-line bg-white p-2.5">
                  <div className="flex items-center justify-between">
                    <span className="font-bold text-ink text-[11px] flex items-center gap-1">
                      <Radar className="h-3.5 w-3.5 text-scan" />
                      Radar de Pre-Activación ({sub11.near_triggers_radar.length} activos en zona baja)
                    </span>
                  </div>
                  <div className="flex flex-col divide-y divide-line max-h-40 overflow-y-auto">
                    {sub11.near_triggers_radar.slice(0, 6).map((nt) => (
                      <div key={nt.ticker} className="py-1.5 flex flex-col gap-0.5">
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-1.5">
                            <span className="font-bold text-ink">{nt.ticker}</span>
                            <span className="text-[9px] rounded bg-slate-100 px-1 text-ink-soft">
                              {nt.cluster_id}
                            </span>
                          </div>
                          <span className="num text-[10px] font-bold text-scan">
                            Score {nt.quant_score} · %K {nt.stoch_k} / %D {nt.stoch_d}
                          </span>
                        </div>
                        <p className="text-[10px] text-ink-soft leading-tight">{nt.audit_note}</p>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Matriz de los 17 Clústeres */}
                <div className="grid grid-cols-2 gap-1.5">
                  {sub11.cluster_health_matrix.map((cl) => (
                    <div
                      key={cl.cluster_id}
                      className="flex flex-col gap-1 rounded-lg border border-line bg-slate-50/70 p-2 text-[10px]"
                    >
                      <div className="flex items-center justify-between">
                        <span className="font-bold text-ink truncate">{cl.cluster_id}</span>
                        <span className="rounded bg-white px-1 py-0.2 border border-line font-semibold text-ink-soft">
                          {cl.benchmark_etf}
                        </span>
                      </div>
                      <div className="flex items-center justify-between text-ink-soft">
                        <span>Alcistas: {cl.advancing_pct}%</span>
                        <span className="num font-semibold text-ink">
                          Score Med: {cl.avg_quant_score}
                        </span>
                      </div>
                      <div className="flex items-center justify-between text-[9px]">
                        <span className="text-ink-soft">
                          Sobreventa: <b>{cl.oversold_Zone_count}</b>
                        </span>
                        <span className="font-bold text-scan">
                          Top: {cl.top_ticker} ({cl.top_ticker_score})
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* VISTA 3: BITÁCORA AUDITADA DE AJUSTES DINÁMICOS (AUTOMÁTICOS Y 1-CLIC) */}
            {auditSubView === "LOG" && (
              <div className="flex flex-col gap-2">
                {auditLogs.length > 0 ? (
                  auditLogs.map((rec) => {
                    const isAuto = rec.trigger_mode.includes("AUTO");
                    const isExpanded = expandedLogId === rec.adjustment_id;
                    return (
                      <div
                        key={rec.adjustment_id}
                        className="flex flex-col gap-1.5 rounded-lg border border-line bg-white p-2.5 text-[11px]"
                      >
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-1.5">
                            <span className="font-bold text-ink">{rec.symbol}</span>
                            <span
                              className={clsx(
                                "rounded px-1.5 py-0.2 text-[9px] font-bold uppercase",
                                isAuto
                                  ? "bg-purple-100 text-purple-800"
                                  : "bg-blue-100 text-scan"
                              )}
                            >
                              {isAuto ? "AUTO SUBAGENTE #12" : "MANUAL 1-CLIC"}
                            </span>
                          </div>
                          <span className="num text-[10px] text-ink-soft">
                            {new Date(rec.timestamp).toLocaleString("es-ES", {
                              day: "2-digit",
                              month: "2-digit",
                              hour: "2-digit",
                              minute: "2-digit",
                            })}
                          </span>
                        </div>

                        <p className="font-semibold text-ink text-[11px] leading-snug">
                          {rec.headline}
                        </p>

                        <div className="flex items-center justify-between rounded bg-slate-50 px-2 py-1 text-[10px] num border border-line/60">
                          <span>
                            SL: <b>${rec.previous_levels.stop_loss_price ?? "—"}</b> →{" "}
                            <b className="text-buy">${rec.new_levels.stop_loss_price ?? "—"}</b>
                          </span>
                          <span>
                            TP: <b>${rec.previous_levels.take_profit_price ?? "—"}</b> →{" "}
                            <b className="text-scan">${rec.new_levels.take_profit_price ?? "—"}</b>
                          </span>
                        </div>

                        <button
                          onClick={() =>
                            setExpandedLogId(isExpanded ? null : rec.adjustment_id)
                          }
                          className="text-left text-[10px] font-semibold text-scan hover:underline"
                        >
                          {isExpanded
                            ? "▲ Ocultar motivo de auditoría"
                            : "▼ Ver explicación y evidencia guardada"}
                        </button>

                        {isExpanded && (
                          <div className="mt-1 flex flex-col gap-1.5 rounded bg-slate-50 p-2 text-[10px] text-ink-soft border border-line/50">
                            <p className="leading-relaxed text-ink">{rec.why_proposed}</p>
                            {rec.quantitative_evidence?.length > 0 && (
                              <ul className="list-disc pl-4 space-y-0.5">
                                {rec.quantitative_evidence.map((ev, i) => (
                                  <li key={i}>{ev}</li>
                                ))}
                              </ul>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })
                ) : (
                  <div className="flex h-36 flex-col items-center justify-center text-center text-xs text-ink-soft gap-1">
                    <FileText className="h-7 w-7 text-slate-300" />
                    <p className="font-semibold text-ink">Sin ajustes registrados todavía</p>
                    <p className="text-[10px]">
                      Cada ajuste ejecutado en modo &quot;1 Clic&quot; o en &quot;Auto-Adaptación&quot;
                      quedará registrado aquí con su justificación cuantitativa completa.
                    </p>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      {/* MODAL / SOLICITUD DE CONFIRMACIÓN CON EXPLICACIÓN DETALLADA (1-CLIC) */}
      {confirmingDiag && (
        <div className="absolute inset-0 z-30 flex flex-col bg-slate-900/60 backdrop-blur-xs p-3 overflow-y-auto">
          <div className="my-auto flex flex-col gap-3 rounded-xl border border-line bg-white p-3.5 shadow-xl text-xs">
            <div className="flex items-start justify-between border-b border-line pb-2">
              <div className="flex items-center gap-1.5">
                <ShieldAlert className="h-4 w-4 text-amber-600 shrink-0" />
                <span className="font-bold text-ink text-sm">
                  Solicitud de Ajuste Dinámico · {confirmingDiag.symbol}
                </span>
              </div>
              <button
                onClick={() => setConfirmingDiag(null)}
                className="rounded p-0.5 text-ink-soft hover:bg-slate-100 hover:text-ink"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            {/* Motivo Detallado de por qué se propone el ajuste */}
            <div className="flex flex-col gap-1.5 rounded-lg bg-amber-50/70 border border-amber-200 p-2.5">
              <span className="text-[10px] font-bold uppercase text-amber-900">
                ¿Por qué propone el Subagente #12 este ajuste?
              </span>
              <p className="text-[11px] text-ink leading-relaxed">
                {confirmingDiag.proposal_explanation.why_proposed}
              </p>
            </div>

            {/* Evidencia Cuantitativa en Tiempo Real */}
            <div className="flex flex-col gap-1 rounded-lg bg-slate-50 border border-line p-2.5">
              <span className="text-[10px] font-bold uppercase text-ink-soft">
                Evidencia Cuantitativa Verificada
              </span>
              <ul className="list-disc pl-4 space-y-1 text-[10px] text-ink">
                {confirmingDiag.proposal_explanation.quantitative_evidence.map((item, idx) => (
                  <li key={idx}>{item}</li>
                ))}
              </ul>
            </div>

            {/* Resumen de Cambio en las Órdenes OCO de Alpaca */}
            <div className="grid grid-cols-2 gap-2 rounded-lg border border-line p-2 text-[11px]">
              <div className="flex flex-col">
                <span className="text-[10px] text-ink-soft font-semibold">Stop Loss (Orden Stop)</span>
                <div className="flex items-center gap-1 num font-bold mt-0.5">
                  <span className="text-sell">
                    ${confirmingDiag.current_levels.stop_loss_price?.toFixed(2)}
                  </span>
                  <ArrowRight className="h-3 w-3 text-ink-soft" />
                  <span className="text-buy">
                    ${confirmingDiag.recommended_levels.stop_loss_price?.toFixed(2)}
                  </span>
                </div>
                <span className="text-[10px] text-buy font-semibold">
                  Ahorra +${confirmingDiag.recommended_levels.risk_reduction_usd.toFixed(2)} de
                  riesgo
                </span>
              </div>

              <div className="flex flex-col border-l border-line pl-2">
                <span className="text-[10px] text-ink-soft font-semibold">
                  Take Profit (Orden Limit)
                </span>
                <div className="flex items-center gap-1 num font-bold mt-0.5">
                  <span className="text-ink-soft">
                    ${confirmingDiag.current_levels.take_profit_price?.toFixed(2)}
                  </span>
                  <ArrowRight className="h-3 w-3 text-ink-soft" />
                  <span className="text-scan">
                    ${confirmingDiag.recommended_levels.take_profit_price?.toFixed(2)}
                  </span>
                </div>
                <span className="text-[10px] text-ink-soft">
                  Objetivo realista (+${confirmingDiag.recommended_levels.target_profit_usd.toFixed(2)})
                </span>
              </div>
            </div>

            <p className="text-[10px] text-ink-soft leading-tight">
              {confirmingDiag.proposal_explanation.decision_helper_note}
            </p>

            <div className="flex items-center gap-2 pt-1">
              <button
                onClick={() => setConfirmingDiag(null)}
                className="flex-1 rounded-lg border border-line bg-white py-2 text-xs font-semibold text-ink hover:bg-slate-50"
              >
                Cancelar
              </button>
              <button
                onClick={() => applyDynamicAdjMutation.mutate(confirmingDiag)}
                disabled={applyDynamicAdjMutation.isPending}
                className="flex-2 flex items-center justify-center gap-1.5 rounded-lg bg-scan py-2 px-3 text-xs font-bold text-white shadow-sm hover:bg-blue-700 disabled:opacity-50"
              >
                <Check className="h-3.5 w-3.5" />
                {applyDynamicAdjMutation.isPending
                  ? "Actualizando en Alpaca..."
                  : "Confirmar y Ejecutar Ajuste en Alpaca"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Footer Fijo de Métricas Profesionales + Sparkline */}
      <div className="border-t border-line bg-slate-50/70 p-3 flex flex-col gap-2">
        <div className="grid grid-cols-3 gap-2 text-center text-xs">
          <div className="flex flex-col rounded bg-white p-1.5 border border-line/60">
            <span className="text-[10px] uppercase font-bold text-ink-soft">Win Rate</span>
            <span className="num font-bold text-ink">
              {summary?.performance?.win_rate_pct != null
                ? `${summary.performance.win_rate_pct}%`
                : "—"}
            </span>
          </div>
          <div className="flex flex-col rounded bg-white p-1.5 border border-line/60">
            <span className="text-[10px] uppercase font-bold text-ink-soft">Profit Factor</span>
            <span className="num font-bold text-ink">
              {summary?.performance?.profit_factor != null
                ? summary.performance.profit_factor
                : "—"}
            </span>
          </div>
          <div className="flex flex-col rounded bg-white p-1.5 border border-line/60">
            <span className="text-[10px] uppercase font-bold text-ink-soft">Ratio R:R Medio</span>
            <span className="num font-bold text-buy">
              {summary?.performance?.avg_rr_ratio != null
                ? `1:${summary.performance.avg_rr_ratio}`
                : "1:2.0"}
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
            <svg
              className="h-full w-full overflow-visible"
              preserveAspectRatio="none"
              viewBox="0 0 100 30"
            >
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
