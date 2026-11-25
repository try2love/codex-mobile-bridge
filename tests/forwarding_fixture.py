"""A future API on the real HTTP listener, with ordinary gateway authorization."""
from urllib.parse import urlsplit

from bridge.api.httpd import Handler


class FutureHandler(Handler):
    def harness_method(self):
        if urlsplit(self.path).path == '/api/future-fixture':
            self.handle_method(self.command not in ('GET', 'HEAD'))
        else:
            super().harness_method()

    def _handle_method(self, write):
        if urlsplit(self.path).path != '/api/future-fixture':
            return super()._handle_method(write)
        self.check_request(write)
        if not self.authorized(write):
            return
        if self.command == 'HEAD':
            self.send_response(200)
            self.headers_common()
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', '32')
            self.end_headers()
            return
        self.output(200, {'method': self.command, 'body': self.read_json() if write else {}})
