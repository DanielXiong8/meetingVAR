"""Browser-only UI preview with synthetic data. No engine, credentials or devices.

Run on macOS or Windows: python3 desktop/preview_ui.py
The server binds to loopback and does not call or import the desktop backend.
"""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import argparse
from urllib.parse import urlsplit

BASE = Path(__file__).resolve().parent
WEB = BASE / "web"


class PreviewHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB), **kwargs)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            source = (WEB / "index.html").read_text(encoding="utf-8")
            source = source.replace('<script src="app.js"></script>',
                                    '<script src="/__preview/mock.js"></script>\n<script src="app.js"></script>')
            self.send_preview(source.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/__preview/mock.js":
            self.send_preview((BASE / "preview" / "mock.js").read_bytes(), "text/javascript; charset=utf-8")
        else:
            super().do_GET()

    def send_preview(self, data, content_type):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), PreviewHandler)
    print(f"Synthetic UI preview: http://127.0.0.1:{args.port} (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
