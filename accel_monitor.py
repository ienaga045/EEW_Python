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
import shutil
import ssl
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
EEW_URLS = (
    "https://api.ydits.net/vxse43",
    "https://api2.ydits.net/vxse43",
    "https://api3.ydits.net/vxse43",
)
REFRESH_MS = 1000
EEW_REFRESH_MS = 2000
USER_AGENT = "EEW_Python-KyoshinAccelerationViewer/1.0"
ALERT_COOLDOWN_SECONDS = 20
ALERT_WARM_PIXEL_THRESHOLD = 8


@dataclass
class AccelerationFrame:
    latest_time: str
    request_time: str
    image_bytes: bytes
    image_url: str


@dataclass
class EewEvent:
    event_id: str
    report_time: str
    origin_time: str
    hypocenter: str
    max_intensity: str
    magnitude: str
    depth_km: str
    report_type: str = "normal"
    is_final: bool = False
    is_canceled: bool = False
    is_warning: bool = False


def fetch_bytes(url: str, timeout: int = 10) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, ssl.SSLError):
            return fetch_bytes_with_curl(url, timeout)
        raise


def fetch_bytes_with_curl(url: str, timeout: int = 10) -> bytes:
    curl = shutil.which("curl")
    if not curl:
        raise RuntimeError("curl が見つからないため TLS フォールバックを実行できません")
    completed = subprocess.run(
        [curl, "-fsSL", "--max-time", str(timeout), "-A", USER_AGENT, url],
        check=True,
        capture_output=True,
    )
    return completed.stdout


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


def normalize_intensity(value: Any) -> str:
    if value is None:
        return "不明"
    text = str(value).strip()
    mapping = {
        "5弱": "5-",
        "5強": "5+",
        "6弱": "6-",
        "6強": "6+",
        "unknown": "不明",
    }
    return mapping.get(text, text)


def parse_eew(payload: dict[str, Any]) -> EewEvent | None:
    if payload.get("status") != "OK" or not payload.get("isEew"):
        return None

    report = payload.get("report") or {}
    earthquake = report.get("earthquake") or {}
    report_time = report.get("time") or payload.get("time") or ""
    origin_time = earthquake.get("originTime") or earthquake.get("arrivalTime") or report_time
    hypocenter = earthquake.get("hypocenterName") or "不明"
    max_intensity = normalize_intensity(earthquake.get("intensity"))
    magnitude = earthquake.get("magnitude")
    depth = earthquake.get("depth")
    is_canceled = bool(report.get("isCanceled"))

    event = EewEvent(
        event_id=f"eew-{origin_time}-{report_time}-{hypocenter}-{is_canceled}",
        report_time=report_time,
        origin_time=origin_time,
        hypocenter=hypocenter,
        max_intensity="不明" if is_canceled else max_intensity,
        magnitude="不明" if magnitude is None else str(magnitude),
        depth_km="不明" if depth is None else f"{depth} km",
        report_type=report.get("type", "normal"),
        is_final=bool(report.get("isFinal")),
        is_canceled=is_canceled,
        is_warning=bool(report.get("isWarning")),
    )
    if earthquake.get("condition"):
        event.magnitude = "仮定震源要素"
        event.depth_km = "仮定震源要素"
    return event


