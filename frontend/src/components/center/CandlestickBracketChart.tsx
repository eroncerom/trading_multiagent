"use client";

import React, { useEffect, useRef } from "react";
import {
  createChart,
  IChartApi,
  ColorType,
  CandlestickSeries,
  LineSeries,
} from "lightweight-charts";
import type { CandleData, SmaPoint } from "@/types/trading";

interface ChartProps {
  candles: CandleData[];
  ema20Series?: SmaPoint[];
  sma50Series?: SmaPoint[];
  smaSeries: SmaPoint[];
  entryPrice?: number;
  stopLossPrice?: number;
  takeProfitPrice?: number;
}

export function CandlestickBracketChart({
  candles,
  ema20Series,
  sma50Series,
  smaSeries,
  entryPrice,
  stopLossPrice,
  takeProfitPrice,
}: ChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!chartContainerRef.current) return;

    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "#FFFFFF" },
        textColor: "#475569",
        fontFamily: "var(--font-jetbrains), monospace",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: "#F1F5F9" },
        horzLines: { color: "#F1F5F9" },
      },
      crosshair: {
        vertLine: { color: "#94A3B8", width: 1, style: 3 },
        horzLine: { color: "#94A3B8", width: 1, style: 3 },
      },
      timeScale: {
        borderColor: "#E2E8F0",
        timeVisible: true,
      },
      rightPriceScale: {
        borderColor: "#E2E8F0",
        scaleMargins: { top: 0.1, bottom: 0.1 },
      },
    });

    chartRef.current = chart;

    // 1. Serie de Velas Japonesas
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#059669",
      downColor: "#E11D48",
      borderUpColor: "#059669",
      borderDownColor: "#E11D48",
      wickUpColor: "#059669",
      wickDownColor: "#E11D48",
    });

    if (candles && candles.length > 0) {
      candleSeries.setData(candles as any);
    }

    // 2. Línea EMA(20) — Tendencia Táctica Corto Plazo
    if (ema20Series && ema20Series.length > 0) {
      const ema20Line = chart.addSeries(LineSeries, {
        color: "#F59E0B",
        lineWidth: 2,
        title: "EMA 20 (Corto)",
        priceLineVisible: false,
      });
      ema20Line.setData(ema20Series as any);
    }

    // 3. Línea SMA(50) — Estructura Intermedia Swing
    if (sma50Series && sma50Series.length > 0) {
      const sma50Line = chart.addSeries(LineSeries, {
        color: "#8B5CF6",
        lineWidth: 2,
        title: "SMA 50 (Medio)",
        priceLineVisible: false,
      });
      sma50Line.setData(sma50Series as any);
    }

    // 4. Línea SMA(200) — Sesgo Estructural Anual
    if (smaSeries && smaSeries.length > 0) {
      const smaLine = chart.addSeries(LineSeries, {
        color: "#2563EB",
        lineWidth: 2,
        title: "SMA 200 (Anual)",
        priceLineVisible: false,
      });
      smaLine.setData(smaSeries as any);
    }

    // 5. Capas Horizontales OCO (Entrada, Take Profit, Stop Loss)
    if (entryPrice) {
      candleSeries.createPriceLine({
        price: entryPrice,
        color: "#2563EB",
        lineWidth: 2,
        lineStyle: 0,
        axisLabelVisible: true,
        title: `ENTRADA $${entryPrice.toFixed(2)}`,
      });
    }

    if (takeProfitPrice) {
      candleSeries.createPriceLine({
        price: takeProfitPrice,
        color: "#059669",
        lineWidth: 2,
        lineStyle: 2,
        axisLabelVisible: true,
        title: `TP (+2R) $${takeProfitPrice.toFixed(2)}`,
      });
    }

    if (stopLossPrice) {
      candleSeries.createPriceLine({
        price: stopLossPrice,
        color: "#E11D48",
        lineWidth: 2,
        lineStyle: 2,
        axisLabelVisible: true,
        title: `SL (-1.5 ATR) $${stopLossPrice.toFixed(2)}`,
      });
    }

    chart.timeScale().fitContent();

    const handleResize = () => {
      if (chartContainerRef.current) {
        chart.applyOptions({
          width: chartContainerRef.current.clientWidth,
          height: chartContainerRef.current.clientHeight,
        });
      }
    };

    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
    };
  }, [candles, ema20Series, sma50Series, smaSeries, entryPrice, stopLossPrice, takeProfitPrice]);

  return (
    <div className="h-full w-full relative">
      {/* Leyenda institucional de medias móviles superpuesta arriba a la izquierda */}
      <div className="pointer-events-none absolute left-3 top-2.5 z-10 flex flex-wrap items-center gap-3 rounded-md border border-slate-200/80 bg-white/90 px-2.5 py-1 text-[10px] font-semibold shadow-xs backdrop-blur-xs">
        <span className="flex items-center gap-1 text-amber-600">
          <span className="inline-block h-2 w-2 rounded-full bg-amber-500" />
          EMA 20 (Corto Plazo)
        </span>
        <span className="flex items-center gap-1 text-violet-600">
          <span className="inline-block h-2 w-2 rounded-full bg-violet-500" />
          SMA 50 (Medio Plazo)
        </span>
        <span className="flex items-center gap-1 text-blue-600">
          <span className="inline-block h-2 w-2 rounded-full bg-blue-600" />
          SMA 200 (Macro Anual)
        </span>
      </div>
      <div ref={chartContainerRef} className="h-full w-full" />
    </div>
  );
}
