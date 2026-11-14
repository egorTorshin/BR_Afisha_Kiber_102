"""Local-only Windows TLS relay for the fixed GKZ afisha page."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from subprocess import run
from tempfile import NamedTemporaryFile

SOURCE = "https://gkzbashkortostan.ru/afisha/"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/afisha":
            self.send_error(404)
            return
        with NamedTemporaryFile(suffix=".html", delete=False) as output:
            path = Path(output.name)
        try:
            result = run(["curl.exe", "--noproxy", "*", "--fail", "--location",
                          "--silent", "--show-error", "--max-time", "25",
                          "--output", str(path), SOURCE], capture_output=True, timeout=30)
            if result.returncode or path.stat().st_size > 2_000_000:
                self.send_error(502)
                return
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 18082), Handler).serve_forever()
