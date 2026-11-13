"use client";

import { useState, useRef, useEffect } from "react";
import {
  Sparkles,
  X,
  Send,
  RotateCcw,
  TrendingUp,
  ShieldAlert,
  HelpCircle,
  Activity,
  Bot,
  User,
  Zap,
} from "lucide-react";
import clsx from "clsx";
import { api } from "@/lib/api";
import type { CopilotMessage } from "@/types/trading";

interface TradingCopilotDrawerProps {
  isOpen: boolean;
  onClose: () => void;
}

const QUICK_ACTIONS = [
  {
    id: "market",
    label: "🌐 ¿Cómo está el mercado?",
    query: "¿Cómo está el mercado hoy?",
  },
  {
    id: "watchlist",
    label: "🎯 Activos en seguimiento",
    query: "¿Qué activos están en seguimiento prioritario?",
  },
  {
    id: "readiness",
    label: "⚡ ¿Hay previsión de operar hoy?",
    query: "¿Hay previsión de operar hoy según el mercado?",
  },
  {
    id: "prepare_tomorrow",
    label: "🌅 Preparar mercado de mañana",
    query: "Prepara la aplicación para el mercado de mañana: revisa parámetros, limpia sesiones previas y deja todo listo para la apertura.",
  },
  {
    id: "overview",
    label: "📊 Resumen de cartera",
    query: "Dame un resumen de la cartera y posiciones vivas",
  },
];

// Parser ligero y seguro para Markdown básico (encabezados, negritas, listas y saltos)
function FormattedMessage({ text }: { text: string }) {
  const lines = text.split("\n");

  return (
    <div className="space-y-2 text-[12.5px] leading-relaxed text-slate-800 dark:text-slate-200">
      {lines.map((line, idx) => {
        const trimmed = line.trim();
        if (!trimmed) {
          return <div key={idx} className="h-1" />;
        }

        // Encabezados ### o ##
        if (trimmed.startsWith("### ")) {
          return (
            <h4 key={idx} className="mt-2 text-xs font-bold uppercase tracking-wider text-indigo-600 dark:text-indigo-400">
              {trimmed.replace("### ", "")}
            </h4>
          );
        }
        if (trimmed.startsWith("## ")) {
          return (
            <h3 key={idx} className="mt-3 text-sm font-bold text-slate-900 dark:text-white">
              {trimmed.replace("## ", "")}
            </h3>
          );
        }

        // Elementos de lista (* o -)
        if (trimmed.startsWith("* ") || trimmed.startsWith("- ")) {
          const content = trimmed.substring(2);
          return (
            <div key={idx} className="flex items-start gap-1.5 pl-2">
              <span className="text-indigo-500 font-bold leading-none mt-1.5">•</span>
              <span className="flex-1">{renderFormattedText(content)}</span>
            </div>
          );
        }

        // Línea estándar
        return <p key={idx}>{renderFormattedText(trimmed)}</p>;
      })}
    </div>
  );
}

function renderFormattedText(str: string) {
  // Manejo sencillo de negritas **texto**
  const parts = str.split(/(\*\*.*?\*\*)/g);
  return parts.map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return (
        <strong key={i} className="font-semibold text-slate-900 dark:text-slate-100">
          {part.slice(2, -2)}
        </strong>
      );
    }
    return part;
  });
}

