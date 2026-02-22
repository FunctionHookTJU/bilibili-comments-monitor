"""
Bilibili 双视频评论监控后端
访问 http://localhost:5000 查看前端页面
"""

import http.server
import json
import time
import urllib.request
import os
import csv
import threading
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer

# GMT+8 时区
CST = timezone(timedelta(hours=8))
LOG_FILE = os.path.join(os.path.dirname(__file__), "comment_log.csv")
LOG_INTERVAL_MINUTES = {0, 20, 40}  # 每小时的第 0、20、40 分钟记录

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


def now_cst() -> datetime:
    return datetime.now(CST)


def write_log(rows: list[dict]):
    """将一组记录追加写入 CSV 日志"""
    file_exists = os.path.isfile(LOG_FILE)
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["time_cst", "bvid", "title", "reply"])
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)


def read_log() -> list[dict]:
    """读取全部日志记录"""
    if not os.path.isfile(LOG_FILE):
        return []
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def calc_avg_speed(bvid: str) -> str:
    """从日志计算该视频的平均评论增长速度（条/分钟）"""
    records = [r for r in read_log() if r["bvid"] == bvid]
    if len(records) < 2:
        return "数据不足"
    first, last = records[0], records[-1]
    try:
        t0 = datetime.fromisoformat(first["time_cst"])
        t1 = datetime.fromisoformat(last["time_cst"])
        r0, r1 = int(first["reply"]), int(last["reply"])
        dt_min = (t1 - t0).total_seconds() / 60
        if dt_min <= 0:
            return "数据不足"
        speed = (r1 - r0) / dt_min
        return f"{speed:.2f} 条/分钟（{speed * 60:.0f} 条/小时）"
    except Exception:
        return "计算失败"


def calc_avg_speed_json(bvid: str) -> dict:
    """返回平均速率的结构化数据，供前端展示"""
    records = [r for r in read_log() if r["bvid"] == bvid]
    if len(records) < 2:
        return {"bvid": bvid, "ok": False, "msg": "日志数据不足（需等待至少两个记录点）"}
    first, last = records[0], records[-1]
    try:
        t0 = datetime.fromisoformat(first["time_cst"])
        t1 = datetime.fromisoformat(last["time_cst"])
        r0, r1 = int(first["reply"]), int(last["reply"])
        dt_min = (t1 - t0).total_seconds() / 60
        if dt_min <= 0:
            return {"bvid": bvid, "ok": False, "msg": "时间跨度为零"}
        speed_min = (r1 - r0) / dt_min
        return {
            "bvid":       bvid,
            "ok":         True,
            "per_min":    round(speed_min, 4),
            "per_hour":   round(speed_min * 60, 1),
            "log_count":  len(records),
            "first_time": first["time_cst"],
            "last_time":  last["time_cst"],
            "delta_reply": r1 - r0,
            "delta_min":  round(dt_min, 1),
        }
    except Exception as e:
        return {"bvid": bvid, "ok": False, "msg": str(e)}


def logger_thread():
    """后台线程：在每整 20 分钟时抓取并记录评论数"""
    logged_key = None  # 防止同一分钟重复记录
    while True:
        t = now_cst()
        key = (t.hour, t.minute)
        if t.minute in LOG_INTERVAL_MINUTES and key != logged_key:
            logged_key = key
            import threading as _t
            results = [None] * len(VIDEOS)

            def _worker(i, bvid):
                results[i] = fetch_video(bvid)

            threads = [_t.Thread(target=_worker, args=(i, bv)) for i, bv in enumerate(VIDEOS)]
            for th in threads: th.start()
            for th in threads: th.join()

            rows = []
            for r in results:
                if r and not r.get("error"):
                    rows.append({
                        "time_cst": now_cst().strftime("%Y-%m-%d %H:%M:%S"),
                        "bvid":     r["bvid"],
                        "title":    r.get("title", ""),
                        "reply":    r["reply"],
                    })
            if rows:
                write_log(rows)
                ts = now_cst().strftime("%H:%M:%S")
                print(f"\n[{ts} CST] 评论日志已记录")
                for row in rows:
                    avg = calc_avg_speed(row["bvid"])
                    print(f"  {row['bvid']}  reply={row['reply']:,}  均速={avg}")
        time.sleep(30)  # 每 30 秒检查一次，避免 CPU 空转


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
        elif self.path == "/api/avg_speed":
            self._serve_avg_speed()
        elif self.path == "/" or self.path == "/index.html":
            self._serve_file("index.html", "text/html; charset=utf-8")
        else:
            self.send_response(404)
            self.end_headers()

    def _serve_avg_speed(self):
        """返回所有视频的日志均速数据"""
        result = [calc_avg_speed_json(bvid) for bvid in VIDEOS]
        body = json.dumps(result, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError):
            pass

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

    # 启动日志后台线程
    t = threading.Thread(target=logger_thread, daemon=True)
    t.start()

    print(f"服务器已启动: http://localhost:{PORT}")
    for bv in VIDEOS:
        print(f"  监控: https://www.bilibili.com/video/{bv}/")
    print(f"  日志文件: {LOG_FILE}")
    print(f"  每整 20 分钟（:00 / :20 / :40 CST）自动记录评论数")
    print("按 Ctrl+C 停止\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务器已停止")
