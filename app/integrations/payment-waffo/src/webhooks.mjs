// Full API event catalog, including renewal/recovery omitted by the skill quick start.
export const requiredWebhookEvents = Object.freeze([
  'order.completed', 'subscription.activated', 'subscription.payment_succeeded',
  'subscription.renewed', 'subscription.recovered', 'subscription.canceling',
  'subscription.uncanceled', 'subscription.canceled', 'subscription.past_due',
  'subscription.plan_changed', 'subscription.plan_change_scheduled', 'subscription.plan_change_failed',
  'refund.succeeded', 'refund.failed'
])

export function webhookCoverage(store, config) {
  const url = `${config.publicOrigin.replace(/\/$/, '')}/api/v1/payments/waffo/webhook`
  const hooks = (store?.storeWebhooks || []).filter(hook =>
    hook.channel === 'http' && hook.testMode === (config.mode === 'test') && hook.url === url)
  const covered = new Set(hooks.flatMap(hook => hook.events || []))
  return { url, hooks, missing: requiredWebhookEvents.filter(type => !covered.has(type)) }
}

// Dry-run by default. Never replace another URL, environment or channel's hooks.
export async function configureWebhook(client, config, { apply = false, receiverReady = false } = {}) {
  if (!config.enabled || !['test', 'prod'].includes(config.mode)) throw Error('Private Waffo configuration required')
  const result = await client.graphql.query({
    query: 'query($id:String!){store(id:$id){id storeWebhooks{id channel url testMode events}}}',
    variables: { id: config.storeId }
  })
  if (result.errors?.length || result.data?.store?.id !== config.storeId) throw Error('Webhook store query failed')
  const coverage = webhookCoverage(result.data.store, config)
  const report = { environment: config.mode, url: coverage.url, matchingHooks: coverage.hooks.length, missingEvents: coverage.missing }
  if (!apply || !coverage.missing.length) return { ...report, changed: false }
  if (!receiverReady) throw Error('Deploy the matching raw-body receiver before applying webhook configuration')
  if (coverage.hooks.length > 1) throw Error('Multiple matching webhooks require manual review')
  const hook = coverage.hooks[0]
  if (hook) {
    if (!hook.id) throw Error('Missing webhook identity')
    await client.webhooks.update({ id: hook.id, events: [...new Set([...(hook.events || []), ...requiredWebhookEvents])] })
  } else {
    await client.webhooks.add({ storeId: config.storeId, channel: 'http', url: coverage.url,
      events: [...requiredWebhookEvents], testMode: config.mode === 'test' },
    { idempotencyKey: `foundation_webhook_${config.mode}_${config.storeId}_v1` })
  }
  // Lost write responses are resolved by rerunning the read-first command, not blind creation.
  const verified = await configureWebhook(client, config)
  if (verified.missingEvents.length) throw Error('Webhook write not yet confirmed; rerun dry-run before retrying')
  return { ...verified, changed: true }
}
