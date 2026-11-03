import test from 'node:test'
import assert from 'node:assert/strict'
import { generateKeyPairSync, createSign } from 'node:crypto'
import { WaffoPancake, verifyWebhook } from '@waffo/pancake-ts'
import { configuration, createAdapter, validateProduct } from '../src/adapter.mjs'
import { requiredWebhookEvents } from '../src/webhooks.mjs'

const { privateKey, publicKey } = generateKeyPairSync('rsa', { modulusLength: 2048, privateKeyEncoding: { type: 'pkcs8', format: 'pem' }, publicKeyEncoding: { type: 'spki', format: 'pem' } })
const config = { enabled: true, token: 'local-fixture-token-not-credentials-0001', merchantId: 'MER_fixture000000000000000', storeId: 'STO_fixture000000000000000', productId: 'PROD_1234567890123456789012', subscriptionProductId: 'PROD_monthly_fixture', privateKey, currency:'USD',oneTimeAmount:'15.00',subscriptionAmount:'10.00',membershipProductId:'member-monthly',publicOrigin: 'https://merchant.example.test', mode: 'test' }
const no = 'WF_' + 'a'.repeat(32)
const product = { id: config.productId, storeId: config.storeId, status: 'active', hasProdVersion: true, prices: [{ currency: 'USD', priceInfo: { amount: '15.00', taxCategory: 'saas' } }] }
const subscriptionProduct = { ...product, id: config.subscriptionProductId, billingPeriod: 'monthly', prices: [{ currency: 'USD', priceInfo: { amount: '10.00', taxCategory: 'saas' } }] }
const checkoutInput = { orderNo: no, purchaseMode: 'ONETIME', buyerIdentity: 'foundation_' + 'a'.repeat(64) }
const event = { id: 'delivery1', eventId: 'PAY_1', eventType: 'order.completed', mode: 'test', storeId: config.storeId,
  data: { orderId: 'ORD_1', currency: 'USD', chargedAmount: '10.00', orderMerchantExternalId: no, buyerEmail: 'private@example.test', paymentLast4: '4242', orderMetadata: { orderNo: no } } }
function signed(raw, timestamp = Date.now()) {
  const signature = createSign('RSA-SHA256').update(`${timestamp}.${raw}`).sign(privateKey, 'base64')
  return `t=${timestamp},v1=${signature}`
}
async function fixture(t, dependencies = {}, overrides = {}) {
  const client = dependencies.client || {}
  client.graphql ||= { query: async () => ({ data: { onetimeProduct: product, subscriptionProduct } }) }
  const server = createAdapter({ ...config, ...overrides }, { ...dependencies, client, verify: dependencies.verify || ((raw, sig, opts) => verifyWebhook(raw, sig, { ...opts, publicKey })) })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  t.after(() => new Promise(resolve => { server.closeAllConnections(); server.close(resolve) }))
  const base = `http://127.0.0.1:${server.address().port}`
  return (path, body, headers = {}) => fetch(base + path, { method: body === undefined ? 'GET' : 'POST', headers: { Authorization: `Bearer ${config.token}`, ...headers }, ...(body === undefined ? {} : { body: typeof body === 'string' ? body : JSON.stringify(body) }) })
}
test('disabled by default; missing/placeholder private key and unsafe origins fail closed', () => {
  assert.equal(configuration({}).enabled, false)
  assert.throws(() => configuration({ WAFFO_ENABLED: 'true' }))
  assert.throws(() => configuration({ WAFFO_ENVIRONMENT: 'sandbox' }))
  assert.throws(() => configuration({ WAFFO_ENABLED: 'true', WAFFO_SERVICE_TOKEN: config.token, WAFFO_PUBLIC_ORIGIN: 'http://evil.test', WAFFO_PRIVATE_KEY: privateKey }))
  assert.throws(() => configuration({ WAFFO_ENABLED: 'true', WAFFO_SERVICE_TOKEN: config.token, WAFFO_PUBLIC_ORIGIN: 'http://127.0.0.1:4181', WAFFO_PRIVATE_KEY: privateKey }))
  assert.equal(configuration({ WAFFO_ENABLED: 'true', WAFFO_SERVICE_TOKEN: config.token, WAFFO_PUBLIC_ORIGIN: config.publicOrigin, WAFFO_PRIVATE_KEY: privateKey }).publicOrigin, config.publicOrigin)
  assert.equal(configuration({ WAFFO_ENABLED: 'true', WAFFO_ENVIRONMENT: 'prod', WAFFO_SERVICE_TOKEN: config.token, WAFFO_PUBLIC_ORIGIN: config.publicOrigin, WAFFO_PRIVATE_KEY: privateKey }).fixedPriceVerified, false)
  assert.throws(() => configuration({ WAFFO_ENABLED: 'true', WAFFO_SERVICE_TOKEN: config.token, WAFFO_PUBLIC_ORIGIN: config.publicOrigin, WAFFO_PRIVATE_KEY: '<paste-your-private-key>' }))
})
test('internal authentication and disabled checkout cannot be bypassed', async t => {
  const request = await fixture(t)
  assert.equal((await request('/options', undefined, { Authorization: 'Bearer wrong' })).status, 401)
  assert.equal((await request('/checkout', { orderNo: no }, { Authorization: '' })).status, 401)
  const disabled = await fixture(t, {}, { enabled: false })
  assert.equal((await disabled('/checkout', { orderNo: no })).status, 503)
  assert.equal((await disabled('/health')).status, 200)
})
test('actual official SDK signs fixed USD15 checkout, stable buyer identity and explicit idempotency', async t => {
  const calls = []
  const client = new WaffoPancake({ merchantId: config.merchantId, privateKey, fetch: async (url, request) => {
    if (url.endsWith('/v1/graphql')) {
      const query = JSON.parse(request.body)
      assert.ok(query.query.includes('prices { currency priceInfo { amount taxCategory } }'))
      return new Response(JSON.stringify({ data: { onetimeProduct: product, subscriptionProduct } }))
    }
    if (url.endsWith('/auth/issue-session-token')) return new Response(JSON.stringify({ data: { token: 'fixture-jwt', expiresAt: new Date(Date.now()+60000).toISOString() } }))
    calls.push({ url, request })
    return new Response(JSON.stringify({ data: { sessionId: 'session1', checkoutUrl: 'https://pancake.waffo.ai/checkout?session=1#token=fixture', expiresAt: new Date(Date.now() + 60000).toISOString() } }), { status: 200 })
  } })
  const request = await fixture(t, { client })
  for (let i = 0; i < 2; i++) assert.equal((await request('/checkout', { ...checkoutInput, amount: '0.01', productId: 'wrong', successUrl: 'https://evil.test' })).status, 200)
  const payload = JSON.parse(calls[0].request.body)
  assert.equal(payload.currency, 'USD'); assert.equal(payload.priceSnapshot.amount, '15.00')
  assert.equal(payload.withTrial, false)
  assert.equal(payload.priceSnapshot.taxIncluded, true)
  assert.equal(payload.productId, config.productId); assert.equal(payload.orderMerchantExternalId, no)
  assert.equal(payload.successUrl, `${config.publicOrigin}/subscription?waffoOrder=${no}`)
  assert.equal(payload.metadata.productId, config.productId)
  assert.ok(calls[0].request.headers['X-Signature'])
  assert.equal(calls[0].request.headers['X-Idempotency-Key'], calls[1].request.headers['X-Idempotency-Key'])
  assert.equal('productType' in payload, false) // Current SDK schema, not stale guide examples.
})
test('malicious checkout redirect and SDK errors are not exposed', async t => {
  const request = await fixture(t, { client: { checkout: { authenticated: { create: async () => ({ sessionId: 's', checkoutUrl: 'https://evil.test/steal', expiresAt: new Date().toISOString() }) } } } })
  const response = await request('/checkout', checkoutInput)
  assert.equal(response.status, 502); assert.equal((await response.text()).includes('evil'), false)
  assert.equal((await request('/checkout', { orderNo: 'bad' })).status, 400)
})
test('real SDK verification preserves signed whitespace and removes buyer/card information', async t => {
  const request = await fixture(t), raw = JSON.stringify(event, null, 2)
  const response = await request('/verify-webhook', raw, { 'X-Waffo-Signature': signed(raw) })
  assert.equal(response.status, 200)
  const returned = await response.json()
  assert.equal(returned.data.chargedAmount, '10.00')
  assert.equal(JSON.stringify(returned).includes('private@example.test'), false)
  assert.equal(JSON.stringify(returned).includes('4242'), false)
})
for (const [name, change, age] of [
  ['tampered body', raw => raw + ' ', 0],
  ['expired signature', raw => raw, -46 * 60000],
  ['future signature', raw => raw, 2 * 60000]
]) test(`rejects ${name}`, async t => {
  const request = await fixture(t), raw = JSON.stringify(event)
  assert.equal((await request('/verify-webhook', change(raw), { 'X-Waffo-Signature': signed(raw, Date.now() + age) })).status, 400)
})
test('wrong environment or store and missing signatures cannot grant', async t => {
  const request = await fixture(t)
  for (const bad of [{ ...event, mode: 'prod' }, { ...event, storeId: 'wrong' }]) {
    const raw = JSON.stringify(bad)
    assert.equal((await request('/verify-webhook', raw, { 'X-Waffo-Signature': signed(raw) })).status, 400)
  }
  assert.equal((await request('/verify-webhook', event)).status, 400)
})
test('oversized callback is bounded', async t => {
  const request = await fixture(t)
  assert.equal((await request('/verify-webhook', 'x'.repeat(262145))).status, 413)
})
test('a recurring, wrong-store, ambiguous-currency or wrong-price product cannot open a checkout', async () => {
  for (const bad of [null, { ...product, storeId: 'wrong' }, { ...product, status: 'inactive' }, { ...product, prices: [...product.prices, ...product.prices] },
    { ...product, prices: [{ currency: 'EUR', priceInfo: { amount: '10.00', taxCategory: 'saas' } }] },
    { ...product, prices: [{ currency: 'USD', priceInfo: { amount: '9.99', taxCategory: 'saas' } }] },
    { ...product, prices: [{ currency: 'USD', priceInfo: { amount: '10.00', taxCategory: 'ebook' } }] }]) {
    await assert.rejects(() => validateProduct({ graphql: { query: async () => ({ data: { onetimeProduct: bad } }) } }, config))
  }
})

test('USD10 monthly checkout uses the subscription product, no trial and no client-controlled price', async t => {
  let sent
  const request = await fixture(t, { client: { checkout: { authenticated: { create: async params => {
    sent = params; return { sessionId:'monthly',checkoutUrl:'https://pancake.waffo.ai/checkout/monthly',expiresAt:new Date(Date.now()+60000).toISOString() }
  } } } } })
  const response = await request('/checkout', { ...checkoutInput,purchaseMode:'SUBSCRIPTION',amount:'1.00' })
  assert.equal(response.status,200); assert.equal(sent.productId,config.subscriptionProductId)
  assert.equal(sent.priceSnapshot.amount,'10.00'); assert.equal(sent.withTrial,false)
  assert.equal(sent.buyerIdentity,checkoutInput.buyerIdentity); assert.equal(sent.metadata.purchaseMode,'SUBSCRIPTION')
  assert.equal((await request('/checkout',{...checkoutInput,purchaseMode:'OTHER'})).status,400)
  assert.equal((await request('/checkout',{...checkoutInput,buyerIdentity:'someone@example.com'})).status,400)
})
test('checkout link expiry never outlives its buyer token; expired credentials fail closed', async t => {
  const expiresAt=new Date(Date.now()+3600000).toISOString();let tokenExpiresAt=new Date(Date.now()+60000).toISOString()
  const request=await fixture(t,{client:{checkout:{authenticated:{create:async()=>({sessionId:'s',checkoutUrl:'https://pancake.waffo.ai/checkout',expiresAt,tokenExpiresAt})}}}})
  assert.equal((await (await request('/checkout',checkoutInput)).json()).expiresAt,tokenExpiresAt)
  tokenExpiresAt=new Date(Date.now()-1000).toISOString();assert.equal((await request('/checkout',checkoutInput)).status,502)
})
test('production checkout requires inclusive-price verification AND a live provider store', async t => {
  const client={graphql:{query:async ({query})=>({data:query.includes('store(id:')?{store:{id:config.storeId,status:'active',prodEnabled:false,payinEnable:true,isLive:false}}:{onetimeProduct:product,subscriptionProduct}})}}
  for(const fixedPriceVerified of [false,true]) {
    const request=await fixture(t,{client},{mode:'prod',fixedPriceVerified})
    assert.equal((await (await request('/options')).json()).ready,false)
    assert.equal((await request('/checkout',checkoutInput)).status,503)
  }
})

test('production opens only with BOTH fixed prices, live store and all production callback events at the trusted origin', async t => {
  const events=[...requiredWebhookEvents]
  const hook={channel:'http',url:config.publicOrigin+'/api/v1/payments/waffo/webhook',testMode:false,events}
  const store={id:config.storeId,status:'active',prodEnabled:true,payinEnable:true,isLive:true,storeWebhooks:[hook]}
  const client={graphql:{query:async({query})=>({data:query.includes('store(id:')?{store}:{onetimeProduct:product,subscriptionProduct}})}}
  const request=await fixture(t,{client},{mode:'prod',fixedPriceVerified:true})
  assert.equal((await (await request('/options')).json()).ready,true)
  for(const bad of [[],[{...hook,testMode:true}],[{...hook,url:'https://evil.test/api/v1/payments/waffo/webhook'}],[{...hook,events:events.slice(0,-1)}]]) {
    store.storeWebhooks=bad
    assert.equal((await (await request('/options')).json()).ready,false)
    assert.equal((await request('/checkout',checkoutInput)).status,503)
  }
})

test('an unpublished draft cannot pass production readiness even if price and store are correct', async () => {
  for (const purchaseMode of ['ONETIME', 'SUBSCRIPTION']) {
    const field = purchaseMode === 'ONETIME' ? 'onetimeProduct' : 'subscriptionProduct'
    const row = purchaseMode === 'ONETIME' ? product : subscriptionProduct
    for (const hasProdVersion of [false, undefined]) {
      const client = { graphql: { query: async () => ({ data: { [field]: { ...row, hasProdVersion } } }) } }
      await assert.rejects(() => validateProduct(client, { ...config, mode: 'prod' }, purchaseMode))
      await assert.doesNotReject(() => validateProduct(client, config, purchaseMode))
    }
  }
})

test('signed refund retains actual refunded amount, not deprecated listing totals or buyer details', async t => {
  const request = await fixture(t)
  const raw = JSON.stringify({ ...event, eventType: 'refund.succeeded', data: {
    ...event.data, refundId: 'REF_1', refundedAmount: '3.00', originalChargedAmount: '15.00',
    refundStatus: 'succeeded', amount: '99.00', refundTicketMerchantExternalId: 'RT_1'
  } })
  const returned = await (await request('/verify-webhook', raw, { 'X-Waffo-Signature': signed(raw) })).json()
  assert.equal(returned.data.refundedAmount, '3.00')
  assert.equal(returned.data.originalChargedAmount, '15.00')
  assert.equal(returned.data.refundTicketMerchantExternalId, 'RT_1')
  assert.equal('amount' in returned.data, false)
  assert.equal('buyerEmail' in returned.data, false)
})
test('wrong subscription billing period or duplicate prices fail closed', async () => {
  for(const bad of [{...subscriptionProduct,billingPeriod:'yearly'}, {...subscriptionProduct,prices:[...subscriptionProduct.prices,...subscriptionProduct.prices]}])
    await assert.rejects(()=>validateProduct({graphql:{query:async()=>({data:{subscriptionProduct:bad}})}},config,'SUBSCRIPTION'))
})
test('signed renewal preserves period/payment linkage but strips arbitrary metadata and PII', async t => {
  const request=await fixture(t)
  const raw=JSON.stringify({...event,eventType:'subscription.payment_succeeded',timestamp:'2026-10-04T00:00:00Z',data:{...event.data,paymentId:'PAY_2',periodNumber:2,orderMetadata:{...event.data.orderMetadata,secret:'private',purchaseMode:'SUBSCRIPTION'}}})
  const data=await (await request('/verify-webhook',raw,{'X-Waffo-Signature':signed(raw)})).json()
  assert.equal(data.timestamp,'2026-10-04T00:00:00Z'); assert.equal(data.data.periodNumber,2); assert.equal(data.data.paymentId,'PAY_2')
  assert.equal('secret' in data.data.orderMetadata,false); assert.equal('buyerEmail' in data.data,false)
})
function providerOrder(status='active') {
  return {id:'ORD_fixture',storeId:config.storeId,testMode:true,status,currency:'USD',updatedAt:'2026-10-04T00:00:00Z',
    orderMerchantExternalId:no,metadata:JSON.stringify({orderNo:no,productId:config.subscriptionProductId,membership:'member-monthly',purchaseMode:'SUBSCRIPTION'}),
    subscriptionProduct:{id:config.subscriptionProductId},billingPeriod:'monthly',currentPeriodNumber:1,
    currentPeriodStart:'2026-10-04T00:00:00Z',currentPeriodEnd:'2026-11-04T00:00:00Z',
    payments:[{id:'PAY_1',status:'succeeded',periodNumber:1,pspAmountDetails:{currency:'USD',amount:'10.00'},amount:{currency:'USD',amount:'1000'},createdAt:'2026-10-04T00:00:00Z'}]}
}
test('merchant recovery uses PSP major units, no fallback to list price, and exact calendar periods', async t => {
  const row=providerOrder(),request=await fixture(t,{client:{graphql:{query:async()=>({data:{subscriptionOrders:[row]}})}}})
  let result=await (await request('/reconcile',{orderNo:no,purchaseMode:'SUBSCRIPTION'})).json()
  assert.equal(result.events[0].data.chargedAmount,'10.00'); assert.equal(result.events[1].data.currentPeriodEnd,'2026-11-04T00:00:00Z')
  row.payments[0].pspAmountDetails={currency:'USD',amount:null}
  result=await (await request('/reconcile',{orderNo:no,purchaseMode:'SUBSCRIPTION'})).json()
  assert.equal('chargedAmount' in result.events[0].data,false)
})
test('cancel resolves provider order by trusted external ID; retry is read-only when already canceling', async t => {
  const row=providerOrder(); let cancels=0
  const request=await fixture(t,{client:{graphql:{query:async()=>({data:{subscriptionOrders:[row]}})},orders:{cancelSubscription:async params=>{assert.equal(params.orderId,row.id);cancels++;row.status='canceling'}}}})
  for(let i=0;i<2;i++) assert.equal((await request('/cancel',{orderNo:no,purchaseMode:'SUBSCRIPTION',providerOrderId:'attacker'})).status,200)
  assert.equal(cancels,1)
  row.orderMerchantExternalId='WF_'+'b'.repeat(32)
  assert.equal((await request('/cancel',{orderNo:no,purchaseMode:'SUBSCRIPTION'})).status,502); assert.equal(cancels,1)
})
