export interface MacroBenchmarkMetrics {
  price: number;
  daily_change_pct: number;
  intraday_from_open_pct: number;
  effective_drop_pct: number;
  vwap: number;
  above_vwap: boolean;
}

export interface MacroRegimeState {
  enabled: boolean;
  status: "RISK_ON" | "CAUTION" | "RISK_OFF_LOCKDOWN";
  semaphore: "GREEN" | "YELLOW" | "RED";
  veto_active: boolean;
  score_boost_required: number;
  reason: string;
  spy: MacroBenchmarkMetrics;
  qqq: MacroBenchmarkMetrics;
  market_breadth: {
    total_assets: number;
    advancing_assets: number;
    declining_assets: number;
    advancing_pct: number;
    falling_knives_count: number;
  };
  vetoed_opportunities_count: number;
  vetoed_tickers: string[];
  cooldown_remaining_minutes: number;
  updated_at: string;
}

export interface ScannerStatus {
  running: boolean;
  is_scanning_now: boolean;
  execution_mode: "MANUAL" | "FULL_AUTO";
  interval_seconds: number;
  session_mode: "TIMED" | "UNLIMITED";
  session_duration_minutes: number | null;
  session_ends_at: string | null;
  session_remaining_seconds: number | null;
  started_at: string | null;
  elapsed_seconds: number;
  max_session_drawdown_pct: number;
  current_session_drawdown_pct: number;
  hard_stop_equity_4pct: number;
  portfolio_value: number;
  portfolio_source: string;
  scan_count: number;
  last_scan_duration_ms: number | null;
  last_stop_reason: string | null;
  last_error: string | null;
  tickers_count: number;
  macro_regime?: MacroRegimeState;
}

export interface LiveSummary {
  effective_portfolio_value: number;
  portfolio_source: string;
  alpaca_connected: boolean;
  alpaca_mode: string;
  alpaca_raw_equity: number | null;
  buying_power: number | null;
  cash: number | null;
  daily_pnl_usd: number | null;
  daily_pnl_pct: number | null;
  open_risk_exposure_usd: number;
  open_risk_exposure_pct: number;
  hard_stop_4pct_price: number;
  current_drawdown_pct: number;
  active_positions: PositionItem[];
  closed_trades: ClosedTradeItem[];
  macro_regime?: MacroRegimeState;
  performance?: {
    total_closed_trades: number;
    win_rate_pct: number | null;
    profit_factor: number | null;
    avg_rr_ratio: number | null;
    net_realized_pnl_usd: number;
    equity_curve: { timestamp: string | null; equity: number }[];
  };
}

export interface PositionItem {
  symbol: string;
  qty: number;
  side: string;
  avg_entry_price: number;
  current_price: number;
  market_value: number;
  unrealized_pl: number;
  unrealized_plpc: number;
  stop_loss_price: number | null;
  take_profit_price: number | null;
  sl_to_tp_progress_pct: number | null;
}

export interface ClosedTradeItem {
  trade_id: string;
  ticker: string;
  shares: number;
  entry_price: number;
  exit_price: number;
  exit_reason: string;
  opened_at: string;
  closed_at: string;
  duration_seconds: number | null;
  realized_pnl_usd: number;
  realized_pnl_pct: number;
  execution_mode: string;
}

export interface ExpertCriterion {
  label: string;
  passed: boolean;
  detail: string;
}

export interface MarketWatchItem {
  ticker: string;
  cluster_id?: string;
  price: number;
  ema_20?: number;
  ema20_slope_pct?: number;
  sma_50?: number;
  sma_200: number;
  adx: number;
  plus_di?: number;
  minus_di?: number;
  di_spread?: number;
  rsi_14?: number;
  stoch_k: number;
  stoch_d: number;
  atr_14: number;
  rvol_20?: number;
  ret_1d_pct?: number;
  ret_5d_pct?: number;
  close_location_value?: number;
  candle_body_atr?: number;
  falling_knife?: boolean;
  short_term_bias?: string;
  quant_score?: number;
  expert_summary?: string;
  expert_criteria?: ExpertCriterion[];
  market_regime: string;
  signal_triggered: boolean;
  provisional_stop_loss: number;
  provisional_take_profit: number;
  rejection_reason?: string;
  updated_at: string;
}

export interface AiAdvice {
  status: "DONE" | "PENDING" | "UNAVAILABLE";
  verdict?: "APPROVE" | "CAUTION" | "REJECT" | null;
  confidence?: number;
  headline?: string;
  rationale?: string[];
  key_risks?: string[];
  data_limits?: string;
  reason?: string;
  latency_ms?: number;
  sources?: { title: string; uri: string }[];
  requested_at?: string;
}

export interface OpportunityItem {
  opportunity_id: string;
  ticker: string;
  cluster_id?: string;
  market_regime: string;
  short_term_bias?: string;
  quant_score?: number;
  falling_knife?: boolean;
  expert_summary?: string;
  expert_criteria?: ExpertCriterion[];
  entry_price: number;
  ema_20?: number;
  ema20_slope_pct?: number;
  sma_50?: number;
  sma_200: number;
  atr_14: number;
  rsi_14?: number;
  rvol_20?: number;
  ret_1d_pct?: number;
  ret_5d_pct?: number;
  close_location_value?: number;
  candle_body_atr?: number;
  stoch_k: number;
  stoch_d: number;
  adx: number;
  plus_di?: number;
  minus_di?: number;
  di_spread?: number;
  detected_at: string;
  status: string;
  order_sizing: {
    shares_to_buy: number;
    total_capital_allocated: number;
    portfolio_allocation_pct: number;
    max_capital_allowed_per_trade: number;
    allocation_tier: string;
    sizing_capped_by: string;
    entry_limit_price: number;
    stop_loss_price: number;
    take_profit_price: number;
  };
  financial_metrics: {
    max_loss_with_commissions: number;
    portfolio_risk_pct: number;
    expected_net_profit: number;
    net_reward_risk_ratio: number;
    round_trip_commission: number;
    commission_to_gross_profit_pct: number;
  };
  ai_thesis?: {
    source: string;
    lines: string[];
  };
  ai_advice?: AiAdvice;
}

export interface CandleData {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface SmaPoint {
  time: string;
  value: number;
}

export interface CandleResponse {
  ticker: string;
  interval: string;
  candles: CandleData[];
  ema20_series?: SmaPoint[];
  sma50_series?: SmaPoint[];
  sma200_series: SmaPoint[];
  indicators: {
    price: number;
    ema_20?: number | null;
    sma_50?: number | null;
    sma_200: number | null;
    stoch_k: number;
    stoch_d: number;
    rsi_14?: number;
    adx: number;
    plus_di?: number;
    minus_di?: number;
    di_spread?: number;
    atr_14: number;
    rvol_20?: number;
    ret_1d_pct?: number;
    ret_5d_pct?: number;
    close_location_value?: number;
    candle_body_atr?: number;
    falling_knife?: boolean;
    short_term_bias?: string;
    quant_score?: number;
    expert_summary?: string;
    expert_criteria?: ExpertCriterion[];
    regime: string;
  };
  bracket_preview: {
    source: string;
    entry_price: number;
    stop_loss_price: number;
    take_profit_price: number;
    viable: boolean;
    reason?: string;
    order_sizing?: OpportunityItem["order_sizing"];
    financial_metrics?: OpportunityItem["financial_metrics"];
  };
}

export interface StartEngineRequest {
  execution_mode: "MANUAL" | "FULL_AUTO";
  session_duration_minutes: number | null;
}
