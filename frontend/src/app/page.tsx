"use client";

import { TopHeaderBar } from "@/components/header/TopHeaderBar";
import { CenterStage } from "@/components/center/CenterStage";
import { TradeLedgerAndAuditPanel } from "@/components/ledger/TradeLedgerAndAuditPanel";
import { useLiveSummary } from "@/hooks/useTradingData";

export default function Home() {
  const summaryQ = useLiveSummary();

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-canvas text-ink">
      <TopHeaderBar />
      <main className="grid min-h-0 flex-1 grid-cols-12 gap-3 p-3 overflow-hidden">
        {/* Zona Central Principal (8 Columnas): Radar Cuantitativo y Gráfico de Disparo */}
        <section className="col-span-8 flex flex-col min-h-0 overflow-hidden">
          <CenterStage />
        </section>

        {/* Panel Lateral (4 Columnas): Posiciones Activas OCO e Historial Auditado */}
        <aside className="col-span-4 flex flex-col min-h-0 overflow-hidden">
          <TradeLedgerAndAuditPanel summary={summaryQ.data} />
        </aside>
      </main>
    </div>
  );
}
