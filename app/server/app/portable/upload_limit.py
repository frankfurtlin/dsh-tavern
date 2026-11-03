"""Bound the backup body before multipart spooling, independently of novel limits."""
from uuid import uuid4

from starlette.formparsers import MultiPartException
from starlette.responses import JSONResponse

from .format import MAX_CIPHERTEXT_BYTES

# The binary quota includes the framing header; another MiB admits multipart
# boundaries and the bounded password field. Nginx is an independent outer cap.
MAX_REQUEST_BYTES = MAX_CIPHERTEXT_BYTES + 128 + 1024**2


class BackupUploadLimit:
    def __init__(self, app, max_bytes=MAX_REQUEST_BYTES):
        if type(max_bytes) is not int or max_bytes < 1024 or max_bytes > MAX_REQUEST_BYTES:
            raise ValueError("Invalid backup request quota")
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") != "POST" or scope.get("path") != "/api/reader/v1/backups/prepare":
            await self.app(scope, receive, send)
            return

        async def error(status, code, message):
            request_id = scope.get("state", {}).get("request_id") or uuid4().hex
            response = JSONResponse({"code": code, "message": message, "requestId": request_id,
                                     "retryable": False, "details": {}}, status_code=status,
                                    headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "X-Request-ID": request_id})
            await response(scope, receive, send)

        lengths = [value for key, value in scope.get("headers", []) if key.lower() == b"content-length"]
        if lengths:
            if len(set(lengths)) != 1 or not lengths[0].isdigit() or len(lengths[0]) > 20:
                await error(400, "INVALID_REQUEST", "上传长度字段无效")
                return
            if int(lengths[0]) > self.max_bytes:
                await error(413, "BACKUP_SIZE_LIMIT", "备份文件超过独立上传配额")
                return
        received = 0
        exceeded = False
        replaced = False

        async def bounded_receive():
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    exceeded = True
                    # Starlette's multipart parser closes its temporary file
                    # handles on this exception, rather than leaving a spool.
                    raise MultiPartException("Backup upload exceeds its bounded channel")
            return message

        async def bounded_send(message):
            nonlocal replaced
            if exceeded:
                if not replaced and message["type"] == "http.response.start":
                    replaced = True
                    await error(413, "BACKUP_SIZE_LIMIT", "备份文件超过独立上传配额")
                return
            await send(message)

        await self.app(scope, bounded_receive, bounded_send)
