"use client";

import React from "react";
import clsx from "clsx";
import {
  Play,
  TrendingUp,
  TrendingDown,
  Compass,
  Activity,
  ArrowUpRight,
  AlertTriangle,
} from "lucide-react";
import type { MarketWatchItem } from "@/types/trading";

interface ScannerGridProps {
  marketWatch: MarketWatchItem[];
  selectedTicker: string | null;
  onSelectTicker: (ticker: string) => void;
  onScanNow: (forceTest: boolean) => void;
  isScanning: boolean;
}

function getRegimeBadge(regime: string, fallingKnife?: boolean) {
  if (regime.includes("BEARISH_BREAKDOWN") || fallingKnife) {
    return {
      label: fallingKnife ? "Cuchillo / Rotura" : "Corrección Corto",
      cls: "bg-sell-bg text-sell border border-rose-200",
      icon: AlertTriangle,
    };
  }
  if (regime.includes("BEARISH_TREND")) {
    return {
      label: "Bajista / Corto",
      cls: "bg-sell-bg text-sell border border-rose-200",
      icon: TrendingDown,
    };
  }
  if (regime.includes("BULLISH_TREND")) {
    return {
      label: "Alcista Sano",
      cls: "bg-buy-bg text-buy border border-emerald-200",
      icon: TrendingUp,
    };
  }
  if (regime.includes("BULLISH_PULLBACK")) {
    return {
      label: "Pullback Sano",
      cls: "bg-amber-50 text-amber-700 border border-amber-200",
      icon: TrendingUp,
    };
  }
  if (regime.includes("LATERAL")) {
    return {
      label: "Lateral / Rango",
      cls: "bg-scan-bg text-scan border border-blue-200",
      icon: Compass,
    };
  }
  return {
    label: "Transición",
    cls: "bg-slate-100 text-ink-soft border border-slate-200",
    icon: Compass,
  };
}

const CLUSTERS_META: { id: string; label: string; count: number }[] = [
  { id: "ALL", label: "Todos (120)", count: 120 },
  { id: "TECH_GROWTH_A", label: "Tech Mega-Cap", count: 15 },
  { id: "TECH_GROWTH_B", label: "Semis & Software", count: 15 },
  { id: "FINANCE_CONSUMER_A", label: "Finanzas Core", count: 15 },
  { id: "FINANCE_CONSUMER_B", label: "Consumo & Retail", count: 15 },
  { id: "HEALTH_INDUSTRIAL_A", label: "Salud & Energía", count: 15 },
  { id: "HEALTH_INDUSTRIAL_B", label: "MedTech & Industria", count: 15 },
  { id: "HIGH_BETA_EXPANSION_A", label: "High Beta Core", count: 15 },
  { id: "HIGH_BETA_EXPANSION_B", label: "Next-Gen Growth", count: 15 },
];

