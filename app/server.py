"""Secure response helpers for the loopback preview, without a legacy launcher."""
import hmac
import json
from http.server import BaseHTTPRequestHandler


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Do not log user queries, credentials or response bodies.

    def safe_host(self):
        return self.headers.get('Host') == self.server.authority

    def authenticated(self):
        supplied = self.headers.get('X-V5-Session', '')
        return hmac.compare_digest(supplied, self.server.session)

    def send(self, status, body, content_type='application/json; charset=utf-8'):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        for key, value in {
            'Content-Type': content_type, 'Content-Length': str(len(body)),
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; "
                "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'",
        }.items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass
