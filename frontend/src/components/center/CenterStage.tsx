"use client";

import React, { useState, useEffect } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { LiveMarketScannerGrid } from "./LiveMarketScannerGrid";
import { InstantActionView } from "./InstantActionView";
import { useCandles, useOpportunities } from "@/hooks/useTradingData";
import { api } from "@/lib/api";

export function CenterStage() {
  const qc = useQueryClient();
  const opportunitiesQ = useOpportunities();

  const opportunities = opportunitiesQ.data?.opportunities || [];
  const marketWatch = opportunitiesQ.data?.market_watch || [];

  // Ticker seleccionado para ver gráfico (o el primero con oportunidad activa)
  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<"RADAR" | "CHART">("RADAR");

  // Transición automática al gráfico si aparece una oportunidad nueva
  useEffect(() => {
    if (opportunities.length > 0 && viewMode === "RADAR") {
      setSelectedTicker(opportunities[0].ticker);
      setViewMode("CHART");
    }
  }, [opportunities.length]);

  const activeOppForSelected = opportunities.find((o) => o.ticker === selectedTicker);
  const candlesQ = useCandles(selectedTicker);

  // Mutaciones de 1 clic
  const scanNowMutation = useMutation({
    mutationFn: (forceTest: boolean) => api.scanNow(forceTest),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["opportunities"] });
      qc.invalidateQueries({ queryKey: ["scanner-status"] });
    },
  });

  const approveMutation = useMutation({
    mutationFn: (ticker: string) => api.approveOpportunity(ticker),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["opportunities"] });
      qc.invalidateQueries({ queryKey: ["live-summary"] });
      qc.invalidateQueries({ queryKey: ["scanner-status"] });
      setViewMode("RADAR");
    },
  });

  const dismissMutation = useMutation({
    mutationFn: (ticker: string) => api.dismissOpportunity(ticker),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["opportunities"] });
      setViewMode("RADAR");
    },
  });

  const adviceMutation = useMutation({
    mutationFn: (ticker: string) => api.requestAiAdvice(ticker),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["opportunities"] });
    },
  });

  const handleSelectTicker = (ticker: string) => {
    setSelectedTicker(ticker);
    setViewMode("CHART");
  };

  return (
    <div className="h-full w-full flex flex-col min-h-0">
      {viewMode === "RADAR" ? (
        <LiveMarketScannerGrid
          marketWatch={marketWatch}
          selectedTicker={selectedTicker}
          onSelectTicker={handleSelectTicker}
          onScanNow={(force) => scanNowMutation.mutate(force)}
          isScanning={scanNowMutation.isPending}
        />
      ) : (
        <InstantActionView
          ticker={selectedTicker || "NVDA"}
          candleData={candlesQ.data}
          isLoadingCandles={candlesQ.isLoading}
          opportunity={activeOppForSelected}
          onBackToRadar={() => setViewMode("RADAR")}
          onApprove={(t) => approveMutation.mutate(t)}
          onDismiss={(t) => dismissMutation.mutate(t)}
          onRequestAdvice={(t) => adviceMutation.mutate(t)}
          isApproving={approveMutation.isPending}
          isDismissing={dismissMutation.isPending}
        />
      )}
    </div>
  );
}
