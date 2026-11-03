import { timingSafeEqual } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { createServer } from 'node:http'
import { WaffoPancake, verifyWebhook } from '@waffo/pancake-ts'
import { webhookCoverage } from './webhooks.mjs'

export function configuration(env = process.env) {
  const mode = env.WAFFO_ENVIRONMENT || 'test'
  if (!['test', 'prod'].includes(mode)) throw new Error('Invalid Waffo environment')
  const enabled = env.WAFFO_ENABLED === 'true'
  const token = env.WAFFO_SERVICE_TOKEN || ''
  const merchantId = env.WAFFO_MERCHANT_ID || ''
  const storeId = env.WAFFO_STORE_ID || ''
  const productId = env.WAFFO_ONETIME_PRODUCT_ID || env.WAFFO_PRODUCT_ID || ''
  const subscriptionProductId = env.WAFFO_SUBSCRIPTION_PRODUCT_ID || ''
  const fixedPriceVerified = env.WAFFO_FIXED_PRICE_VERIFIED === 'true'
  const publicOrigin = env.WAFFO_PUBLIC_ORIGIN || ''
  const currency=env.WAFFO_CURRENCY||'USD',oneTimeAmount=env.WAFFO_ONETIME_AMOUNT||'15.00',subscriptionAmount=env.WAFFO_SUBSCRIPTION_AMOUNT||'10.00',membershipProductId=env.WAFFO_MEMBERSHIP_PRODUCT_ID||'member-monthly'
  if(!/^[A-Z]{3}$/.test(currency)||![oneTimeAmount,subscriptionAmount].every(value=>/^\d{1,8}\.\d{2}$/.test(value)&&Number(value)>0))throw new Error('Invalid currency or amount')
  let privateKey = ''
  if (enabled) {
    if (token.length < 32) throw new Error('A private service token of at least 32 characters is required')
    if (!publicOrigin) throw new Error('WAFFO_PUBLIC_ORIGIN is required')
    const url = new URL(publicOrigin)
    if (url.username || url.password || url.search || url.hash || url.pathname !== '/' ||
        url.protocol !== 'https:') {
      throw new Error('Invalid trusted public origin')
    }
    privateKey = env.WAFFO_PRIVATE_KEY_FILE ? readFileSync(env.WAFFO_PRIVATE_KEY_FILE, 'utf8') : (env.WAFFO_PRIVATE_KEY || '')
    if (!privateKey || privateKey.includes('<paste-')) throw new Error('A valid private key file is required')
  }
  return { enabled, token, merchantId, storeId, productId, subscriptionProductId, fixedPriceVerified, publicOrigin, privateKey, mode, currency, oneTimeAmount, subscriptionAmount, membershipProductId }
}

export function sdkClient(config) {
  return new WaffoPancake({ merchantId: config.merchantId, privateKey: config.privateKey,
    environment: config.mode, fetch: (url, options) => fetch(url, { ...options, signal: AbortSignal.timeout(8000) }) })
}

export async function validateProduct(client, config, purchaseMode = 'ONETIME') {
  const recurring = purchaseMode === 'SUBSCRIPTION', productId = recurring ? config.subscriptionProductId : config.productId
  const result = await client.graphql.query({
    query: `query ($id: String!) { ${recurring ? 'subscriptionProduct' : 'onetimeProduct'}(id: $id) { id storeId prices { currency priceInfo { amount taxCategory } } status hasProdVersion ${recurring ? 'billingPeriod' : ''} } }`,
    variables: { id: productId }
  })
  const product = result.data?.[recurring ? 'subscriptionProduct' : 'onetimeProduct'], prices = product?.prices
  const usdPrices = Array.isArray(prices) ? prices.filter(item => item.currency === config.currency) : []
  const price = usdPrices.length === 1 ? usdPrices[0].priceInfo : null
  if (result.errors?.length || !productId || product?.id !== productId || product?.storeId !== config.storeId || product?.status !== 'active'
      || price?.amount !== (recurring ? config.subscriptionAmount : config.oneTimeAmount) || price?.taxCategory !== 'saas' || (recurring && product?.billingPeriod !== 'monthly')
      || (config.mode === 'prod' && product?.hasProdVersion !== true)) {
    throw new Error('Product type, store, period or fixed price mismatch')
  }
}

async function ready(client, config) {
  if (!config.productId || !config.subscriptionProductId) return false
  await validateProduct(client, config); await validateProduct(client, config, 'SUBSCRIPTION')
  if (config.mode === 'prod') {
    if (!config.fixedPriceVerified) return false
    const result = await client.graphql.query({ query: 'query($id:String!){store(id:$id){id status prodEnabled payinEnable isLive storeWebhooks{channel url testMode events}}}', variables: { id: config.storeId } })
    const store = result.data?.store
    return !result.errors?.length && store?.id === config.storeId && store.status === 'active' && store.prodEnabled === true && store.payinEnable === true && store.isLive === true
      && webhookCoverage(store, config).missing.length === 0
  }
  return true
}

