"""Loopback HTTP proxy that authenticates only to the configured upstream.

CONNECT tunnels remain encrypted end-to-end; no certificates or extensions.
Credentials stay in memory, never in Chrome arguments or response bodies.
"""
import base64
import ipaddress
from contextlib import contextmanager
import select
import socket
import socketserver
import ssl
import threading
from urllib.parse import urlsplit


def _recv_exact(sock, size):
    result = bytearray()
    while len(result) < size:
        chunk = sock.recv(size - len(result))
        if not chunk:
            raise OSError('Proxy closed the connection')
        result.extend(chunk)
    return bytes(result)


def _socks_connect(sock, host, port, username, password):
    """SOCKS5 remote DNS and optional RFC 1929 authentication."""
    auth = bool(username or password)
    methods = b'\x00\x02' if auth else b'\x00'
    sock.sendall(b'\x05' + bytes([len(methods)]) + methods)
    reply = _recv_exact(sock, 2)
    if reply[0] != 5 or reply[1] == 255:
        raise OSError('SOCKS5 rejected authentication methods')
    if reply[1] == 2:
        user, secret = username.encode('utf-8'), password.encode('utf-8')
        if not auth or len(user) > 255 or len(secret) > 255:
            raise OSError('Invalid SOCKS5 credentials')
        sock.sendall(b'\x01' + bytes([len(user)]) + user + bytes([len(secret)]) + secret)
        if _recv_exact(sock, 2) != b'\x01\x00':
            raise OSError('SOCKS5 authentication rejected')
    elif reply[1] != 0:
        raise OSError('Unsupported SOCKS5 authentication method')
    try:
        address = ipaddress.ip_address(host)
        target = (b'\x01' if address.version == 4 else b'\x04') + address.packed
    except ValueError:
        encoded = host.encode('idna')
        if not 0 < len(encoded) <= 255:
            raise OSError('Invalid SOCKS5 target')
        target = b'\x03' + bytes([len(encoded)]) + encoded
    sock.sendall(b'\x05\x01\x00' + target + int(port).to_bytes(2, 'big'))
    reply = _recv_exact(sock, 4)
    if reply[:3] != b'\x05\x00\x00':
        raise OSError('SOCKS5 rejected connection')
    size = {1: 4, 4: 16}.get(reply[3])
    if reply[3] == 3:
        size = _recv_exact(sock, 1)[0]
    if size is None:
        raise OSError('Invalid SOCKS5 reply')
    _recv_exact(sock, size + 2)


