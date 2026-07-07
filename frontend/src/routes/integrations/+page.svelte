<script lang="ts">
	import AiDropzonePanel from '$lib/components/integrations/AiDropzonePanel.svelte';
	import IntegrationTabs from '$lib/components/integrations/IntegrationTabs.svelte';
</script>

<svelte:head>
	<title>Integrations · Kronos Quant</title>
</svelte:head>

<IntegrationTabs active="ai-clients">
	<AiDropzonePanel />
</IntegrationTabs>
