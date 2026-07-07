import type { StrategyContainerHistoryItem, StrategyContainerPayload } from '$lib/api/lifecycle';

type PineExportSource = 'macd' | 'ema_cross' | 'donchian' | 'rsi' | 'fallback';

export interface TradingViewExport {
	filename: string;
	pine: string;
	source: PineExportSource;
	warnings: string[];
}

function asRecord(value: unknown): Record<string, unknown> {
	return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function cleanIdentifier(value: unknown): string {
	return String(value ?? '')
		.trim()
		.toLowerCase()
		.replace(/[^a-z0-9]+/g, '_')
		.replace(/^_+|_+$/g, '')
		.slice(0, 80) || 'strategy';
}

function pineString(value: unknown): string {
	return String(value ?? '')
		.replace(/\\/g, '\\\\')
		.replace(/"/g, '\\"')
		.slice(0, 120);
}

function numericParam(params: Record<string, unknown>, keys: string[], fallback: number): number {
	for (const key of keys) {
		const parsed = Number(params[key]);
		if (Number.isFinite(parsed)) return parsed;
	}
	return fallback;
}

function hasAny(params: Record<string, unknown>, keys: string[]): boolean {
	return keys.some((key) => Object.prototype.hasOwnProperty.call(params, key));
}

function epochMs(value: unknown): number | null {
	if (typeof value !== 'string' || !value.trim()) return null;
	const parsed = Date.parse(value);
	return Number.isFinite(parsed) ? parsed : null;
}

function metricLine(label: string, value: unknown): string | null {
	if (value === null || value === undefined || value === '') return null;
	return `//   ${label.padEnd(15)}: ${String(value)}`;
}

function formatMetric(value: unknown): string | null {
	const numeric = Number(value);
	if (Number.isFinite(numeric)) return numeric.toFixed(Math.abs(numeric) >= 100 ? 0 : 2);
	return typeof value === 'string' && value.trim() ? value.trim() : null;
}

function latestBacktest(history: StrategyContainerHistoryItem[]): StrategyContainerHistoryItem | null {
	return [...history].sort((a, b) => Date.parse(b.created_at || '') - Date.parse(a.created_at || ''))[0] ?? null;
}

function isFailedOrPendingRun(item: StrategyContainerHistoryItem): boolean {
	// Mirror the page's status convention: an ABSENT status means succeeded (most real
	// backtest rows carry no status field), so only treat explicit non-success states as
	// unusable for the verification baseline.
	const status = String(item.config?.status ?? item.metrics?.status ?? '').trim().toLowerCase();
	return status === 'failed' || status === 'error' || status === 'running' || status === 'queued' || status === 'pending';
}

function selectedVerificationRun(container: StrategyContainerPayload): StrategyContainerHistoryItem | null {
	const pinnedId = String(container.strategy.pinned_backtest_id ?? '').trim();
	const backtests = container.history.backtests ?? [];
	const pinned = backtests.find((item) => item.result_id === pinnedId);
	if (pinned) return pinned;
	// Prefer the newest non-failed run as the comparison baseline so the exported header
	// doesn't present a failed run's (empty) metrics/window as the verification result.
	const usable = backtests.filter((item) => !isFailedOrPendingRun(item));
	return latestBacktest(usable.length > 0 ? usable : backtests);
}

function resolveParams(container: StrategyContainerPayload, verificationRun: StrategyContainerHistoryItem | null): Record<string, unknown> {
	const runParams = asRecord(verificationRun?.config?.params);
	const containerParams = asRecord(container.configuration.params);
	return Object.keys(runParams).length > 0 ? runParams : containerParams;
}

function detectSource(container: StrategyContainerPayload, params: Record<string, unknown>): PineExportSource {
	const identity = [
		container.strategy.name,
		container.strategy.type,
		container.configuration.type,
		container.configuration.strategy_name,
	].join(' ').toLowerCase();
	// Note: the bare `signal` key is intentionally NOT a MACD probe — it is a generic
	// param name other strategies (e.g. PPO) carry, and matching it here would export
	// them as MACD crossovers with unrelated logic. Genuine MACD strategies match on the
	// `macd` identity (name/type) or the explicit macd_* keys.
	if (identity.includes('macd') || hasAny(params, ['macd_fast', 'macd_slow', 'macd_signal'])) return 'macd';
	if (identity.includes('donchian') || hasAny(params, ['entry_len', 'exit_len', 'channel_length', 'donchian_period'])) return 'donchian';
	if (identity.includes('ema') || hasAny(params, ['ema_fast', 'ema_slow', 'fast_ema', 'slow_ema'])) return 'ema_cross';
	if (identity.includes('rsi') || hasAny(params, ['rsi_period', 'rsi_entry', 'rsi_exit', 'rsi_oversold', 'rsi_overbought'])) return 'rsi';
	return 'fallback';
}

function buildHeader(container: StrategyContainerPayload, params: Record<string, unknown>, run: StrategyContainerHistoryItem | null, source: PineExportSource): string {
	const metrics = run?.metrics ?? {};
	const metricLines = [
		metricLine('strategy_id', container.strategy.id),
		metricLine('strategy_name', container.strategy.name),
		metricLine('symbol', run?.symbol || container.configuration.symbol || container.strategy.symbol),
		metricLine('timeframe', run?.timeframe || container.configuration.timeframe || container.strategy.timeframe),
		metricLine('verification', run?.result_id ? `${run.result_id}${container.strategy.pinned_backtest_id === run.result_id ? ' (active)' : ''}` : 'current defaults'),
		metricLine('start', run?.start_date),
		metricLine('end', run?.end_date),
		metricLine('total_return', formatMetric(metrics.total_return_pct ?? metrics.total_return)),
		metricLine('cagr', formatMetric(metrics.annualized_return_pct ?? metrics.cagr)),
		metricLine('sharpe', formatMetric(metrics.sharpe_ratio)),
		metricLine('max_dd', formatMetric(metrics.max_drawdown_pct ?? metrics.max_drawdown)),
		metricLine('trades', formatMetric(metrics.total_trades)),
		metricLine('profit_factor', formatMetric(metrics.profit_factor)),
	].filter(Boolean);
	return [
		'// ============================================================================',
		`// Kronos Quant TradingView export - ${container.strategy.id}`,
		'// ============================================================================',
		'// Paste this into TradingView Pine Editor, add it to the matching chart,',
		'// then compare Strategy Tester results against the Kronos Quant run below.',
		'//',
		...metricLines,
		`//   exporter       : ${source}`,
		`//   params         : ${JSON.stringify(params)}`,
		'// ============================================================================',
		'',
	].join('\n');
}

function buildWindow(run: StrategyContainerHistoryItem | null): string {
	const startMs = epochMs(run?.start_date) ?? Date.UTC(2020, 0, 1);
	const endMs = epochMs(run?.end_date) ?? Date.UTC(2035, 0, 1);
	return [
		'use_window = input.bool(true, "Restrict to Kronos Quant verification window", group = "Date range")',
		`start_ts = input.time(${startMs}, "Window start (UTC)", group = "Date range")`,
		`end_ts = input.time(${endMs}, "Window end (UTC)", group = "Date range")`,
		'in_window = not use_window or (time >= start_ts and time <= end_ts)',
		'',
	].join('\n');
}

function strategyDeclaration(container: StrategyContainerPayload): string {
	return [
		'//@version=6',
		'strategy(',
		`     title              = "Kronos Quant ${pineString(container.strategy.id)} - ${pineString(container.strategy.name)}",`,
		`     shorttitle         = "${pineString(container.strategy.id)}",`,
		'     overlay            = true,',
		'     pyramiding         = 0,',
		'     initial_capital    = 10000,',
		'     default_qty_type   = strategy.percent_of_equity,',
		'     default_qty_value  = 100,',
		'     commission_type    = strategy.commission.percent,',
		'     commission_value   = 0.055,',
		'     slippage           = 0,',
		'     calc_on_every_tick = false,',
		`     process_orders_on_close = true)`,
		'',
	].join('\n');
}

function macdPine(params: Record<string, unknown>, run: StrategyContainerHistoryItem | null): string {
	const fast = numericParam(params, ['fast', 'macd_fast'], 12);
	const slow = numericParam(params, ['slow', 'macd_slow'], 26);
	const signal = numericParam(params, ['signal', 'macd_signal'], 9);
	return [
		`fast_len = input.int(${fast}, "MACD fast length", minval = 1, group = "Signal")`,
		`slow_len = input.int(${slow}, "MACD slow length", minval = 2, group = "Signal")`,
		`signal_len = input.int(${signal}, "MACD signal length", minval = 1, group = "Signal")`,
		'',
		buildWindow(run),
		'[macd_line, signal_line, hist] = ta.macd(close, fast_len, slow_len, signal_len)',
		'plot(macd_line, "MACD", color = color.new(color.aqua, 0), display = display.pane)',
		'plot(signal_line, "Signal", color = color.new(color.orange, 0), display = display.pane)',
		'',
		'long_entry = ta.crossover(macd_line, signal_line)',
		'long_exit = ta.crossunder(macd_line, signal_line)',
		'',
		'if in_window and long_entry',
		'    strategy.entry("Long", strategy.long)',
		'',
		'if long_exit',
		'    strategy.close("Long")',
	].join('\n');
}

function emaCrossPine(params: Record<string, unknown>, run: StrategyContainerHistoryItem | null): string {
	const fast = numericParam(params, ['ema_fast', 'fast_ema', 'fast'], 20);
	const slow = numericParam(params, ['ema_slow', 'slow_ema', 'slow'], 50);
	return [
		`fast_len = input.int(${fast}, "EMA fast length", minval = 1, group = "Signal")`,
		`slow_len = input.int(${slow}, "EMA slow length", minval = 2, group = "Signal")`,
		'',
		buildWindow(run),
		'ema_fast = ta.ema(close, fast_len)',
		'ema_slow = ta.ema(close, slow_len)',
		'plot(ema_fast, "EMA fast", color = color.new(color.orange, 0))',
		'plot(ema_slow, "EMA slow", color = color.new(color.blue, 0))',
		'',
		'long_entry = ta.crossover(ema_fast, ema_slow)',
		'long_exit = ta.crossunder(ema_fast, ema_slow)',
		'',
		'if in_window and long_entry',
		'    strategy.entry("Long", strategy.long)',
		'',
		'if long_exit',
		'    strategy.close("Long")',
	].join('\n');
}

function donchianPine(params: Record<string, unknown>, run: StrategyContainerHistoryItem | null): string {
	const entry = numericParam(params, ['entry_len', 'entry_length', 'channel_length', 'donchian_period'], 20);
	const exit = numericParam(params, ['exit_len', 'exit_length'], Math.max(2, Math.round(entry / 2)));
	return [
		`entry_len = input.int(${entry}, "Entry channel length", minval = 2, group = "Signal")`,
		`exit_len = input.int(${exit}, "Exit channel length", minval = 2, group = "Signal")`,
		'',
		buildWindow(run),
		'prior_high = ta.highest(high[1], entry_len)',
		'prior_low = ta.lowest(low[1], exit_len)',
		'plot(prior_high, "Prior entry high", color = color.new(color.green, 0))',
		'plot(prior_low, "Prior exit low", color = color.new(color.red, 0))',
		'',
		'long_entry = close > prior_high',
		'long_exit = close < prior_low',
		'',
		'if in_window and long_entry',
		'    strategy.entry("Long", strategy.long)',
		'',
		'if long_exit',
		'    strategy.close("Long")',
	].join('\n');
}

function rsiPine(params: Record<string, unknown>, run: StrategyContainerHistoryItem | null): string {
	const period = numericParam(params, ['rsi_period', 'period', 'length'], 14);
	const entry = numericParam(params, ['rsi_entry', 'rsi_oversold', 'oversold'], 30);
	const exit = numericParam(params, ['rsi_exit', 'rsi_overbought', 'overbought'], 70);
	return [
		`rsi_len = input.int(${period}, "RSI length", minval = 2, group = "Signal")`,
		`entry_level = input.float(${entry}, "Entry level", group = "Signal")`,
		`exit_level = input.float(${exit}, "Exit level", group = "Signal")`,
		'',
		buildWindow(run),
		'rsi_value = ta.rsi(close, rsi_len)',
		'plot(rsi_value, "RSI", color = color.new(color.purple, 0), display = display.pane)',
		'hline(entry_level, "Entry level", color = color.new(color.green, 40), display = display.pane)',
		'hline(exit_level, "Exit level", color = color.new(color.red, 40), display = display.pane)',
		'',
		'long_entry = ta.crossover(rsi_value, entry_level)',
		'long_exit = ta.crossunder(rsi_value, exit_level) or rsi_value > exit_level',
		'',
		'if in_window and long_entry',
		'    strategy.entry("Long", strategy.long)',
		'',
		'if long_exit',
		'    strategy.close("Long")',
	].join('\n');
}

function fallbackPine(params: Record<string, unknown>, run: StrategyContainerHistoryItem | null): string {
	const length = numericParam(params, ['length', 'period', 'lookback'], 20);
	return [
		'// Generic export scaffold: this strategy family was not recognized automatically.',
		'// Edit the long_entry and long_exit expressions below to match the Python strategy.',
		`basis_len = input.int(${length}, "Reference length", minval = 2, group = "Signal")`,
		'',
		buildWindow(run),
		'basis = ta.sma(close, basis_len)',
		'plot(basis, "Reference SMA", color = color.new(color.gray, 0))',
		'',
		'long_entry = ta.crossover(close, basis)',
		'long_exit = ta.crossunder(close, basis)',
		'',
		'if in_window and long_entry',
		'    strategy.entry("Long", strategy.long)',
		'',
		'if long_exit',
		'    strategy.close("Long")',
	].join('\n');
}

export function buildTradingViewExport(container: StrategyContainerPayload): TradingViewExport {
	const run = selectedVerificationRun(container);
	const params = resolveParams(container, run);
	const source = detectSource(container, params);
	const body =
		source === 'macd' ? macdPine(params, run)
		: source === 'ema_cross' ? emaCrossPine(params, run)
		: source === 'donchian' ? donchianPine(params, run)
		: source === 'rsi' ? rsiPine(params, run)
		: fallbackPine(params, run);
	const warnings = source === 'fallback'
		? ['Strategy family was not recognized; exported a valid Pine scaffold that needs signal review.']
		: [];
	const pine = [
		buildHeader(container, params, run, source),
		strategyDeclaration(container),
		body,
		'',
		'buy_signal = in_window and long_entry and strategy.position_size <= 0',
		'sell_signal = long_exit and strategy.position_size > 0',
		'window_forced_sell = use_window and (time > end_ts) and strategy.position_size > 0',
		'',
		'if window_forced_sell',
		'    strategy.close_all(comment = "SELL window end")',
		'',
		'plotshape(buy_signal, title = "BUY", text = "BUY", style = shape.labelup, location = location.belowbar, color = color.new(color.green, 0), textcolor = color.white, size = size.tiny)',
		'plotshape(sell_signal or window_forced_sell, title = "SELL", text = "SELL", style = shape.labeldown, location = location.abovebar, color = color.new(color.red, 0), textcolor = color.white, size = size.tiny)',
		'alertcondition(buy_signal, title = "Kronos Quant BUY", message = "Kronos Quant BUY signal")',
		'alertcondition(sell_signal or window_forced_sell, title = "Kronos Quant SELL", message = "Kronos Quant SELL signal")',
		'bgcolor(in_window ? color.new(color.blue, 92) : na, title = "Kronos Quant verification window")',
		'',
	].join('\n');
	return {
		filename: `${cleanIdentifier(container.strategy.id)}_${cleanIdentifier(container.strategy.name)}_tradingview.pine`,
		pine,
		source,
		warnings,
	};
}
