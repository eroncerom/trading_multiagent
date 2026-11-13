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
  Search,
  X,
  Plus,
  Trash2,
  GraduationCap,
  Sparkles,
  RefreshCw,
  CheckCircle2,
  AlertCircle,
} from "lucide-react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
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

export const TICKER_NAMES: Record<string, string> = {
  // Cluster 1: TECH_GROWTH_A
  NVDA: "NVIDIA Corp", AAPL: "Apple Inc", MSFT: "Microsoft Corp", AMZN: "Amazon.com", GOOGL: "Alphabet Inc (Google)",
  META: "Meta Platforms", TSLA: "Tesla Inc", NFLX: "Netflix Inc", PLTR: "Palantir Technologies", AMD: "Advanced Micro Devices",
  AVGO: "Broadcom Inc", CRM: "Salesforce Inc", UBER: "Uber Technologies", INTC: "Intel Corp", CSCO: "Cisco Systems",

  // Cluster 2: TECH_GROWTH_B
  ORCL: "Oracle Corp", ADBE: "Adobe Inc", QCOM: "Qualcomm Inc", TXN: "Texas Instruments", AMAT: "Applied Materials",
  INTU: "Intuit Inc", NOW: "ServiceNow", IBM: "IBM Corp", MU: "Micron Technology", LRCX: "Lam Research",
  ADI: "Analog Devices", KLAC: "KLA Corp", SNPS: "Synopsys Inc", CDNS: "Cadence Design", MRVL: "Marvell Technology",

  // Cluster 3: FINANCE_CONSUMER_A
  JPM: "JPMorgan Chase", BAC: "Bank of America", WFC: "Wells Fargo", GS: "Goldman Sachs", MS: "Morgan Stanley",
  V: "Visa Inc", MA: "Mastercard Inc", PYPL: "PayPal Holdings", KO: "Coca-Cola Co", PEP: "PepsiCo Inc",
  COST: "Costco Wholesale", WMT: "Walmart Inc", NKE: "Nike Inc", MCD: "McDonald's Corp", DIS: "Walt Disney Co",

  // Cluster 4: FINANCE_CONSUMER_B
  AXP: "American Express", BLK: "BlackRock Inc", C: "Citigroup Inc", SCHW: "Charles Schwab", SPGI: "S&P Global",
  PGR: "Progressive Corp", CB: "Chubb Limited", HD: "Home Depot", LOW: "Lowe's Companies", TGT: "Target Corp",
  SBUX: "Starbucks Corp", TJX: "TJX Companies", BKNG: "Booking Holdings", PM: "Philip Morris", PG: "Procter & Gamble",

  // Cluster 5: HEALTH_INDUSTRIAL_A
  JNJ: "Johnson & Johnson", PFE: "Pfizer Inc", ABBV: "AbbVie Inc", MRK: "Merck & Co", LLY: "Eli Lilly & Co",
  UNH: "UnitedHealth Group", CAT: "Caterpillar Inc", DE: "Deere & Co", GE: "General Electric", HON: "Honeywell",
  BA: "Boeing Co", LMT: "Lockheed Martin", XOM: "ExxonMobil Corp", CVX: "Chevron Corp", UPS: "United Parcel Service",

  // Cluster 6: HEALTH_INDUSTRIAL_B
  TMO: "Thermo Fisher Scientific", ABT: "Abbott Laboratories", DHR: "Danaher Corp", ISRG: "Intuitive Surgical", AMGN: "Amgen Inc",
  GILD: "Gilead Sciences", VRTX: "Vertex Pharmaceuticals", BMY: "Bristol Myers Squibb", RTX: "RTX Corp", ETN: "Eaton Corp",
  UNP: "Union Pacific", ADP: "Automatic Data Processing", COP: "ConocoPhillips", SLB: "Schlumberger", EOG: "EOG Resources",

  // Cluster 7: HIGH_BETA_EXPANSION_A
  SOFI: "SoFi Technologies", COIN: "Coinbase Global", HOOD: "Robinhood Markets", SHOP: "Shopify Inc", SNOW: "Snowflake Inc",
  CRWD: "CrowdStrike Holdings", PANW: "Palo Alto Networks", NET: "Cloudflare Inc", XYZ: "XYZ Corp", AFRM: "Affirm Holdings",
  SMCI: "Super Micro Computer", ARM: "Arm Holdings", RIVN: "Rivian Automotive", DKNG: "DraftKings Inc", MARA: "MARA Holdings",

  // Cluster 8: HIGH_BETA_EXPANSION_B
  MELI: "MercadoLibre", SE: "Sea Limited", DASH: "DoorDash Inc", RBLX: "Roblox Corp", U: "Unity Software",
  ROKU: "Roku Inc", TTD: "The Trade Desk", APP: "AppLovin Corp", DDOG: "Datadog Inc", ZS: "Zscaler Inc",
  MDB: "MongoDB Inc", MSTR: "MicroStrategy Inc", RIOT: "Riot Platforms", IONQ: "IonQ Inc", RKLB: "Rocket Lab USA",

  // Cluster 9: CYBER_CLOUD_ENTERPRISE
  FTNT: "Fortinet Inc", OKTA: "Okta Inc", TEAM: "Atlassian Corp", WDAY: "Workday Inc", HUBS: "HubSpot Inc",
  DOCU: "DocuSign Inc", TWLO: "Twilio Inc", ZM: "Zoom Video Communications", DOCN: "DigitalOcean", CFLT: "Confluent Inc",
  IOT: "Samsara Inc", GTLB: "GitLab Inc", ESTC: "Elastic N.V.", NTNX: "Nutanix Inc", VEEV: "Veeva Systems",

  // Cluster 10: SEMI_AI_INFRASTRUCTURE
  ASML: "ASML Holding", TSM: "Taiwan Semiconductor (TSMC)", ON: "ON Semiconductor", NXPI: "NXP Semiconductors", MPWR: "Monolithic Power Systems",
  SWKS: "Skyworks Solutions", QRVO: "Qorvo Inc", TER: "Teradyne Inc", ENTG: "Entegris Inc", ONTO: "Onto Innovation",
  ANET: "Arista Networks", DELL: "Dell Technologies", HPE: "Hewlett Packard Enterprise", VRT: "Vertiv Holdings", APH: "Amphenol Corp",

  // Cluster 11: FINTECH_INSURANCE_REIT
  "BRK-B": "Berkshire Hathaway", BRK_B: "Berkshire Hathaway", CME: "CME Group", ICE: "Intercontinental Exchange", MCO: "Moody's Corp",
  AON: "Aon plc", MMC: "Marsh McLennan", MET: "MetLife Inc", AFL: "Aflac Inc", ALL: "Allstate Corp",
  TRV: "The Travelers Companies", PLD: "Prologis Inc", AMT: "American Tower", EQIX: "Equinix Inc", O: "Realty Income", SPG: "Simon Property Group",

  // Cluster 12: CONSUMER_TRAVEL_AUTO
  ABNB: "Airbnb Inc", MAR: "Marriott International", HLT: "Hilton Worldwide", EXPE: "Expedia Group", RCL: "Royal Caribbean Group",
  CCL: "Carnival Corp", DAL: "Delta Air Lines", UAL: "United Airlines", GM: "General Motors", F: "Ford Motor",
  CMG: "Chipotle Mexican Grill", YUM: "Yum! Brands", DPZ: "Domino's Pizza", LULU: "Lululemon Athletica", ORLY: "O'Reilly Automotive",

  // Cluster 13: BIOTECH_MED_DIAGNOSTICS
  REGN: "Regeneron Pharmaceuticals", BIIB: "Biogen Inc", MRNA: "Moderna Inc", ALNY: "Alnylam Pharmaceuticals", INCY: "Incyte Corp",
  EXAS: "Exact Sciences", IDXX: "IDEXX Laboratories", DXCM: "Dexcom Inc", ZBH: "Zimmer Biomet", BSX: "Boston Scientific",
  SYK: "Stryker Corp", MDT: "Medtronic plc", BDX: "Becton Dickinson", EW: "Edwards Lifesciences", IQV: "IQVIA Holdings",

  // Cluster 14: DEFENSE_AERO_TRANSPORT
  NOC: "Northrop Grumman", GD: "General Dynamics", LHX: "L3Harris Technologies", HWM: "Howmet Aerospace", TDG: "TransDigm Group",
  HEI: "HEICO Corp", TXT: "Textron Inc", AXON: "Axon Enterprise", FDX: "FedEx Corp", CSX: "CSX Corp",
  NSC: "Norfolk Southern", ODFL: "Old Dominion Freight Line", URI: "United Rentals", PWR: "Quanta Services", EMR: "Emerson Electric",

  // Cluster 15: ENERGY_MINING_MATERIALS
  OXY: "Occidental Petroleum", MPC: "Marathon Petroleum", PSX: "Phillips 66", VLO: "Valero Energy", HAL: "Halliburton Co",
  BKR: "Baker Hughes", KMI: "Kinder Morgan", WMB: "Williams Companies", CCJ: "Cameco Corp", NEM: "Newmont Corp",
  FCX: "Freeport-McMoRan", GOLD: "Barrick Gold", LIN: "Linde plc", APD: "Air Products & Chemicals", SHW: "Sherwin-Williams",

  // Cluster 16: UTILITIES_GRID_DIVIDEND
  NEE: "NextEra Energy", CEG: "Constellation Energy", VST: "Vistra Corp", DUK: "Duke Energy", SO: "Southern Company",
  AEP: "American Electric Power", D: "Dominion Energy", EXC: "Exelon Corp", CL: "Colgate-Palmolive", KMB: "Kimberly-Clark",
  MDLZ: "Mondelez International", GIS: "General Mills", HSY: "The Hershey Company", KHC: "Kraft Heinz", KR: "Kroger Co",

  // Cluster 17: IPO_INCUBATOR
  RDDT: "Reddit Inc", KVUE: "Kenvue Inc", BIRK: "Birkenstock Holding", CART: "Maplebear (Instacart)",
  ALAB: "Astera Labs", ANRO: "Alto Neuroscience", TEM: "Tempus AI", CAVA: "CAVA Group",
};

