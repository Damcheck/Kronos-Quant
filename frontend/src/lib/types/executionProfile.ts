// Shared execution-profile draft shape used by the strategy detail page and the
// reusable ExecutionSettingsFields form. Kept in $lib so both the page-local logic
// (validation, sizing-mode defaults, payload encoding) and the presentational form
// component agree on the exact field set.
export type SizingMode = 'full' | 'fraction' | 'fixed' | 'atr' | 'kelly';

export type ExecutionProfileDraft = {
	initial_capital: string;
	fee_bps: string;
	slippage_bps: string;
	leverage: string;
	sizing_mode: SizingMode;
	risk_per_trade: string;
	fixed_size: string;
	atr_stop_multiplier: string;
	kelly_multiplier: string;
	kelly_lookback: string;
	stop_loss_pct: string;
	take_profit_pct: string;
	trailing_stop_pct: string;
	time_stop_bars: string;
};