const orderPattern = /^WF_[a-f0-9]{32}$/
const modes = ['ONETIME', 'SUBSCRIPTION']
const safeDataFields = ['orderId','orderStatus','paymentId','paymentStatus','orderMerchantExternalId','currency','chargedAmount',
  'periodNumber','billingPeriod','currentPeriodStart','currentPeriodEnd','refundId','refundAmount','refundedAmount',
  'originalChargedAmount','refundTicketMerchantExternalId','refundStatus']
function sanitized(event) {
  const data = Object.fromEntries(safeDataFields.filter(key => event.data?.[key] != null).map(key => [key, event.data[key]]))
  // Explicit metadata whitelist: buyer details and arbitrary customer metadata never enter the app.
  const source = event.data?.orderMetadata || {}
  data.orderMetadata = Object.fromEntries(['orderNo','productId','membership','purchaseMode'].filter(key => source[key] != null).map(key => [key, source[key]]))
  return { id: event.id, eventId: event.eventId, eventType: event.eventType, timestamp: event.timestamp, mode: event.mode, storeId: event.storeId, data }
}

// Merchant-authenticated read-only recovery; never infer payment from list price or redirect success.
async function reconcile(client, config, input) {
  const recurring = input.purchaseMode === 'SUBSCRIPTION', field = recurring ? 'subscriptionOrders' : 'onetimeOrders'
  const result = await client.graphql.query({ query: `query($store:String!,$no:String!){${field}(storeId:$store,limit:2,filter:{orderMerchantExternalId:{eq:$no}}){
    id storeId testMode status currency orderMerchantExternalId metadata updatedAt
    ${recurring ? 'billingPeriod currentPeriodStart currentPeriodEnd currentPeriodNumber subscriptionProduct{id}' : 'onetimeProduct{id}'}
    payments{id status periodNumber pspAmountDetails{currency amount} isFullyRefunded refundedAmount{amount currency} createdAt}
  }}`, variables: { store: config.storeId, no: input.orderNo } })
  if (result.errors?.length) throw new Error('Recovery query failed')
  const rows = result.data?.[field]
  if (!Array.isArray(rows) || rows.length > 1) throw new Error('Ambiguous provider order')
  if (!rows.length) return { events: [] }
  const order = rows[0]
  if (order.storeId !== config.storeId || order.testMode !== (config.mode === 'test') || order.orderMerchantExternalId !== input.orderNo) throw new Error('Recovery ownership mismatch')
  const metadata = typeof order.metadata === 'string' ? JSON.parse(order.metadata) : order.metadata
  const productId = order[recurring ? 'subscriptionProduct' : 'onetimeProduct']?.id
  if (metadata?.orderNo !== input.orderNo || metadata.productId !== productId || metadata.membership !== config.membershipProductId) throw new Error('Recovery metadata mismatch')
  const common = { orderId: order.id, orderMerchantExternalId: input.orderNo, orderMetadata: metadata }
  const events = []
  const add = (type, id, data, timestamp) => events.push(sanitized({ id, eventId: id, eventType: type, mode: config.mode, storeId: config.storeId, timestamp, data: { ...common, ...data } }))
  for (const payment of order.payments || []) {
    if (payment.status !== 'succeeded') continue
    add(recurring ? 'subscription.payment_succeeded' : 'order.completed', payment.id, { paymentId: payment.id,
      orderStatus: order.status, paymentStatus: payment.status, periodNumber: payment.periodNumber,
      currency: payment.pspAmountDetails?.currency, chargedAmount: payment.pspAmountDetails?.amount }, payment.createdAt)
    if (payment.isFullyRefunded || Number(payment.refundedAmount?.amount) > 0) add('refund.succeeded', `reconcile_${payment.id}_${payment.refundedAmount?.amount}`, { paymentId: payment.id, refundedAmount: payment.refundedAmount?.amount }, order.updatedAt)
  }
  if (recurring) add('subscription.snapshot', `snapshot_${order.id}_${Date.parse(order.updatedAt)}_${order.status}_${order.currentPeriodNumber || 0}`, {
    orderStatus: order.status, billingPeriod: order.billingPeriod, periodNumber: order.currentPeriodNumber,
    currentPeriodStart: order.currentPeriodStart, currentPeriodEnd: order.currentPeriodEnd }, order.updatedAt)
  return { events, providerOrderId: order.id, status: order.status }
}

function authenticated(header, token) {
  const actual = Buffer.from(header || ''), expected = Buffer.from(`Bearer ${token}`)
  return token.length >= 32 && actual.length === expected.length && timingSafeEqual(actual, expected)
}

