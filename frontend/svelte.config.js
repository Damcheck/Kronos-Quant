import adapterAuto from '@sveltejs/adapter-auto';
import adapterStatic from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

const usePackaged = process.env.FORVEN_PACKAGE_BUILD === '1';

// Extract the backend origin from VITE_API_BASE for CSP whitelisting at build time.
// e.g. "https://kronos-backend-production-93bc.up.railway.app" → add both https and wss origins.
function getBackendCspOrigins() {
	const base = (process.env.VITE_API_BASE || '').trim().replace(/\/+$/, '');
	if (!base || base.startsWith('/')) return [];
	try {
		const url = new URL(base);
		const httpOrigin = `${url.protocol}//${url.host}`;
		const wsProtocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
		const wsOrigin = `${wsProtocol}//${url.host}`;
		return [httpOrigin, wsOrigin];
	} catch {
		return [];
	}
}

const backendCspOrigins = getBackendCspOrigins();

/** @type {import('@sveltejs/kit').Config} */
const config = {
	preprocess: vitePreprocess(),

	kit: {
		adapter: usePackaged
			? adapterStatic({ pages: 'build', assets: 'build', fallback: 'index.html', strict: false })
			: adapterAuto(),
		prerender: {
			handleUnseenRoutes: 'warn'
		},
		// SECURITY (audit 2026-06-22, M5): a Content-Security-Policy is the
		// defense-in-depth backstop for the localStorage-resident API/operator
		// keys — any in-origin script execution (a future DOM-XSS, a malicious
		// extension) is otherwise full authenticated API access + key theft.
		// script-src 'self' (SvelteKit hashes its own bootstrap) blocks injected
		// inline/remote scripts; styles stay unsafe-inline so charts/Tailwind keep
		// working; connect-src is scoped to the local API + the Binance market WS
		// plus any remote backend origin declared via VITE_API_BASE at build time.
		csp: {
			mode: 'hash',
			directives: {
				'default-src': ['self'],
				'script-src': ['self'],
				'style-src': ['self', 'unsafe-inline'],
				'img-src': ['self', 'data:', 'blob:', 'https:'],
				'font-src': ['self', 'data:'],
				'connect-src': [
					'self',
					'http://localhost:*',
					'http://127.0.0.1:*',
					'ws://localhost:*',
					'ws://127.0.0.1:*',
					'wss://stream.binance.com:9443',
					...backendCspOrigins
				],
				'object-src': ['none'],
				'base-uri': ['self'],
				'frame-ancestors': ['none'],
				'form-action': ['self']
			}
		}
	}
};

export default config;

