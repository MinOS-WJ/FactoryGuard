"""Windows DPAPI-backed credential storage with explicit secret references."""

from __future__ import annotations

import ctypes
import json
import secrets
from dataclasses import dataclass
from typing import Protocol

from factoryguard.devices.base import DeviceCredentials


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_byte))]


class SecretProtectionError(RuntimeError):
    pass


class SecretProtector(Protocol):
    def protect(self, plaintext: bytes) -> bytes: ...
    def unprotect(self, ciphertext: bytes) -> bytes: ...


class WindowsDpapiProtector:
    """Protect secrets for the current Windows user without a separate key."""

    def __init__(self) -> None:
        if not hasattr(ctypes.windll, "crypt32"):
            raise SecretProtectionError("Windows DPAPI is unavailable")
        self._crypt32 = ctypes.windll.crypt32
        self._kernel32 = ctypes.windll.kernel32

    def protect(self, plaintext: bytes) -> bytes:
        return self._transform(plaintext, protect=True)

    def unprotect(self, ciphertext: bytes) -> bytes:
        return self._transform(ciphertext, protect=False)

    def _transform(self, value: bytes, *, protect: bool) -> bytes:
        input_buffer = ctypes.create_string_buffer(value)
        input_blob = _DataBlob(len(value), ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_byte)))
        output_blob = _DataBlob()
        entropy_buffer = ctypes.create_string_buffer(b"FactoryGuard.DeviceCredential.v1")
        entropy = _DataBlob(
            len(entropy_buffer.raw), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_byte))
        )
        flags = 0
        if protect:
            result = self._crypt32.CryptProtectData(
                ctypes.byref(input_blob),
                "FactoryGuard Device Credential",
                ctypes.byref(entropy),
                None,
                None,
                flags,
                ctypes.byref(output_blob),
            )
        else:
            result = self._crypt32.CryptUnprotectData(
                ctypes.byref(input_blob),
                None,
                ctypes.byref(entropy),
                None,
                None,
                flags,
                ctypes.byref(output_blob),
            )
        if not result:
            raise SecretProtectionError("Windows DPAPI failed to protect device credentials")
        try:
            return ctypes.string_at(output_blob.pbData, output_blob.cbData)
        finally:
            self._kernel32.LocalFree(output_blob.pbData)


class XorTestProtector:
    """Explicit in-memory/test protector. Never used by production startup."""

    def __init__(self, key: bytes | None = None) -> None:
        self.key = key or secrets.token_bytes(32)

    def protect(self, plaintext: bytes) -> bytes:
        return bytes(
            value ^ self.key[index % len(self.key)] for index, value in enumerate(plaintext)
        )

    unprotect = protect


@dataclass(slots=True)
class CredentialVault:
    """Maps opaque credential_ref values to DPAPI-protected JSON secrets."""

    protector: SecretProtector

    def encode(self, credentials: DeviceCredentials) -> str:
        payload = json.dumps(
            {
                "username": credentials.username,
                "password": credentials.password,
                "token": credentials.token,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        return self.protector.protect(payload).hex()

    def decode(self, value: str) -> DeviceCredentials:
        try:
            raw = bytes.fromhex(value)
            payload = json.loads(self.protector.unprotect(raw).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SecretProtectionError("device credential payload is invalid") from exc
        return DeviceCredentials(
            username=str(payload.get("username", "")),
            password=str(payload.get("password", "")),
            token=str(payload.get("token", "")),
        )


@dataclass(slots=True)
class PlaintextCredentialVault:
    """Readable JSON credential encoding for small local deployments."""

    def encode(self, credentials: DeviceCredentials) -> str:
        return json.dumps(
            {
                "username": credentials.username,
                "password": credentials.password,
                "token": credentials.token,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def decode(self, value: str) -> DeviceCredentials:
        try:
            payload = json.loads(value)
        except (TypeError, json.JSONDecodeError) as exc:
            raise SecretProtectionError("credential payload is invalid JSON") from exc
        return DeviceCredentials(
            username=str(payload.get("username", "")),
            password=str(payload.get("password", "")),
            token=str(payload.get("token", "")),
        )


def default_vault() -> CredentialVault:
    return CredentialVault(WindowsDpapiProtector())


def new_credential_ref() -> str:
    return f"cred-{secrets.token_hex(12)}"


__all__ = [
    "CredentialVault",
    "PlaintextCredentialVault",
    "SecretProtectionError",
    "SecretProtector",
    "WindowsDpapiProtector",
    "XorTestProtector",
    "default_vault",
    "new_credential_ref",
]
