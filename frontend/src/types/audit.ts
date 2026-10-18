export interface AuditFinding {
  severity: "CRITICAL" | "WARNING" | "INFO" | "OK";
  code: string;
  title: string;
  analysis: string;
  recommendation: string;
}

export interface AuditReportItem {
  audit_id: string;
  created_at: string;
  session_id: string;
  trigger_reason: string;
  critical_findings: AuditFinding[];
  recommended_parameter_adjustments: Record<string, any>;
  auto_applied: boolean;
  quantitative_metrics: {
    total_trades_evaluated: number;
    closed_trades: number;
    open_or_pending_trades: number;
    wins: number;
    losses: number;
    win_rate_pct: number;
    profit_factor: number;
    net_realized_pnl_usd: number;
  };
}
