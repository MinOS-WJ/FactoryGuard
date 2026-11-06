"""Small RFC 3261 message and digest helpers for GB/T 28181 signaling."""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from urllib.parse import unquote


class SipParseError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SipMessage:
    method: str
    request_uri: str
    status_code: int | None
    reason: str
    headers: tuple[tuple[str, str], ...]
    body: bytes = b""
    version: str = "SIP/2.0"

    @classmethod
    def parse(cls, data: bytes) -> SipMessage:
        if not data:
            raise SipParseError("empty SIP message")
        try:
            header_blob, body = data.split(b"\r\n\r\n", 1)
        except ValueError:
            header_blob, body = data.split(b"\n\n", 1) if b"\n\n" in data else (data, b"")
        lines = header_blob.replace(b"\r\n", b"\n").split(b"\n")
        if not lines or not lines[0]:
            raise SipParseError("missing SIP start line")
        try:
            start = lines[0].decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise SipParseError("SIP start line is not UTF-8") from exc
        headers: list[tuple[str, str]] = []
        for raw in lines[1:]:
            if not raw:
                continue
            if raw[:1] in b" \t" and headers:
                name, value = headers[-1]
                headers[-1] = (name, value + " " + raw.decode("utf-8", errors="replace").strip())
                continue
            if b":" not in raw:
                raise SipParseError(f"invalid SIP header: {raw[:80]!r}")
            name, value = raw.split(b":", 1)
            headers.append(
                (
                    name.decode("utf-8", errors="replace").strip(),
                    value.decode("utf-8", errors="replace").strip(),
                )
            )
        if start.startswith("SIP/2.0 "):
            parts = start.split(" ", 2)
            if len(parts) < 2 or not parts[1].isdigit():
                raise SipParseError("invalid SIP response status")
            return cls(
                "", "", int(parts[1]), parts[2] if len(parts) > 2 else "", tuple(headers), body
            )
        parts = start.split(" ")
        if len(parts) != 3 or not parts[2].startswith("SIP/"):
            raise SipParseError("invalid SIP request line")
        return cls(parts[0].upper(), parts[1], None, "", tuple(headers), body, parts[2])

    @property
    def is_request(self) -> bool:
        return self.status_code is None

    @property
    def header(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for name, value in self.headers:
            result.setdefault(name.lower(), value)
        return result

    def get(self, name: str, default: str = "") -> str:
        return self.header.get(name.lower(), default)

    @property
    def via(self) -> str:
        return self.get("via") or self.get("v")

    @property
    def from_header(self) -> str:
        return self.get("from") or self.get("f")

    @property
    def to_header(self) -> str:
        return self.get("to") or self.get("t")

    @property
    def call_id(self) -> str:
        return self.get("call-id") or self.get("i")

    @property
    def cseq(self) -> str:
        return self.get("cseq")

    @property
    def branch(self) -> str:
        match = re.search(r"branch=([^;\s]+)", self.via, re.IGNORECASE)
        return match.group(1) if match else ""

    @property
    def tag(self) -> str:
        match = re.search(r"tag=([^;\s]+)", self.to_header, re.IGNORECASE)
        return match.group(1) if match else ""

    @property
    def from_tag(self) -> str:
        match = re.search(r"tag=([^;\s]+)", self.from_header, re.IGNORECASE)
        return match.group(1) if match else ""

    @property
    def content_type(self) -> str:
        return self.get("content-type")

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def response(
        self,
        status: int,
        reason: str,
        *,
        extra_headers: tuple[tuple[str, str], ...] = (),
        body: bytes = b"",
    ) -> SipMessage:
        if not self.is_request:
            raise ValueError("cannot build a response from a SIP response")
        headers = [
            ("Via", self.via),
            ("From", self.from_header),
            (
                "To",
                self.to_header
                if (status < 300 and self.tag)
                else _with_tag(self.to_header, "fg-" + secrets.token_hex(6)),
            ),
            ("Call-ID", self.call_id),
            ("CSeq", self.cseq),
            ("Content-Length", str(len(body))),
        ]
        headers.extend(extra_headers)
        return SipMessage("", "", status, reason, tuple(headers), body)

    def encode(self) -> bytes:
        if self.is_request:
            start = f"{self.method} {self.request_uri} {self.version}"
        else:
            start = f"{self.version} {self.status_code} {self.reason}"
        lines = [start]
        seen_content_length = False
        for name, value in self.headers:
            if name.lower() == "content-length":
                seen_content_length = True
                value = str(len(self.body))
            lines.append(f"{name}: {value}")
        if not seen_content_length:
            lines.append(f"Content-Length: {len(self.body)}")
        return ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8") + self.body

    @classmethod
    def request(
        cls,
        method: str,
        request_uri: str,
        *,
        from_header: str,
        to_header: str,
        call_id: str,
        cseq: int,
        via: str,
        content_type: str = "",
        body: bytes = b"",
        extra_headers: tuple[tuple[str, str], ...] = (),
    ) -> SipMessage:
        headers = [
            ("Via", via),
            ("Max-Forwards", "70"),
            ("From", from_header),
            ("To", to_header),
            ("Call-ID", call_id),
            ("CSeq", f"{cseq} {method.upper()}"),
        ]
        if content_type:
            headers.append(("Content-Type", content_type))
        headers.extend(extra_headers)
        headers.append(("Content-Length", str(len(body))))
        return cls(method.upper(), request_uri, None, "", tuple(headers), body)


@dataclass(frozen=True, slots=True)
class DigestChallenge:
    realm: str
    nonce: str
    opaque: str = ""
    algorithm: str = "MD5"
    qop: str = "auth"

    @classmethod
    def parse(cls, value: str) -> DigestChallenge:
        fields = dict(re.findall(r'(\w+)=("[^"]*"|[^,\s]+)', value))
        clean = {key: unquote(item.strip('"')) for key, item in fields.items()}
        return cls(
            realm=clean.get("realm", ""),
            nonce=clean.get("nonce", ""),
            opaque=clean.get("opaque", ""),
            algorithm=clean.get("algorithm", "MD5"),
            qop=clean.get("qop", "auth"),
        )

    def header(self) -> str:
        value = f'Digest realm="{self.realm}", nonce="{self.nonce}", algorithm={self.algorithm}, qop="{self.qop}"'
        if self.opaque:
            value += f', opaque="{self.opaque}"'
        return value


def digest_response(
    *,
    username: str,
    password: str,
    realm: str,
    nonce: str,
    method: str,
    uri: str,
    qop: str = "auth",
    nc: str = "00000001",
    cnonce: str | None = None,
    algorithm: str = "MD5",
) -> str:
    algorithm = algorithm.upper()
    if algorithm not in {"MD5", "MD5-SESS"}:
        raise ValueError("only MD5 SIP digest is supported")
    cnonce = cnonce or secrets.token_hex(8)
    hash_name = "md5"
    ha1 = hashlib.new(hash_name, f"{username}:{realm}:{password}".encode()).hexdigest()
    if algorithm == "MD5-SESS":
        ha1 = hashlib.new(hash_name, f"{ha1}:{nonce}:{cnonce}".encode()).hexdigest()
    ha2 = hashlib.new(hash_name, f"{method}:{uri}".encode()).hexdigest()
    if qop:
        response = hashlib.new(
            hash_name, f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}".encode()
        ).hexdigest()
    else:
        response = hashlib.new(hash_name, f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()
    parts = [
        f'username="{username}"',
        f'realm="{realm}"',
        f'nonce="{nonce}"',
        f'uri="{uri}"',
        f'response="{response}"',
        f"algorithm={algorithm}",
    ]
    if qop:
        parts.extend((f"qop={qop}", f"nc={nc}", f'cnonce="{cnonce}"'))
    return "Digest " + ", ".join(parts)


def verify_digest(authorization: str, *, password: str, method: str, uri: str) -> bool:
    if not authorization.lower().startswith("digest "):
        return False
    fields = dict(re.findall(r'(\w+)=("[^"]*"|[^,\s]+)', authorization[7:]))
    clean = {key: value.strip('"') for key, value in fields.items()}
    try:
        expected = digest_response(
            username=clean.get("username", ""),
            password=password,
            realm=clean.get("realm", ""),
            nonce=clean.get("nonce", ""),
            method=method,
            uri=clean.get("uri", uri),
            qop=clean.get("qop", ""),
            nc=clean.get("nc", "00000001"),
            cnonce=clean.get("cnonce"),
            algorithm=clean.get("algorithm", "MD5"),
        )
    except (ValueError, TypeError):
        return False
    actual_response = clean.get("response", "")
    expected_fields = dict(re.findall(r'(\w+)=("[^"]*"|[^,\s]+)', expected[7:]))
    return secrets.compare_digest(actual_response, expected_fields.get("response", "").strip('"'))


def _with_tag(value: str, tag: str) -> str:
    return value if re.search(r"tag=", value, re.IGNORECASE) else f"{value};tag={tag}"


__all__ = ["DigestChallenge", "SipMessage", "SipParseError", "digest_response", "verify_digest"]
