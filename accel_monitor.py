#!/usr/bin/env python3
"""
NIED Kyoshin Monitor maximum acceleration viewer.

This app displays NIED's official realtime "maximum acceleration" image as-is.
It intentionally does not extract numeric values from pixel colors.
"""

from __future__ import annotations

import base64
import json
import platform
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from tkinter import ttk
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


KMONI_BASE = "http://www.kmoni.bosai.go.jp"
LATEST_URL = f"{KMONI_BASE}/webservice/server/pros/latest.json"
REFRESH_MS = 1000
USER_AGENT = "EEW_Python-KyoshinAccelerationViewer/1.0"
ALERT_COOLDOWN_SECONDS = 20
ALERT_WARM_PIXEL_THRESHOLD = 8


@dataclass
class AccelerationFrame:
    latest_time: str
    request_time: str
    image_bytes: bytes
    image_url: str


def fetch_bytes(url: str, timeout: int = 10) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_json(url: str, timeout: int = 10) -> Any:
    return json.loads(fetch_bytes(url, timeout=timeout).decode("utf-8"))


def parse_kmoni_time(text: str) -> datetime:
    return datetime.strptime(text, "%Y/%m/%d %H:%M:%S")


def acceleration_image_url(latest_time: str) -> str:
    value = parse_kmoni_time(latest_time)
    day = value.strftime("%Y%m%d")
    stamp = value.strftime("%Y%m%d%H%M%S")
    return f"{KMONI_BASE}/data/map_img/RealTimeImg/acmap_s/{day}/{stamp}.acmap_s.gif"


def load_acceleration_frame() -> AccelerationFrame:
    latest = fetch_json(LATEST_URL, timeout=10)
    latest_time = latest["latest_time"]
    request_time = latest.get("request_time", "")
    image_url = acceleration_image_url(latest_time)
    image_bytes = fetch_bytes(image_url, timeout=10)
    return AccelerationFrame(
        latest_time=latest_time,
        request_time=request_time,
        image_bytes=image_bytes,
        image_url=image_url,
    )


def beep(repeats: int = 3) -> None:
    system = platform.system()
    for _ in range(repeats):
        try:
            if system == "Darwin":
                subprocess.Popen(["afplay", "/System/Library/Sounds/Sosumi.aiff"])
            elif system == "Windows":
                import winsound

                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            else:
                sys.stdout.write("\a")
                sys.stdout.flush()
        except Exception:
            sys.stdout.write("\a")
            sys.stdout.flush()
        time.sleep(0.35)


