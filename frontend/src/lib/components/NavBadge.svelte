<script lang="ts">
	import type { NavMetric, NavPulse } from '$lib/stores/navMetrics';

	/** Heartbeat state indicator (standing facts: pending approvals, HALT, scans running). */
	export let metric: NavMetric | undefined = undefined;
	/** Realtime event pulse (things that happened since the tab was last visited) — wins over metric. */
	export let pulse: NavPulse | undefined = undefined;
	/** No badge while the route is being viewed; visiting is what clears it. */
	export let active = false;

	const COUNT_COLORS: Record<string, string> = {
		danger: 'bg-red-500 text-white',
		warn: 'bg-amber-500 text-black',
		success: 'bg-green-500 text-black',
		info: 'bg-cyan-500 text-black',
		neutral: 'bg-gray-600 text-white',
	};

	const COUNT_COLORS_SEEN: Record<string, string> = {
		danger: 'bg-red-500/25 text-red-300',
		warn: 'bg-amber-500/25 text-amber-300',
		success: 'bg-green-500/25 text-green-300',
		info: 'bg-cyan-500/25 text-cyan-300',
		neutral: 'bg-gray-600/30 text-gray-400',
	};

	const PING_COLORS: Record<string, string> = {
		danger: 'bg-red-400',
		warn: 'bg-amber-400',
		success: 'bg-green-400',
		info: 'bg-cyan-400',
		neutral: 'bg-gray-400',
	};

	const PILL_COLORS: Record<string, string> = {
		danger: 'border-red-500 text-red-400',
		warn: 'border-amber-500 text-amber-400',
		success: 'border-green-500 text-green-400',
		info: 'border-cyan-500 text-cyan-400',
		neutral: 'border-gray-500 text-gray-400',
	};

	const DOT_COLORS: Record<string, string> = {
		danger: 'bg-red-500',
		warn: 'bg-amber-500',
		success: 'bg-green-500',
		info: 'bg-cyan-500',
		neutral: 'bg-gray-500',
	};

	function countLabel(count: number): string {
		return count > 99 ? '99+' : String(count);
	}

	$: showPulse = !active && !!pulse && pulse.count > 0;
	// Count/activity badges are NEWS: once the route has been visited they
	// disappear entirely until the underlying seen_key changes (new approval,
	// new trade set, new notifications). Only status pills (HALT, AUTH) persist
	// while their condition holds — they flag standing hazards, not news.
	$: showMetric =
		!active
		&& !showPulse
		&& !!metric
		&& metric.kind !== 'none'
		&& (metric.kind === 'status' || !metric.seen);
	$: metricDimmed = !!metric && metric.seen && metric.severity !== 'danger';
</script>

{#if showPulse && pulse}
	<span class="relative flex shrink-0" title={pulse.summary}>
		<span class="absolute inline-flex h-full w-full rounded-full opacity-60 animate-ping {PING_COLORS[pulse.severity] ?? PING_COLORS.neutral}" aria-hidden="true"></span>
		<span class="relative min-w-[18px] h-[18px] px-1 rounded-full text-[10px] font-bold flex items-center justify-center {COUNT_COLORS[pulse.severity] ?? COUNT_COLORS.neutral}">
			{countLabel(pulse.count)}
		</span>
	</span>
{:else if showMetric && metric}
	{#if metric.kind === 'count' && metric.count > 0}
		<span
			class="shrink-0 min-w-[18px] h-[18px] px-1 rounded-full text-[10px] font-bold flex items-center justify-center {(metricDimmed ? COUNT_COLORS_SEEN : COUNT_COLORS)[metric.severity] ?? COUNT_COLORS.neutral}"
			title={metric.summary}
		>
			{countLabel(metric.count)}
		</span>
	{:else if metric.kind === 'status' && metric.label}
		<span
			class="shrink-0 border rounded px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider {PILL_COLORS[metric.severity] ?? PILL_COLORS.neutral} {metricDimmed ? 'opacity-50' : ''}"
			title={metric.summary}
		>
			{metric.label}
		</span>
	{:else if metric.kind === 'activity'}
		<span
			class="shrink-0 w-2 h-2 rounded-full animate-pulse {DOT_COLORS[metric.severity] ?? DOT_COLORS.neutral} {metricDimmed ? 'opacity-50' : ''}"
			title={metric.summary}
		></span>
	{/if}
{/if}
