"""
Asesor IA (Gemini) — segunda opinión cualitativa sobre una oportunidad ya validada por el motor cuantitativo.

Principios de diseño:
- NUNCA bloquea el camino crítico: en MANUAL se ejecuta en segundo plano; en FULL_AUTO solo se
  espera con un timeout corto y, si falla, se aplica la política configurada (fail-open por defecto).
- NUNCA puede aprobar algo que el motor cuantitativo rechazó: solo puede VETAR o matizar.
- Salida estructurada (JSON) y validada; cualquier respuesta rara se trata como "no disponible".
- La clave se lee de GEMINI_API_KEY y no se registra en logs.
"""
import json
import logging
import os
import time
from typing import Any, Optional

import httpx

logger = logging.getLogger("quant-engine.ai")

GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
VALID_VERDICTS = ("APPROVE", "CAUTION", "REJECT")

SYSTEM_PROMPT = (
    "Eres el Subagente #3 del Comité de Admisión (Especialista en Empresa, Catalizadores y Riesgo). "
    "Evalúas entradas largas filtradas por el motor 'Los 3 Trucos' (Marea Diaria+VWAP, Pullback sin sobreprecio, "
    "Ignición intradía 5m/15m con volumen). Tu misión es vetar trampas antes de arriesgar capital: "
    "1) EMITE REJECT OBLIGATORIO si hay resultados (earnings) en las próximas 48 horas, investigación regulatoria, "
    "downgrade severo reciente, dilución/oferta secundaria o noticia tóxica del ticker. "
    "2) EMITE REJECT si el flujo direccional diario es bajista (-DI > +DI) o hay cuchillo cayendo (falling_knife). "
    "3) EMITE REJECT si el precio entra sobreextendido tras subida vertical (dist_from_ema20_atr > 1.2), si el volumen "
    "en velas rojas de corrección supera al de las verdes, o si el Take Profit supera resistencias inmediatas. "
    "4) Usa CAUTION si no puedes verificar noticias recientes o el contexto macro sectorial está débil. "
    "Solo emite APPROVE si el contexto de empresa está limpio, sin earnings en <48h, y el setup intradía tiene "
    "recorrido realista sin trampas de liquidez. Responde en español."
)

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "verdict": {"type": "STRING", "enum": list(VALID_VERDICTS)},
        "confidence": {"type": "INTEGER", "description": "0-100"},
        "headline": {"type": "STRING", "description": "Una frase, máx 110 caracteres"},
        "rationale": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "2-3 puntos"},
        "key_risks": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "0-3 riesgos"},
        "data_limits": {"type": "STRING", "description": "Qué no pudiste verificar"},
    },
    "required": ["verdict", "confidence", "headline", "rationale", "key_risks", "data_limits"],
}


def ai_config() -> dict[str, Any]:
    return {
        "api_key": os.getenv("GEMINI_API_KEY", "").strip(),
        "model": os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip(),
        "timeout_seconds": float(os.getenv("GEMINI_TIMEOUT_SECONDS", "10")),
        "enabled": os.getenv("AI_ADVISOR_ENABLED", "true").lower() == "true",
        "use_search": os.getenv("GEMINI_USE_SEARCH", "true").lower() == "true",
    }


def _parse_json_lenient(text: str) -> dict[str, Any]:
    """Extrae el primer objeto JSON de la respuesta (tolera ```json ... ``` y texto alrededor)."""
    s = text.strip()
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("la respuesta no contiene JSON")
    return json.loads(s[start : end + 1])


def _extract_sources(data: dict[str, Any]) -> list[dict[str, str]]:
    """Fuentes web consultadas por el grounding (para poder auditar de dónde sale la opinión)."""
    out: list[dict[str, str]] = []
    try:
        meta = (data.get("candidates") or [{}])[0].get("groundingMetadata") or {}
        for ch in meta.get("groundingChunks") or []:
            web = ch.get("web") or {}
            if web.get("uri"):
                out.append({"title": str(web.get("title", ""))[:120], "uri": str(web["uri"])})
    except Exception:
        pass
    return out[:6]



def unavailable(reason: str, latency_ms: Optional[float] = None, model: Optional[str] = None) -> dict[str, Any]:
    return {
        "status": "UNAVAILABLE",
        "verdict": None,
        "reason": reason,
        "model": model,
        "latency_ms": latency_ms,
    }


