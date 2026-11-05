import ipaddress
import socket
import time
import asyncio
import hashlib
import json
from contextlib import suppress
from urllib.parse import urlsplit
import httpx
from cryptography.fernet import Fernet
from .config import settings
from .errors import Problem
from .context_wire import wire_json
from .completion_policy import is_mimo_go
from .task_lease import LeaseLost
from .provider_secrets import CredentialEchoFilter, reject_echo
from .provider_transport import send_connected
from .provider_network import proxy_options, resolve_public


REQUEST_TOTAL_TIMEOUT_SECONDS = 120
STREAM_TOTAL_TIMEOUT_SECONDS = 300
VISION_READ_IDLE_SECONDS = 180


def encrypt_key(key):
    return Fernet(settings.provider_master_key.encode()).encrypt(key.encode()).decode()


def decrypt_key(cipher):
    return Fernet(settings.provider_master_key.encode()).decrypt(cipher.encode()).decode()


def validate_url(url):
    if settings.ai_mode == "mock" and url == "mock://deterministic":
        return None, None
    parsed = urlsplit(url)
    allowed = [h.strip().lower() for h in settings.provider_allowed_hosts.split(",") if h.strip()]
    if parsed.scheme != "https" or not parsed.hostname or parsed.hostname.lower() not in allowed or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.port not in (None, 443):
        raise Problem("PROVIDER_HOST_REJECTED", "供应商地址须为管理员允许的 HTTPS 主机", 403)
    try:
        addresses = {entry[4][0] for entry in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)}
    except OSError:
        addresses = set()
    if settings.provider_http_proxy and (not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses)):
        addresses = set(resolve_public(parsed.hostname))
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise Problem("PROVIDER_HOST_REJECTED", "供应商地址指向不允许的网络", 403)
    # Validate the complete set once, then pin every connection attempt to it.
    return parsed.hostname, tuple(sorted(addresses))


