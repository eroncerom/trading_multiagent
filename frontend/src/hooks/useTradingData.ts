"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

export function useScannerStatus() {
  return useQuery({
    queryKey: ["scanner-status"],
    queryFn: api.scannerStatus,
    refetchInterval: 2000,
  });
}

export function useLiveSummary() {
  return useQuery({
    queryKey: ["live-summary"],
    queryFn: api.liveSummary,
    refetchInterval: 3000,
  });
}

export function useOpportunities() {
  return useQuery({
    queryKey: ["opportunities"],
    queryFn: api.listOpportunities,
    refetchInterval: 2500,
  });
}

export function useCandles(ticker: string | null, activeChart = false) {
  return useQuery({
    queryKey: ["candles", ticker],
    queryFn: () => (ticker ? api.getCandles(ticker, 100) : null),
    enabled: !!ticker && activeChart,
    staleTime: 10000,
    refetchInterval: activeChart ? 10000 : false,
    refetchIntervalInBackground: false,
  });
}

export function useAuditReports() {
  return useQuery({
    queryKey: ["audit-reports"],
    queryFn: api.getAuditReports,
    refetchInterval: 5000,
  });
}

export function useLiveAuditIntelligence() {
  return useQuery({
    queryKey: ["live-audit-intelligence"],
    queryFn: api.getLiveAuditIntelligence,
    refetchInterval: 8000,
  });
}
