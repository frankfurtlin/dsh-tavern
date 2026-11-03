"""Recover connection establishment only, never replay a submitted model POST."""
import asyncio
import time

import httpx

from .errors import Problem


CONNECT_ATTEMPTS = 3
CONNECT_BACKOFF_SECONDS = .25
NOT_SENT_CODES = frozenset({
    'PROVIDER_HOST_REJECTED', 'PROVIDER_HOST_UNAVAILABLE',
    'PROVIDER_CREDENTIALS_INVALID', 'PROVIDER_CONNECTION_FAILED',
    'SCENE_IMAGE_INVALID',  # Internal image validation precedes every HTTP write.
})


async def send_connected(client, endpoint, hostname, addresses, body, headers, *, stream, cancel_check, started):
    # Legacy callers and isolated transports can provide one pinned address.
    pins = (addresses,) if isinstance(addresses, str) else tuple(addresses)
    # Try each validated address once, up to eight, before declaring an
    # unsent request unavailable. Three attempts used to omit the fourth edge.
    attempts = max(CONNECT_ATTEMPTS, min(len(pins), 8))
    for attempt in range(attempts):
        if cancel_check:
            await asyncio.to_thread(cancel_check)
        writing_started = False

        async def trace(name, info):
            nonlocal writing_started
            # Mark before the first HTTP write, including partial writes. Do
            # not inspect or retain trace info: it can contain private headers.
            if name.endswith(('send_request_headers.started', 'send_request_body.started')) and getattr(info.get('request'),'method',None)!=b'CONNECT':
                writing_started = True

        request = client.build_request('POST', endpoint.copy_with(host=pins[attempt % len(pins)]), json=body, headers=headers)
        request.extensions.update(sni_hostname=hostname, trace=trace)
        try:
            response = await client.send(request, stream=stream)
            return response, attempt + 1
        except (httpx.ConnectError, httpx.ConnectTimeout) as error:
            if writing_started:
                raise
            # ConnectError/ConnectTimeout originate in TCP or TLS setup, before
            # HTTP submission. Read/write errors and HTTP responses never enter
            # this branch. TLS and the hostname stay identical on every attempt.
            if attempt + 1 == attempts:
                raise Problem('PROVIDER_CONNECTION_FAILED', '暂时连接不上模型，当前步骤尚未发送', 502, {
                    'reason': 'connection_failed', 'exceptionType': type(error).__name__,
                    'latencyMs': round((time.monotonic()-started)*1000),
                    'connectionAttempts': attempt + 1, 'requestSent': False, 'httpStatus': None,
                }) from None
            await asyncio.sleep(CONNECT_BACKOFF_SECONDS * 2**attempt)
