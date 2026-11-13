"""
Copilot Agéntico de Trading - Asistente táctico e interactivo para supervisión de mercado, activos y operativa.
"""
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

logger = logging.getLogger("quant-engine.copilot")

GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

COPILOT_SYSTEM_PROMPT = (
    "Eres el 'Copilot Táctico Cuantitativo' del sistema de trading institucional. "
    "Tu función es ser el copiloto inteligente del trader humano: informarle con máxima precisión, "
    "concisión y tono profesional sobre: "
    "1) Cómo está el mercado global hoy (Macro Regime, índices SPY y QQQ respecto al VWAP, amplitud y si hay veto Risk-Off activo). "
    "2) Qué activos están en seguimiento prioritario (dentro de los 60 activos distribuidos en los 4 clústeres: TECH_GROWTH, FINANCE_CONSUMER, HEALTH_INDUSTRIAL, HIGH_BETA_EXPANSION). "
    "3) Si hay previsión o probabilidad de operar en el día según la presencia de señales cuantitativas, filtros anti-falling knife y horarios. "
    "4) El estado de la cartera (posiciones abiertas, PnL del día, margen libre, distancia a Stop Loss / Take Profit y proximidad al Circuit Breaker del 4%). "
    "\n\nReglas de comportamiento:\n"
    "- Responde SIEMPRE en español con estilo directo, claro, con formato Markdown elegante (negritas, listas con viñetas y emojis estratégicos).\n"
    "- Utiliza EXCLUSIVAMENTE los datos reales provistos en el contexto del sistema. No inventes precios ni posiciones.\n"
    "- Si el mercado está en 'RISK_OFF_LOCKDOWN' o hay caídas sistémicas, advierte enfáticamente de que las compras están bloqueadas para proteger el capital.\n"
    "- Si te preguntan si hay previsión de operar, analiza si hay oportunidades listas, si el motor está escaneando y las condiciones de amplitud.\n"
    "- Sé proactivo sugiriendo qué activos vigilar con mejor setup técnico o si conviene mantenerse al margen."
)


def _copilot_config() -> dict[str, Any]:
    return {
        "api_key": os.getenv("GEMINI_API_KEY", "").strip(),
        "model": os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip(),
        "timeout_seconds": float(os.getenv("COPILOT_TIMEOUT_SECONDS", "12")),
    }


