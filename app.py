"""
Bilibili 评论监控后端
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
from http.server import ThreadingHTTPServer

CST = timezone(timedelta(hours=8))
LOG_FILE = os.path.join(os.path.dirname(__file__), "comment_log.csv")
LOG_INTERVAL_MINUTES = {0, 20, 40}
N_RECENT = 3

VIDEOS = [
    "BV1fy4y1L7Rq",
    "BV1MPdBB8EEN",
]

VIEW_BVS = {"BV1MPdBB8EEN"}

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
    file_exists = os.path.isfile(LOG_FILE)
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["time_cst", "bvid", "title", "reply", "view"])
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)


def read_log() -> list[dict]:
    if not os.path.isfile(LOG_FILE):
        return []
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def fetch_video(bvid: str) -> dict:
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


def fetch_videos_parallel() -> list[dict]:
    results = [None] * len(VIDEOS)

    def worker(i, bvid):
        results[i] = fetch_video(bvid)

    threads = [threading.Thread(target=worker, args=(i, bv)) for i, bv in enumerate(VIDEOS)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=15)
    return results


def calc_avg_speed_json(bvid: str, current_val: int, current_time: datetime, field: str = "reply") -> dict:
    records = [r for r in read_log() if r["bvid"] == bvid]
    if not records:
        return {"bvid": bvid, "ok": False, "msg": "日志数据不足（需等待至少一个日志记录点）"}
    baseline = records[-min(N_RECENT, len(records))]
    try:
        t0 = datetime.fromisoformat(baseline["time_cst"]).replace(tzinfo=CST)
        r0 = int(baseline.get(field) or 0)
        dt_min = (current_time - t0).total_seconds() / 60
        if dt_min <= 0:
            return {"bvid": bvid, "ok": False, "msg": "时间跨度为零"}
        speed_min = (current_val - r0) / dt_min
        window = min(N_RECENT, len(records))
        return {
            "bvid":        bvid,
            "ok":          True,
            "per_min":     round(speed_min, 4),
            "per_hour":    round(speed_min * 60, 1),
            "log_count":   len(records),
            "window":      window,
            "from_time":   baseline["time_cst"],
            "delta_reply": current_val - r0,
            "delta_min":   round(dt_min, 1),
        }
    except Exception as e:
        return {"bvid": bvid, "ok": False, "msg": str(e)}


def logger_thread():
    logged_key = None
    while True:
        try:
            t = now_cst()
            key = (t.hour, t.minute)
            if t.minute in LOG_INTERVAL_MINUTES and key != logged_key:
                logged_key = key
                results = fetch_videos_parallel()
                rows = []
                for r in results:
                    if r and not r.get("error"):
                        rows.append({
                            "time_cst": now_cst().strftime("%Y-%m-%d %H:%M:%S"),
                            "bvid":     r["bvid"],
                            "title":    r.get("title", ""),
                            "reply":    r["reply"],
                            "view":     r.get("view", 0),
                        })
                if rows:
                    write_log(rows)
                    ts = now_cst().strftime("%H:%M:%S")
                    print(f"\n[{ts} CST] 评论日志已记录")
                    now = now_cst()
                    for row in rows:
                        is_view = row["bvid"] in VIEW_BVS
                        field = "view" if is_view else "reply"
                        val = row[field]
                        avg = calc_avg_speed_json(row["bvid"], val, now, field)
                        unit = "次" if is_view else "条"
                        if avg.get("ok"):
                            print(f"  {row['bvid']}  {field}={val:,}  "
                                  f"近期均速={avg['per_min']:.2f} {unit}/分钟（{avg['per_hour']:.0f} {unit}/小时）")
                        else:
                            print(f"  {row['bvid']}  {field}={val:,}  近期均速={avg.get('msg', '未知')}")
        except Exception as e:
            print(f"[logger] 异常: {e}")
        time.sleep(30)


def _send_json(handler, data):
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Content-Length", len(body))
    handler.end_headers()
    try:
        handler.wfile.write(body)
    except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError):
        pass


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == "/api/all":
            _send_json(self, fetch_videos_parallel())
        elif self.path == "/api/avg_speed":
            self._serve_avg_speed()
        elif self.path in ("/", "/index.html"):
            self._serve_file("index.html", "text/html; charset=utf-8")
        else:
            self.send_response(404)
            self.end_headers()

    def _serve_avg_speed(self):
        live = fetch_videos_parallel()
        now = now_cst()
        result = []
        for item in live:
            if item is None or item.get("error"):
                bvid = item["bvid"] if item else "unknown"
                result.append({"bvid": bvid, "ok": False, "msg": "实时数据获取失败"})
            else:
                is_view = item["bvid"] in VIEW_BVS
                field = "view" if is_view else "reply"
                result.append(calc_avg_speed_json(item["bvid"], item[field], now, field))
        _send_json(self, result)

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
                pass
        except FileNotFoundError:
            self.send_response(404)
            self.end_headers()


if __name__ == "__main__":
    PORT = 80
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)

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
        server.shutdown()
        print("\n服务器已停止")