class ChatCompletionsAdapter:
    def generate(self, profile, system_prompt, data, output_limit, *, conversation_id=None, thinking_mode=None, sampling=None):
        return asyncio.run(self._generate(profile, system_prompt, data, output_limit, conversation_id=conversation_id, thinking_mode=thinking_mode, sampling=sampling))

    def generate_stream(self, profile, system_prompt, data, output_limit, *, on_delta, cancel_check, conversation_id=None, thinking_mode=None, sampling=None):
        return asyncio.run(self._generate(profile, system_prompt, data, output_limit, conversation_id=conversation_id,
            thinking_mode=thinking_mode, on_delta=on_delta, cancel_check=cancel_check, sampling=sampling))

    async def _generate(self, profile, system_prompt, data, output_limit, *, conversation_id=None, thinking_mode=None, on_delta=None, cancel_check=None, sampling=None):
        started = time.monotonic()
        try:
            hostname, pinned_addresses = await asyncio.wait_for(asyncio.to_thread(validate_url, profile.base_url), timeout=10)
        except TimeoutError:
            raise Problem("PROVIDER_HOST_UNAVAILABLE", "供应商主机解析超时，未发送请求", 422)
        try:
            key = decrypt_key(profile.key_cipher)
        except Exception:
            raise Problem("PROVIDER_CREDENTIALS_INVALID", "供应商凭据无法解密，未发送请求", 422)
        endpoint = httpx.URL(profile.base_url.rstrip("/") + "/chat/completions")
        # Pin the validated address for this call. TLS SNI and Host retain the approved hostname.
        vision_stream = data.get('_visionStream') is True and data.get('_structured') is True and bool(data.get('_sceneImages') or data.get('_sceneImage'))
        if hostname=='apihub.agnes-ai.com' and profile.model_id in ('agnes-2.5-flash','agnes-3.0-flash'):
            # The measured Agnes vision gateway can emit reasoning and DONE
            # without a finish frame or final content. Use its complete JSON
            # response for NEW requests; an old incomplete exchange remains
            # unknown and is never retried here. The total deadline still holds.
            vision_stream=False
        # Collect internal MiMo JSON through SSE as well as narrative output.
        # A non-streaming response hides all progress behind the ordinary
        # two-minute deadline. Internal JSON remains private until DONE and a
        # finish receipt; partial plans can never become world state.
        structured_stream = data.get('_structured') is True and (vision_stream or is_mimo_go(profile))
        streaming = on_delta is not None or structured_stream
        wire_data = {k:v for k,v in data.items() if k != '_visionStream'}
        request_body = {"model": profile.model_id, "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": wire_json(wire_data)}], profile.output_parameter: output_limit}
        if sampling:
            if sampling.get("temperature") is not None:
                request_body["temperature"] = float(sampling["temperature"])
            if sampling.get("topP") is not None:
                request_body["top_p"] = float(sampling["topP"])
            if sampling.get("stop"):
                request_body["stop"] = [str(item)[:200] for item in sampling["stop"]][:4]
        if data.get('_sceneImage') or data.get('_sceneImages'):
            # Only internally built, bounded image bytes; never fetch arbitrary URLs.
            pictures=data.get('_sceneImages', [data.get('_sceneImage')])
            if (not isinstance(pictures,list) or not 1<=len(pictures)<=11
                    or any(not isinstance(encoded,str) or not encoded.startswith('data:image/jpeg;base64,')
                           or len(encoded)>350000 for encoded in pictures)):
                raise Problem('SCENE_IMAGE_INVALID','人物校验图片无效，未发送请求',422,
                    {'requestSent':False,'responseReceived':False})
            request_body['messages'][1]['content']=[
                {'type':'text','text':wire_json({k:v for k,v in wire_data.items() if k not in ('_sceneImage','_sceneImages')})},
                *[{'type':'image_url','image_url':{'url':encoded}} for encoded in pictures]]
        if streaming:
            request_body.update(stream=True, stream_options={"include_usage": True})
        headers = {"Authorization": "Bearer " + key, "Host": hostname, "User-Agent": "story-reader/1.0"}
        if hostname == "opencode.ai" and httpx.URL(profile.base_url).path.rstrip("/") == "/zen/go/v1":
            # Identify our own client and a stable, opaque conversation scope.
            scope = conversation_id or data.get("scopeHash") or data.get("snapshotHash") or (data.get("character") or {}).get("id") or "connection"
            headers["x-opencode-session"] = "reader-" + hashlib.sha256((str(getattr(profile, "id", profile.model_id)) + ":" + str(scope)).encode()).hexdigest()
            if is_mimo_go(profile):
                # Worker passes the stage policy frozen in the approved plan.
                # Direct connection probes retain their 32-token non-thinking limit.
                if thinking_mode not in (None, "enabled", "disabled"):
                    raise Problem("CONSENT_REQUIRED", "模型执行策略无效，未发送请求", 403)
                request_body["thinking"] = {"type": thinking_mode or "disabled"}
        if profile.supports_json and data.get("_structured"):
            request_body["response_format"] = {"type": "json_object"}
            request_body["temperature"] = 0.2
        response_status = None
        stream_diagnostic = {}
        try:
            async def exchange():
              nonlocal response_status
              async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10, read=VISION_READ_IDLE_SECONDS if structured_stream else 120), follow_redirects=False, trust_env=False, **proxy_options(hostname, asynchronous=True)) as client:
                exchange_response = send_connected(client, endpoint, hostname, pinned_addresses, request_body, headers,
                    stream=streaming, cancel_check=cancel_check, started=started)
                response, connection_attempts = await self._with_cancel(exchange_response, cancel_check) if cancel_check else await exchange_response
                response_status = response.status_code
                if streaming:
                    content_type=response.headers.get('content-type','').split(';',1)[0].strip().lower()
                    stream_diagnostic['responseType']=content_type if content_type in ('text/event-stream','application/json','text/html','text/plain') else 'other'
                if response.status_code != 200:
                    from .provider_rejection import diagnostics
                    rejection=await diagnostics(response,credential=key)
                    await response.aclose()
                    raise Problem("PROVIDER_FAILED", "模型服务返回错误，未切换通道", 502,
                        {"reason": "http_error", "httpStatus": response_status,**rejection,
                         "latencyMs": round((time.monotonic()-started)*1000)})
                if streaming:
                    try:
                        echo_filter = CredentialEchoFilter(key)
                        def guarded_delta(chunk):
                            safe = echo_filter.add(chunk)
                            if safe and on_delta is not None:
                                on_delta(safe)
                        result = await self._read_stream(response, guarded_delta, cancel_check, output_limit, started, diagnostic=stream_diagnostic)
                        tail = echo_filter.finish()
                        if tail and on_delta is not None:
                            await asyncio.to_thread(on_delta, tail)
                        reject_echo(result['text'], key)
                        result['connectionAttempts'] = connection_attempts
                        return result
                    finally:
                        await response.aclose()
                result = response.json()
                choice = result["choices"][0]
                text = choice["message"]["content"]
                if not isinstance(text, str):
                    raise ValueError()
                reject_echo(text, key)
                return {"text": text, "finishReason": choice.get("finish_reason"), "usage": self.normalizeUsage(result.get("usage")), "connectionAttempts": connection_attempts, "latencyMs": round((time.monotonic() - started) * 1000)}
            # Internal JSON is collected privately, never published as partial
            # plans/anchors. Streaming avoids a two-minute whole-response deadline;
            # both first-token/read-idle and total duration remain bounded.
            total_timeout = STREAM_TOTAL_TIMEOUT_SECONDS if streaming else REQUEST_TOTAL_TIMEOUT_SECONDS
            return await asyncio.wait_for(exchange(), timeout=max(.01, total_timeout-(time.monotonic()-started)))
        except (httpx.TimeoutException, TimeoutError) as error:
            raise Problem("PROVIDER_STATUS_UNKNOWN", "调用超时，费用状态未知，请查询已有任务", 502,
                {"reason": "timeout", "exceptionType": type(error).__name__, "httpStatus": response_status,
                 "latencyMs": round((time.monotonic()-started)*1000), "streamDiagnostic":stream_diagnostic}) from None
        except (Problem, LeaseLost):
            raise
        except Exception as error:
            reason = "transport_error" if isinstance(error, httpx.TransportError) else "invalid_response" if response_status == 200 else "client_error"
            raise Problem("PROVIDER_STATUS_UNKNOWN", "供应商结果无法确认，未自动重试", 502,
                {"reason": reason, "exceptionType": type(error).__name__, "httpStatus": response_status,
                 "latencyMs": round((time.monotonic()-started)*1000), "streamDiagnostic":stream_diagnostic}) from None

    async def _read_stream(self, response, on_delta, cancel_check, output_limit, started, *, diagnostic=None):
        """Parse SSE frames; neither reasoning nor supplier error bodies are prose."""
        async def read():
            fragments, frame, finish, usage, done = [], [], None, None, False
            size = 0
            stats = diagnostic if diagnostic is not None else {}
            stats.update(frames=0,contentCharacters=0,reasoningCharacters=0,lines=0,doneSeen=False)
            async def consume():
                nonlocal finish, usage, done, size
                payload = "\n".join(frame)
                frame.clear()
                if payload == "[DONE]":
                    done = True
                    stats['doneSeen']=True
                    return
                result = json.loads(payload)
                stats['frames']+=1
                stats['lastFrameLatencyMs']=round((time.monotonic()-started)*1000)
                if not isinstance(result, dict) or result.get("error"):
                    raise ValueError("invalid stream frame")
                if result.get("usage") is not None:
                    usage = result["usage"]
                choices = result.get("choices", [])
                if not isinstance(choices, list):
                    raise ValueError("invalid choices")
                for choice in choices:
                    if not isinstance(choice, dict) or choice.get("index", 0) != 0:
                        continue
                    delta = choice.get("delta") or {}
                    if not isinstance(delta, dict):
                        raise ValueError("invalid delta")
                    content = delta.get("content")
                    reasoning=delta.get('reasoning_content')
                    if isinstance(reasoning,str):stats['reasoningCharacters']+=len(reasoning)
                    if content is not None and not isinstance(content, str):
                        raise ValueError("invalid content")
                    if content:
                        size += len(content)
                        if size > min(262144, max(4096, output_limit*32)):
                            raise ValueError("stream exceeded output bound")
                        fragments.append(content)
                        stats.setdefault('firstContentLatencyMs',round((time.monotonic()-started)*1000))
                        stats['contentCharacters']=size
                        await asyncio.to_thread(on_delta, content)
                    if choice.get("finish_reason") is not None:
                        finish = choice["finish_reason"]
                        stats['finishReason']=finish if finish in ('stop','length','content_filter','tool_calls') else 'other'
            async for line in response.aiter_lines():
                stats['lines']+=1
                if len(line) > 1048576:
                    raise ValueError("oversized stream frame")
                if not line:
                    if frame:
                        await consume()
                    if done:
                        break
                elif line.startswith("data:"):
                    frame.append(line[5:].lstrip(" "))
                    if sum(map(len, frame)) > 1048576:
                        raise ValueError("oversized stream frame")
            if frame:
                await consume()
            # A clean TCP close alone cannot establish that generation completed.
            if not done or finish is None:
                raise ValueError("incomplete stream")
            return {"text": "".join(fragments), "finishReason": finish, "usage": self.normalizeUsage(usage),
                "latencyMs": round((time.monotonic()-started)*1000)}
        return await self._with_cancel(read(), cancel_check)

    async def _with_cancel(self, operation, cancel_check):
        async def monitor():
            while True:
                await asyncio.sleep(.15)
                await asyncio.to_thread(cancel_check)
        reader = asyncio.create_task(operation)
        watcher = asyncio.create_task(monitor()) if cancel_check else None
        try:
            if watcher:
                finished, _ = await asyncio.wait({reader, watcher}, return_when=asyncio.FIRST_COMPLETED)
                if watcher in finished:
                    await watcher
            return await reader
        finally:
            reader.cancel()
            if watcher:
                watcher.cancel()
            with suppress(asyncio.CancelledError):
                await reader
            if watcher:
                with suppress(asyncio.CancelledError):
                    await watcher

    def cancel(self, call_id):
        return {"supported": False, "state": "unsupported"}

    def queryStatus(self, call_id):
        return {"supported": False, "state": "unsupported"}

    def normalizeUsage(self, usage):
        details = {}
        if isinstance(usage, dict) and type(usage.get("prompt_tokens")) is int and type(usage.get("completion_tokens")) is int and usage["prompt_tokens"] >= 0 and usage["completion_tokens"] >= 0:
            return {"inputTokens": usage["prompt_tokens"], "outputTokens": usage["completion_tokens"], "state": "known", **details}
        return {"inputTokens": None, "outputTokens": None, "state": "unknown", **details}


