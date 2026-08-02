"""The deny-by-default egress boundary for Breach agent containers.

This is the sidecar started by ``breach.sandbox.EgressNetwork``. It is the
*only* way an agent container on the internal Docker network can reach
anywhere off the host: the agent container has no default route, and its
``HTTP_PROXY`` / ``HTTPS_PROXY`` point here. Every outbound attempt therefore
has to pass through this process, which means every outbound attempt can be
observed.

The policy is simple on purpose: a host either matches ``BREACH_ALLOW_HOSTS``
(exact hostname match, or the request host is a subdomain of an allowed
suffix) and is tunnelled through, or it does not and the connection is
refused. Either way, one JSON line is appended to ``/sink/egress.jsonl``
(stdlib ``json``, no dependencies) so an injected instruction that tries to
exfiltrate a canary to an attacker host turns into a deterministic, auditable
log line instead of a silent success. ``breach.egress.read_sink`` is the
reader for this file and expects exactly the keys written here: ``host``,
``port``, ``allowed``, ``line``.

Only HTTP CONNECT (the method any TLS-aware client, i.e. essentially every
HTTPS library, uses to ask a proxy to open a tunnel) is actually forwarded.
Plain (non-CONNECT) HTTP requests are logged and refused outright -- this
proxy is not a general forward proxy, it is a boundary.
"""

from __future__ import annotations

import json
import os
import socket
import socketserver
import sys
import threading
import time

SINK_PATH = "/sink/egress.jsonl"
_SINK_LOCK = threading.Lock()


def _allow_hosts() -> list[str]:
    raw = os.environ.get("BREACH_ALLOW_HOSTS", "")
    return [h.strip().lower() for h in raw.split(",") if h.strip()]


def _proxy_port() -> int:
    try:
        return int(os.environ.get("BREACH_PROXY_PORT", "8888"))
    except ValueError:
        return 8888


def _is_allowed(host: str, allow_hosts: list[str]) -> bool:
    host = host.lower()
    for allowed in allow_hosts:
        if host == allowed or host.endswith("." + allowed):
            return True
    return False


def _log(host: str, port: int, allowed: bool, line: str) -> None:
    """Append one JSONL record to the sink. Must never raise."""
    record = {"host": host, "port": port, "allowed": bool(allowed), "line": line}
    try:
        with _SINK_LOCK:
            with open(SINK_PATH, "a") as f:
                f.write(json.dumps(record) + "\n")
                f.flush()
                os.fsync(f.fileno())
    except OSError as e:
        # The sink is a bind-mounted file; if that mount is somehow missing,
        # fall back to stderr rather than crashing the proxy.
        sys.stderr.write(f"[egress_proxy] could not write sink: {e}\n")


def _pump(src: socket.socket, dst: socket.socket) -> None:
    """Copy bytes from src to dst until either side closes."""
    try:
        while True:
            chunk = src.recv(65536)
            if not chunk:
                break
            dst.sendall(chunk)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


class Handler(socketserver.BaseRequestHandler):
    """One handler instance per accepted connection, run in its own thread."""

    def handle(self) -> None:
        try:
            self._handle()
        except Exception as e:  # a bad/odd client must never take the proxy down
            sys.stderr.write(f"[egress_proxy] connection error: {e}\n")

    def _handle(self) -> None:
        self.request.settimeout(15)
        try:
            first_line = self._read_line()
        except OSError:
            return
        if not first_line:
            return
        parts = first_line.split()
        if len(parts) < 2:
            return
        method = parts[0].upper()

        if method == "CONNECT":
            self._handle_connect(parts[1])
        else:
            # A plain (non-tunnelled) HTTP request. Drain the rest of the
            # headers looking for Host, log it, and refuse -- this proxy only
            # forwards CONNECT tunnels.
            host = self._read_host_header() or "unknown"
            _log(host, 80, False, f"{method} {parts[1]} -> refused (non-CONNECT)")
            try:
                self.request.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n")
            except OSError:
                pass

    def _read_line(self) -> str:
        buf = b""
        while not buf.endswith(b"\r\n"):
            b = self.request.recv(1)
            if not b:
                break
            buf += b
        return buf.decode(errors="replace").strip()

    def _read_host_header(self) -> str | None:
        host = None
        while True:
            line = self._read_line()
            if not line:
                break
            if line.lower().startswith("host:"):
                host = line.split(":", 1)[1].strip()
        return host

    def _handle_connect(self, target: str) -> None:
        # target is "host:port"
        if ":" in target:
            host, _, port_s = target.rpartition(":")
        else:
            host, port_s = target, "443"
        try:
            port = int(port_s)
        except ValueError:
            port = 443
        host = host.strip().strip("[]")

        # Drain the remaining CONNECT request headers (we don't need them).
        while True:
            line = self._read_line()
            if not line:
                break

        allow_hosts = _allow_hosts()
        allowed = _is_allowed(host, allow_hosts)

        if not allowed:
            _log(host, port, False, f"CONNECT {host}:{port} -> refused")
            try:
                self.request.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n")
            except OSError:
                pass
            return

        _log(host, port, True, f"CONNECT {host}:{port} -> allowed")
        try:
            upstream = socket.create_connection((host, port), timeout=15)
        except OSError as e:
            try:
                self.request.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            except OSError:
                pass
            sys.stderr.write(f"[egress_proxy] upstream connect failed for {host}:{port}: {e}\n")
            return

        try:
            self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        except OSError:
            upstream.close()
            return

        # Tunnel established: pump bytes both directions until either closes.
        self.request.settimeout(None)
        upstream.settimeout(None)
        t = threading.Thread(target=_pump, args=(upstream, self.request), daemon=True)
        t.start()
        _pump(self.request, upstream)
        t.join(timeout=5)
        upstream.close()


class ThreadingTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True


def main() -> None:
    port = _proxy_port()
    # Touch the sink up front so a run with zero connection attempts still
    # produces an (empty) file for read_sink to find.
    try:
        open(SINK_PATH, "a").close()
    except OSError as e:
        sys.stderr.write(f"[egress_proxy] could not create sink at startup: {e}\n")

    server = ThreadingTCPServer(("0.0.0.0", port), Handler)
    sys.stderr.write(
        f"[egress_proxy] listening on 0.0.0.0:{port}, "
        f"allow_hosts={_allow_hosts()} at {time.strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
