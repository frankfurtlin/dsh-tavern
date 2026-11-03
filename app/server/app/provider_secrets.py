"""Do not publish or journal a supplier echo of the credential used by a call."""
import re
from .errors import Problem


def decoded_escapes(text):
    """Decode complete JSON string escapes and keep their raw start offsets.

    Parsing is iterative and deliberately independent of JSON object depth.
    An incomplete escape is held by the stream filter until its next chunk.
    """
    pieces, starts, index, incomplete = [], [], 0, None
    simple = {'/':'/', '"':'"', '\\':'\\', 'b':'\b', 'f':'\f', 'n':'\n', 'r':'\r', 't':'\t'}
    while index < len(text):
        start, char = index, text[index]
        if char == '\\':
            if index + 1 == len(text):
                incomplete = start
                break
            escaped = text[index + 1]
            if escaped in simple:
                char, index = simple[escaped], index + 2
            elif escaped == 'u':
                code = text[index + 2:index + 6]
                if len(code) < 4:
                    incomplete = start
                    break
                if re.fullmatch(r'[a-f0-9]{4}',code,re.I):
                    point = int(code,16)
                    if 0xD800 <= point <= 0xDBFF:
                        pair = text[index + 6:index + 12]
                        if len(pair) < 6:
                            incomplete = start
                            break
                        if re.fullmatch(r'\\u[dD][c-fC-F][a-f0-9]{2}',pair):
                            char = chr(0x10000 + ((point - 0xD800) << 10) + int(pair[2:],16) - 0xDC00)
                            index += 12
                        else:
                            char, index = chr(point), index + 6
                    else:
                        char, index = chr(point), index + 6
                else:
                    index += 1
            else:
                index += 1
        else:
            index += 1
        pieces.append(char)
        starts.append(start)
    return ''.join(pieces), starts, incomplete


def reject_echo(text, key):
    if not key or not isinstance(text, str):
        return
    # A model can return the literal key inside a JSON-escaped string. Check
    # that representation before any parsing, publication or result journal.
    decoded, _, _ = decoded_escapes(text)
    if key in text or key in decoded:
        raise Problem('PROVIDER_CREDENTIAL_ECHO', '模型返回包含敏感凭据，本次输出已停止', 502)


class CredentialEchoFilter:
    """Hold a split literal prefix until it cannot become the private key."""
    def __init__(self, key):
        self.key, self.pending, self.received = key, '', ''

    def add(self, chunk):
        received = self.received + chunk
        reject_echo(received, self.key)
        self.received = received
        candidate = self.pending + chunk
        decoded, starts, incomplete = decoded_escapes(candidate)
        hold_start = incomplete if incomplete is not None else len(candidate)
        if self.key:
            for text, mapped in ((candidate,None),(decoded,starts)):
                for size in range(min(len(text),len(self.key)-1),0,-1):
                    if text.endswith(self.key[:size]):
                        hold_start = min(hold_start, mapped[len(text)-size] if mapped is not None else len(text)-size)
                        break
        if hold_start < len(candidate):
            self.pending = candidate[hold_start:]
            return candidate[:hold_start]
        self.pending = ''
        return candidate

    def finish(self):
        reject_echo(self.received, self.key)
        result, self.pending = self.pending, ''
        return result