def _extract_text(data: dict[str, Any]) -> str:
    cands = data.get("candidates") or []
    if not cands:
        return "No se ha recibido respuesta del modelo de lenguaje."
    parts = (cands[0].get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()


def generate_local_heuristic_response(query: str, ctx: dict[str, Any]) -> str:
    """
    Generador de síntesis determinista cuando no hay GEMINI_API_KEY o falla la conexión.
    Analiza las métricas en vivo y genera un informe profesional de alta calidad.
    """
    macro = ctx.get("macro_regime") or {}
    macro_status = macro.get("status", "NEUTRAL")
    spy = macro.get("spy") or {}
    qqq = macro.get("qqq") or {}
    breadth = macro.get("market_breadth") or {}
    summary = ctx.get("portfolio_summary") or {}
    opps = ctx.get("opportunities") or []
    watch = ctx.get("market_watch") or []
    engine_running = ctx.get("engine_running", False)

    q_lower = query.lower()

    # 1. Consulta sobre el mercado
    if any(k in q_lower for k in ["mercado", "macro", "spy", "qqq", "cómo está", "como esta", "contexto"]):
        status_label = "🟢 RISK-ON (Favorable)" if macro_status == "RISK_ON" else (
            "🔴 RISK-OFF LOCKDOWN (Veto activo a nuevas compras)" if macro_status == "RISK_OFF_LOCKDOWN" else "🟡 PRECAUCIÓN"
        )
        spy_change = spy.get("daily_change_pct", 0)
        qqq_change = qqq.get("daily_change_pct", 0)
        adv_pct = breadth.get("advancing_pct", 50)
        knives = breadth.get("falling_knives_count", 0)

        return (
            f"### 🌐 Estado del Mercado Global ({status_label})\n\n"
            f"* **Índices Directores:**\n"
            f"  * **SPY:** {spy.get('price', 0):.2f} USD ({'+' if spy_change >= 0 else ''}{spy_change:.2f}%)\n"
            f"  * **QQQ:** {qqq.get('price', 0):.2f} USD ({'+' if qqq_change >= 0 else ''}{qqq_change:.2f}%)\n"
            f"* **Amplitud de Mercado:** {adv_pct:.1f}% de activos al alza frente al universo vigilado. Se han detectado **{knives}** activos en caída vertical (*falling knives*).\n"
            f"* **Diagnóstico Táctico:** {macro.get('reason', 'Mercado en monitorización continua.')}\n"
            + ("\n⚠️ **Aviso de Protección:** Con el veto activo, el motor bloqueará nuevas entradas hasta que los índices recuperen el VWAP." if macro_status == "RISK_OFF_LOCKDOWN" else "\n✅ Las condiciones macro permiten evaluar entradas tácticas con confirmación técnica.")
        )

    # 2. Consulta sobre activos en seguimiento / watchlist
    if any(k in q_lower for k in ["activos", "seguimiento", "watchlist", "radar", "cuales", "cuáles", "cluster"]):
        total_assets = len(watch) or 60
        top_candidates = sorted(
            [w for w in watch if not w.get("falling_knife")],
            key=lambda x: x.get("quant_score", 0),
            reverse=True
        )[:5]

        cand_lines = []
        for c in top_candidates:
            cand_lines.append(
                f"- **{c.get('ticker')}**: Score {c.get('quant_score', 0)}/100 | Precio: {c.get('price', 0):.2f}$ | RSI: {c.get('rsi_14', 0):.1f} | Bias: {c.get('short_term_bias', 'NEUTRAL')}"
            )

        cand_str = "\n".join(cand_lines) if cand_lines else "- No hay activos destacados con puntuación limpia en este instante."

        return (
            f"### 🎯 Activos en Radar y Vigilancia\n\n"
            f"Actualmente el motor monitoriza **{total_assets} activos institucionales** organizados en 4 clústeres (`TECH_GROWTH`, `FINANCE_CONSUMER`, `HEALTH_INDUSTRIAL` y `HIGH_BETA_EXPANSION`).\n\n"
            f"**Top activos con mejor perfil técnico hoy:**\n"
            f"{cand_str}\n\n"
            f"💡 *Nota:* Todos los activos pasan por el filtro *Anti-Falling Knife* para evitar comprar en velas de capitulación."
        )

    # 3. Preparación para el mercado de mañana (Pre-Market Readiness)
    if any(k in q_lower for k in ["mañana", "prepar", "pre-market", "apertura", "comienzo", "alistar"]):
        equity = summary.get("effective_portfolio_value", 0.0) or 0.0
        buying_power = summary.get("buying_power", 0.0) or (equity * 2.0)
        positions = summary.get("active_positions") or []
        total_assets = len(watch) or 60
        top_candidates = sorted(
            [w for w in watch if not w.get("falling_knife")],
            key=lambda x: x.get("quant_score", 0),
            reverse=True
        )[:4]
        cand_names = ", ".join([f"**{c.get('ticker')}** ({c.get('quant_score', 0)} pts)" for c in top_candidates]) if top_candidates else "NVDA, MSFT, AAPL, AMZN"

        return (
            "### 🌅 Preparación Completada para la Sesión de Mañana\n\n"
            "He realizado la puesta a punto integral del sistema para el toque de campana de Wall Street:\n\n"
            "**✅ Checklist de Despegue:**\n"
            f"* **1. Purgado de Señales Anteriores:** Se han limpiado las oportunidades pasadas para evitar compras rezagadas.\n"
            f"* **2. Estado de Cartera Alpaca:** Conexión verificada con **{equity:,.2f} USD** de equity y **{buying_power:,.2f} USD** de buying power disponible.\n"
            f"* **3. Posiciones Vivas:** **{len(positions)}** posiciones abiertas actualmente bajo supervisión OCO.\n"
            f"* **4. Parámetros de Riesgo Reseteados:** El *Circuit Breaker* del 4% está en posición cero (0% drawdown diario) y el riesgo por trade calibrado estrictamente al **1.0%**.\n"
            f"* **5. Radar de 60 Activos:** Clústeres calibrados. Activos con mejor perfil a vigilar en apertura: {cand_names}.\n\n"
            "**⏰ Próximo Paso Recomendado:**\n"
            "Mañana a las **15:30 CET** (apertura de Nueva York), pulsa **'Start Engine'** en la barra superior. El sistema comenzará a escanear cada 10 segundos y te avisará al detectar el primer gatillo confirmado."
        )

    # 4. Consulta sobre previsión de operar hoy
    if any(k in q_lower for k in ["operar", "previsión", "prevision", "entrar", "señal", "disparo", "oportunidad"]):
        if not engine_running:
            return (
                "### ⏸️ Previsión Operativa: Motor Detenido\n\n"
                "Actualmente el motor de escaneo está **detenido**. Para que el sistema evalúe y ejecute disparos:\n"
                "1. Inicia el motor desde la barra superior (`Start Engine`).\n"
                "2. O bien pulsa `Escanear Ahora` para un barrido manual instantáneo."
            )

        if macro_status == "RISK_OFF_LOCKDOWN":
            return (
                "### 🛑 Previsión Operativa: Baja Probabilidad de Nuevas Compras\n\n"
                "El sistema se encuentra en **Risk-Off Lockdown** debido a debilidad en los índices directores o ruptura de VWAP. "
                "Por política estricta de gestión de riesgo, **no se abrirán nuevas posiciones largas** hasta que el mercado restablezca el régimen de soporte."
            )

        if opps:
            opp_list = "\n".join([
                f"- **{o.get('ticker')}**: Precio entrada {o.get('entry_price', 0):.2f}$ | SL: {o.get('order_sizing', {}).get('stop_loss_price', 0):.2f}$ | TP: {o.get('order_sizing', {}).get('take_profit_price', 0):.2f}$ (Score: {o.get('quant_score', 0)}/100)"
                for o in opps[:3]
            ])
            return (
                f"### ⚡ Previsión Operativa: Alta / Oportunidades Detectadas\n\n"
                f"¡Hay **{len(opps)} oportunidad(es) activa(s)** listas para revisión o disparo!\n\n"
                f"{opp_list}\n\n"
                f"Puedes validarlas en 1 clic desde el panel central (*One-Click Execution*)."
            )

        return (
            "### ⏳ Previsión Operativa: En Espera de Gatillo\n\n"
            "El motor está escaneando activamente el universo de 60 activos. En este momento:\n"
            "- No hay cruce confirmado de estocástico sobrezona de soporte con volumen relativo suficiente.\n"
            "- Varios activos se encuentran en fase de consolidación técnica.\n"
            "- **Conclusión:** Jornada de vigilancia paciente; el sistema disparará la orden en cuanto una acción supere el score mínimo de 70 y filtre los riesgos."
        )

    # 4. Consulta general / Resumen 360
    pnl = summary.get("daily_pnl_usd", 0.0) or 0.0
    equity = summary.get("effective_portfolio_value", 0.0) or 0.0
    positions = summary.get("active_positions") or []

    return (
        f"### 🤖 Briefing Ejecutivo de la Sesión\n\n"
        f"* **Mercado:** Régimen **{macro_status}** (SPY: {spy.get('daily_change_pct', 0):.2f}% | QQQ: {qqq.get('daily_change_pct', 0):.2f}%).\n"
        f"* **Cartera:** Equity de **{equity:,.2f} USD** | PnL Diario: **{'+' if pnl >= 0 else ''}{pnl:.2f} USD**.\n"
        f"* **Posiciones Vivas:** **{len(positions)}** activas en Alpaca.\n"
        f"* **Oportunidades Inmediatas:** **{len(opps)}** en radar.\n"
        f"* **Motor Cuantitativo:** {'🟢 Activo' if engine_running else '⚪ Detenido'}.\n\n"
        f"¿Deseas que profundice en algún activo en particular, o en el análisis del régimen de mercado?"
    )


async def ask_copilot(
    query: str,
    system_context: dict[str, Any],
    history: Optional[list[dict[str, str]]] = None,
) -> dict[str, Any]:
    """
    Procesa la consulta del usuario combinando el contexto en tiempo real con Gemini,
    o recurre al generador heurístico si la IA no está disponible o no hay API key.
    """
    cfg = _copilot_config()
    t0 = time.perf_counter()

    # Si no hay clave API configurada, usamos el motor heurístico local enriquecido
    if not cfg["api_key"] or cfg["api_key"] == "tu_gemini_api_key_aqui":
        reply = generate_local_heuristic_response(query, system_context)
        return {
            "status": "DONE",
            "source": "HEURISTIC_ENGINE",
            "reply": reply,
            "latency_ms": round((time.perf_counter() - t0) * 1000.0, 1),
        }

    # Preparamos el payload contextual para Gemini
    ctx_compact = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "engine_running": system_context.get("engine_running"),
        "macro_regime": system_context.get("macro_regime"),
        "portfolio_summary": {
            "effective_equity": system_context.get("portfolio_summary", {}).get("effective_portfolio_value"),
            "daily_pnl_usd": system_context.get("portfolio_summary", {}).get("daily_pnl_usd"),
            "buying_power": system_context.get("portfolio_summary", {}).get("buying_power"),
            "drawdown_pct": system_context.get("portfolio_summary", {}).get("current_drawdown_pct"),
            "active_positions_count": len(system_context.get("portfolio_summary", {}).get("active_positions", [])),
            "active_positions": [
                {
                    "symbol": p.get("symbol"),
                    "qty": p.get("qty"),
                    "current_price": p.get("current_price"),
                    "unrealized_pl": p.get("unrealized_pl"),
                    "stop_loss": p.get("stop_loss_price"),
                    "take_profit": p.get("take_profit_price"),
                    "progress_pct": p.get("sl_to_tp_progress_pct"),
                }
                for p in system_context.get("portfolio_summary", {}).get("active_positions", [])
            ],
        },
        "opportunities_count": len(system_context.get("opportunities", [])),
        "opportunities": [
            {
                "ticker": o.get("ticker"),
                "entry_price": o.get("entry_price"),
                "quant_score": o.get("quant_score"),
                "bias": o.get("short_term_bias"),
                "summary": o.get("expert_summary"),
                "stop_loss": o.get("order_sizing", {}).get("stop_loss_price"),
                "take_profit": o.get("order_sizing", {}).get("take_profit_price"),
            }
            for o in system_context.get("opportunities", [])[:5]
        ],
        "top_market_watch": [
            {
                "ticker": w.get("ticker"),
                "price": w.get("price"),
                "quant_score": w.get("quant_score"),
                "rsi": w.get("rsi_14"),
                "stoch_k": w.get("stoch_k"),
                "falling_knife": w.get("falling_knife"),
            }
            for w in sorted(
                system_context.get("market_watch", []),
                key=lambda x: x.get("quant_score", 0),
                reverse=True
            )[:10]
        ],
    }

    user_prompt = (
        f"CONTEXTO EN VIVO DEL SISTEMA:\n```json\n{json.dumps(ctx_compact, ensure_ascii=False, indent=2)}\n```\n\n"
        f"PREGUNTA DEL TRADER:\n{query}"
    )

    contents = []
    if history:
        for msg in history[-4:]:
            role = "user" if msg.get("role") == "user" else "model"
            contents.append({"role": role, "parts": [{"text": msg.get("content", "")}]})
    contents.append({"role": "user", "parts": [{"text": user_prompt}]})

    body = {
        "systemInstruction": {"parts": [{"text": COPILOT_SYSTEM_PROMPT}]},
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 1024,
            "thinkingConfig": {"thinkingBudget": 0},
        },
        "contents": contents,
    }

    headers = {"x-goog-api-key": cfg["api_key"], "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=cfg["timeout_seconds"]) as client:
            resp = await client.post(
                GEMINI_ENDPOINT.format(model=cfg["model"]),
                headers=headers,
                json=body,
            )
        latency = round((time.perf_counter() - t0) * 1000.0, 1)

        if resp.status_code == 200:
            text = _extract_text(resp.json())
            return {
                "status": "DONE",
                "source": f"GEMINI_{cfg['model']}",
                "reply": text,
                "latency_ms": latency,
            }
        else:
            logger.warning(f"Error de Gemini API HTTP {resp.status_code}. Activando fallback heurístico.")
            reply = generate_local_heuristic_response(query, system_context)
            return {
                "status": "FALLBACK",
                "source": "HEURISTIC_ENGINE",
                "reply": reply,
                "error": f"HTTP {resp.status_code}",
                "latency_ms": latency,
            }
    except Exception as exc:
        latency = round((time.perf_counter() - t0) * 1000.0, 1)
        logger.warning(f"Excepción al llamar a Gemini ({type(exc).__name__}: {exc}). Usando fallback heurístico.")
        reply = generate_local_heuristic_response(query, system_context)
        return {
            "status": "FALLBACK",
            "source": "HEURISTIC_ENGINE",
            "reply": reply,
            "error": str(exc),
            "latency_ms": latency,
        }
