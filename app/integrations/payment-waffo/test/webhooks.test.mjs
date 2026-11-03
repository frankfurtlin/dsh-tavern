import test from 'node:test'
import assert from 'node:assert/strict'
import { configureWebhook, requiredWebhookEvents, webhookCoverage } from '../src/webhooks.mjs'

const config = { enabled: true, mode: 'prod', storeId: 'STO_fixture', currency:'USD',oneTimeAmount:'15.00',subscriptionAmount:'10.00',membershipProductId:'member-monthly',publicOrigin: 'https://merchant.example.test' }
const url = config.publicOrigin + '/api/v1/payments/waffo/webhook'
function fixture(hooks = []) {
  const store = { id: config.storeId, storeWebhooks: hooks }, writes = []
  const client = {
    graphql: { query: async () => ({ data: { store } }) },
    webhooks: {
      add: async (params, options) => { writes.push({ action: 'add', params, options }); store.storeWebhooks.push({ id: 'WBH_1', ...params }) },
      update: async params => { writes.push({ action: 'update', params }); Object.assign(store.storeWebhooks.find(hook => hook.id === params.id), params) }
    }
  }
  return { client, store, writes }
}
test('setup defaults to read-only and requires explicit receiver-ready acknowledgement for writes', async () => {
  const f = fixture()
  assert.deepEqual((await configureWebhook(f.client, config)).missingEvents, requiredWebhookEvents)
  assert.equal(f.writes.length, 0)
  await assert.rejects(() => configureWebhook(f.client, config, { apply: true }))
  assert.equal(f.writes.length, 0)
})
test('setup adds the exact environment/URL once, then reads existing configuration on retry', async () => {
  const f = fixture([{ id: 'OTHER', url: 'https://other.example.test/hook', testMode: false, channel: 'http', events: ['order.completed'] }])
  assert.equal((await configureWebhook(f.client, config, { apply: true, receiverReady: true })).changed, true)
  assert.equal((await configureWebhook(f.client, config, { apply: true, receiverReady: true })).changed, false)
  assert.equal(f.writes.length, 1)
  assert.equal(f.writes[0].params.testMode, false)
  assert.equal(f.writes[0].params.url, url)
  assert.ok(f.writes[0].options.idempotencyKey)
  assert.equal(f.store.storeWebhooks[0].id, 'OTHER')
})
test('setup preserves unrelated events and never changes test mode, URL or channel on update', async () => {
  const f = fixture([{ id: 'WBH_1', url, channel: 'http', testMode: false, events: ['merchant.custom'] }])
  await configureWebhook(f.client, config, { apply: true, receiverReady: true })
  assert.deepEqual(Object.keys(f.writes[0].params).sort(), ['events', 'id'])
  assert.ok(f.writes[0].params.events.includes('merchant.custom'))
  assert.equal(webhookCoverage(f.store, config).missing.length, 0)
})
test('test hooks and notification channels cannot satisfy production callback coverage', async () => {
  const f = fixture([{ id: 'TEST', url, channel: 'http', testMode: true, events: requiredWebhookEvents },
    { id: 'CHAT', url, channel: 'slack', testMode: false, events: requiredWebhookEvents }])
  await configureWebhook(f.client, config, { apply: true, receiverReady: true })
  assert.equal(f.writes[0].action, 'add')
  const sandbox = fixture()
  await configureWebhook(sandbox.client, { ...config, mode: 'test' }, { apply: true, receiverReady: true })
  assert.equal(sandbox.writes[0].params.testMode, true)
})
test('ambiguous hooks, GraphQL errors, and unconfirmed writes fail closed', async () => {
  const hook = { id: 'WBH_1', url, channel: 'http', testMode: false, events: [] }
  const f = fixture([hook, { ...hook, id: 'WBH_2' }])
  await assert.rejects(() => configureWebhook(f.client, config, { apply: true, receiverReady: true }))
  assert.equal(f.writes.length, 0)
  f.client.graphql.query = async () => ({ data: null, errors: [{ message: 'denied' }] })
  await assert.rejects(() => configureWebhook(f.client, config))
  const lost = fixture()
  lost.client.webhooks.add = async () => undefined
  await assert.rejects(() => configureWebhook(lost.client, config, { apply: true, receiverReady: true }))
})
