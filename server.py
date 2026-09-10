"""Simple HTTP server with Range request support for video seeking."""
import http.server
import html
import json
import mimetypes
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor


YOUTUBE_ITEMS = (
    {"handle": "ofabex", "videoId": "yWGRD-5bRKg"},
    {"handle": "kkpsyy", "videoId": "eWnEv-hed3g"},
    {"handle": "sukivmpie", "videoId": "3JzzWgvQwk8"},
    {"handle": "EsqueletoIA", "videoId": "I8PLa7tm2lM"},
    {"handle": "VecinaA", "videoId": "X5IEasuKIzM"},
)
YOUTUBE_CACHE_SECONDS = 300
YOUTUBE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36"
)
_youtube_cache = {"expires": 0, "payload": None}
_youtube_cache_lock = threading.Lock()


def _fetch_text(url):
    request = urllib.request.Request(url, headers={
        "User-Agent": YOUTUBE_USER_AGENT,
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    })
    with urllib.request.urlopen(request, timeout=15) as response:
        return response.read().decode("utf-8", errors="replace")


def _fetch_json(url):
    return json.loads(_fetch_text(url))


def _match(pattern, source):
    result = re.search(pattern, source, re.IGNORECASE)
    return result.group(1) if result else None


def _decode_youtube_text(value):
    if not value:
        return None
    try:
        return html.unescape(json.loads('"' + value + '"'))
    except (json.JSONDecodeError, TypeError):
        return html.unescape(value.replace("\\u0026", "&"))


def _format_count(number, singular, plural):
    number = int(number)
    if number >= 1_000_000:
        value, suffix = number / 1_000_000, "mi"
    elif number >= 1_000:
        value, suffix = number / 1_000, "mil"
    else:
        return f"{number} {singular if number == 1 else plural}"
    digits = 0 if value >= 100 else (1 if value >= 10 else 2)
    compact = f"{value:.{digits}f}".rstrip("0").rstrip(".").replace(".", ",")
    return f"{compact} {suffix} {plural}"


def _scrape_channel(handle):
    page = _fetch_text(f"https://www.youtube.com/@{handle}/about?hl=pt-BR")
    name = html.unescape(_match(r'<meta property="og:title" content="([^"]+)"', page) or handle)
    avatar = _decode_youtube_text(_match(r'<link rel="image_src" href="([^"]+)"', page))
    subscribers = _decode_youtube_text(_match(r'"subscriberCountText":"([^"]+)"', page))
    channel_id = _match(r'"externalId":"([^"]+)"', page)
    return {
        "name": name,
        "avatar": avatar,
        "subscriberText": subscribers,
        "channelId": channel_id,
    }


def _scrape_video(video_id):
    page = _fetch_text(f"https://www.youtube.com/watch?v={video_id}&hl=pt-BR")
    raw_views = _match(r'"viewCount":"(\d+)"', page)
    title = html.unescape(_match(r'<meta property="og:title" content="([^"]+)"', page) or "")
    views = int(raw_views) if raw_views else None
    return {
        "title": title,
        "viewCount": views,
        "viewText": _format_count(views, "visualização", "visualizações") if views is not None else None,
    }


def _load_stats_from_public_pages():
    def load_item(item):
        result = {"handle": item["handle"], "videoId": item["videoId"]}
        try:
            result["channel"] = _scrape_channel(item["handle"])
        except Exception:
            result["channel"] = {"name": item["handle"], "avatar": None, "subscriberText": None}
        try:
            result["video"] = _scrape_video(item["videoId"])
        except Exception:
            result["video"] = {"title": "", "viewCount": None, "viewText": None}
        return result

    with ThreadPoolExecutor(max_workers=len(YOUTUBE_ITEMS)) as executor:
        return list(executor.map(load_item, YOUTUBE_ITEMS))


def _load_stats_from_api(api_key):
    channels = {}
    for item in YOUTUBE_ITEMS:
        query = urllib.parse.urlencode({
            "part": "snippet,statistics",
            "forHandle": "@" + item["handle"],
            "key": api_key,
        })
        response = _fetch_json("https://www.googleapis.com/youtube/v3/channels?" + query)
        channel = response.get("items", [])[0]
        snippet, statistics = channel["snippet"], channel["statistics"]
        subscriber_count = None if statistics.get("hiddenSubscriberCount") else int(statistics["subscriberCount"])
        thumbnails = snippet.get("thumbnails", {})
        avatar = (thumbnails.get("high") or thumbnails.get("medium") or thumbnails.get("default") or {}).get("url")
        channels[item["handle"]] = {
            "name": snippet["title"],
            "avatar": avatar,
            "subscriberCount": subscriber_count,
            "subscriberText": _format_count(subscriber_count, "inscrito", "inscritos") if subscriber_count is not None else None,
            "channelId": channel["id"],
        }

    video_ids = ",".join(item["videoId"] for item in YOUTUBE_ITEMS)
    video_query = urllib.parse.urlencode({"part": "snippet,statistics", "id": video_ids, "key": api_key})
    video_response = _fetch_json("https://www.googleapis.com/youtube/v3/videos?" + video_query)
    videos = {}
    for video in video_response.get("items", []):
        count = int(video["statistics"]["viewCount"])
        videos[video["id"]] = {
            "title": video["snippet"]["title"],
            "viewCount": count,
            "viewText": _format_count(count, "visualização", "visualizações"),
        }

    return [{
        "handle": item["handle"],
        "videoId": item["videoId"],
        "channel": channels[item["handle"]],
        "video": videos.get(item["videoId"], {"title": "", "viewCount": None, "viewText": None}),
    } for item in YOUTUBE_ITEMS]


def get_youtube_stats():
    now = time.time()
    with _youtube_cache_lock:
        if _youtube_cache["payload"] and _youtube_cache["expires"] > now:
            return _youtube_cache["payload"]

    api_key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    try:
        items = _load_stats_from_api(api_key) if api_key else _load_stats_from_public_pages()
        source = "youtube-data-api" if api_key else "youtube-public"
    except Exception:
        items = _load_stats_from_public_pages()
        source = "youtube-public"

    payload = {
        "items": items,
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": source,
    }
    with _youtube_cache_lock:
        _youtube_cache["payload"] = payload
        _youtube_cache["expires"] = now + YOUTUBE_CACHE_SECONDS
    return payload

class RangeHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        super().end_headers()

    def do_GET(self):
        if urllib.parse.urlsplit(self.path).path == "/api/youtube-stats":
            body = json.dumps(get_youtube_stats(), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

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
    with http.server.ThreadingHTTPServer(("", PORT), RangeHTTPRequestHandler) as httpd:
        print(f"Serving on http://localhost:{PORT} with Range support and YouTube stats API")
        httpd.serve_forever()
