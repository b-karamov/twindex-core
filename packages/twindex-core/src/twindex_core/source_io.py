from __future__ import annotations

import ipaddress
import os
import socket
import ssl
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlsplit

import httpcore
import httpx

MAX_BYTES = 20 * 1024 * 1024
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 100
MAX_ZIP_ENTRIES = 2000
MAX_CONVERSATION_PREVIEW_BYTES = 512 * 1024


class SourceSecurityError(ValueError):
    pass


@dataclass(frozen=True)
class AcquiredSource:
    raw: bytes
    title: str
    uri: str
    suffix: str
    metadata: dict = field(default_factory=dict)


def read_local_sources(path: Path) -> list[AcquiredSource]:
    """Read only a requested file or Markdown folder; never request OS privileges."""
    path = path.expanduser()
    if path.is_symlink():
        raise SourceSecurityError("Symlink sources are not allowed")
    if path.is_dir():
        files = []

        def walk_error(error):
            raise error

        for root, dirs, names in os.walk(path, followlinks=False, onerror=walk_error):
            dirs[:] = [name for name in dirs if not (Path(root) / name).is_symlink()]
            for name in names:
                file = Path(root) / name
                if file.suffix.lower() == ".md" and not file.is_symlink():
                    files.append(file)
                    if len(files) > MAX_ZIP_ENTRIES:
                        raise SourceSecurityError("Too many Markdown files")
        result, total = [], 0
        for file in sorted(files):
            items = read_local_sources(file)
            total += sum(len(item.raw) for item in items)
            if total > 100 * 1024 * 1024:
                raise SourceSecurityError("Folder exceeds 100 MiB")
            result.extend(items)
        return result
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise SourceSecurityError("Source must be a regular file")
        if info.st_size > MAX_BYTES:
            raise SourceSecurityError("Source exceeds 20 MiB")
        raw = handle.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise SourceSecurityError("Source exceeds 20 MiB")
    return [
        AcquiredSource(
            raw=raw,
            title=path.name,
            uri=path.resolve().as_uri(),
            suffix=path.suffix.lower(),
        )
    ]


def _public_url(url: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise SourceSecurityError(
            "Only public HTTP(S) URLs without credentials are allowed"
        )
    try:
        addresses = socket.getaddrinfo(
            parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
        )
    except OSError as exc:
        raise SourceSecurityError("URL host cannot be resolved") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise SourceSecurityError(
                "Private, loopback, and special network addresses are not allowed"
            )


class _PublicBackend(httpcore.SyncBackend):
    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[
            tuple[int, int, int]
            | tuple[int, int, bytes | bytearray]
            | tuple[int, int, None, int]
        ]
        | None = None,
    ) -> httpcore.NetworkStream:
        try:
            addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise SourceSecurityError("URL host cannot be resolved") from exc
        ips = [ipaddress.ip_address(address[4][0]) for address in addresses]
        if not ips or any(not ip.is_global for ip in ips):
            raise SourceSecurityError("URL DNS resolved to a non-public address")
        return super().connect_tcp(
            str(ips[0]),
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )


class URLAcquirer:
    def __init__(self, http_client: httpx.Client | None = None):
        self.http_client = http_client

    def fetch(self, url: str) -> AcquiredSource:
        pool = (
            None
            if self.http_client
            else httpcore.ConnectionPool(
                ssl_context=ssl.create_default_context(),
                network_backend=_PublicBackend(),
                max_connections=1,
                max_keepalive_connections=0,
                retries=0,
            )
        )
        started = time.monotonic()
        try:
            current = url
            for _ in range(4):
                _public_url(current)
                if time.monotonic() - started > 30:
                    raise SourceSecurityError("URL fetch exceeded 30 seconds")
                if self.http_client:
                    with self.http_client.stream(
                        "GET", current, follow_redirects=False
                    ) as response:
                        status = response.status_code
                        headers = dict(response.headers)
                        raw = (
                            self._read_url_body(response.iter_bytes(), started)
                            if status not in {301, 302, 303, 307, 308}
                            else b""
                        )
                else:
                    assert pool is not None
                    with pool.stream(
                        "GET",
                        current,
                        extensions={
                            "timeout": {
                                "connect": 10,
                                "read": 20,
                                "write": 10,
                                "pool": 10,
                            }
                        },
                    ) as response:
                        status = response.status
                        headers = {
                            key.decode("latin1").lower(): value.decode("latin1")
                            for key, value in response.headers
                        }
                        raw = (
                            self._read_url_body(response.iter_stream(), started)
                            if status not in {301, 302, 303, 307, 308}
                            else b""
                        )
                if status in {301, 302, 303, 307, 308}:
                    location = headers.get("location")
                    if not location:
                        raise SourceSecurityError("Redirect without location")
                    current = urljoin(current, location)
                    continue
                if status >= 400:
                    raise SourceSecurityError(f"URL returned HTTP {status}")
                content_type = headers.get("content-type", "").split(";", 1)[0].lower()
                suffix = {
                    "application/pdf": ".pdf",
                    "image/png": ".png",
                    "image/jpeg": ".jpg",
                    "text/plain": ".txt",
                    "text/html": ".html",
                    "": ".txt",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
                }.get(content_type)
                if suffix is None:
                    raise SourceSecurityError(
                        f"Unsupported URL content type: {content_type}"
                    )
                return AcquiredSource(
                    raw=raw,
                    title=current,
                    uri=current,
                    suffix=suffix,
                    metadata={"content_type": content_type, "transport": "url"},
                )
            raise SourceSecurityError("Too many URL redirects")
        finally:
            if pool:
                pool.close()

    @staticmethod
    def _read_url_body(chunks, started: float) -> bytes:
        raw = bytearray()
        for chunk in chunks:
            raw.extend(chunk)
            if len(raw) > MAX_BYTES:
                raise SourceSecurityError("URL content exceeds 20 MiB")
            if time.monotonic() - started > 30:
                raise SourceSecurityError("URL fetch exceeded 30 seconds")
        return bytes(raw)
