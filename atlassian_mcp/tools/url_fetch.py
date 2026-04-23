"""SSRF-safe URL fetcher for attach_from_url tools.

Contract:
- Only http/https schemes allowed.
- DNS resolved manually; every resolved IP checked against a blocklist
  (loopback, RFC1918 private, link-local, multicast, reserved, unspecified).
- IPv4-mapped IPv6 addresses are unwrapped before validation, so
  ::ffff:10.0.0.1 cannot be used to bypass checks.
- Redirects followed manually (max 5 hops); every hop re-validated.
- Content-Length, when present, is checked before reading the body.
  Regardless of Content-Length, the streamed body is capped at max_bytes
  and the download is aborted if exceeded.
- Only GET is issued. No cookies or credentials are sent.

The helper returns (filename, mime, bytes). Callers decide what to do
with the payload (upload to Jira/Confluence, etc.).
"""
from __future__ import annotations

import ipaddress
import logging
import re
import socket
from dataclasses import dataclass
from urllib.parse import unquote, urljoin, urlparse

import requests

from atlassian_mcp.config import settings
from atlassian_mcp.tools.common import ToolError

log = logging.getLogger(__name__)

MAX_REDIRECTS = 5
CONNECT_TIMEOUT = 10
READ_TIMEOUT = 30
CHUNK_SIZE = 64 * 1024
USER_AGENT = "atlassian-mcp/attach-from-url"


@dataclass
class FetchedFile:
    filename: str
    mime: str | None
    data: bytes


def _validate_scheme_and_host(url: str) -> tuple[str, str]:
    """Return (scheme, hostname) after basic validation, or raise ToolError."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ToolError(
            f"Scheme '{parsed.scheme}' not allowed. Only http and https permitted."
        )
    if not parsed.hostname:
        raise ToolError(f"URL has no hostname: {url}")
    return parsed.scheme, parsed.hostname


def _classify_ip(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> tuple[bool, str]:
    """Return (blocked, reason). Unwraps IPv4-mapped IPv6 first."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_loopback:
        return True, "loopback"
    if ip.is_link_local:
        return True, "link-local"
    if ip.is_private:
        return True, "private"
    if ip.is_multicast:
        return True, "multicast"
    if ip.is_reserved:
        return True, "reserved"
    if ip.is_unspecified:
        return True, "unspecified"
    return False, ""


def _resolve_and_validate(hostname: str) -> None:
    """DNS resolve hostname. Raise ToolError if any resolved IP is blocked."""
    try:
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise ToolError(f"DNS resolution failed for {hostname}: {e}") from e

    if not infos:
        raise ToolError(f"No addresses resolved for {hostname}")

    for _, _, _, _, sockaddr in infos:
        raw = sockaddr[0]
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            continue
        blocked, reason = _classify_ip(ip)
        if blocked:
            raise ToolError(
                f"Host {hostname} resolves to {ip} ({reason}); "
                f"fetching from private/reserved addresses is not allowed."
            )


# RFC 5987 extended form: filename*=UTF-8''example.txt
_CD_FILENAME_STAR = re.compile(
    r"filename\*\s*=\s*(?P<charset>[^']*)'(?P<lang>[^']*)'(?P<value>[^;]+)",
    re.IGNORECASE,
)
# Quoted or bare filename: filename="a.txt" or filename=a.txt
_CD_FILENAME = re.compile(
    r'filename\s*=\s*(?:"(?P<quoted>[^"]+)"|(?P<bare>[^;\s]+))',
    re.IGNORECASE,
)


def _filename_from_content_disposition(header: str | None) -> str | None:
    if not header:
        return None
    m = _CD_FILENAME_STAR.search(header)
    if m:
        charset = (m.group("charset") or "utf-8").strip() or "utf-8"
        value = m.group("value").strip()
        try:
            return unquote(value, encoding=charset) or None
        except LookupError:
            return unquote(value) or None
    m = _CD_FILENAME.search(header)
    if m:
        return ((m.group("quoted") or m.group("bare")) or "").strip() or None
    return None


def _filename_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    if not parsed.path:
        return None
    last = parsed.path.rstrip("/").split("/")[-1]
    return unquote(last).strip() or None


def fetch_url(
    url: str,
    filename: str | None = None,
    mime: str | None = None,
    max_bytes: int | None = None,
) -> FetchedFile:
    """Fetch a URL safely.

    Args:
        url: http(s) URL. Scheme + resolved IPs are validated.
        filename: override auto-detection.
        mime: override auto-detection (takes precedence over Content-Type).
        max_bytes: cap on body size. Default settings.max_url_fetch_size.

    Returns FetchedFile(filename, mime, data).

    Raises ToolError on any SSRF/size/redirect/HTTP issue or missing filename.
    """
    max_size = max_bytes if max_bytes is not None else settings.max_url_fetch_size

    current_url = url
    resolved_filename = filename
    resolved_mime = mime
    buf = bytearray()

    for hop in range(MAX_REDIRECTS + 1):
        _, hostname = _validate_scheme_and_host(current_url)
        _resolve_and_validate(hostname)

        log.info("fetch_url hop=%d url=%s", hop, current_url)

        try:
            resp = requests.get(
                current_url,
                stream=True,
                allow_redirects=False,
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                headers={"User-Agent": USER_AGENT},
            )
        except requests.RequestException as e:
            raise ToolError(
                f"Request to {current_url} failed: {type(e).__name__}: {e}"
            ) from e

        try:
            # Redirect?
            if 300 <= resp.status_code < 400 and resp.status_code != 304:
                location = resp.headers.get("Location")
                if not location:
                    raise ToolError(
                        f"Redirect {resp.status_code} with no Location header at "
                        f"{current_url}"
                    )
                current_url = urljoin(current_url, location)
                continue

            if not resp.ok:
                raise ToolError(
                    f"HTTP {resp.status_code} fetching {current_url}: "
                    f"{resp.reason or 'no reason'}"
                )

            cl_raw = resp.headers.get("Content-Length")
            if cl_raw:
                try:
                    cl = int(cl_raw)
                except ValueError:
                    cl = 0
                if cl > max_size:
                    raise ToolError(
                        f"Content-Length {cl} exceeds max_bytes {max_size}"
                    )

            for chunk in resp.iter_content(CHUNK_SIZE):
                if not chunk:
                    continue
                buf.extend(chunk)
                if len(buf) > max_size:
                    raise ToolError(
                        f"Body exceeded max_bytes {max_size} "
                        f"(aborted at {len(buf)} bytes)"
                    )

            if resolved_mime is None:
                ct = resp.headers.get("Content-Type")
                if ct:
                    resolved_mime = ct.split(";", 1)[0].strip() or None

            if resolved_filename is None:
                resolved_filename = _filename_from_content_disposition(
                    resp.headers.get("Content-Disposition")
                )
            if resolved_filename is None:
                resolved_filename = _filename_from_url(current_url)

            break
        finally:
            resp.close()
    else:
        raise ToolError(
            f"Too many redirects (> {MAX_REDIRECTS}) starting at {url}"
        )

    if not resolved_filename:
        raise ToolError(
            "Could not determine filename from URL or Content-Disposition. "
            "Pass filename= explicitly."
        )

    return FetchedFile(
        filename=resolved_filename,
        mime=resolved_mime,
        data=bytes(buf),
    )
