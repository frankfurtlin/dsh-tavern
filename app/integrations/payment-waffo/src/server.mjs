import { configuration, createAdapter } from './adapter.mjs'

try {
  const server = createAdapter(configuration())
  server.requestTimeout = 20000
  server.headersTimeout = 10000
  server.listen(Number(process.env.PORT || 3010), process.env.HOST || '127.0.0.1', () => console.log('Waffo adapter listening'))
} catch {
  console.error('Waffo configuration invalid. Check private server configuration; credentials are not logged.')
  process.exitCode = 1
}