class AccelerationMonitor(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("NIED 強震モニタ 最大加速度")
        self.geometry("430x560")
        self.minsize(410, 540)

        self.result_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.running = tk.BooleanVar(value=True)
        self.sound_enabled = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value="起動中")
        self.latest_time = tk.StringVar(value="-")
        self.request_time = tk.StringVar(value="-")
        self.image_url = tk.StringVar(value="-")
        self.photo: tk.PhotoImage | None = None
        self.refresh_after_id: str | None = None
        self.first_frame = True
        self.last_alert_at = 0.0

        self._build_ui()
        self.after(100, self.refresh)
        self.after(200, self._drain_queue)

    def _build_ui(self) -> None:
        self.configure(bg="#666666")

        header = ttk.Frame(self, padding=(14, 10))
        header.pack(fill="x")
        ttk.Label(header, text="最大加速度", font=("", 16, "bold")).pack(side="left")
        ttk.Checkbutton(header, text="警告音", variable=self.sound_enabled).pack(side="right", padx=(8, 0))
        ttk.Checkbutton(header, text="自動更新", variable=self.running, command=self._toggle_auto_refresh).pack(
            side="right", padx=(8, 0)
        )
        ttk.Button(header, text="更新", command=self.refresh).pack(side="right", padx=(8, 0))
        ttk.Button(header, text="音テスト", command=lambda: threading.Thread(target=beep, daemon=True).start()).pack(
            side="right", padx=(8, 0)
        )

        body = ttk.Frame(self, padding=(14, 0, 14, 10))
        body.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(body, bg="#666666", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _event: self._redraw_image())

        info = ttk.Frame(self, padding=(14, 0, 14, 12))
        info.pack(fill="x")

        ttk.Label(info, text="観測時刻").grid(row=0, column=0, sticky="w", padx=(0, 10))
        ttk.Label(info, textvariable=self.latest_time).grid(row=0, column=1, sticky="w")
        ttk.Label(info, text="取得時刻").grid(row=1, column=0, sticky="w", padx=(0, 10))
        ttk.Label(info, textvariable=self.request_time).grid(row=1, column=1, sticky="w")
        ttk.Label(info, text="状態").grid(row=2, column=0, sticky="w", padx=(0, 10))
        ttk.Label(info, textvariable=self.status).grid(row=2, column=1, sticky="w")
        ttk.Label(info, text="ソース").grid(row=3, column=0, sticky="w", padx=(0, 10))
        ttk.Label(info, textvariable=self.image_url, wraplength=320).grid(row=3, column=1, sticky="w")
        info.columnconfigure(1, weight=1)

    def refresh(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if self.refresh_after_id:
            self.after_cancel(self.refresh_after_id)
            self.refresh_after_id = None
        self.status.set("取得中...")
        self.worker = threading.Thread(target=self._worker, daemon=True)
        self.worker.start()

    def _worker(self) -> None:
        try:
            frame = load_acceleration_frame()
            self.result_queue.put(("ok", frame))
        except (HTTPError, URLError, TimeoutError, OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
            self.result_queue.put(("error", exc))

    def _drain_queue(self) -> None:
        try:
            kind, payload = self.result_queue.get_nowait()
        except queue.Empty:
            pass
        else:
            if kind == "ok":
                self._show_frame(payload)
            else:
                self.status.set(f"取得失敗: {payload}")
                self._schedule_refresh()
        self.after(200, self._drain_queue)

    def _show_frame(self, frame: AccelerationFrame) -> None:
        encoded = base64.b64encode(frame.image_bytes).decode("ascii")
        self.photo = tk.PhotoImage(data=encoded)
        self.latest_time.set(frame.latest_time)
        self.request_time.set(frame.request_time or datetime.now().strftime("%Y/%m/%d %H:%M:%S"))
        self.image_url.set(frame.image_url)
        warm_pixels = self._count_warm_pixels()
        shaking = warm_pixels >= ALERT_WARM_PIXEL_THRESHOLD
        self.status.set("揺れ検知" if shaking else "表示中")
        self._redraw_image()
        self._maybe_alert(shaking)
        self._schedule_refresh()

    def _toggle_auto_refresh(self) -> None:
        if self.running.get():
            self._schedule_refresh()
        elif self.refresh_after_id:
            self.after_cancel(self.refresh_after_id)
            self.refresh_after_id = None

    def _schedule_refresh(self) -> None:
        if not self.running.get() or self.refresh_after_id:
            return
        self.refresh_after_id = self.after(REFRESH_MS, self._scheduled_refresh)

    def _scheduled_refresh(self) -> None:
        self.refresh_after_id = None
        self.refresh()

    def _count_warm_pixels(self) -> int:
        if not self.photo:
            return 0
        count = 0
        width = self.photo.width()
        height = self.photo.height()
        for y in range(height):
            for x in range(width):
                color = self.photo.get(x, y)
                if isinstance(color, tuple):
                    red, green, blue = color[:3]
                else:
                    red, green, blue = self.winfo_rgb(color)
                    red //= 256
                    green //= 256
                    blue //= 256
                if self._is_shaking_color(red, green, blue):
                    count += 1
                    if count >= ALERT_WARM_PIXEL_THRESHOLD:
                        return count
        return count

    def _is_shaking_color(self, red: int, green: int, blue: int) -> bool:
        if red >= 210 and green >= 210 and blue >= 210:
            return True
        if red >= 190 and green >= 70 and blue <= 180:
            return True
        if red >= 180 and green <= 90 and blue <= 120:
            return True
        return False

    def _maybe_alert(self, shaking: bool) -> None:
        if self.first_frame:
            self.first_frame = False
            return
        if not shaking or not self.sound_enabled.get():
            return
        now = time.monotonic()
        if now - self.last_alert_at < ALERT_COOLDOWN_SECONDS:
            return
        self.last_alert_at = now
        threading.Thread(target=beep, daemon=True).start()
        self.bell()

    def _redraw_image(self) -> None:
        self.canvas.delete("all")
        if not self.photo:
            self.canvas.create_text(
                self.canvas.winfo_width() / 2,
                self.canvas.winfo_height() / 2,
                text="読み込み中",
                fill="#d7dde3",
                font=("", 16, "bold"),
            )
            return

        width = max(self.canvas.winfo_width(), 1)
        height = max(self.canvas.winfo_height(), 1)
        x = width / 2
        y = height / 2
        self.canvas.create_image(x, y, image=self.photo, anchor="center")


def main() -> None:
    app = AccelerationMonitor()
    app.mainloop()


if __name__ == "__main__":
    main()