const CLUSTERS_META: { id: string; label: string; count: number }[] = [
  { id: "ALL", label: "Todos", count: 248 },
  { id: "IPO_INCUBATOR", label: "🐣 Incubadora IPOs", count: 15 },
  { id: "TECH_GROWTH_A", label: "Tech Mega-Cap", count: 15 },
  { id: "TECH_GROWTH_B", label: "Semis & Software", count: 15 },
  { id: "FINANCE_CONSUMER_A", label: "Finanzas Core", count: 15 },
  { id: "FINANCE_CONSUMER_B", label: "Consumo & Retail", count: 15 },
  { id: "HEALTH_INDUSTRIAL_A", label: "Salud & Pharma", count: 15 },
  { id: "HEALTH_INDUSTRIAL_B", label: "MedTech & Industria", count: 15 },
  { id: "HIGH_BETA_EXPANSION_A", label: "High Beta Core", count: 15 },
  { id: "HIGH_BETA_EXPANSION_B", label: "Next-Gen Growth", count: 15 },
  { id: "CYBER_CLOUD_ENTERPRISE", label: "Ciberseguridad & Cloud", count: 15 },
  { id: "SEMI_AI_INFRASTRUCTURE", label: "Semis & Hardware IA", count: 15 },
  { id: "FINTECH_INSURANCE_REIT", label: "Fintech, Seguros & REITs", count: 15 },
  { id: "CONSUMER_TRAVEL_AUTO", label: "Viajes, Auto & Ocio", count: 15 },
  { id: "BIOTECH_MED_DIAGNOSTICS", label: "Biotecnología & Diagnóstico", count: 15 },
  { id: "DEFENSE_AERO_TRANSPORT", label: "Defensa & Logística", count: 15 },
  { id: "ENERGY_MINING_MATERIALS", label: "Energía & Minería", count: 15 },
  { id: "UTILITIES_GRID_DIVIDEND", label: "Utilities & Dividendos", count: 15 },
];

