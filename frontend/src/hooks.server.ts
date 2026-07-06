import { env } from '$env/dynamic/private';
import type { Handle } from '@sveltejs/kit';

export const handle: Handle = async ({ event, resolve }) => {
	// Optional Basic Auth for the frontend
	const username = env.DASHBOARD_USERNAME || env.VITE_DASHBOARD_USERNAME;
	const password = env.DASHBOARD_PASSWORD || env.VITE_DASHBOARD_PASSWORD;

	if (username && password) {
		const auth = event.request.headers.get('Authorization');

		if (auth !== `Basic ${Buffer.from(`${username}:${password}`).toString('base64')}`) {
			return new Response('Not authorized', {
				status: 401,
				headers: {
					'WWW-Authenticate': 'Basic realm="Dashboard", charset="UTF-8"',
				},
			});
		}
	}

	return resolve(event);
};