def load_current_eew() -> EewEvent | None:
    last_error: Exception | None = None
    for url in EEW_URLS:
        try:
            return parse_eew(fetch_json(url, timeout=5))
        except (
            HTTPError,
            URLError,
            TimeoutError,
            OSError,
            subprocess.CalledProcessError,
            json.JSONDecodeError,
        ) as exc:
            last_error = exc
    if last_error:
        raise last_error
    return None


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
        self.geometry("430x610")
        self.minsize(410, 600)

        self.result_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.eew_worker: threading.Thread | None = None
        self.running = tk.BooleanVar(value=True)
        self.shaking_sound_enabled = tk.BooleanVar(value=True)
        self.eew_sound_enabled = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value="起動中")
        self.eew_status = tk.StringVar(value="発表なし")
        self.latest_time = tk.StringVar(value="-")
        self.request_time = tk.StringVar(value="-")
        self.image_url = tk.StringVar(value="-")
        self.photo: tk.PhotoImage | None = None
        self.refresh_after_id: str | None = None
        self.eew_after_id: str | None = None
        self.first_frame = True
        self.known_eew_id = ""
        self.last_alert_at = 0.0

        self._build_ui()
        self.after(100, self.refresh_all)
        self.after(200, self._drain_queue)

    def _build_ui(self) -> None:
        self.configure(bg="#666666")

        header = ttk.Frame(self, padding=(14, 10))
        header.pack(fill="x")
        ttk.Label(header, text="最大加速度", font=("", 16, "bold")).pack(side="left")
        ttk.Checkbutton(header, text="自動更新", variable=self.running, command=self._toggle_auto_refresh).pack(
            side="right", padx=(8, 0)
        )
        ttk.Button(header, text="更新", command=self.refresh_all).pack(side="right", padx=(8, 0))

        sound_bar = ttk.Frame(self, padding=(14, 0, 14, 10))
        sound_bar.pack(fill="x")
        ttk.Checkbutton(sound_bar, text="揺れ検知音", variable=self.shaking_sound_enabled).pack(side="left")
        ttk.Button(sound_bar, text="揺れ音テスト", command=self._test_shaking_sound).pack(side="left", padx=(8, 0))
        ttk.Checkbutton(sound_bar, text="EEW音", variable=self.eew_sound_enabled).pack(side="left", padx=(18, 0))
        ttk.Button(sound_bar, text="EEW音テスト", command=self._test_eew_sound).pack(side="left", padx=(8, 0))

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
        ttk.Label(info, text="EEW").grid(row=3, column=0, sticky="w", padx=(0, 10))
        ttk.Label(info, textvariable=self.eew_status, wraplength=320).grid(row=3, column=1, sticky="w")
        ttk.Label(info, text="ソース").grid(row=4, column=0, sticky="w", padx=(0, 10))
        ttk.Label(info, textvariable=self.image_url, wraplength=320).grid(row=4, column=1, sticky="w")
        info.columnconfigure(1, weight=1)

    def refresh_all(self) -> None:
        self.refresh()
        self.refresh_eew()

    def refresh(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if self.refresh_after_id:
            self.after_cancel(self.refresh_after_id)
            self.refresh_after_id = None
        self.status.set("取得中...")
        self.worker = threading.Thread(target=self._worker, daemon=True)
        self.worker.start()

    def refresh_eew(self) -> None:
        if self.eew_worker and self.eew_worker.is_alive():
            return
        if self.eew_after_id:
            self.after_cancel(self.eew_after_id)
            self.eew_after_id = None
        self.eew_worker = threading.Thread(target=self._eew_worker, daemon=True)
        self.eew_worker.start()

    def _worker(self) -> None:
        try:
            frame = load_acceleration_frame()
            self.result_queue.put(("accel_ok", frame))
        except (HTTPError, URLError, TimeoutError, OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
            self.result_queue.put(("accel_error", exc))

    def _eew_worker(self) -> None:
        try:
            self.result_queue.put(("eew_ok", load_current_eew()))
        except Exception as exc:
            self.result_queue.put(("eew_error", exc))

    def _drain_queue(self) -> None:
        try:
            kind, payload = self.result_queue.get_nowait()
        except queue.Empty:
            pass
        else:
            if kind == "accel_ok":
                self._show_frame(payload)
            elif kind == "eew_ok":
                self._show_eew(payload)
            elif kind == "eew_error":
                self.eew_status.set(f"EEW: 取得失敗 {payload}")
                self._schedule_eew_refresh()
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
            self._schedule_eew_refresh()
            return
        if self.refresh_after_id:
            self.after_cancel(self.refresh_after_id)
            self.refresh_after_id = None
        if self.eew_after_id:
            self.after_cancel(self.eew_after_id)
            self.eew_after_id = None

    def _schedule_refresh(self) -> None:
        if not self.running.get() or self.refresh_after_id:
            return
        self.refresh_after_id = self.after(REFRESH_MS, self._scheduled_refresh)

    def _schedule_eew_refresh(self) -> None:
        if not self.running.get() or self.eew_after_id:
            return
        self.eew_after_id = self.after(EEW_REFRESH_MS, self._scheduled_eew_refresh)

    def _scheduled_refresh(self) -> None:
        self.refresh_after_id = None
        self.refresh()

    def _scheduled_eew_refresh(self) -> None:
        self.eew_after_id = None
        self.refresh_eew()

    def _show_eew(self, event: EewEvent | None) -> None:
        if event is None:
            self.eew_status.set("発表なし")
            self.known_eew_id = ""
            self._schedule_eew_refresh()
            return

        label = "取消" if event.is_canceled else f"{event.hypocenter} 最大震度 {event.max_intensity}"
        suffix = []
        if event.is_warning:
            suffix.append("警報")
        if event.is_final:
            suffix.append("最終報")
        if event.report_type != "normal":
            suffix.append(event.report_type)
        detail = f" ({', '.join(suffix)})" if suffix else ""
        self.eew_status.set(f"{label}{detail}")

        is_new = event.event_id != self.known_eew_id
        self.known_eew_id = event.event_id
        if is_new and event.report_type == "normal" and self.eew_sound_enabled.get():
            threading.Thread(target=beep, daemon=True).start()
            self.bell()
        self._schedule_eew_refresh()

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
        if not shaking or not self.shaking_sound_enabled.get():
            return
        now = time.monotonic()
        if now - self.last_alert_at < ALERT_COOLDOWN_SECONDS:
            return
        self.last_alert_at = now
        threading.Thread(target=beep, daemon=True).start()
        self.bell()

    def _test_shaking_sound(self) -> None:
        threading.Thread(target=beep, daemon=True).start()

    def _test_eew_sound(self) -> None:
        threading.Thread(target=beep, daemon=True).start()

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
