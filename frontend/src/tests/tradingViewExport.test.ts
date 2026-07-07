import { describe, expect, it } from 'vitest';
import { buildTradingViewExport } from '../lib/utils/tradingViewExport';
import type { StrategyContainerPayload } from '../lib/api/lifecycle';

function buildContainer(overrides: Partial<StrategyContainerPayload> = {}): StrategyContainerPayload {
	const container: StrategyContainerPayload = {
		strategy: {
			id: 'S0001',
			name: 'BTC-MACD-S0001',
			hypothesis_id: null,
			hypothesis_display_id: null,
			display_id: null,
			state: 'backtesting',
			type: 'btc_macd_s0001',
			source: 'manual',
			source_ref: null,
			owner: 'brain',
			symbol: 'BTC/USDT',
			timeframe: '1h',
			definition_json: null,
			dataset_hash: null,
			policy_version: 1,
			build_version: null,
			metrics_json: null,
			metrics: null,
			paper_session_id: null,
			paper_started_at: null,
			last_policy_result_json: null,
			blocked_reason: null,
			model: null,
			model_id: null,
			created_at: '2026-03-01T00:00:00Z',
			updated_at: '2026-03-01T00:00:00Z',
			state_changed_at: null,
			failed_at: null,
			retention_expires_at: null,
			pinned_backtest_id: 'B1001',
		},
		configuration: {
			symbol: 'BTC/USDT',
			timeframe: '1h',
			params: { fast: 12, slow: 26, signal: 9 },
			type: 'manual',
		},
		history: {
			all: [],
			backtests: [
				{
					result_id: 'B1001',
					strategy_id: 'S0001',
					result_type: 'backtest',
					symbol: 'BTC/USDT',
					timeframe: '1h',
					start_date: '2025-03-11T00:00:00Z',
					end_date: '2026-03-11T00:00:00Z',
					metrics: {
						annualized_return_pct: 7.59,
						sharpe_ratio: 0.4,
						max_drawdown_pct: 27.11,
						total_trades: 15,
					},
					config: {
						params: { fast: 8, slow: 21, signal: 5 },
					},
					created_at: '2026-03-11T22:14:15Z',
					deleted_at: null,
				},
			],
			optimizations: [],
			walk_forward: [],
			validation: [],
		},
		execution: {
			trades: [],
			positions: [],
		},
		events: [],
	};
	return { ...container, ...overrides };
}

describe('TradingView export', () => {
	it('builds a Pine v6 MACD strategy from the active backtest params', () => {
		const result = buildTradingViewExport(buildContainer());

		expect(result.filename).toBe('s0001_btc_macd_s0001_tradingview.pine');
		expect(result.source).toBe('macd');
		expect(result.pine).toContain('//@version=6');
		expect(result.pine).toContain('fast_len = input.int(8, "MACD fast length"');
		expect(result.pine).toContain('slow_len = input.int(21, "MACD slow length"');
		expect(result.pine).toContain('signal_len = input.int(5, "MACD signal length"');
		expect(result.pine).toContain('long_entry = ta.crossover(macd_line, signal_line)');
		expect(result.pine).toContain('strategy.entry("Long", strategy.long)');
		expect(result.pine).toContain('strategy.close("Long")');
		expect(result.pine).toContain('strategy.close_all(comment = "SELL window end")');
		expect(result.pine).toContain('plotshape(buy_signal, title = "BUY", text = "BUY"');
		expect(result.pine).toContain('plotshape(sell_signal or window_forced_sell, title = "SELL", text = "SELL"');
		expect(result.pine).toContain('alertcondition(buy_signal, title = "Kronos Quant BUY"');
		expect(result.pine).toContain('alertcondition(sell_signal or window_forced_sell, title = "Kronos Quant SELL"');
		expect(result.pine).toContain('//   verification   : B1001 (active)');
	});

	it('exports a valid scaffold when the strategy family is unknown', () => {
		const result = buildTradingViewExport(buildContainer({
			strategy: {
				...buildContainer().strategy,
				name: 'Mystery Alpha',
				type: 'mystery_alpha',
			},
			configuration: {
				symbol: 'BTC/USDT',
				timeframe: '1h',
				params: { lookback: 33 },
			},
			history: {
				...buildContainer().history,
				backtests: [],
			},
		}));

		expect(result.source).toBe('fallback');
		expect(result.warnings[0]).toContain('not recognized');
		expect(result.pine).toContain('Generic export scaffold');
		expect(result.pine).toContain('basis_len = input.int(33');
	});
});