export function LiveMarketScannerGrid({
  marketWatch,
  selectedTicker,
  onSelectTicker,
  onScanNow,
  isScanning,
}: ScannerGridProps) {
  const [activeCluster, setActiveCluster] = React.useState<string>("ALL");

  const filteredItems = React.useMemo(() => {
    if (activeCluster === "ALL") return marketWatch;
    return marketWatch.filter((item) => {
      if (item.cluster_id === activeCluster) return true;
      // Compatibilidad hacia atrás con prefijos antiguos
      if (activeCluster === "TECH_GROWTH_A" && item.cluster_id === "TECH_GROWTH") return true;
      if (activeCluster === "FINANCE_CONSUMER_A" && item.cluster_id === "FINANCE_CONSUMER") return true;
      if (activeCluster === "HEALTH_INDUSTRIAL_A" && item.cluster_id === "HEALTH_INDUSTRIAL") return true;
      if (activeCluster === "HIGH_BETA_EXPANSION_A" && item.cluster_id === "HIGH_BETA_EXPANSION") return true;
      return false;
    });
  }, [marketWatch, activeCluster]);

  return (
    <div className="flex h-full flex-col rounded-xl border border-line bg-surface overflow-hidden shadow-sm">
      {/* Barra de Controles del Radar */}
      <div className="flex items-center justify-between border-b border-line px-4 py-2.5 bg-slate-50/50 flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <Activity className="h-4 w-4 text-scan animate-pulse" />
          <span className="text-xs font-bold text-ink">
            Radar Cuantitativo Multiclúster (8 Subagentes · {marketWatch.length || 120} Activos)
          </span>
          <span className="text-[11px] font-medium text-scan">· Barrido paralelo 8 hilos</span>
          <span className="inline-flex items-center rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-semibold text-emerald-700 border border-emerald-200">
            ⚡ Subagente Disparador Activo
          </span>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => onScanNow(true)}
            disabled={isScanning}
            className="flex items-center gap-1 rounded-md border border-scan/30 bg-scan-bg px-2.5 py-1 text-xs font-medium text-scan transition-colors hover:bg-blue-100 disabled:opacity-50"
            title="Selecciona el activo con mejor estructura cuantitativa entre los 4 clústeres"
          >
            <Play className="h-3 w-3" /> Simular Mejor Setup (PoC)
          </button>

          <button
            onClick={() => onScanNow(false)}
            disabled={isScanning}
            className="flex items-center gap-1 rounded-md border border-line bg-white px-3 py-1 text-xs font-semibold text-ink transition-colors hover:bg-slate-50 disabled:opacity-50"
          >
            {isScanning ? "Escaneando..." : "Escanear Ahora"}
          </button>
        </div>
      </div>

      {/* Pestañas de Subagentes por Clúster */}
      <div className="flex items-center border-b border-line bg-slate-100/70 px-3 py-1.5 gap-1.5 overflow-x-auto">
        {CLUSTERS_META.map((c) => {
          const isActive = activeCluster === c.id;
          return (
            <button
              key={c.id}
              onClick={() => setActiveCluster(c.id)}
              className={clsx(
                "rounded-md px-2.5 py-1 text-xs font-semibold transition-all whitespace-nowrap",
                isActive
                  ? "bg-white text-ink shadow-xs ring-1 ring-slate-200"
                  : "text-ink-soft hover:bg-white/60 hover:text-ink",
              )}
            >
              {c.label}
            </button>
          );
        })}
      </div>

      {/* Tabla de Alta Densidad con Telemetría de Corto Plazo */}
      <div className="flex-1 overflow-auto">
        <table className="w-full text-left text-xs">
          <thead className="sticky top-0 border-b border-line bg-slate-50 text-[10px] uppercase font-bold text-ink-soft tracking-wider z-10">
            <tr>
              <th className="py-2 px-3">Ticker / Clúster</th>
              <th className="py-2 px-3">Score</th>
              <th className="py-2 px-3">Régimen Corto Plazo</th>
              <th className="py-2 px-3">Flujo DMI (+DI / -DI)</th>
              <th className="py-2 px-3">Stoch (14,4) & RSI</th>
              <th className="py-2 px-3">EMA20 / SMA200</th>
              <th className="py-2 px-3">ATR / RVOL</th>
              <th className="py-2 px-3 text-right">Gráfico</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {filteredItems.map((item) => {
              const isSelected = selectedTicker === item.ticker;
              const badge = getRegimeBadge(item.market_regime, item.falling_knife);
              const BadgeIcon = badge.icon;
              const score = item.quant_score ?? 50;
              const ema20 = item.ema_20 ?? item.price;
              const pctVsEma20 = ema20 > 0 ? ((item.price - ema20) / ema20) * 100 : 0;
              const pctVsSma200 =
                item.sma_200 > 0 ? ((item.price - item.sma_200) / item.sma_200) * 100 : 0;
              const plusDi = item.plus_di ?? 20;
              const minusDi = item.minus_di ?? 20;
              const diPositive = plusDi >= minusDi;
              const ret1d = item.ret_1d_pct ?? 0;
              const isOversold = item.stoch_k <= 30;

              return (
                <tr
                  key={item.ticker}
                  onClick={() => onSelectTicker(item.ticker)}
                  title={item.expert_summary || ""}
                  className={clsx(
                    "cursor-pointer transition-colors hover:bg-slate-50",
                    isSelected && "bg-blue-50/50 font-medium",
                  )}
                >
                  <td className="py-2.5 px-3">
                    <div className="flex flex-col">
                      <div className="flex items-center gap-1.5">
                        <span className="font-bold text-ink">{item.ticker}</span>
                        <span className="num font-semibold text-ink">${item.price.toFixed(2)}</span>
                      </div>
                      <div className="flex items-center gap-1.5 text-[10px]">
                        <span
                          className={clsx(
                            "num font-semibold",
                            ret1d >= 0 ? "text-buy" : "text-sell",
                          )}
                        >
                          {ret1d >= 0 ? "+" : ""}
                          {ret1d.toFixed(2)}%
                        </span>
                        {item.cluster_id && (
                          <span className="text-[9px] text-ink-soft opacity-75">
                            · {item.cluster_id.replace("_", " ")}
                          </span>
                        )}
                      </div>
                    </div>
                  </td>

                  <td className="py-2.5 px-3">
                    <span
                      className={clsx(
                        "num inline-flex items-center justify-center rounded-md px-2 py-0.5 text-[11px] font-bold",
                        score >= 70 && "bg-buy-bg text-buy ring-1 ring-emerald-200",
                        score >= 50 && score < 70 && "bg-amber-50 text-amber-700 ring-1 ring-amber-200",
                        score < 50 && "bg-sell-bg text-sell ring-1 ring-rose-200",
                      )}
                    >
                      {score}/100
                    </span>
                  </td>

                  <td className="py-2.5 px-3">
                    <span
                      className={clsx(
                        "inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-semibold",
                        badge.cls,
                      )}
                    >
                      <BadgeIcon className="h-3 w-3 shrink-0" />
                      {badge.label}
                    </span>
                  </td>

                  <td className="py-2.5 px-3">
                    <div className="flex flex-col">
                      <span
                        className={clsx(
                          "num text-[11px] font-semibold",
                          diPositive ? "text-buy" : "text-sell",
                        )}
                      >
                        +DI {plusDi.toFixed(0)} / -DI {minusDi.toFixed(0)}
                      </span>
                      <span className="num text-[10px] text-ink-soft">
                        ADX {item.adx.toFixed(1)} · {diPositive ? "Compras" : "Ventas (Corto)"}
                      </span>
                    </div>
                  </td>

                  <td className="py-2.5 px-3">
                    <div className="flex flex-col gap-0.5">
                      <div className="flex items-center justify-between text-[11px] gap-2">
                        <span
                          className={clsx(
                            "num font-semibold",
                            isOversold ? "text-buy font-bold" : "text-ink",
                          )}
                        >
                          %K {item.stoch_k.toFixed(0)}/%D {item.stoch_d.toFixed(0)}
                        </span>
                        <span className="num text-[10px] text-ink-soft">
                          RSI {(item.rsi_14 ?? 50).toFixed(0)}
                        </span>
                      </div>
                      <div className="h-1.5 w-24 overflow-hidden rounded-full bg-slate-100">
                        <div
                          className={clsx(
                            "h-full transition-all",
                            item.falling_knife
                              ? "bg-sell"
                              : isOversold
                                ? "bg-buy"
                                : "bg-slate-300",
                          )}
                          style={{ width: `${Math.min(100, Math.max(0, item.stoch_k))}%` }}
                        />
                      </div>
                    </div>
                  </td>

                  <td className="py-2.5 px-3">
                    <div className="flex flex-col">
                      <span
                        className={clsx(
                          "num text-[11px] font-semibold",
                          pctVsEma20 >= 0 ? "text-buy" : "text-sell",
                        )}
                      >
                        EMA20: {pctVsEma20 >= 0 ? "+" : ""}
                        {pctVsEma20.toFixed(1)}%
                      </span>
                      <span
                        className={clsx(
                          "num text-[10px]",
                          pctVsSma200 >= 0 ? "text-ink-soft" : "text-sell",
                        )}
                      >
                        SMA200: {pctVsSma200 >= 0 ? "+" : ""}
                        {pctVsSma200.toFixed(1)}%
                      </span>
                    </div>
                  </td>

                  <td className="py-2.5 px-3">
                    <div className="flex flex-col">
                      <span className="num text-[11px] text-ink">
                        ${item.atr_14.toFixed(2)} ({((item.atr_14 / item.price) * 100).toFixed(1)}%)
                      </span>
                      <span className="num text-[10px] text-ink-soft">
                        RVOL {(item.rvol_20 ?? 1.0).toFixed(2)}x
                      </span>
                    </div>
                  </td>

                  <td className="py-2.5 px-3 text-right">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        onSelectTicker(item.ticker);
                      }}
                      className="rounded p-1 text-ink-soft hover:bg-slate-200 hover:text-ink"
                      title="Ver gráfico con EMA20, SMA50 y SMA200"
                    >
                      <ArrowUpRight className="h-4 w-4" />
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>

        {filteredItems.length === 0 && (
          <div className="flex flex-col items-center justify-center p-12 text-center">
            <Activity className="h-8 w-8 text-scan/60 animate-pulse mb-3" />
            <p className="text-sm font-bold text-ink">Radar en espera de datos</p>
            <p className="text-xs text-ink-soft mt-1 max-w-md">
              El motor cuantitativo no tiene datos cargados para este clúster. Pulsa el botón para iniciar un barrido inmediato del universo de 60 acciones.
            </p>
            <button
              onClick={() => onScanNow(false)}
              disabled={isScanning}
              className="mt-4 flex items-center gap-1.5 rounded-md bg-scan px-4 py-2 text-xs font-semibold text-white shadow-sm hover:bg-blue-600 transition-colors disabled:opacity-50 cursor-pointer"
            >
              <Play className="h-3.5 w-3.5" /> {isScanning ? "Escaneando universo..." : "Escanear Ahora"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
