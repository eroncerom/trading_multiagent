"use client";

import { useState } from "react";
import dynamic from "next/dynamic";
import { Sparkles } from "lucide-react";
import { TopHeaderBar } from "@/components/header/TopHeaderBar";
import { TradingCopilotDrawer } from "@/components/copilot/TradingCopilotDrawer";
import { useLiveSummary } from "@/hooks/useTradingData";

const CenterStage = dynamic(
  () => import("@/components/center/CenterStage").then((m) => m.CenterStage),
  { ssr: false }
);

const TradeLedgerAndAuditPanel = dynamic(
  () => import("@/components/ledger/TradeLedgerAndAuditPanel").then((m) => m.TradeLedgerAndAuditPanel),
  { ssr: false }
);

export default function Home() {
  const summaryQ = useLiveSummary();
  const [isCopilotOpen, setIsCopilotOpen] = useState(false);

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-canvas text-ink relative">
      <TopHeaderBar onOpenCopilot={() => setIsCopilotOpen(true)} />
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

      {/* Botón Flotante Rápido de Acceso al Copilot */}
      <button
        onClick={() => setIsCopilotOpen(true)}
        className="fixed bottom-4 right-5 z-30 flex items-center gap-2 rounded-full bg-gradient-to-r from-indigo-600 to-violet-600 px-3.5 py-2 text-xs font-bold text-white shadow-lg shadow-indigo-600/30 transition-all hover:scale-105 active:scale-95 cursor-pointer hover:shadow-indigo-600/50"
        title="Abrir Copilot Táctico"
      >
        <Sparkles className="h-4 w-4 animate-pulse text-amber-200" />
        <span>Copilot Táctico</span>
      </button>

      {/* Drawer Desplegable del Copilot */}
      <TradingCopilotDrawer
        isOpen={isCopilotOpen}
        onClose={() => setIsCopilotOpen(false)}
      />
    </div>
  );
}