def build_user_prompt(opp: dict[str, Any], portfolio_value: float) -> str:
    sizing = opp.get("order_sizing", {}) or {}
    metrics = opp.get("financial_metrics", {}) or {}
    entry = float(opp.get("entry_price", 0) or 0)
    sma = float(opp.get("sma_200") or 0)
    pct_sma = ((entry - sma) / sma * 100.0) if sma else None
    payload = {
        "ticker": opp.get("ticker"),
        "motor": "LOS_3_TRUCOS_INTRADIA",
        "regimen_mercado": opp.get("market_regime"),
        "sesgo_corto_plazo": opp.get("short_term_bias"),
        "quant_score_0_100": opp.get("quant_score"),
        "diagnostico_experto": opp.get("expert_summary"),
        "precio_entrada": entry,
        "vwap_sesion": opp.get("vwap"),
        "ema20_15m": opp.get("ema20_15m"),
        "atr_15m": opp.get("atr_15m"),
        "rsi_15m": opp.get("rsi_15m"),
        "rvol_intradia": opp.get("rvol_intraday"),
        "pullback_low": opp.get("pullback_low"),
        "distancia_ema20_en_atrs": opp.get("dist_from_ema20_atr"),
        "vol_promedio_velas_rojas_pb": opp.get("avg_red_pb_vol"),
        "vol_promedio_velas_verdes_impulso": opp.get("avg_green_imp_vol"),
        "ema_20_diaria": opp.get("ema_20"),
        "sma_50_diaria": opp.get("sma_50"),
        "sma_200_diaria": sma,
        "pct_sobre_sma200": round(pct_sma, 2) if pct_sma is not None else None,
        "adx_14": opp.get("adx"),
        "plus_di_compradores": opp.get("plus_di"),
        "minus_di_vendedores": opp.get("minus_di"),
        "di_spread": opp.get("di_spread"),
        "rvol_20_diario": opp.get("rvol_20"),
        "retorno_1d_pct": opp.get("ret_1d_pct"),
        "alerta_cuchillo_cayendo": opp.get("falling_knife"),
        "resistencia_reciente_swing_high": opp.get("intraday_swing_high") or opp.get("recent_swing_high"),
        "stop_loss": sizing.get("stop_loss_price") or opp.get("stop_loss_price"),
        "take_profit": sizing.get("take_profit_price") or opp.get("take_profit_price"),
        "acciones": sizing.get("shares_to_buy"),
        "capital_asignado_usd": sizing.get("total_capital_allocated"),
        "riesgo_cartera_pct": metrics.get("portfolio_risk_pct"),
        "beneficio_neto_esperado_usd": metrics.get("expected_net_profit"),
        "cartera_usd": round(portfolio_value, 2),
    }
    return "Setup Los 3 Trucos (intradía 5m/15m + 1D) a auditar por Comité #3 (JSON):\n" + json.dumps(payload, ensure_ascii=False)


def _extract_text(data: dict[str, Any]) -> str:
    cands = data.get("candidates") or []
    if not cands:
        raise ValueError(f"sin candidatos (promptFeedback={data.get('promptFeedback')})")
    parts = (cands[0].get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


def _validate(raw: dict[str, Any]) -> dict[str, Any]:
    verdict = str(raw.get("verdict", "")).upper()
    if verdict not in VALID_VERDICTS:
        raise ValueError(f"veredicto inválido: {verdict!r}")
    conf = max(0, min(100, int(raw.get("confidence", 0))))
    return {
        "verdict": verdict,
        "confidence": conf,
        "headline": str(raw.get("headline", ""))[:160],
        "rationale": [str(x)[:240] for x in (raw.get("rationale") or [])][:3],
        "key_risks": [str(x)[:200] for x in (raw.get("key_risks") or [])][:3],
        "data_limits": str(raw.get("data_limits", ""))[:240],
    }


async def analyze_opportunity(
    opp: dict[str, Any],
    portfolio_value: float,
    timeout_seconds: Optional[float] = None,
) -> dict[str, Any]:
    """Pide a Gemini el dictamen. Nunca lanza excepción: devuelve status DONE o UNAVAILABLE."""
    cfg = ai_config()
    if not cfg["enabled"]:
        return unavailable("AI_ADVISOR_ENABLED=false")
    if not cfg["api_key"]:
        return unavailable("GEMINI_API_KEY no configurada")

    timeout = timeout_seconds if timeout_seconds is not None else cfg["timeout_seconds"]
    use_search = cfg["use_search"]
    user_text = build_user_prompt(opp, portfolio_value)
    gen_cfg: dict[str, Any] = {
        "temperature": 0.2,
        "maxOutputTokens": 2048,
        "thinkingConfig": {"thinkingBudget": 0},
    }
    body: dict[str, Any] = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "generationConfig": gen_cfg,
    }
    if use_search:
        # Grounding con Google Search (noticias reales). No es compatible con JSON mode estricto:
        # se pide el JSON en el prompt y se parsea de forma tolerante.
        body["tools"] = [{"google_search": {}}]
        user_text += (
            "\n\nBusca noticias y resultados recientes de la empresa antes de valorar. Responde ÚNICAMENTE "
            "con un objeto JSON (sin texto adicional) con las claves: verdict (APPROVE|CAUTION|REJECT), "
            "confidence (0-100), headline, rationale (lista de 2-3 textos), key_risks (lista de 0-3 textos), "
            "data_limits (texto)."
        )
    else:
        gen_cfg["responseMimeType"] = "application/json"
        gen_cfg["responseSchema"] = RESPONSE_SCHEMA
    body["contents"] = [{"role": "user", "parts": [{"text": user_text}]}]

    headers = {"x-goog-api-key": cfg["api_key"], "Content-Type": "application/json"}
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                GEMINI_ENDPOINT.format(model=cfg["model"]), headers=headers, json=body
            )
        latency = round((time.perf_counter() - t0) * 1000.0, 0)
        if resp.status_code != 200:
            return unavailable(f"HTTP {resp.status_code}", latency, cfg["model"])
        data = resp.json()
        advice = _validate(_parse_json_lenient(_extract_text(data)))
        return {
            "status": "DONE",
            "model": cfg["model"],
            "latency_ms": latency,
            "used_search": use_search,
            "sources": _extract_sources(data),
            **advice,
        }

    except httpx.TimeoutException:
        return unavailable(f"timeout > {timeout}s", round((time.perf_counter() - t0) * 1000.0, 0), cfg["model"])
    except Exception as exc:  # respuesta mal formada, red, etc.
        logger.warning(f"Asesor IA no disponible: {type(exc).__name__}: {exc}")
        return unavailable(f"{type(exc).__name__}", round((time.perf_counter() - t0) * 1000.0, 0), cfg["model"])