async function body(request) {
  const parts = []; let length = 0
  for await (const part of request) {
    length += part.length
    if (length > 262144) throw Object.assign(new Error('Payload too large'), { status: 413 })
    parts.push(part)
  }
  return new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(parts))
}

// Dependencies are injectable only by local tests, never via request data or environment.
export function createAdapter(config, dependencies = {}) {
  const client = config.enabled ? (dependencies.client || sdkClient(config)) : null
  const verify = dependencies.verify || verifyWebhook
  return createServer(async (request, response) => {
    const send = (status, value) => { response.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }); response.end(JSON.stringify(value)) }
    try {
      if (request.method === 'GET' && request.url === '/health') return send(200, { status: 'UP' })
      if (!authenticated(request.headers.authorization, config.token)) return send(401, { error: 'Unauthorized' })
      if (!config.enabled) return send(503, { error: 'Waffo disabled' })
      if (request.method === 'GET' && request.url === '/options') {
        const available = await ready(client, config)
        return send(200, {
        ready: available, environment: config.mode, merchantId: config.merchantId,
        storeId: config.storeId, productId: config.productId, subscriptionProductId: config.subscriptionProductId,
        currency: config.currency, amount: config.oneTimeAmount, subscriptionAmount: config.subscriptionAmount
        })
      }
      if (request.method !== 'POST') return send(404, { error: 'Not found' })
      const raw = await body(request)
      if (request.url === '/verify-webhook') {
        let event
        try { event = verify(raw, request.headers['x-waffo-signature'], { environment: config.mode }) }
        catch { return send(400, { error: 'Invalid webhook signature' }) }
        if (event.mode !== config.mode || event.storeId !== config.storeId) return send(400, { error: 'Webhook environment or store mismatch' })
        return send(200, sanitized(event))
      }
      const input = JSON.parse(raw)
      if (!orderPattern.test(input.orderNo || '') || !modes.includes(input.purchaseMode)) return send(400, { error: 'Invalid checkout request' })
      if (request.url === '/reconcile') return send(200, await reconcile(client, config, input))
      if (request.url === '/cancel') {
        if (input.purchaseMode !== 'SUBSCRIPTION') return send(400, { error: 'Not a subscription' })
        const before = await reconcile(client, config, input)
        if (!before.providerOrderId) return send(409, { error: 'Subscription not yet confirmed' })
        if (!['canceling','canceled','closed','expired'].includes(before.status)) {
          await client.orders.cancelSubscription({ orderId: before.providerOrderId })
        }
        // Read back after success or on a safe retry; do not label a lost response as canceled.
        return send(200, await reconcile(client, config, input))
      }
      if (request.url !== '/checkout') return send(404, { error: 'Not found' })
      if (!await ready(client, config)) return send(503, { error: 'Production readiness not verified' })
      const recurring = input.purchaseMode === 'SUBSCRIPTION', productId = recurring ? config.subscriptionProductId : config.productId
      if (typeof input.buyerIdentity !== 'string' || !/^foundation_[a-f0-9]{64}$/.test(input.buyerIdentity)) return send(400, { error: 'Invalid buyer identity' })
      const result = await client.checkout.authenticated.create({ productId, currency: config.currency, buyerIdentity: input.buyerIdentity,
        // The REST reference still accepts taxIncluded; verify the final cashier total before production activation.
        priceSnapshot: { amount: recurring ? config.subscriptionAmount : config.oneTimeAmount, taxIncluded: true, taxCategory: 'saas' }, withTrial: false,
        successUrl: `${config.publicOrigin.replace(/\/$/, '')}/subscription?waffoOrder=${input.orderNo}`,
        expiresInSeconds: 2700, orderMerchantExternalId: input.orderNo,
        metadata: { orderNo: input.orderNo, membership: config.membershipProductId, productId, purchaseMode: input.purchaseMode }
      }, { idempotencyKey: `foundation_${config.mode}_${input.orderNo}` })
      const url = new URL(result.checkoutUrl)
      if (url.protocol !== 'https:' || url.hostname !== 'pancake.waffo.ai' || url.username || url.password) throw new Error('Untrusted checkout URL')
      const sessionExpiry = Date.parse(result.expiresAt), tokenExpiry = result.tokenExpiresAt == null ? sessionExpiry : Date.parse(result.tokenExpiresAt)
      const expires = Math.min(sessionExpiry, tokenExpiry)
      if (!result.sessionId || !Number.isFinite(expires) || expires <= Date.now()) throw new Error('Invalid checkout response')
      // The buyer credential can expire before the checkout session. Never expose a longer usable-link lifetime.
      return send(200, { sessionId: result.sessionId, checkoutUrl: result.checkoutUrl, expiresAt: new Date(expires).toISOString() })
    } catch (error) {
      // Never echo SDK errors (may include signed request/response data) into logs or clients.
      send(error.status === 413 ? 413 : 502, { error: 'Payment provider request could not be completed' })
    }
  })
}
