"""
Bilibili 双视频评论监控后端
访问 http://localhost:5000 查看前端页面
"""

import http.server
import json
import time
import urllib.request
import os
from http.server import HTTPServer

VIDEOS = [
    "BV1fy4y1L7Rq",
    "BV1HfKiz3Ezf",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
}


def fetch_video(bvid: str) -> dict:
    """从 Bilibili API 获取单个视频统计数据"""
    url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("code") == 0:
                stat = data["data"]["stat"]
                return {
                    "bvid":      bvid,
                    "title":     data["data"].get("title", ""),
                    "reply":     stat.get("reply", 0),
                    "danmaku":   stat.get("danmaku", 0),
                    "view":      stat.get("view", 0),
                    "like":      stat.get("like", 0),
                    "timestamp": time.time(),
                    "error":     None,
                }
            else:
                return {"bvid": bvid, "error": data.get("message", "未知错误")}
    except Exception as e:
        return {"bvid": bvid, "error": str(e)}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # 静默日志

    def do_GET(self):
        if self.path == "/api/all":
            self._serve_all()
        elif self.path == "/" or self.path == "/index.html":
            self._serve_file("index.html", "text/html; charset=utf-8")
        else:
            self.send_response(404)
            self.end_headers()

    def _serve_all(self):
        """并行获取所有视频数据"""
        import threading
        results = [None] * len(VIDEOS)

        def worker(i, bvid):
            results[i] = fetch_video(bvid)

        threads = [threading.Thread(target=worker, args=(i, bv)) for i, bv in enumerate(VIDEOS)]
        for t in threads: t.start()
        for t in threads: t.join()

        body = json.dumps(results, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError):
            pass

    def _serve_file(self, filename, content_type):
        filepath = os.path.join(os.path.dirname(__file__), filename)
        try:
            with open(filepath, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", len(body))
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError):
                pass  # 浏览器提前断开连接，忽略
        except FileNotFoundError:
            self.send_response(404)
            self.end_headers()


if __name__ == "__main__":
    PORT = 5000
    server = HTTPServer(("localhost", PORT), Handler)
    print(f"服务器已启动: http://localhost:{PORT}")
    for bv in VIDEOS:
        print(f"  监控: https://www.bilibili.com/video/{bv}/")
    print("按 Ctrl+C 停止\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务器已停止")