export function TradingCopilotDrawer({ isOpen, onClose }: TradingCopilotDrawerProps) {
  const [messages, setMessages] = useState<CopilotMessage[]>([
    {
      id: "welcome",
      role: "assistant",
      content:
        "👋 **¡Hola! Soy tu Copilot Cuantitativo.**\n\nEstoy conectado a la telemetría en vivo del sistema. Puedo informarte sobre:\n* **El régimen de mercado** (SPY/QQQ vs VWAP, sesgo y amplitud).\n* **Activos en seguimiento** de los 4 clústeres y sus puntuaciones técnicas.\n* **Previsión de operativa** hoy y si hay riesgo de veto *Risk-Off*.\n* **Estado de tu cartera** en Alpaca (posiciones, PnL y Circuit Breaker).\n\nSelecciona una consulta rápida o pregúntame lo que necesites.",
      timestamp: "Ahora",
      source: "SISTEMA",
    },
  ]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isOpen) {
      setTimeout(() => inputRef.current?.focus(), 150);
    }
  }, [isOpen]);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, loading]);

  const handleSend = async (customQuery?: string, quickAction?: string) => {
    const textToSend = customQuery || input;
    if (!textToSend.trim() || loading) return;

    const userMsg: CopilotMessage = {
      id: `usr-${Date.now()}`,
      role: "user",
      content: textToSend,
      timestamp: "Ahora",
    };

    setMessages((prev) => [...prev, userMsg]);
    if (!customQuery) setInput("");
    setLoading(true);

    try {
      const historyPayload = messages
        .filter((m) => m.id !== "welcome")
        .slice(-4)
        .map((m) => ({ role: m.role, content: m.content }));

      const res = await api.copilotQuery({
        query: textToSend,
        history: historyPayload,
        quick_action: quickAction as any,
      });

      const assistantMsg: CopilotMessage = {
        id: `asst-${Date.now()}`,
        role: "assistant",
        content: res.reply || "No he podido procesar la respuesta.",
        source: res.source,
        latency_ms: res.latency_ms,
        timestamp: "Ahora",
      };

      setMessages((prev) => [...prev, assistantMsg]);
    } catch (err: any) {
      const errorMsg: CopilotMessage = {
        id: `err-${Date.now()}`,
        role: "assistant",
        content: `⚠️ Hubo un error al consultar el Copilot: ${err?.message || "Servicio no disponible"}. Asegúrate de que el motor backend esté en ejecución.`,
        source: "ERROR",
        timestamp: "Ahora",
      };
      setMessages((prev) => [...prev, errorMsg]);
    } finally {
      setLoading(false);
    }
  };

  const handleClear = () => {
    setMessages([
      {
        id: "welcome-reset",
        role: "assistant",
        content: "Chat reiniciado. ¿En qué te puedo ayudar ahora con los activos o el mercado?",
        timestamp: "Ahora",
        source: "SISTEMA",
      },
    ]);
  };

  return (
    <>
      {/* Backdrop */}
      {isOpen && (
        <div
          className="fixed inset-0 z-40 bg-slate-950/40 backdrop-blur-[2px] transition-opacity"
          onClick={onClose}
        />
      )}

      {/* Drawer Panel */}
      <aside
        className={clsx(
          "fixed right-0 top-0 z-50 flex h-full w-full max-w-[460px] flex-col border-l border-slate-200 bg-white shadow-2xl transition-transform duration-300 ease-in-out dark:border-slate-800 dark:bg-slate-900",
          isOpen ? "translate-x-0" : "translate-x-full"
        )}
      >
        {/* Header */}
        <div className="flex h-[68px] shrink-0 items-center justify-between border-b border-slate-200 px-4 dark:border-slate-800">
          <div className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-tr from-indigo-600 to-violet-500 text-white shadow-sm shadow-indigo-500/20">
              <Sparkles className="h-5 w-5" />
            </div>
            <div>
              <div className="flex items-center gap-1.5">
                <span className="text-sm font-bold text-slate-900 dark:text-white">Copilot Táctico</span>
                <span className="inline-flex items-center rounded-md bg-indigo-50 px-1.5 py-0.5 text-[9.5px] font-semibold text-indigo-700 dark:bg-indigo-950/80 dark:text-indigo-300">
                  IA Live
                </span>
              </div>
              <span className="text-[11px] text-slate-500 dark:text-slate-400">
                Supervisión de Activos y Mercado
              </span>
            </div>
          </div>

          <div className="flex items-center gap-1">
            <button
              onClick={handleClear}
              title="Reiniciar conversación"
              className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-slate-800 dark:hover:text-slate-200"
            >
              <RotateCcw className="h-4 w-4" />
            </button>
            <button
              onClick={onClose}
              title="Cerrar panel"
              className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-slate-800 dark:hover:text-slate-200"
            >
              <X className="h-5 w-5" />
            </button>
          </div>
        </div>

        {/* Quick Actions Bar */}
        <div className="shrink-0 border-b border-slate-100 bg-slate-50/70 p-2.5 dark:border-slate-800/80 dark:bg-slate-900/50">
          <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-400 px-1 mb-1.5">
            Consultas Rápidas
          </div>
          <div className="flex flex-wrap gap-1.5">
            {QUICK_ACTIONS.map((action) => (
              <button
                key={action.id}
                disabled={loading}
                onClick={() => handleSend(action.query, action.id)}
                className="rounded-lg border border-slate-200/90 bg-white px-2 py-1 text-[11px] font-medium text-slate-700 shadow-xs hover:border-indigo-300 hover:bg-indigo-50/50 hover:text-indigo-700 disabled:opacity-50 dark:border-slate-700/80 dark:bg-slate-800 dark:text-slate-300 dark:hover:border-indigo-500/50 dark:hover:bg-indigo-950/50 dark:hover:text-indigo-200"
              >
                {action.label}
              </button>
            ))}
          </div>
        </div>

        {/* Messages List */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-3.5">
          {messages.map((msg) => {
            const isUser = msg.role === "user";
            return (
              <div
                key={msg.id}
                className={clsx("flex gap-2.5", isUser ? "flex-row-reverse" : "flex-row")}
              >
                <div
                  className={clsx(
                    "flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-xs font-semibold shadow-xs",
                    isUser
                      ? "bg-slate-700 text-white dark:bg-slate-600"
                      : "bg-indigo-600 text-white"
                  )}
                >
                  {isUser ? <User className="h-3.5 w-3.5" /> : <Bot className="h-3.5 w-3.5" />}
                </div>

                <div
                  className={clsx(
                    "flex max-w-[85%] flex-col rounded-2xl px-3.5 py-2.5 shadow-xs",
                    isUser
                      ? "rounded-tr-none bg-indigo-600 text-white"
                      : "rounded-tl-none border border-slate-200/90 bg-slate-50/80 dark:border-slate-800 dark:bg-slate-800/80"
                  )}
                >
                  {isUser ? (
                    <p className="text-[12.5px] leading-relaxed whitespace-pre-wrap">{msg.content}</p>
                  ) : (
                    <FormattedMessage text={msg.content} />
                  )}

                  <div
                    className={clsx(
                      "mt-1.5 flex items-center justify-between gap-2 border-t pt-1 text-[9.5px]",
                      isUser
                        ? "border-indigo-500/40 text-indigo-100"
                        : "border-slate-200/60 text-slate-400 dark:border-slate-700/60"
                    )}
                  >
                    <span>{msg.timestamp}</span>
                    {msg.source && (
                      <span className="font-mono text-[9px]">
                        {msg.source}
                        {msg.latency_ms ? ` · ${msg.latency_ms}ms` : ""}
                      </span>
                    )}
                  </div>
                </div>
              </div>
            );
          })}

          {loading && (
            <div className="flex gap-2.5">
              <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-indigo-600 text-white shadow-xs">
                <Bot className="h-3.5 w-3.5" />
              </div>
              <div className="flex items-center gap-2 rounded-2xl rounded-tl-none border border-slate-200 bg-slate-50 px-3.5 py-2 text-[12px] text-slate-500 dark:border-slate-800 dark:bg-slate-800 dark:text-slate-400">
                <Zap className="h-3.5 w-3.5 animate-pulse text-indigo-500" />
                <span>Analizando telemetría y régimen de mercado...</span>
              </div>
            </div>
          )}
        </div>

        {/* Input Bar */}
        <div className="shrink-0 border-t border-slate-200 bg-white p-3 dark:border-slate-800 dark:bg-slate-900">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              handleSend();
            }}
            className="flex items-center gap-2"
          >
            <input
              ref={inputRef}
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Pregunta sobre un activo, el mercado o la operativa..."
              disabled={loading}
              className="flex-1 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-[12.5px] text-slate-800 placeholder-slate-400 transition-colors focus:border-indigo-500 focus:bg-white focus:outline-none dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100 dark:placeholder-slate-500 dark:focus:border-indigo-400 dark:focus:bg-slate-900"
            />
            <button
              type="submit"
              disabled={!input.trim() || loading}
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-indigo-600 text-white shadow-sm transition-opacity hover:bg-indigo-700 disabled:opacity-40"
            >
              <Send className="h-4 w-4" />
            </button>
          </form>
          <div className="mt-1.5 text-center text-[10px] text-slate-400">
            Copilot asistencial humano · Supervisión de 60 activos y gestión de riesgo 1%.
          </div>
        </div>
      </aside>
    </>
  );
}
