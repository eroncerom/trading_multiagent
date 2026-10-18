"use client";

import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, Play, ShieldAlert, Square, Zap } from "lucide-react";
import clsx from "clsx";
import { api } from "@/lib/api";
import { formatClock } from "@/lib/marketHours";
import { useNow } from "@/hooks/useNow";
import type { ScannerStatus } from "@/types/trading";

const DURATIONS: { label: string; value: number | null }[] = [
  { label: "Libre", value: null },
  { label: "30 min", value: 30 },
  { label: "1 h", value: 60 },
  { label: "2 h", value: 120 },
  { label: "4 h", value: 240 },
];

export function EngineSessionController({
  status,
  updatedAt,
}: {
  status: ScannerStatus | undefined;
  updatedAt: number;
}) {
  const qc = useQueryClient();
  const now = useNow(200);
  const [mode, setMode] = useState<"MANUAL" | "FULL_AUTO">("MANUAL");
  const [duration, setDuration] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const running = status?.running ?? false;

  // Mientras el motor corre, el selector refleja el modo real de la sesión
  useEffect(() => {
    if (status?.running) setMode(status.execution_mode);
  }, [status?.running, status?.execution_mode]);

  const start = useMutation({
    mutationFn: () => api.startEngine({ execution_mode: mode, session_duration_minutes: duration }),
    onSuccess: () => {
      setError(null);
      qc.invalidateQueries({ queryKey: ["scanner-status"] });
      qc.invalidateQueries({ queryKey: ["live-summary"] });
    },
    onError: (e: Error) => setError(e.message),
  });
  const stop = useMutation({
    mutationFn: api.stopEngine,
    onSuccess: () => {
      setError(null);
      qc.invalidateQueries({ queryKey: ["scanner-status"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const busy = start.isPending || stop.isPending;

  // Cronómetro anclado al timestamp exacto de inicio (started_at) con resolución de 200 ms:
  // evita que el polling cada 2s de React Query reinicie la fracción de segundo o dé saltos dobles.
  const startedAtMs = status?.started_at ? Date.parse(status.started_at) : NaN;
  const endsAtMs = status?.session_ends_at ? Date.parse(status.session_ends_at) : NaN;
  const sinceFetch = now && updatedAt ? Math.max(0, (now.getTime() - updatedAt) / 1000) : 0;

  const elapsed = running
    ? now && !Number.isNaN(startedAtMs)
      ? Math.max(0, (now.getTime() - startedAtMs) / 1000)
      : (status?.elapsed_seconds ?? 0) + sinceFetch
    : 0;

  const remaining =
    running && status?.session_remaining_seconds != null
      ? now && !Number.isNaN(endsAtMs)
        ? Math.max(0, (endsAtMs - now.getTime()) / 1000)
        : Math.max(0, status.session_remaining_seconds - sinceFetch)
      : null;

  function handleToggle() {
    if (running) {
      stop.mutate();
      return;
    }
    if (mode === "FULL_AUTO") {
      const ok = window.confirm(
        "MODO FULL AUTO: el motor ejecutará las entradas SIN pedir confirmación y se detendrá al perder el 4% de la cartera. ¿Arrancar?",
      );
      if (!ok) return;
    }
    start.mutate();
  }

  return (
    <div className="flex items-center gap-3">
      <div className="flex flex-col">
        <span className="text-[10px] font-semibold uppercase tracking-wider text-ink-soft">
          Sesión
        </span>
        <span
          className={clsx(
            "num text-xl font-semibold leading-none",
            running ? "text-ink" : "text-slate-400",
          )}
        >
          {formatClock(elapsed)}
        </span>
        {remaining != null && (
          <span className="num mt-0.5 text-[10px] text-scan">quedan {formatClock(remaining)}</span>
        )}
      </div>

      <button
        onClick={handleToggle}
        disabled={busy}
        className={clsx(
          "flex h-10 items-center gap-2 rounded-lg px-4 text-sm font-semibold transition-colors disabled:opacity-60",
          running
            ? "bg-sell-bg text-sell ring-1 ring-rose-200 hover:bg-rose-100"
            : "bg-buy-bg text-buy ring-1 ring-emerald-200 hover:bg-emerald-100",
        )}
      >
        {busy ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : running ? (
          <Square className="h-4 w-4" />
        ) : (
          <Play className="h-4 w-4" />
        )}
        {running ? "Stop Engine" : "Start Engine"}
      </button>

      <div className="flex rounded-lg bg-slate-100 p-0.5 text-xs font-semibold">
        {(["MANUAL", "FULL_AUTO"] as const).map((m) => (
          <button
            key={m}
            disabled={running}
            onClick={() => setMode(m)}
            className={clsx(
              "flex items-center gap-1 rounded-md px-2.5 py-1.5 transition-colors disabled:cursor-not-allowed",
              mode === m
                ? m === "FULL_AUTO"
                  ? "bg-warn-bg text-warn shadow-sm"
                  : "bg-white text-ink shadow-sm"
                : "text-ink-soft",
            )}
          >
            {m === "FULL_AUTO" ? <Zap className="h-3 w-3" /> : null}
            {m === "MANUAL" ? "Manual" : "Full Auto"}
          </button>
        ))}
      </div>

      <select
        value={duration ?? ""}
        disabled={running}
        onChange={(e) => setDuration(e.target.value === "" ? null : Number(e.target.value))}
        className="h-8 rounded-lg border border-line bg-white px-2 text-xs font-medium text-ink-soft disabled:opacity-60"
        aria-label="Duración de la sesión"
      >
        {DURATIONS.map((d) => (
          <option key={d.label} value={d.value ?? ""}>
            {d.label}
          </option>
        ))}
      </select>

      {error && (
        <span className="flex max-w-56 items-center gap-1 truncate text-xs text-sell" title={error}>
          <ShieldAlert className="h-3.5 w-3.5 shrink-0" /> {error}
        </span>
      )}
    </div>
  );
}