def gateway_usage(value):
    """Normalize the DSH gateway receipt; never invent token counts."""
    if not isinstance(value, dict):
        return {"state": "unknown", "inputTokens": None, "outputTokens": None}
    state = value.get("state")
    if state not in ("known", "estimated"):
        state = "unknown"

    def token(name):
        amount = value.get(name)
        return amount if type(amount) is int and amount >= 0 else None

    return {"state": state, "inputTokens": token("inputTokens"), "outputTokens": token("outputTokens")}


class DshAgentAdapter(ChatCompletionsAdapter):
    """Adapter for the DSH model gateway (docs/step2-dsh-generation.md §3).

    The gateway runs inside the DSH process and reuses its logged-in model
    selection, so this adapter neither validates provider URLs nor decrypts
    profile keys. Only the cancellation watchdog is shared with the base class.
    """

    def generate(self, profile, system_prompt, data, output_limit, *, conversation_id=None, thinking_mode=None, sampling=None):
        return asyncio.run(self._complete(system_prompt, data, output_limit, conversation_id, sampling))

    def generate_stream(self, profile, system_prompt, data, output_limit, *, on_delta, cancel_check, conversation_id=None, thinking_mode=None, sampling=None):
        return asyncio.run(self._stream(system_prompt, data, output_limit, on_delta, cancel_check, conversation_id, sampling))

    def _endpoint(self, suffix):
        base = (settings.dsh_agent_url or "").strip().rstrip("/")
        parsed = urlsplit(base)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise Problem("PROVIDER_UNAVAILABLE", "DSH 模型网关不可用，未收到回复", 502, {"reason": "invalid_configuration"})
        return base + suffix

    def _headers(self):
        headers = {"User-Agent": "story-reader/1.0"}
        secret = (settings.dsh_agent_secret or "").strip()
        if secret:
            headers["Authorization"] = "Bearer " + secret
        return headers

    def _timeout(self):
        total = max(0.01, float(settings.dsh_agent_timeout_seconds))
        return httpx.Timeout(total, connect=min(10.0, total))

    def _body(self, system_prompt, data, output_limit, conversation_id=None, sampling=None):
        body = {"system": system_prompt, "messages": [{"role": "user", "content": wire_json(data)}], "maxTokens": output_limit}
        if sampling:
            if sampling.get("temperature") is not None:
                body["temperature"] = float(sampling["temperature"])
            if sampling.get("topP") is not None:
                body["topP"] = float(sampling["topP"])
            if sampling.get("stop"):
                body["stop"] = [str(item)[:200] for item in sampling["stop"]][:4]
        # 上游 opencode-go 要求请求带会话标识（x-opencode-session）；用聊天会话 id
        # 保持同一段对话的路由/缓存稳定，避免每轮被当成一次性请求。
        if conversation_id:
            body["sessionId"] = str(conversation_id)[:200]
        if data.get("_structured"):
            body["temperature"] = 0.2
        return body

    def _gateway_error(self, reason, started, *, message="DSH 模型网关不可用，未收到回复", **details):
        return Problem("PROVIDER_UNAVAILABLE", message, 502,
            {"reason": reason, "latencyMs": round((time.monotonic() - started) * 1000), **details})

    def _check_status(self, status, started):
        if status == 200:
            return
        if status == 401:
            raise Problem("AGENT_UNAUTHORIZED", "DSH 模型网关未授权，请联系管理员更新共享密钥", 502,
                {"reason": "http_error", "httpStatus": 401, "latencyMs": round((time.monotonic() - started) * 1000)})
        raise self._gateway_error("http_error", started, httpStatus=status)

    async def _complete(self, system_prompt, data, output_limit, conversation_id=None, sampling=None):
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self._timeout(), follow_redirects=False, trust_env=False) as client:
                response = await client.post(self._endpoint("/complete"), json=self._body(system_prompt, data, output_limit, conversation_id, sampling), headers=self._headers())
                self._check_status(response.status_code, started)
                try:
                    payload = response.json()
                except ValueError:
                    raise self._gateway_error("invalid_response", started) from None
        except (Problem, LeaseLost):
            raise
        except (httpx.TimeoutException, TimeoutError):
            raise self._gateway_error("timeout", started) from None
        except httpx.TransportError:
            raise self._gateway_error("transport_error", started) from None
        except Exception:
            raise self._gateway_error("invalid_response", started) from None
        text = payload.get("text") if isinstance(payload, dict) else None
        if not isinstance(text, str):
            raise self._gateway_error("invalid_response", started)
        finish = payload.get("finishReason")
        return {"text": text, "finishReason": finish if isinstance(finish, str) else None,
                "usage": gateway_usage(payload.get("usage")), "latencyMs": round((time.monotonic() - started) * 1000)}

    async def _stream(self, system_prompt, data, output_limit, on_delta, cancel_check, conversation_id=None, sampling=None):
        started = time.monotonic()

        async def read():
            fragments, finish, usage, done, size = [], None, None, False, 0
            bound = min(262144, max(4096, output_limit * 32))

            async with httpx.AsyncClient(timeout=self._timeout(), follow_redirects=False, trust_env=False) as client:
                async with client.stream("POST", self._endpoint("/stream"), json=self._body(system_prompt, data, output_limit, conversation_id, sampling), headers=self._headers()) as response:
                    if response.status_code != 200:
                        await response.aread()
                        self._check_status(response.status_code, started)
                    async for line in response.aiter_lines():
                        if len(line) > 1048576:
                            raise self._gateway_error("invalid_response", started)
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].lstrip(" ")
                        if payload == "[DONE]":
                            done = True
                            break
                        try:
                            frame = json.loads(payload)
                        except ValueError:
                            raise self._gateway_error("invalid_response", started) from None
                        if not isinstance(frame, dict):
                            raise self._gateway_error("invalid_response", started)
                        kind = frame.get("type")
                        if kind == "delta":
                            text = frame.get("text")
                            if not isinstance(text, str):
                                raise self._gateway_error("invalid_response", started)
                            if text:
                                size += len(text)
                                if size > bound:
                                    raise self._gateway_error("invalid_response", started)
                                fragments.append(text)
                                if on_delta is not None:
                                    await asyncio.to_thread(on_delta, text)
                        elif kind == "usage":
                            usage = gateway_usage(frame.get("usage") if "usage" in frame else frame)
                        elif kind == "finish":
                            reason = frame.get("reason")
                            if isinstance(reason, str):
                                finish = reason
                        elif kind == "error":
                            error = frame.get("error") if isinstance(frame.get("error"), dict) else {}
                            code = error.get("code") if isinstance(error.get("code"), str) else "AGENT_UPSTREAM_FAILED"
                            if code == "AGENT_UNAUTHORIZED":
                                raise Problem("AGENT_UNAUTHORIZED", "DSH 模型网关未授权，请联系管理员更新共享密钥", 502,
                                    {"reason": "gateway_error", "gatewayCode": code, "latencyMs": round((time.monotonic() - started) * 1000)})
                            raise Problem("PROVIDER_UNAVAILABLE", "DSH 模型网关不可用，未收到回复", 502,
                                {"reason": "gateway_error", "gatewayCode": code[:64], "latencyMs": round((time.monotonic() - started) * 1000)})
            if not done:
                raise self._gateway_error("incomplete_stream", started)
            return {"text": "".join(fragments), "finishReason": finish or "stop", "usage": usage or gateway_usage(None),
                    "latencyMs": round((time.monotonic() - started) * 1000)}

        try:
            return await read() if cancel_check is None else await self._with_cancel(read(), cancel_check)
        except (Problem, LeaseLost):
            raise
        except (httpx.TimeoutException, TimeoutError):
            raise self._gateway_error("timeout", started) from None
        except httpx.TransportError:
            raise self._gateway_error("transport_error", started) from None
        except Exception:
            raise self._gateway_error("invalid_response", started) from None


