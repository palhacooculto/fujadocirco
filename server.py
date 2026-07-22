"""Simple HTTP server with Range request support for video seeking."""
import http.server
import os
import mimetypes

class RangeHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def send_head(self):
        path = self.translate_path(self.path)
        if os.path.isdir(path):
            return super().send_head()

        try:
            f = open(path, 'rb')
        except OSError:
            self.send_error(404, "File not found")
            return None

        ctype = self.guess_type(path)
        fs = os.fstat(f.fileno())
        file_size = fs.st_size

        range_header = self.headers.get('Range')
        if range_header:
            try:
                range_spec = range_header.strip().split('=')[1]
                byte_range = range_spec.split('-')
                start = int(byte_range[0]) if byte_range[0] else 0
                end = int(byte_range[1]) if byte_range[1] else file_size - 1
                end = min(end, file_size - 1)
                length = end - start + 1

                self.send_response(206)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                self.send_header("Content-Length", str(length))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()

                f.seek(start)
                # Return a wrapper that limits reads
                return _RangeFile(f, length)
            except Exception:
                f.close()
                self.send_error(416, "Range Not Satisfiable")
                return None
        else:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(file_size))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            return f

class _RangeFile:
    """Wraps a file to limit how many bytes are read."""
    def __init__(self, f, length):
        self.f = f
        self.remaining = length

    def read(self, n=-1):
        if self.remaining <= 0:
            return b''
        if n < 0 or n > self.remaining:
            n = self.remaining
        data = self.f.read(n)
        self.remaining -= len(data)
        return data

    def close(self):
        self.f.close()

if __name__ == '__main__':
    PORT = 8000
    with http.server.HTTPServer(("", PORT), RangeHTTPRequestHandler) as httpd:
        print(f"Serving on http://localhost:{PORT} with Range support")
        httpd.serve_forever()
