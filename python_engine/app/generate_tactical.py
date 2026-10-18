import urllib.request
import json
import os

req = urllib.request.Request('http://localhost:8000/scanner/scan-now', method='POST', data=b'')
with urllib.request.urlopen(req, timeout=120) as resp:
    data = json.loads(resp.read().decode())
mw = data.get('market_watch', [])

print(f"Total empresas analizadas en market_watch: {len(mw)}")

# Add calculated fields
for s in mw:
    p = float(s['price'])
    ema20 = float(s['ema_20'])
    sma50 = float(s['sma_50'])
    sma200 = float(s['sma_200'])
    k = float(s['stoch_k'])
    d = float(s['stoch_d'])
    rsi = float(s['rsi_14'])
    
    s['above_ema20'] = (p >= ema20)
    s['above_sma50'] = (p >= sma50)
    s['above_sma200'] = (p >= sma200)
    s['dist_ema20_pct'] = ((p - ema20) / ema20 * 100.0) if ema20 else 0.0
    s['dist_sma50_pct'] = ((p - sma50) / sma50 * 100.0) if sma50 else 0.0
    s['dist_sma200_pct'] = ((p - sma200) / sma200 * 100.0) if sma200 else 0.0

    # Categorize oscillator status:
    # 1. 'PULLBACK_TRIGGER_READY': Stoch reset/turning up (K <= 55 or K crossing D from lower levels) and RSI 40-60 (not overbought, ready for entry)
    # 2. 'MOMENTUM_OVERBOUGHT': Stoch > 80, RSI > 70 (strong trend, but extended, needs pullback or breakout confirmation)
    # 3. 'TURNING_UP': K > D but under 80
    # 4. 'FALLING/OVERSOLD': K < D
    if k <= 55 and (k >= d or abs(k - d) <= 4.0) and 40.0 <= rsi <= 60.0:
        osc_state = "IDEAL_PULLBACK_READY"
    elif k > d and k < 80 and 45.0 <= rsi <= 65.0:
        osc_state = "BULLISH_HOOK_READY"
    elif k >= 80 or rsi >= 70.0:
        osc_state = "EXTENDED_OVERBOUGHT"
    elif k < d and k >= 60:
        osc_state = "COOLING_DOWN"
    elif k <= 30:
        osc_state = "OVERSOLD_BASE"
    else:
        osc_state = "NEUTRAL"
    s['osc_state'] = osc_state

# Sort by quant_score desc
mw.sort(key=lambda x: (x['quant_score'], x['above_ema20'], -x['falling_knife']), reverse=True)

with open("python_engine/app/tactical_watchlist_data.json", "w", encoding="utf-8") as f:
    json.dump(mw, f, indent=2)

print("\n" + "="*80)
print("=== SELECCIÓN TÁCTICA PARA EL LUNES: MEJORES ACTIVOS PREPARADOS ===")
print("="*80)

top_candidates = [s for s in mw if s['above_ema20'] and not s['falling_knife'] and s['quant_score'] >= 60]

print(f"{'Ticker':<6} | {'Cluster':<19} | {'Score':<5} | {'Precio':<8} | {'EMA20':<8} | {'Dist%':<7} | {'Stoch K/D':<11} | {'RSI':<5} | {'ATR14':<6} | {'SL (1.5x)':<9} | {'TP (+2R)':<9} | {'Setup Táctico'}")
print("-" * 135)
for s in top_candidates:
    t = s['ticker']
    cl = s['cluster_id']
    sc = s['quant_score']
    p = s['price']
    e20 = s['ema_20']
    dist = s['dist_ema20_pct']
    stk = f"{s['stoch_k']:.1f}/{s['stoch_d']:.1f}"
    rsi = s['rsi_14']
    atr = s['atr_14']
    sl = round(p - 1.5 * atr, 2)
    tp = round(p + 3.0 * atr, 2)
    
    if s['osc_state'] == 'IDEAL_PULLBACK_READY':
        setup = "COMPRA EN SOPORTE / PULLBACK (Sin sobrecompra)"
    elif s['osc_state'] == 'BULLISH_HOOK_READY':
        setup = "GIRO ALCISTA ACTIVO (Buen momentum)"
    elif dist > 5.0 and s['osc_state'] == 'EXTENDED_OVERBOUGHT':
        setup = "TENDENCIAL FUERTE (Esperar dip a EMA20/VWAP)"
    else:
        setup = "TENDENCIAL SANO"
        
    print(f"{t:<6} | {cl:<19} | {sc:<5} | {p:<8.2f} | {e20:<8.2f} | {dist:>+5.1f}% | {stk:<11} | {rsi:<5.1f} | {atr:<6.2f} | {sl:<9.2f} | {tp:<9.2f} | {setup}")