class MockAdapter(ChatCompletionsAdapter):
    """Deterministic fixture adapter. Only explicitly enabled by AI_MODE=mock."""
    def generate_stream(self, profile, system_prompt, data, output_limit, *, on_delta, cancel_check, **kwargs):
        result = self.generate(profile, system_prompt, data, output_limit)
        if data.get("message") == "[fixture:stream-slow]":
            for _ in range(3):
                cancel_check()
                time.sleep(.1)
        for offset in range(0, len(result["text"]), 12):
            cancel_check()
            if data.get("message") == "[fixture:stream-interrupt]" and offset >= 24:
                raise Problem("PROVIDER_STATUS_UNKNOWN", "测试流已中断", 502, {"reason": "transport_error"})
            on_delta(result["text"][offset:offset+12])
            time.sleep(.12 if data.get("message") == "[fixture:stream-slow]" else .08)
        return result

    def generate(self, profile, system_prompt, data, output_limit):
        import json
        prefix = "\n".join(b["text"] for b in data.get("blocks", data.get("prefix", []))) + "\n" + "\n".join(e.get("quote", "") for e in data.get("evidence", []))
        if system_prompt.startswith("analysis-"):
            characters, facts, evidence = [], [], []
            # These fixture identities do not run in real mode or populate unanalysed books.
            for name in ("林渡", "阿岑", "梅舟"):
                matches = [b for b in data["blocks"] if name in b["text"]]
                if not matches:
                    continue
                eids = []
                for b in matches:
                    eid = f"e{len(evidence)}"
                    evidence.append({"id": eid, "blockId": b["blockId"], "quote": b["text"]})
                    eids.append(eid)
                prior = next((c for c in data.get("priorCharacters", []) if c["displayName"] == name), None)
                characters.append({"localId": name, "displayName": name, "evidenceIds": eids, "priorRef": prior["id"] if prior else None, "continuity": "explicit" if prior else "uncertain"})
                if name == "阿岑" and any(("阿岑" in b["text"] and ("死亡" in b["text"] or "死于" in b["text"])) for b in matches):
                    death_eids = [e["id"] for e in evidence if "阿岑" in e["quote"] and ("死亡" in e["quote"] or "死于" in e["quote"])]
                    facts.append({"localId": "alive-acen", "subjectRef": name, "predicate": "alive", "value": False, "certainty": "explicit", "evidenceIds": death_eids, "occurredOrder": None, "knownBy": ["林渡", "阿岑"]})
                elif name == "阿岑" and "阿岑仍然活着" in prefix:
                    alive_eids = [e["id"] for e in evidence if "阿岑仍然活着" in e["quote"]]
                    facts.append({"localId": "alive-acen", "subjectRef": name, "predicate": "alive", "value": True, "certainty": "explicit", "evidenceIds": alive_eids, "occurredOrder": None, "knownBy": ["林渡", "阿岑"]})
                if name == "林渡" and "说话简短" in prefix:
                    facts.append({"localId": "style", "subjectRef": name, "predicate": "speaking_style", "value": "简短、行动导向", "certainty": "explicit", "evidenceIds": eids[:1], "occurredOrder": None, "knownBy": [name]})
                if name == "林渡" and "夜渡" in prefix:
                    secret = [e for e in evidence if "夜渡" in e["quote"]]
                    learned = any("梅舟读到旧信" in e["quote"] for e in secret)
                    facts.append({"localId": "identity", "subjectRef": name, "predicate": "secret_identity", "value": "夜渡", "certainty": "explicit", "evidenceIds": [e["id"] for e in secret], "occurredOrder": None, "knownBy": ["林渡"] + (["梅舟"] if learned else [])})
            for fact in facts:
                fact["temporalRelation"] = "at_scope"
                fact["knowledge"] = [{"characterRef": name, "evidenceIds": fact["evidenceIds"], "learnedOrder": None, "validity": "at_scope"} for name in fact["knownBy"]]
            text = json.dumps({"schemaVersion": 1, "scopeHash": data["scopeHash"], "characters": characters, "facts": facts, "evidence": evidence, "unresolved": [], "summary": prefix}, ensure_ascii=False)
        elif system_prompt.startswith("validation-"):
            leaked = "夜渡" in data["output"] and "夜渡" not in prefix
            text = json.dumps({"verdict": "blocked" if leaked else "pass", "reasonCode": "SPOILER" if leaked else "SAFE", "evidenceIds": [], "severity": "P0" if leaked else "none"})
        elif system_prompt.startswith('world_character_chat_review-'):
            text=json.dumps({'consistent':True,'reason':'fixture review'})
        elif system_prompt.startswith("world_character_chat-"):
            name=data['source']['character']['name']
            text=f'我是{name}。我们可以聊聊此刻的想法，也可以把你的问题当作一种假设来讨论。已经发生的事情会留在原来的故事里；这段对话不会替你交接宝石，也不会改变任何人的去向。\n\n我会根据现在确实知道的事情回答。对于尚未亲眼见过或没有证据的问题，我宁愿保留判断。你想先谈哪一件事？'
        elif system_prompt.startswith("role_chat-"):
            name = data["character"]["displayName"]
            if any(f["predicate"] == "secret_identity" for f in data.get("characterFacts", [])) and ("身份" in data.get("message", "") or "夜渡" in data.get("message", "")):
                text = "我已经读过旧信。林渡就是代号夜渡的送信者，这件事我现在知道了。但知道一个名字，并不表示我能猜到所有还没有发生的事。雾港的灯还亮着，我们可以先把眼前的绳子系好，再谈信里已经明确写出来的内容。"
            elif "身份" in data.get("message", "") or "两年前" in data.get("message", "") or "夜渡" in data.get("message", ""):
                text = "先把眼前的事情做好。你问的那些事，现在没有可以告诉你的答案。东岸的风又起了，绳子得收紧，灯还亮着。阿岑的事情，要按我们此刻已经知道的来谈，不能凭一句猜测就改变发生过的事。等到该知道的时候，再去看那封信吧。"
            elif any(f["predicate"] == "alive" and f["value"] is False for f in data.get("characterFacts", [])):
                text = "阿岑已经不在了。船往前走，我却还记得东岸的码头和那阵钟声。我不能把发生过的事说成没有发生，也不能用你希望听见的话填补那片沉默。先收好绳子吧。如果你愿意，我们可以谈谈此刻还能做什么。"
            elif any(f["predicate"] == "alive" and f["value"] is True for f in data.get("characterFacts", [])):
                text = "阿岑还活着。我们已经从仓库里出来，现在留在东岸，把眼前能做的事一件件做好。我不会把另一种走向当成我们此刻发生过的事，也不会替明天作保证。绳子已经系牢，码头的风还有些冷，先找个能歇脚的地方吧。"
            else:
                text = f"我是{name}。我留意眼前的绳结，也留意雾中遮住的路。东岸的码头还在，北岸的灯也亮着。你问的事，我只按眼前已经知道的来答；没有发生的事，我不作保证。你可以继续问读到的事情，说说此刻最担心的是什么。我会把能确认的判断一步一步说清楚。"
        elif system_prompt.startswith("tavern-"):
            character = data.get("character") or {}
            name = character.get("name") or "角色"
            text = (f"（{name}停下手里的动作，抬眼看你）你来得正好。窗外的雨还没停，"
                    f"桌上那盏灯只剩半圈光。我先不急着回答，想听听你这一路是怎么走过来的。"
                    f"如果你愿意，就把刚才没说完的那句话接下去，我会认真听。")
        elif system_prompt.startswith("card_memory_extract-"):
            text = json.dumps({"memories": [
                {"kind": "fact", "title": "读者的猫", "content": "读者养了一只叫团子的猫。", "salience": 60},
            ]}, ensure_ascii=False)
        elif system_prompt.startswith("card_draft-"):
            text = json.dumps({
                "name": "雾港信使",
                "description": "一位常年在雾港码头送信的年轻人，披着旧呢斗篷，袖口沾着盐霜。",
                "personality": "说话简短，习惯先观察再开口；对承诺看得比性命重。",
                "scenario": "雨夜的码头，灯影在水面上摇晃，他把最后一封没有署名的信递给你。",
                "first_message": "（把信压在斗篷下，压低声音）这封信只给愿意等雨停的人。你若接过去，就得先答应我，不把收信人的名字说出去。",
                "tags": ["悬疑", "信使", "雾港"],
            }, ensure_ascii=False)
        elif system_prompt.startswith("card_greetings-"):
            card = data.get("card") or {}
            name = card.get("name") or "角色"
            scene = card.get("scenario") or "此刻"
            count = max(1, min(5, int(data.get("count") or 3)))
            openings = ["你来得正好。", "雨还没停。", "先坐下吧。", "我等你很久了。", "把门带上。"]
            text = json.dumps({"greetings": [
                f"（{name}看了你一眼）{openings[index % len(openings)]}关于{scene[:24]}，我想先听听你的说法。"
                for index in range(count)
            ]}, ensure_ascii=False)
        elif system_prompt.startswith("connection"):
            text = "连接正常"
        else:
            saving = "救" in data.get("instruction", "") or "留下" in data.get("instruction", "")
            index = data.get("chapter", {}).get("index", 1)
            start = ("林渡留下撬开仓库门，救出了阿岑。阿岑仍然活着，两人留在东岸。" if saving else "林渡驶离东岸，仓库坍塌，阿岑死于当夜。林渡独自漂向外海。") if index == 1 else f"承接前章，林渡继续沿着已经选择的路线前行。第{index}天的灯重新亮起，码头上的新消息催促他作出下一步决定。"
            day = f"第{index}天的" if data.get("chapter", {}).get("count", 1) > 1 else ""
            text = start + "\n\n" + "\n\n".join(f"{day}雾里传来第{i + 1}阵风。码头木板发出轻响，林渡把绳结重新系牢。他没有急着开口，只看着灯影在水上慢慢移动。岸上的人停住脚步，等他做出下一步决定。" for i in range(10))
            if data.get("chapter", {}).get("count", 1) > 1:
                text = f"标题：第{data['chapter'].get('number', index)}章 · 雾港新程\n\n" + text
        return {"text": text, "finishReason": "stop", "usage": {"inputTokens": len(wire_json(data).encode()), "outputTokens": len(text), "state": "estimated"}, "latencyMs": 1}
