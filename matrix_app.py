"""A CV matrix for the ES-8: chapters down the left, eight knobs across (one per ES-8 output). NO AI, no network.

Each row is a chapter: eight voltages (-5 to +5 V) for ES-8 outputs 1-8, how long the row lasts, and how much of
that time is spent gliding from the previous row. Rows play top to bottom (optionally looping), then the
outputs glide back to 0 V.

    python matrix_app.py              # opens http://127.0.0.1:8765 in the browser; the ES-8 is opened when you press Play
    python matrix_app.py --dry-run    # the page works, nothing is ever sent to the ES-8

The grid is kept in matrix_grid.json next to this file. A saved piece (pieces/*.json) can be loaded into outputs
1-4; the grid can be exported back as a piece (the extra outputs 5-8 are stored beside the levels).
"""

from __future__ import annotations

import argparse
import json
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).parent
GRID_FILE = HERE / "matrix_grid.json"
PIECES = HERE / "pieces"
PAGE = HERE / "matrix_app.html"
OUTPUTS = 8
MAX_V = 5.0                                   # the ES-8 class caps at +-5 V as well
VOICES = ["env1", "env2", "piano", "drone"]   # outputs 1-4 when a piece is loaded or exported


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def clean_row(r: dict) -> dict:
    t = clamp(float(r.get("time", 60)), 1.0, 3600.0)
    vals = [clamp(float(v), -MAX_V, MAX_V) for v in (list(r.get("values", [])) + [0.0] * OUTPUTS)[:OUTPUTS]]
    return {"name": str(r.get("name", ""))[:40], "time": t, "glide": clamp(float(r.get("glide", 10)), 0.0, t), "values": vals}


def default_rows() -> list[dict]:
    return [clean_row({"name": f"Chapter {i + 1}", "time": 60, "glide": 10}) for i in range(4)]


class Matrix:
    def __init__(self, dry_run: bool):
        self.dry_run = dry_run
        self.lock = threading.Lock()
        self.rows = default_rows()
        self.speed = 1.0                      # multiplies row times and glides (0.5 = twice as fast)
        self.loop = False
        self.es8 = None
        self.thread = None
        self.stop_ev = threading.Event()
        self.current = -1
        self.row_started = 0.0
        self.jump = None
        self.reapply = False
        self.message = ""
        if GRID_FILE.exists():
            try:
                saved = json.loads(GRID_FILE.read_text())
                self.rows = [clean_row(r) for r in saved["rows"]] or default_rows()
                self.speed = clamp(float(saved.get("speed", 1.0)), 0.05, 20.0)
                self.loop = bool(saved.get("loop", False))
            except (ValueError, KeyError, TypeError):
                self.message = "matrix_grid.json could not be read; started with a blank grid"

    # ---- state shared with the page
    def snapshot(self) -> dict:
        with self.lock:
            return {"rows": self.rows, "speed": self.speed, "loop": self.loop}

    def status(self) -> dict:
        volts = None
        if self.es8 is not None:
            try:
                volts = [round(self.es8.cv_volts(c), 2) for c in range(1, OUTPUTS + 1)]
            except Exception:
                volts = None
        playing = self.thread is not None and self.thread.is_alive()
        row = self.rows[self.current] if 0 <= self.current < len(self.rows) else None
        return {"playing": playing, "current": self.current if playing else -1, "dry_run": self.dry_run,
                "elapsed": round(time.monotonic() - self.row_started, 1) if playing else 0,
                "row_time": round(row["time"] * self.speed, 1) if row else 0, "volts": volts, "message": self.message}

    def set_grid(self, data: dict):
        rows = [clean_row(r) for r in data["rows"]] or default_rows()
        with self.lock:
            self.rows = rows
            self.speed = clamp(float(data.get("speed", 1.0)), 0.05, 20.0)
            self.loop = bool(data.get("loop", False))
            self.reapply = True               # a knob moved: the playing row follows it
        GRID_FILE.write_text(json.dumps(self.snapshot(), indent=1))

    # ---- playing
    def play(self, start: int):
        if self.thread is not None and self.thread.is_alive():
            with self.lock:
                self.jump = clamp(int(start), 0, len(self.rows) - 1)
            return
        self.stop_ev.clear()
        self.message = ""
        self.thread = threading.Thread(target=self._run, args=(clamp(int(start), 0, len(self.rows) - 1),), daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_ev.set()
        if self.thread is not None:
            self.thread.join(timeout=8)

    def _apply(self, row: dict, glide: float):
        if self.es8 is None:
            return
        targets = [clamp(v, -MAX_V, MAX_V) for v in row["values"]]
        dist = max(abs(t - self.es8.cv_volts(c + 1)) for c, t in enumerate(targets))
        self.es8.set_slew(clamp(dist / max(glide, 0.2), 0.05, 100.0) if dist > 0 else 1.0)
        for c, t in enumerate(targets):
            self.es8.set_cv(c + 1, t)

    def _run(self, i: int):
        try:
            if not self.dry_run:
                from es8 import ES8
                self.es8 = ES8(in_channels=[1], out_channel=0, level=0.0, cv_channels=tuple(range(1, OUTPUTS + 1)),
                               cv_slew=1.0)
            while not self.stop_ev.is_set():
                with self.lock:
                    if i >= len(self.rows):
                        if not self.loop:
                            break
                        i = 0
                    row = dict(self.rows[i])
                    speed = self.speed
                    self.jump = None
                    self.reapply = False
                self.current = i
                self.row_started = time.monotonic()
                self._apply(row, row["glide"] * speed)
                deadline = self.row_started + row["time"] * speed
                while time.monotonic() < deadline and not self.stop_ev.wait(0.1):
                    with self.lock:
                        jump, again = self.jump, self.reapply
                        self.reapply = False
                        live = dict(self.rows[min(i, len(self.rows) - 1)])
                    if jump is not None:
                        i = jump - 1
                        break
                    if again:
                        self._apply(live, 0.5)
                i += 1
        except BaseException as e:               # e.g. the ES-8 is not connected
            self.message = f"stopped: {e}"
        finally:
            if self.es8 is not None:
                self.es8.close()
                self.es8 = None
            self.current = -1

    # ---- pieces
    def pieces(self) -> list[str]:
        return sorted(p.name for p in PIECES.glob("*.json"))

    def load_piece(self, name: str):
        piece = json.loads((PIECES / Path(name).name).read_text())
        rows = []
        for c in piece["chapters"]:
            vals = [clamp(c["levels"].get(v, 0.0), 0.0, 1.0) * MAX_V for v in VOICES]
            vals += [float(x) for x in (c.get("out5_8") or [0, 0, 0, 0])][:4]
            rows.append(clean_row({"name": c.get("name", ""), "time": c["minutes"] * 60, "glide": c["ramp_seconds"], "values": vals}))
        with self.lock:
            self.rows = rows
        GRID_FILE.write_text(json.dumps(self.snapshot(), indent=1))

    def export_piece(self, title: str) -> str:
        slug = "".join(ch if ch.isalnum() else "-" for ch in title.lower()).strip("-") or "matrix"
        chapters = []
        with self.lock:
            rows = list(self.rows)
        for n, r in enumerate(rows, 1):
            chapters.append({"n": n, "name": r["name"], "minutes": round(r["time"] / 60, 3), "ramp_seconds": round(r["glide"], 1),
                             "levels": {v: round(clamp(r["values"][k], 0.0, MAX_V) / MAX_V, 3) for k, v in enumerate(VOICES)},
                             "out5_8": [round(x, 2) for x in r["values"][4:]]})
        piece = {"title": title, "saved": time.strftime("%Y-%m-%d"), "source": "matrix_app.py (hand-edited grid)",
                 "voices": VOICES, "note": "Outputs 1-4 are levels 0-1 (0-5 V); out5_8 are volts for ES-8 outputs 5-8 and are ignored by play_piece.py.",
                 "chapters": chapters}
        PIECES.mkdir(exist_ok=True)
        path = PIECES / f"{slug}.json"
        path.write_text(json.dumps(piece, indent=1))
        return path.name


def make_handler(m: Matrix):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, obj, code=200, ctype="application/json"):
            body = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self._send(PAGE.read_bytes(), ctype="text/html; charset=utf-8")
            elif self.path == "/api/grid":
                self._send({**m.snapshot(), "pieces": m.pieces()})
            elif self.path == "/api/status":
                self._send(m.status())
            else:
                self._send({"error": "not found"}, 404)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(n) or b"{}")
            try:
                if self.path == "/api/grid":
                    m.set_grid(data)
                    self._send({"ok": True})
                elif self.path == "/api/play":
                    m.play(data.get("row", 0))
                    self._send({"ok": True})
                elif self.path == "/api/stop":
                    m.stop()
                    self._send({"ok": True})
                elif self.path == "/api/load_piece":
                    m.load_piece(data["file"])
                    self._send({**m.snapshot(), "pieces": m.pieces()})
                elif self.path == "/api/export_piece":
                    self._send({"file": m.export_piece(data.get("title", "matrix"))})
                else:
                    self._send({"error": "not found"}, 404)
            except Exception as e:
                self._send({"error": str(e)}, 400)

    return Handler


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--dry-run", action="store_true", help="the page works but nothing is ever sent to the ES-8")
    p.add_argument("--no-browser", action="store_true")
    args = p.parse_args()
    m = Matrix(args.dry_run)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(m))   # this Mac only
    url = f"http://127.0.0.1:{args.port}"
    print(f"CV matrix at {url}" + ("  (dry run: nothing is sent)" if args.dry_run else "  (the ES-8 opens when you press Play)"))
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        m.stop()
        server.server_close()


if __name__ == "__main__":
    main()
