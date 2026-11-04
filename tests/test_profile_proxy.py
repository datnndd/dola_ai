"""Real loopback network tests with fake upstreams; no provider keys or internet."""
import base64
from contextlib import contextmanager
import json
from pathlib import Path
import socket
import socketserver
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from browser_runtime import browser_runtime
from dola_browser import launch_context
from proxy_bridge import ProxyBridge, _headers, _recv_exact
from seedance_browser import proxy_account


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class HTTPUpstream(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            headers = _headers(self.rfile)
            self.server.requests.append(headers)
            auth = b'Proxy-Authorization: Basic ' + base64.b64encode(b'test-user:test-pass') + b'\r\n'
            if auth not in headers:
                self.wfile.write(b'HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 0\r\n\r\n')
                return
            if headers[0].startswith(b'CONNECT '):
                self.wfile.write(b'HTTP/1.1 200 Connection Established\r\n\r\n'); self.wfile.flush()
                self.wfile.write(self.rfile.read(4)); self.wfile.flush()
            else:
                body = json.dumps({'ip': self.server.exit_ip}).encode()
                self.wfile.write(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: '
                                 + str(len(body)).encode() + b'\r\nConnection: close\r\n\r\n' + body)
        except (OSError, ValueError):
            pass


class SOCKSUpstream(socketserver.StreamRequestHandler):
    rbufsize = 0
    def handle(self):
        try:
            sock = self.connection
            version, size = _recv_exact(sock, 2)
            methods = _recv_exact(sock, size)
            if version != 5 or 2 not in methods:
                return
            sock.sendall(b'\x05\x02')
            version, length = _recv_exact(sock, 2)
            user = _recv_exact(sock, length)
            length = _recv_exact(sock, 1)[0]
            password = _recv_exact(sock, length)
            if version != 1 or user != b'test-user' or password != b'test-pass':
                sock.sendall(b'\x01\x01'); return
            sock.sendall(b'\x01\x00')
            version, command, reserved, kind = _recv_exact(sock, 4)
            length = {1:4, 4:16}.get(kind)
            if kind == 3:
                length = _recv_exact(sock, 1)[0]
            host = _recv_exact(sock, length)
            port = int.from_bytes(_recv_exact(sock, 2), 'big')
            self.server.targets.append((host, port))
            sock.sendall(b'\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00')
            data = _recv_exact(sock, 4)
            if data == b'GET ':
                headers = _headers(self.rfile)
                self.server.requests.append([data + headers[0]] + headers[1:])
                body = b'socks-ok'
                sock.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 8\r\nConnection: close\r\n\r\n' + body)
            else:
                sock.sendall(data)
        except (OSError, ValueError, TypeError):
            pass


@contextmanager
def upstream(handler, ip='203.0.113.10'):
    server = Server(('127.0.0.1', 0), handler)
    server.requests = []; server.targets = []; server.exit_ip = ip
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        yield server
    finally:
        server.shutdown(); server.server_close(); thread.join(2)


def spec(server, scheme='http'):
    return {'server': f'{scheme}://127.0.0.1:{server.server_address[1]}',
            'username': 'test-user', 'password': 'test-pass'}


def connection(bridge):
    parsed = urlsplit(bridge.url)
    return socket.create_connection((parsed.hostname, parsed.port), timeout=3)


class ProfileProxyTests(unittest.TestCase):
    def test_http_and_socks_tunnels_with_auth(self):
        for handler, scheme in [(HTTPUpstream,'http'),(SOCKSUpstream,'socks5')]:
            with self.subTest(scheme=scheme), upstream(handler) as server, ProxyBridge(spec(server,scheme)) as bridge:
                with connection(bridge) as sock:
                    sock.sendall(b'CONNECT remote.invalid:443 HTTP/1.1\r\nHost: remote.invalid:443\r\n\r\n')
                    with sock.makefile('rb', buffering=0) as stream:
                        self.assertIn(b'200', _headers(stream)[0])
                        sock.sendall(b'ping')
                        self.assertEqual(_recv_exact(sock,4), b'ping')
                if scheme == 'socks5':
                    self.assertIn((b'remote.invalid',443),server.targets)

    def test_socks_plain_http_uses_origin_path_without_proxy_credentials(self):
        with upstream(SOCKSUpstream) as server, ProxyBridge(spec(server,'socks5')) as bridge:
            with connection(bridge) as sock:
                sock.sendall(b'GET http://remote.invalid/path?q=1 HTTP/1.1\r\nHost: remote.invalid\r\n\r\n')
                with sock.makefile('rb',buffering=0) as stream:
                    self.assertIn(b'200',_headers(stream)[0])
                    self.assertEqual(stream.read(8),b'socks-ok')
            self.assertEqual(server.requests[0][0],b'GET /path?q=1 HTTP/1.1\r\n')
            self.assertFalse(any(h.lower().startswith(b'proxy-authorization:') for h in server.requests[0]))

    def test_profile_browser_routes_through_bridge_and_new_upstream_on_reopen(self):
        account = dict(id='local',proxy_mode='manual')
        settings = dict(use_gpm=False,use_3big_browser=True,load_extension=False)
        with upstream(HTTPUpstream,'203.0.113.10') as first, upstream(HTTPUpstream,'203.0.113.20') as second, \
             tempfile.TemporaryDirectory() as folder:
            account['profile'] = str(Path(folder)/'profile')
            for server in (first,second):
                current = dict(account,proxy=spec(server))
                runtime_account, lease = proxy_account(settings,current,None,None,unique=False)
                self.assertEqual(set(runtime_account['proxy']),{'server'})
                address=urlsplit(runtime_account['proxy']['server'])
                self.assertEqual(address.hostname,'127.0.0.1')
                self.assertNotEqual(address.port,server.server_address[1])
                lease.close()
                with browser_runtime(settings) as runtime:
                    context=launch_context(runtime,settings,current,visible=False)
                    try:
                        page=context.pages[0] if context.pages else context.new_page()
                        page.goto('http://proxy-check.invalid/ip',wait_until='domcontentloaded')
                        payload=json.loads(page.locator('body').inner_text())
                        self.assertEqual(payload['ip'],server.exit_ip)
                        endpoint=urlsplit(context._lease.bridge.url)
                    finally:
                        context.close()
                    with self.assertRaises(OSError):
                        socket.create_connection((endpoint.hostname,endpoint.port),timeout=.5)

    def test_bad_auth_fails_closed(self):
        with upstream(HTTPUpstream) as server:
            proxy=dict(spec(server),password='wrong')
            with ProxyBridge(proxy) as bridge, connection(bridge) as sock:
                sock.sendall(b'CONNECT remote.invalid:443 HTTP/1.1\r\nHost: remote.invalid:443\r\n\r\n')
                with sock.makefile('rb',buffering=0) as stream:
                    self.assertIn(b'502',_headers(stream)[0])

    def test_unstarted_bridge_close_and_failed_launch_cleanup(self):
        with upstream(HTTPUpstream) as server:
            bridge=ProxyBridge(spec(server)); bridge.close(); bridge.close()
            address=urlsplit(bridge.url)
            with self.assertRaises(OSError):
                socket.create_connection((address.hostname,address.port),timeout=.5)
            observed=[]
            def fail(runtime,settings,account,*args):
                observed.append(urlsplit(account['proxy']['server']))
                raise RuntimeError('launch failed')
            with patch('dola_browser._launch_context',side_effect=fail):
                with self.assertRaisesRegex(RuntimeError,'launch failed'):
                    launch_context(None,{'use_gpm':False},dict(id='local',proxy_mode='manual',proxy=spec(server)))
            with self.assertRaises(OSError):
                socket.create_connection((observed[0].hostname,observed[0].port),timeout=.5)


if __name__ == '__main__':
    unittest.main()
