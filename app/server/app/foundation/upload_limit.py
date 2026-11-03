"""Bound product bodies before parsing multipart uploads or loading webhook JSON."""
from uuid import uuid4
from starlette.responses import JSONResponse
from starlette.formparsers import MultiPartException


class ProductUploadLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get('path', '')
        limits = {'/api/reader/v1/product/objects':9*1024*1024,
                  '/api/reader/v1/product/telemetry/track':65536,
                  '/api/reader/v1/product/payments/waffo/webhook':262144,
                  '/api/v1/payments/waffo/webhook':262144,
                  '/api/reader/v1/product/payments/i6ls/notify':65536}
        limit = limits.get(path)
        if path.startswith('/api/world/v1/worlds/') and path.endswith('/character-looks/upload'):
            limit=4*1024*1024+16384
        if scope['type'] != 'http' or scope.get('method') != 'POST' or limit is None:
            return await self.app(scope, receive, send)
        async def reject(status=413):
            request_id = scope.get('state', {}).get('request_id') or uuid4().hex
            response = JSONResponse({'code':'PRODUCT_BODY_LIMIT','message':'请求超过此功能的大小限制',
                'requestId':request_id,'retryable':False,'details':{}},status_code=status,
                headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
            await response(scope, receive, send)
        lengths = [v for k,v in scope.get('headers', []) if k.lower()==b'content-length']
        if lengths and (len(set(lengths)) != 1 or not lengths[0].isdigit() or len(lengths[0]) > 20):
            return await reject(400)
        if lengths and int(lengths[0]) > limit:
            return await reject()
        received, exceeded, replaced = 0, False, False
        async def bounded_receive():
            nonlocal received, exceeded
            message = await receive()
            if message['type'] == 'http.request':
                received += len(message.get('body',b''))
                if received > limit:
                    exceeded = True
                    raise MultiPartException('Product body limit exceeded')
            return message
        async def bounded_send(message):
            nonlocal replaced
            if exceeded:
                if not replaced and message['type']=='http.response.start':
                    replaced = True
                    await reject()
                return
            await send(message)
        await self.app(scope,bounded_receive,bounded_send)