def _headers(stream):
    lines, size = [], 0
    while True:
        line = stream.readline(65537)
        size += len(line)
        if not line or size > 65536:
            raise OSError('Incomplete or oversized proxy headers')
        lines.append(line)
        if line == b'\r\n':
            return lines


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = False

    def handle_error(self, request, client_address):
        # Socket failures are expected during browser shutdown; never dump secrets.
        pass


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        upstream = None
        response_started = False
        bridge = self.server.bridge
        bridge.track(self.connection)
        try:
            self.connection.settimeout(20)
            request = _headers(self.rfile)
            method, target, version = request[0].decode('ascii').strip().split(' ', 2)
            if method != 'CONNECT' and urlsplit(target).scheme != 'http':
                raise OSError('Expected HTTP proxy request')
            upstream = socket.create_connection(bridge.address, timeout=20)
            if bridge.tls:
                upstream = ssl.create_default_context().wrap_socket(upstream, server_hostname=bridge.address[0])
            bridge.track(upstream)
            if bridge.socks:
                destination = urlsplit('http://' + target) if method == 'CONNECT' else urlsplit(target)
                if not destination.hostname:
                    raise OSError('Missing proxy target')
                _socks_connect(upstream, destination.hostname,
                               destination.port or (443 if method == 'CONNECT' else 80),
                               bridge.username, bridge.password)
                if method == 'CONNECT':
                    self.connection.sendall(b'HTTP/1.1 200 Connection Established\r\n\r\n')
                    response_started = True
                else:
                    path = destination.path or '/'
                    if destination.query:
                        path += '?' + destination.query
                    request[0] = f'{method} {path} {version}\r\n'.encode('ascii')
            headers = [request[0]]
            for line in request[1:-1]:
                key = line.split(b':', 1)[0].lower()
                if key not in (b'proxy-authorization', b'proxy-connection', b'connection'):
                    headers.append(line)
            if bridge.authorization and not bridge.socks:
                headers.append(b'Proxy-Authorization: Basic ' + bridge.authorization + b'\r\n')
            if method != 'CONNECT':
                headers.append(b'Connection: close\r\n')
                if not bridge.socks:
                    headers.append(b'Proxy-Connection: close\r\n')
            if not (bridge.socks and method == 'CONNECT'):
                upstream.sendall(b''.join(headers) + b'\r\n')
            if method == 'CONNECT' and not bridge.socks:
                # Unbuffered stream ensures bytes after the headers stay on the socket.
                with upstream.makefile('rb', buffering=0) as source:
                    reply = _headers(source)
                status = int(reply[0].split()[1])
                if status != 200:
                    bridge.failure('Proxy từ chối user/pass đã lưu.' if status == 407 else f'Proxy trả HTTP {status}; chưa mở được kết nối.')
                    raise OSError('Upstream rejected tunnel')
                self.connection.sendall(b'HTTP/1.1 200 Connection Established\r\n\r\n')
                response_started = True
            elif method != 'CONNECT':
                # Plain HTTP uses a single request per connection; forward its body.
                # Read through rfile so buffered bytes are not lost.
                fields = {line.split(b':', 1)[0].lower(): line.split(b':', 1)[1].strip()
                          for line in request[1:-1] if b':' in line}
                if b'transfer-encoding' in fields:
                    raise OSError('Chunked plain HTTP requests are unsupported')
                remaining = int(fields.get(b'content-length', b'0'))
                while remaining:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        raise OSError('Incomplete request body')
                    upstream.sendall(chunk)
                    remaining -= len(chunk)
                with upstream.makefile('rb', buffering=0) as source:
                    reply = _headers(source)
                if int(reply[0].split()[1]) == 407:
                    bridge.failure('Proxy từ chối user/pass đã lưu.')
                    raise OSError('Upstream authentication rejected')
                self.connection.sendall(b''.join(reply))
                response_started = True
            sockets = [self.connection, upstream]
            while not bridge.stopped.is_set():
                pending = isinstance(upstream, ssl.SSLSocket) and upstream.pending()
                readable = [upstream] if pending else select.select(sockets, [], [], 1)[0]
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    (upstream if source is self.connection else self.connection).sendall(data)
        except (OSError, ValueError):
            if not response_started:
                try:
                    self.connection.sendall(b'HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
                except OSError:
                    pass
        finally:
            for sock in (upstream, self.connection):
                if sock is not None:
                    bridge.untrack(sock)
                    sock.close()


class ProxyBridge:
    def __init__(self, proxy, report=None):
        parsed = urlsplit(proxy['server'])
        if parsed.scheme not in ('http', 'https', 'socks5', 'socks5h') or not parsed.hostname:
            raise ValueError('Proxy bridge cần proxy HTTP/HTTPS hoặc SOCKS5 hợp lệ.')
        self.address = (parsed.hostname, parsed.port or {'http': 80, 'https': 443}.get(parsed.scheme, 1080))
        self.tls = parsed.scheme == 'https'
        self.socks = parsed.scheme in ('socks5', 'socks5h')
        self.username, self.password = proxy.get('username', ''), proxy.get('password', '')
        self.authorization = (base64.b64encode((self.username + ':' + self.password).encode('utf-8'))
                              if self.username or self.password else b'')
        self.report = report or (lambda text: None)
        self.reported = set()
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        self.sockets = set()
        self.server = _Server(('127.0.0.1', 0), _Handler)
        self.server.bridge = self
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .1}, daemon=True)

    def failure(self, message):
        with self.lock:
            if message in self.reported:
                return
            self.reported.add(message)
        self.report(message)

    def track(self, sock):
        with self.lock:
            if self.stopped.is_set():
                sock.close()
                raise OSError('Proxy stopped')
            self.sockets.add(sock)

    def untrack(self, sock):
        with self.lock:
            self.sockets.discard(sock)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.close()

    def start(self):
        if self.thread.ident is None and not self.stopped.is_set():
            self.thread.start()

    def close(self):
        with self.lock:
            if self.stopped.is_set():
                return
            self.stopped.set()
        if self.thread.ident is not None:
            self.server.shutdown()
        self.server.server_close()
        with self.lock:
            sockets = list(self.sockets)
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        if self.thread.ident is not None:
            self.thread.join(timeout=2)


@contextmanager
def native_proxy(proxy, report):
    if proxy and (proxy.get('username') or proxy.get('password') or proxy['server'].startswith(('socks5:', 'socks5h:'))):
        with ProxyBridge(proxy, report) as bridge:
            report('Đã bật xác thực proxy tự động bằng user/pass đã lưu.')
            yield bridge.url
    else:
        yield proxy['server'] if proxy else ''