export function LiveMarketScannerGrid({
  marketWatch,
  selectedTicker,
  onSelectTicker,
  onScanNow,
  isScanning,
}: ScannerGridProps) {
  const qc = useQueryClient();
  const [activeCluster, setActiveCluster] = React.useState<string>("ALL");
  const [searchQuery, setSearchQuery] = React.useState<string>("");
  const [showSuggestions, setShowSuggestions] = React.useState<boolean>(false);

  // Estados de Modales
  const [lookupModalOpen, setLookupModalOpen] = React.useState<boolean>(false);
  const [lookupQuery, setLookupQuery] = React.useState<string>("");
  const [lookupResult, setLookupResult] = React.useState<any>(null);
  const [isLookingUp, setIsLookingUp] = React.useState<boolean>(false);
  const [selectedReplacementTicker, setSelectedReplacementTicker] = React.useState<string>("");
  const [actionFeedback, setActionFeedback] = React.useState<{ type: "success" | "error"; msg: string } | null>(null);

  // Modal de Graduación
  const [graduatingTicker, setGraduatingTicker] = React.useState<string | null>(null);
  const [targetCluster, setTargetCluster] = React.useState<string>("TECH_GROWTH_A");
  const [targetReplaceTicker, setTargetReplaceTicker] = React.useState<string>("");

  // Consulta en tiempo real de la Incubadora
  const incubatorQuery = useQuery({
    queryKey: ["incubator-status"],
    queryFn: () => api.getIncubatorStatus(),
    refetchInterval: 12000,
  });
  const incubatorStatus = incubatorQuery.data;

  // Mutaciones de la Incubadora
  const addMutation = useMutation({
    mutationFn: (vars: { ticker: string; replace_ticker?: string }) =>
      api.addTickerToIncubator(vars.ticker, vars.replace_ticker),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["incubator-status"] });
      qc.invalidateQueries({ queryKey: ["scanner-opportunities"] });
      setActionFeedback({ type: "success", msg: data.message });
      setLookupModalOpen(false);
      setLookupResult(null);
      setSelectedReplacementTicker("");
      onScanNow(false);
    },
    onError: (err: any) => {
      setActionFeedback({ type: "error", msg: err.message || "Error al añadir a la incubadora" });
    },
  });

  const removeMutation = useMutation({
    mutationFn: (ticker: string) => api.removeTickerFromIncubator(ticker),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["incubator-status"] });
      qc.invalidateQueries({ queryKey: ["scanner-opportunities"] });
      setActionFeedback({ type: "success", msg: data.message });
      onScanNow(false);
    },
    onError: (err: any) => {
      setActionFeedback({ type: "error", msg: err.message || "Error al quitar de la incubadora" });
    },
  });

  const graduateMutation = useMutation({
    mutationFn: (vars: { ticker: string; target_cluster: string; replace_ticker: string }) =>
      api.graduateTickerFromIncubator(vars.ticker, vars.target_cluster, vars.replace_ticker),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["incubator-status"] });
      qc.invalidateQueries({ queryKey: ["scanner-opportunities"] });
      setActionFeedback({ type: "success", msg: data.message });
      setGraduatingTicker(null);
      setTargetReplaceTicker("");
      onScanNow(false);
    },
    onError: (err: any) => {
      setActionFeedback({ type: "error", msg: err.message || "Error al graduar ticker" });
    },
  });

  const handleLookup = async (sym: string) => {
    if (!sym.trim()) return;
    setIsLookingUp(true);
    setActionFeedback(null);
    try {
      const res = await api.lookupTicker(sym.trim().toUpperCase());
      setLookupResult(res);
      if (res.valid && res.incubator_full && res.incubator_tickers?.length) {
        setSelectedReplacementTicker(res.incubator_tickers[0]);
      }
    } catch (err: any) {
      setLookupResult({ valid: false, message: err.message || "Error de conexión consultando Wall Street." });
    } finally {
      setIsLookingUp(false);
    }
  };

  const handleLookupAndOpenModal = (sym: string) => {
    setLookupQuery(sym.trim().toUpperCase());
    setLookupModalOpen(true);
    handleLookup(sym.trim().toUpperCase());
  };

  const handleDirectAddFromSuggestion = (ticker: string) => {
    addMutation.mutate({ ticker });
  };

  const filteredItems = React.useMemo(() => {
    let list = marketWatch;
    const q = searchQuery.trim().toLowerCase();

    if (q) {
      return list.filter((item) => {
        const t = (item.ticker || "").toLowerCase();
        const name = (TICKER_NAMES[item.ticker] || "").toLowerCase();
        const cluster = (item.cluster_id || "").toLowerCase();
        return t.includes(q) || name.includes(q) || cluster.includes(q);
      });
    }

    if (activeCluster === "ALL") return list;
    return list.filter((item) => {
      if (item.cluster_id === activeCluster) return true;
      if (activeCluster === "IPO_INCUBATOR" && item.cluster_id === "IPO_INCUBATOR") return true;
      // Compatibilidad hacia atrás con prefijos antiguos
      if (activeCluster === "TECH_GROWTH_A" && item.cluster_id === "TECH_GROWTH") return true;
      if (activeCluster === "FINANCE_CONSUMER_A" && item.cluster_id === "FINANCE_CONSUMER") return true;
      if (activeCluster === "HEALTH_INDUSTRIAL_A" && item.cluster_id === "HEALTH_INDUSTRIAL") return true;
      if (activeCluster === "HIGH_BETA_EXPANSION_A" && item.cluster_id === "HIGH_BETA_EXPANSION") return true;
      return false;
    });
  }, [marketWatch, activeCluster, searchQuery]);

  return (
    <div className="flex h-full flex-col rounded-xl border border-line bg-surface overflow-hidden shadow-sm relative">
      {/* Notificación de Éxito / Error */}
      {actionFeedback && (
        <div
          className={clsx(
            "flex items-center justify-between px-4 py-2 text-xs font-semibold z-20 border-b",
            actionFeedback.type === "success"
              ? "bg-emerald-50 text-emerald-800 border-emerald-200"
              : "bg-rose-50 text-rose-800 border-rose-200",
          )}
        >
          <div className="flex items-center gap-2">
            {actionFeedback.type === "success" ? (
              <CheckCircle2 className="h-4 w-4 text-buy shrink-0" />
            ) : (
              <AlertCircle className="h-4 w-4 text-sell shrink-0" />
            )}
            <span>{actionFeedback.msg}</span>
          </div>
          <button
            onClick={() => setActionFeedback(null)}
            className="p-0.5 text-ink-soft hover:text-ink cursor-pointer"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      )}

      {/* Barra de Controles del Radar */}
      <div className="flex items-center justify-between border-b border-line px-4 py-2.5 bg-slate-50/50 flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <Activity className="h-4 w-4 text-scan animate-pulse" />
          <span className="text-xs font-bold text-ink" suppressHydrationWarning>
            Radar Cuantitativo Multiclúster (17 Subagentes · {marketWatch.length || 248} Activos)
          </span>
          <span className="text-[11px] font-medium text-scan">· Barrido paralelo 17 hilos</span>
          <span className="inline-flex items-center rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-semibold text-emerald-700 border border-emerald-200">
            ⚡ Subagente Disparador Activo
          </span>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => onScanNow(true)}
            disabled={isScanning}
            className="flex items-center gap-1 rounded-md border border-scan/30 bg-scan-bg px-2.5 py-1 text-xs font-medium text-scan transition-colors hover:bg-blue-100 disabled:opacity-50 cursor-pointer"
            title="Selecciona el activo con mejor estructura cuantitativa entre los 17 clústeres"
          >
            <Play className="h-3 w-3" /> Simular Mejor Setup (PoC)
          </button>

          <button
            onClick={() => onScanNow(false)}
            disabled={isScanning}
            className="flex items-center gap-1 rounded-md border border-line bg-white px-3 py-1 text-xs font-semibold text-ink transition-colors hover:bg-slate-50 disabled:opacity-50 cursor-pointer"
          >
            {isScanning ? "Escaneando..." : "Escanear Ahora"}
          </button>
        </div>
      </div>

      {/* Buscador Rápido de Empresas y Tickers */}
      <div className="flex items-center justify-between border-b border-line bg-white px-3 py-2 gap-2">
        <div className="relative flex-1 max-w-md">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-ink-soft" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && filteredItems.length > 0) {
                onSelectTicker(filteredItems[0].ticker);
              }
            }}
            placeholder="Buscar ticker o empresa (ej: NVDA, Apple, Fortinet, Ciberseguridad)..."
            className="w-full rounded-md border border-line bg-slate-50 pl-8 pr-7 py-1 text-xs text-ink placeholder:text-ink-soft focus:bg-white focus:border-scan focus:outline-none focus:ring-1 focus:ring-scan transition-colors"
          />
          {searchQuery && (
            <button
              onClick={() => setSearchQuery("")}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-ink-soft hover:text-ink p-0.5"
              title="Borrar búsqueda"
            >
              <X className="h-3 w-3" />
            </button>
          )}
        </div>
        <div className="flex items-center gap-2 text-[11px]">
          {searchQuery ? (
            <span className="font-semibold text-scan">
              {filteredItems.length} resultado{filteredItems.length === 1 ? "" : "s"} para &quot;{searchQuery}&quot;
            </span>
          ) : (
            <span className="text-ink-soft hidden sm:inline">
              Presiona <kbd className="rounded border border-line bg-slate-100 px-1 py-0.5 text-[10px] font-mono">Enter</kbd> para ir al gráfico del 1er resultado
            </span>
          )}
        </div>
      </div>

      {/* Sugerencia para consultar en Wall Street e incorporar a la Incubadora si no hay coincidencias locales */}
      {searchQuery && filteredItems.length === 0 && (
        <div className="flex items-center justify-between border-b border-scan/20 bg-blue-50/70 px-4 py-2 text-xs">
          <div className="flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-scan shrink-0" />
            <span className="text-ink">
              ¿Buscando <strong className="font-bold text-scan">{searchQuery.toUpperCase()}</strong>? No está en los clústeres actuales. Puedes consultarlo en Wall Street para incorporarlo a la Incubadora IPO ({incubatorStatus?.used_slots ?? 8}/15 plazas).
            </span>
          </div>
          <button
            onClick={() => handleLookupAndOpenModal(searchQuery)}
            disabled={isLookingUp}
            className="flex items-center gap-1.5 rounded-md bg-scan px-3 py-1 font-semibold text-white shadow-xs hover:bg-blue-600 transition-colors disabled:opacity-50 shrink-0 cursor-pointer"
          >
            {isLookingUp ? <RefreshCw className="h-3 w-3 animate-spin" /> : <Search className="h-3 w-3" />}
            {isLookingUp ? "Consultando..." : `Consultar ${searchQuery.toUpperCase()}`}
          </button>
        </div>
      )}

      {/* Pestañas de Subagentes por Clúster */}
      <div className="flex items-center border-b border-line bg-slate-100/70 px-3 py-1.5 gap-1.5 overflow-x-auto scrollbar-thin">
        {CLUSTERS_META.map((c) => {
          const isActive = !searchQuery && activeCluster === c.id;
          const displayLabel = c.id === "ALL"
            ? `Todos (${marketWatch.length || 248})`
            : c.id === "IPO_INCUBATOR"
            ? `🐣 Incubadora IPOs (${incubatorStatus?.used_slots ?? 8}/15)`
            : c.label;

          return (
            <button
              key={c.id}
              onClick={() => {
                setActiveCluster(c.id);
                setSearchQuery("");
              }}
              className={clsx(
                "rounded-md px-2.5 py-1 text-xs font-semibold transition-all whitespace-nowrap cursor-pointer",
                isActive
                  ? "bg-white text-ink shadow-xs ring-1 ring-slate-200"
                  : "text-ink-soft hover:bg-white/60 hover:text-ink",
              )}
            >
              {displayLabel}
            </button>
          );
        })}
      </div>

      {/* Banner de Estado de la Incubadora IPO (cuando la pestaña activa es IPO_INCUBATOR) */}
      {activeCluster === "IPO_INCUBATOR" && (
        <div className="flex items-center justify-between border-b border-amber-200 bg-amber-50/80 px-4 py-2 text-xs flex-wrap gap-2">
          <div className="flex items-center gap-2">
            <span className="text-base">🐣</span>
            <div>
              <span className="font-bold text-ink">Incubadora de Salidas a Bolsa (IPOs) & Debuts Recientes</span>
              <span className="ml-2 rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-bold text-amber-800 border border-amber-300">
                {incubatorStatus?.used_slots ?? 8}/15 plazas ocupadas
              </span>
              <span className="ml-1 text-[11px] text-ink-soft">
                ({incubatorStatus?.available_slots ?? 7} plazas libres para nuevas incorporaciones)
              </span>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => {
                setLookupQuery("");
                setLookupResult(null);
                setLookupModalOpen(true);
              }}
              className="flex items-center gap-1 rounded bg-amber-600 px-2.5 py-1 text-xs font-semibold text-white shadow-xs hover:bg-amber-700 transition-colors cursor-pointer"
            >
              <Plus className="h-3.5 w-3.5" /> Añadir Ticker a Incubadora
            </button>
            {incubatorStatus?.recent_hot_suggestions && incubatorStatus.recent_hot_suggestions.length > 0 && (
              <button
                onClick={() => setShowSuggestions(!showSuggestions)}
                className="flex items-center gap-1 rounded border border-amber-300 bg-white px-2.5 py-1 text-xs font-semibold text-amber-800 hover:bg-amber-100/50 transition-colors cursor-pointer"
              >
                <Sparkles className="h-3.5 w-3.5 text-amber-600" />
                {showSuggestions ? "Ocultar Sugerencias" : "Ver Sugerencias de IPOs"}
              </button>
            )}
          </div>
        </div>
      )}

      {/* Sugerencias de IPOs recientes en Wall Street */}
      {activeCluster === "IPO_INCUBATOR" && showSuggestions && incubatorStatus?.recent_hot_suggestions && incubatorStatus.recent_hot_suggestions.length > 0 && (
        <div className="border-b border-amber-200 bg-white px-4 py-2.5">
          <span className="text-[10px] font-bold uppercase tracking-wider text-ink-soft block mb-1.5">
            🎯 Debuts recientes en Wall Street listos para añadir con 1 clic:
          </span>
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-2">
            {incubatorStatus.recent_hot_suggestions.map((sug) => (
              <div key={sug.ticker} className="flex items-center justify-between border border-line rounded-lg p-2 bg-slate-50">
                <div className="flex flex-col">
                  <div className="flex items-center gap-1.5">
                    <span className="font-bold text-ink text-xs">{sug.ticker}</span>
                    <span className="text-[9px] rounded bg-amber-100 px-1 text-amber-800">{sug.debut}</span>
                  </div>
                  <span className="text-[10px] text-ink-soft truncate max-w-[120px]">{sug.name}</span>
                  <span className="text-[9px] text-ink-soft/80">{sug.sector}</span>
                </div>
                <button
                  onClick={() => handleDirectAddFromSuggestion(sug.ticker)}
                  disabled={addMutation.isPending}
                  className="rounded bg-scan px-2.5 py-1 text-[10px] font-bold text-white hover:bg-blue-600 disabled:opacity-50 transition-colors shrink-0 cursor-pointer"
                >
                  + Añadir
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
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
                        <span
                          className="text-[10px] text-ink-soft truncate max-w-[130px] font-medium"
                          title={TICKER_NAMES[item.ticker] || item.cluster_id}
                        >
                          · {TICKER_NAMES[item.ticker] || item.cluster_id?.replace(/_/g, " ")}
                        </span>
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
                    <div className="flex items-center justify-end gap-1">
                      {(item.cluster_id === "IPO_INCUBATOR" || activeCluster === "IPO_INCUBATOR") && (
                        <>
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              setGraduatingTicker(item.ticker);
                              const defaultTarget = incubatorStatus?.available_clusters?.[0]?.id || "TECH_GROWTH_A";
                              setTargetCluster(defaultTarget);
                              const defaultList = marketWatch
                                .filter((m) => m.cluster_id === defaultTarget)
                                .map((m) => m.ticker);
                              if (defaultList.length > 0) {
                                setTargetReplaceTicker(defaultList[0]);
                              }
                            }}
                            className="rounded p-1 text-emerald-600 hover:bg-emerald-50 hover:text-emerald-700 transition-colors cursor-pointer"
                            title="Graduar de la Incubadora a un clúster sectorial definitivo"
                          >
                            <GraduationCap className="h-4 w-4" />
                          </button>
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              if (confirm(`¿Dar de baja a ${item.ticker} de la Incubadora para liberar una plaza?`)) {
                                removeMutation.mutate(item.ticker);
                              }
                            }}
                            disabled={removeMutation.isPending}
                            className="rounded p-1 text-rose-500 hover:bg-rose-50 hover:text-rose-700 transition-colors disabled:opacity-50 cursor-pointer"
                            title="Dar de baja de la Incubadora y liberar plaza"
                          >
                            <Trash2 className="h-4 w-4" />
                          </button>
                        </>
                      )}
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          onSelectTicker(item.ticker);
                        }}
                        className="rounded p-1 text-ink-soft hover:bg-slate-200 hover:text-ink cursor-pointer"
                        title="Ver gráfico con EMA20, SMA50 y SMA200"
                      >
                        <ArrowUpRight className="h-4 w-4" />
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>

        {filteredItems.length === 0 && (
          <div className="flex flex-col items-center justify-center p-12 text-center">
            {searchQuery ? (
              <>
                <Search className="h-8 w-8 text-slate-300 mb-2" />
                <p className="text-sm font-bold text-ink">Sin resultados locales para &quot;{searchQuery}&quot;</p>
                <p className="text-xs text-ink-soft mt-1 max-w-md">
                  No se encontró ningún activo monitorizado con ese ticker o nombre. Puedes consultarlo en Wall Street para incorporarlo a la Incubadora IPO.
                </p>
                <div className="flex items-center gap-2 mt-3">
                  <button
                    onClick={() => handleLookupAndOpenModal(searchQuery)}
                    className="rounded bg-scan px-3 py-1.5 text-xs font-bold text-white hover:bg-blue-600 transition-colors cursor-pointer"
                  >
                    🔍 Consultar {searchQuery.toUpperCase()} en Mercado
                  </button>
                  <button
                    onClick={() => setSearchQuery("")}
                    className="rounded border border-line bg-white px-3 py-1.5 text-xs font-semibold text-ink hover:bg-slate-50 transition-colors cursor-pointer"
                  >
                    Limpiar búsqueda
                  </button>
                </div>
              </>
            ) : (
              <>
                <Activity className="h-8 w-8 text-scan/60 animate-pulse mb-3" />
                <p className="text-sm font-bold text-ink">Radar en espera de datos</p>
                <p className="text-xs text-ink-soft mt-1 max-w-md">
                  El motor cuantitativo no tiene datos cargados para este clúster. Pulsa el botón para iniciar un barrido inmediato del universo de 248 acciones.
                </p>
                <button
                  onClick={() => onScanNow(false)}
                  disabled={isScanning}
                  className="mt-4 flex items-center gap-1.5 rounded-md bg-scan px-4 py-2 text-xs font-semibold text-white shadow-sm hover:bg-blue-600 transition-colors disabled:opacity-50 cursor-pointer"
                >
                  <Play className="h-3.5 w-3.5" /> {isScanning ? "Escaneando universo..." : "Escanear Ahora"}
                </button>
              </>
            )}
          </div>
        )}
      </div>

      {/* MODAL 1: BÚSQUEDA Y ALTA EN INCUBADORA */}
      {lookupModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-xs p-4">
          <div className="w-full max-w-md rounded-xl bg-white p-5 shadow-2xl border border-line flex flex-col gap-4">
            <div className="flex items-center justify-between border-b border-line pb-3">
              <div className="flex items-center gap-2">
                <span className="text-xl">🐣</span>
                <div>
                  <h3 className="text-sm font-bold text-ink">Añadir Activo a la Incubadora IPO</h3>
                  <p className="text-[11px] text-ink-soft">
                    {incubatorStatus?.used_slots ?? 8}/15 plazas ocupadas ({incubatorStatus?.available_slots ?? 7} libres)
                  </p>
                </div>
              </div>
              <button
                onClick={() => {
                  setLookupModalOpen(false);
                  setLookupResult(null);
                }}
                className="rounded p-1 text-ink-soft hover:bg-slate-100 hover:text-ink cursor-pointer"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            {/* Cuadro de Búsqueda de Ticker */}
            <div className="flex items-center gap-2">
              <div className="relative flex-1">
                <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-ink-soft" />
                <input
                  type="text"
                  value={lookupQuery}
                  onChange={(e) => setLookupQuery(e.target.value.toUpperCase())}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && lookupQuery.trim()) {
                      handleLookup(lookupQuery);
                    }
                  }}
                  placeholder="Ticker de Wall Street (ej: ANTH, CAVA, RDDT)..."
                  className="w-full rounded-md border border-line bg-slate-50 pl-9 pr-3 py-1.5 text-xs font-semibold text-ink uppercase placeholder:normal-case focus:bg-white focus:border-scan focus:outline-none focus:ring-1 focus:ring-scan"
                />
              </div>
              <button
                onClick={() => handleLookup(lookupQuery)}
                disabled={isLookingUp || !lookupQuery.trim()}
                className="flex items-center gap-1 rounded-md bg-scan px-3 py-1.5 text-xs font-bold text-white hover:bg-blue-600 disabled:opacity-50 transition-colors cursor-pointer"
              >
                {isLookingUp ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : "Consultar"}
              </button>
            </div>

            {/* Resultado de la Consulta */}
            {lookupResult && (
              <div className="rounded-lg border border-line bg-slate-50 p-3.5 flex flex-col gap-2.5">
                {lookupResult.valid ? (
                  <>
                    <div className="flex items-start justify-between">
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="text-base font-extrabold text-ink">{lookupResult.ticker}</span>
                          <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-bold text-emerald-800">
                            Activo Verificado
                          </span>
                        </div>
                        <p className="text-xs font-semibold text-ink-soft">{lookupResult.name}</p>
                        <p className="text-[10px] text-ink-soft/80">{lookupResult.sector}</p>
                      </div>
                      <div className="text-right">
                        <span className="text-sm font-bold text-ink">${lookupResult.price.toFixed(2)}</span>
                      </div>
                    </div>

                    {lookupResult.already_monitored ? (
                      <div className="rounded bg-blue-50 border border-blue-200 p-2 text-xs text-blue-900">
                        Este activo ya está monitorizado en el clúster <strong>{lookupResult.cluster_id}</strong>.
                      </div>
                    ) : (
                      <>
                        {lookupResult.incubator_full ? (
                          <div className="flex flex-col gap-2 pt-2 border-t border-line">
                            <span className="text-xs font-bold text-amber-800">
                              ⚠️ Plazas completas (15/15). Elige a qué activo relevar:
                            </span>
                            <div className="max-h-32 overflow-y-auto divide-y divide-line rounded border border-line bg-white">
                              {lookupResult.incubator_tickers?.map((t: string) => (
                                <label
                                  key={t}
                                  className="flex items-center justify-between p-2 text-xs hover:bg-slate-50 cursor-pointer"
                                >
                                  <span className="font-semibold text-ink">{t} ({TICKER_NAMES[t] || "Incubadora"})</span>
                                  <input
                                    type="radio"
                                    name="replacement"
                                    checked={selectedReplacementTicker === t}
                                    onChange={() => setSelectedReplacementTicker(t)}
                                    className="text-scan"
                                  />
                                </label>
                              ))}
                            </div>
                            <button
                              onClick={() => {
                                if (selectedReplacementTicker) {
                                  addMutation.mutate({
                                    ticker: lookupResult.ticker,
                                    replace_ticker: selectedReplacementTicker,
                                  });
                                }
                              }}
                              disabled={addMutation.isPending || !selectedReplacementTicker}
                              className="rounded-md bg-amber-600 py-2 text-xs font-bold text-white shadow-xs hover:bg-amber-700 disabled:opacity-50 transition-colors cursor-pointer"
                            >
                              {addMutation.isPending ? "Añadiendo..." : `Reemplazar ${selectedReplacementTicker} por ${lookupResult.ticker}`}
                            </button>
                          </div>
                        ) : (
                          <button
                            onClick={() => addMutation.mutate({ ticker: lookupResult.ticker })}
                            disabled={addMutation.isPending}
                            className="mt-1 rounded-md bg-scan py-2 text-xs font-bold text-white shadow-xs hover:bg-blue-600 disabled:opacity-50 transition-colors cursor-pointer"
                          >
                            {addMutation.isPending
                              ? "Añadiendo..."
                              : `➕ Incorporar ${lookupResult.ticker} a la Incubadora (${(lookupResult.incubator_slots_used || 0) + 1}/15)`}
                          </button>
                        )}
                      </>
                    )}
                  </>
                ) : (
                  <div className="text-xs text-rose-700 flex items-center gap-1.5">
                    <AlertCircle className="h-4 w-4 shrink-0" />
                    <span>{lookupResult.message || "No se encontró el ticker en mercados de EE.UU."}</span>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* MODAL 2: GRADUACIÓN A CLÚSTER SECTORIAL */}
      {graduatingTicker && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-xs p-4">
          <div className="w-full max-w-md rounded-xl bg-white p-5 shadow-2xl border border-line flex flex-col gap-4">
            <div className="flex items-center justify-between border-b border-line pb-3">
              <div className="flex items-center gap-2">
                <GraduationCap className="h-5 w-5 text-emerald-600" />
                <div>
                  <h3 className="text-sm font-bold text-ink">Graduar {graduatingTicker} a Clúster Sectorial</h3>
                  <p className="text-[11px] text-ink-soft">
                    Mueve la empresa a su sector definitivo relevando al activo que elijas para mantener siempre 15.
                  </p>
                </div>
              </div>
              <button
                onClick={() => setGraduatingTicker(null)}
                className="rounded p-1 text-ink-soft hover:bg-slate-100 hover:text-ink cursor-pointer"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            {/* Selector de Clúster Destino */}
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-bold text-ink">1. Selecciona el Clúster Sectorial Destino:</label>
              <select
                value={targetCluster}
                onChange={(e) => {
                  const newC = e.target.value;
                  setTargetCluster(newC);
                  const list = marketWatch
                    .filter((m) => m.cluster_id === newC)
                    .map((m) => m.ticker);
                  if (list.length > 0) {
                    setTargetReplaceTicker(list[0]);
                  }
                }}
                className="rounded-md border border-line bg-white px-3 py-1.5 text-xs font-semibold text-ink focus:border-scan focus:outline-none"
              >
                {incubatorStatus?.available_clusters?.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name} ({c.id})
                  </option>
                ))}
              </select>
            </div>

            {/* Selector del Activo a Relevar en el Destino */}
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-bold text-ink">
                2. Selecciona a qué activo de {targetCluster} relevar:
              </label>
              <div className="max-h-40 overflow-y-auto divide-y divide-line rounded border border-line bg-white">
                {marketWatch
                  .filter((m) => m.cluster_id === targetCluster)
                  .map((item) => (
                    <label
                      key={item.ticker}
                      className="flex items-center justify-between p-2 text-xs hover:bg-slate-50 cursor-pointer"
                    >
                      <div className="flex flex-col">
                        <span className="font-bold text-ink">{item.ticker} · {TICKER_NAMES[item.ticker] || ""}</span>
                        <span className="text-[10px] text-ink-soft">
                          Score {item.quant_score ?? 50}/100 · ${item.price.toFixed(2)}
                        </span>
                      </div>
                      <input
                        type="radio"
                        name="graduate_replace"
                        checked={targetReplaceTicker === item.ticker}
                        onChange={() => setTargetReplaceTicker(item.ticker)}
                        className="text-scan"
                      />
                    </label>
                  ))}
              </div>
            </div>

            <button
              onClick={() => {
                if (graduatingTicker && targetCluster && targetReplaceTicker) {
                  graduateMutation.mutate({
                    ticker: graduatingTicker,
                    target_cluster: targetCluster,
                    replace_ticker: targetReplaceTicker,
                  });
                }
              }}
              disabled={graduateMutation.isPending || !targetReplaceTicker}
              className="rounded-md bg-emerald-600 py-2 text-xs font-bold text-white shadow-xs hover:bg-emerald-700 disabled:opacity-50 transition-colors cursor-pointer"
            >
              {graduateMutation.isPending
                ? "Graduando..."
                : `🎓 Confirmar Graduación (${graduatingTicker} releva a ${targetReplaceTicker})`}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
