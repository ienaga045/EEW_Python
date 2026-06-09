#!/usr/bin/env python3
"""
JMA earthquake monitor.

Polls JMA's public earthquake JSON feed and shows recent seismic intensity
information on a simple Japan map. Emergency Earthquake Warnings are read from
YDITS' VXSE43 JSON endpoint, which republishes JMA EEW warning telegrams.
"""

from __future__ import annotations

import json
import platform
import queue
import re
import shutil
import ssl
import subprocess
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field
from datetime import datetime
from tkinter import messagebox, ttk
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


JMA_LIST_URL = "https://www.jma.go.jp/bosai/quake/data/list.json"
JMA_DETAIL_BASE = "https://www.jma.go.jp/bosai/quake/data/"
EEW_URLS = (
    "https://api.ydits.net/vxse43",
    "https://api2.ydits.net/vxse43",
    "https://api3.ydits.net/vxse43",
)
QUAKE_POLL_SECONDS = 30
EEW_POLL_SECONDS = 2


PREF_POINTS = {
    "北海道": (43.1, 141.3),
    "青森県": (40.8, 140.7),
    "岩手県": (39.7, 141.2),
    "宮城県": (38.3, 140.9),
    "秋田県": (39.7, 140.1),
    "山形県": (38.2, 140.3),
    "福島県": (37.8, 140.5),
    "茨城県": (36.3, 140.4),
    "栃木県": (36.6, 139.9),
    "群馬県": (36.4, 139.1),
    "埼玉県": (35.9, 139.6),
    "千葉県": (35.6, 140.1),
    "東京都": (35.7, 139.7),
    "神奈川県": (35.4, 139.6),
    "新潟県": (37.9, 139.0),
    "富山県": (36.7, 137.2),
    "石川県": (36.6, 136.7),
    "福井県": (36.1, 136.2),
    "山梨県": (35.7, 138.6),
    "長野県": (36.2, 138.2),
    "岐阜県": (35.4, 136.8),
    "静岡県": (35.0, 138.4),
    "愛知県": (35.2, 137.0),
    "三重県": (34.7, 136.5),
    "滋賀県": (35.0, 136.1),
    "京都府": (35.0, 135.8),
    "大阪府": (34.7, 135.5),
    "兵庫県": (34.7, 134.8),
    "奈良県": (34.7, 135.8),
    "和歌山県": (34.2, 135.2),
    "鳥取県": (35.5, 134.2),
    "島根県": (35.4, 133.0),
    "岡山県": (34.7, 133.9),
    "広島県": (34.4, 132.5),
    "山口県": (34.2, 131.5),
    "徳島県": (34.1, 134.6),
    "香川県": (34.3, 134.0),
    "愛媛県": (33.8, 132.8),
    "高知県": (33.6, 133.5),
    "福岡県": (33.6, 130.4),
    "佐賀県": (33.3, 130.3),
    "長崎県": (32.8, 129.9),
    "熊本県": (32.8, 130.7),
    "大分県": (33.2, 131.6),
    "宮崎県": (31.9, 131.4),
    "鹿児島県": (31.6, 130.6),
    "沖縄県": (26.2, 127.7),
}


INTENSITY_RANK = {
    "0": 0,
    "1": 1,
    "2": 2,
    "3": 3,
    "4": 4,
    "5-": 5,
    "5+": 6,
    "6-": 7,
    "6+": 8,
    "7": 9,
}

INTENSITY_COLORS = {
    "0": "#9aa7b2",
    "1": "#4f9dd9",
    "2": "#48b36b",
    "3": "#d0b533",
    "4": "#e3822e",
    "5-": "#df4f38",
    "5+": "#c93442",
    "6-": "#9c2f72",
    "6+": "#6f2b8f",
    "7": "#473168",
}


@dataclass
class Observation:
    name: str
    intensity: str
    pref: str = ""


@dataclass
class EarthquakeEvent:
    event_id: str
    title: str = "地震情報"
    report_time: str = ""
    origin_time: str = ""
    hypocenter: str = "不明"
    latitude: float | None = None
    longitude: float | None = None
    depth_km: str = "不明"
    magnitude: str = "不明"
    max_intensity: str = "不明"
    tsunami: str = ""
    observations: list[Observation] = field(default_factory=list)
    raw_json_name: str = ""
    event_type: str = "quake"
    is_eew_active: bool = False
    is_eew_final: bool = False
    is_eew_canceled: bool = False
    is_eew_warning: bool = False


def fetch_json(url: str, timeout: int = 10) -> Any:
    request = Request(url, headers={"User-Agent": "JMA-Earthquake-Monitor/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, ssl.SSLCertVerificationError) and "www.jma.go.jp" in url:
            context = ssl._create_unverified_context()
            with urlopen(request, timeout=timeout, context=context) as response:
                return json.loads(response.read().decode("utf-8"))
        if isinstance(reason, ssl.SSLError):
            return fetch_json_with_curl(url, timeout)
        raise


def fetch_json_with_curl(url: str, timeout: int = 10) -> Any:
    curl = shutil.which("curl")
    if not curl:
        raise RuntimeError("curl が見つからないため TLS フォールバックを実行できません")
    completed = subprocess.run(
        [curl, "-fsSL", "--max-time", str(timeout), "-A", "JMA-Earthquake-Monitor/1.0", url],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def normalize_intensity(value: Any) -> str:
    if value is None:
        return "不明"
    text = str(value).strip()
    mapping = {
        "10": "1",
        "20": "2",
        "30": "3",
        "40": "4",
        "45": "5-",
        "50": "5+",
        "55": "6-",
        "60": "6+",
        "70": "7",
        "5弱": "5-",
        "5強": "5+",
        "6弱": "6-",
        "6強": "6+",
        "unknown": "不明",
    }
    return mapping.get(text, text)


def intensity_rank(value: str) -> int:
    return INTENSITY_RANK.get(normalize_intensity(value), -1)


def max_intensity(values: list[str]) -> str:
    known = [normalize_intensity(v) for v in values if intensity_rank(v) >= 0]
    if not known:
        return "不明"
    return max(known, key=intensity_rank)


def parse_coordinate(text: Any) -> tuple[float | None, float | None, str]:
    if not isinstance(text, str):
        return None, None, "不明"
    matches = re.findall(r"[+-]\d+(?:\.\d+)?", text)
    lat = float(matches[0]) if len(matches) >= 1 else None
    lon = float(matches[1]) if len(matches) >= 2 else None
    depth = "不明"
    if len(matches) >= 3:
        try:
            depth = f"{abs(int(float(matches[2]) / 1000))} km"
        except ValueError:
            depth = "不明"
    return lat, lon, depth


def collect_observations(node: Any, current_pref: str = "") -> list[Observation]:
    observations: list[Observation] = []
    if isinstance(node, dict):
        pref = current_pref
        if node.get("Kind", {}).get("Name") == "府県予報区":
            pref = node.get("Name", pref)
        name = str(node.get("Name", ""))
        intensity = node.get("MaxInt") or node.get("Int")
        if intensity and name:
            normalized = normalize_intensity(intensity)
            observations.append(
                Observation(
                    name=name,
                    intensity=normalized,
                    pref=pref,
                )
            )
        for value in node.values():
            observations.extend(collect_observations(value, pref))
    elif isinstance(node, list):
        for item in node:
            observations.extend(collect_observations(item, current_pref))
    return observations


def parse_event(summary: dict[str, Any], detail: dict[str, Any] | None = None) -> EarthquakeEvent:
    json_name = summary.get("json", "")
    event_id = json_name or f"{summary.get('at', '')}-{summary.get('ttl', '')}"
    event = EarthquakeEvent(
        event_id=event_id,
        title=summary.get("ttl", "地震情報"),
        report_time=summary.get("rdt", ""),
        origin_time=summary.get("at", ""),
        hypocenter=summary.get("anm", "不明"),
        magnitude=str(summary.get("mag", "不明")),
        max_intensity=normalize_intensity(summary.get("maxi") or summary.get("MaxInt")),
        raw_json_name=json_name,
    )
    lat, lon, depth = parse_coordinate(summary.get("cod"))
    event.latitude, event.longitude, event.depth_km = lat, lon, depth

    if not detail:
        return event

    head = detail.get("Head", {})
    body = detail.get("Body", {})
    event.title = head.get("Title", event.title)
    event.report_time = head.get("ReportDateTime", event.report_time)

    earthquake = body.get("Earthquake", {})
    hypocenter = earthquake.get("Hypocenter", {}).get("Area", {})
    event.hypocenter = hypocenter.get("Name", event.hypocenter)
    lat, lon, depth = parse_coordinate(hypocenter.get("Coordinate"))
    event.latitude = lat if lat is not None else event.latitude
    event.longitude = lon if lon is not None else event.longitude
    event.depth_km = depth if depth != "不明" else event.depth_km
    event.origin_time = earthquake.get("OriginTime") or earthquake.get("ArrivalTime") or event.origin_time
    event.magnitude = str(earthquake.get("Magnitude", event.magnitude))

    intensity = body.get("Intensity", {}).get("Observation", {})
    event.max_intensity = normalize_intensity(intensity.get("MaxInt", event.max_intensity))
    event.observations = collect_observations(intensity)
    if event.observations:
        event.max_intensity = max_intensity([o.intensity for o in event.observations])

    comments = body.get("Comments", {})
    forecast = comments.get("ForecastComment", {})
    event.tsunami = forecast.get("Text", "")
    return event


def load_latest_events(limit: int = 20) -> list[EarthquakeEvent]:
    summaries = fetch_json(JMA_LIST_URL)
    events: list[EarthquakeEvent] = []
    for summary in summaries[:limit]:
        detail = None
        json_name = summary.get("json")
        if json_name:
            try:
                detail = fetch_json(JMA_DETAIL_BASE + json_name, timeout=8)
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError):
                detail = None
        events.append(parse_event(summary, detail))
    return events


def parse_eew(payload: dict[str, Any]) -> EarthquakeEvent | None:
    if payload.get("status") != "OK" or not payload.get("isEew"):
        return None

    report = payload.get("report") or {}
    earthquake = report.get("earthquake") or {}
    report_type = report.get("type", "normal")
    is_canceled = bool(report.get("isCanceled"))
    hypocenter = earthquake.get("hypocenterName") or "不明"
    report_time = report.get("time") or payload.get("time") or ""
    origin_time = earthquake.get("originTime") or earthquake.get("arrivalTime") or report_time
    max_int = normalize_intensity(earthquake.get("intensity"))
    magnitude = earthquake.get("magnitude")
    depth = earthquake.get("depth")

    event = EarthquakeEvent(
        event_id=f"eew-{origin_time}-{report_time}-{hypocenter}-{is_canceled}",
        title="緊急地震速報",
        report_time=report_time,
        origin_time=origin_time,
        hypocenter=hypocenter,
        magnitude="不明" if magnitude is None else str(magnitude),
        depth_km="不明" if depth is None else f"{depth} km",
        max_intensity=max_int,
        event_type="eew",
        is_eew_active=True,
        is_eew_final=bool(report.get("isFinal")),
        is_eew_canceled=is_canceled,
        is_eew_warning=bool(report.get("isWarning")),
    )
    if earthquake.get("condition"):
        event.magnitude = "仮定震源要素"
        event.depth_km = "仮定震源要素"
    if report_type in {"drill", "test"}:
        event.title = f"緊急地震速報({report_type})"
    if is_canceled:
        event.max_intensity = "不明"
    return event


def load_current_eew() -> EarthquakeEvent | None:
    last_error: Exception | None = None
    for url in EEW_URLS:
        try:
            return parse_eew(fetch_json(url, timeout=5))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError, subprocess.CalledProcessError) as exc:
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


class EarthquakeMonitor(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("JMA 日本列島 震度・緊急地震速報モニター")
        self.geometry("1120x760")
        self.minsize(920, 620)

        self.events: list[EarthquakeEvent] = []
        self.current_eew: EarthquakeEvent | None = None
        self.known_ids: set[str] = set()
        self.known_eew_id = ""
        self.first_load = True
        self.quake_worker: threading.Thread | None = None
        self.eew_worker: threading.Thread | None = None
        self.result_queue: queue.Queue[tuple[str, Any]] = queue.Queue()

        self.status = tk.StringVar(value="起動中")
        self.last_update = tk.StringVar(value="-")
        self.threshold = tk.StringVar(value="1")
        self.sound_enabled = tk.BooleanVar(value=True)
        self.polling_enabled = tk.BooleanVar(value=True)

        self._build_ui()
        self.after(200, self.refresh_quakes)
        self.after(500, self.refresh_eew)
        self.after(250, self._drain_queue)

    def _build_ui(self) -> None:
        self.configure(bg="#f5f7fa")
        header = ttk.Frame(self, padding=(16, 12))
        header.pack(fill="x")

        ttk.Label(header, text="JMA 日本列島 震度・緊急地震速報モニター", font=("", 18, "bold")).pack(side="left")
        ttk.Checkbutton(header, text="警告音", variable=self.sound_enabled).pack(side="right", padx=(8, 0))
        ttk.Checkbutton(header, text="自動更新", variable=self.polling_enabled).pack(side="right", padx=(8, 0))
        ttk.Button(header, text="手動更新", command=self.refresh_all).pack(side="right", padx=(8, 0))
        ttk.Button(header, text="音テスト", command=lambda: threading.Thread(target=beep, daemon=True).start()).pack(
            side="right", padx=(8, 0)
        )
        ttk.Label(header, text="警告しきい値").pack(side="right", padx=(16, 4))
        ttk.Combobox(
            header,
            textvariable=self.threshold,
            values=list(INTENSITY_RANK.keys())[1:],
            width=4,
            state="readonly",
        ).pack(side="right")

        body = ttk.PanedWindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=16, pady=(0, 12))

        map_frame = ttk.Frame(body, padding=10)
        body.add(map_frame, weight=3)

        self.canvas = tk.Canvas(map_frame, bg="#eef5fb", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _event: self.draw_map())

        side = ttk.Frame(body, padding=10)
        body.add(side, weight=2)

        self.summary_label = ttk.Label(side, text="読み込み中", font=("", 14, "bold"), wraplength=420)
        self.summary_label.pack(anchor="w", fill="x")

        self.detail_label = ttk.Label(side, text="", justify="left", wraplength=420)
        self.detail_label.pack(anchor="w", fill="x", pady=(8, 12))

        columns = ("time", "place", "mag", "max")
        self.tree = ttk.Treeview(side, columns=columns, show="headings", height=12)
        self.tree.heading("time", text="発生時刻")
        self.tree.heading("place", text="震央")
        self.tree.heading("mag", text="M")
        self.tree.heading("max", text="最大")
        self.tree.column("time", width=135, stretch=False)
        self.tree.column("place", width=160)
        self.tree.column("mag", width=46, stretch=False, anchor="center")
        self.tree.column("max", width=54, stretch=False, anchor="center")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._select_from_tree)

        footer = ttk.Frame(self, padding=(16, 0, 16, 12))
        footer.pack(fill="x")
        ttk.Label(footer, textvariable=self.status).pack(side="left")
        ttk.Label(footer, textvariable=self.last_update).pack(side="right")

    def refresh_all(self) -> None:
        self.refresh_quakes()
        self.refresh_eew()

    def refresh_quakes(self) -> None:
        if self.quake_worker and self.quake_worker.is_alive():
            return
        self.status.set("気象庁地震情報を取得中...")
        self.quake_worker = threading.Thread(target=self._load_quakes_worker, daemon=True)
        self.quake_worker.start()

    def refresh_eew(self) -> None:
        if self.eew_worker and self.eew_worker.is_alive():
            return
        self.eew_worker = threading.Thread(target=self._load_eew_worker, daemon=True)
        self.eew_worker.start()

    def _load_quakes_worker(self) -> None:
        try:
            events = load_latest_events()
            self.result_queue.put(("quakes_ok", events))
        except Exception as exc:
            self.result_queue.put(("quakes_error", exc))

    def _load_eew_worker(self) -> None:
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
            if kind == "quakes_ok":
                self._handle_events(payload)
            elif kind == "eew_ok":
                self._handle_eew(payload)
            elif kind == "eew_error":
                self.status.set(f"EEW取得失敗: {payload}")
                self._schedule_eew_refresh()
            else:
                self.status.set(f"取得失敗: {payload}")
                self._schedule_quake_refresh()
        self.after(250, self._drain_queue)

    def _handle_events(self, events: list[EarthquakeEvent]) -> None:
        previous = self.known_ids
        incoming = {event.event_id for event in events}
        new_events = [event for event in events if event.event_id not in previous]
        self.events = events
        self.known_ids = incoming
        self._populate_tree()
        self.draw_map()

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.status.set(f"{len(events)}件を表示中")
        self.last_update.set(f"最終更新 {now}")

        if events:
            self._show_event(events[0])

        threshold = intensity_rank(self.threshold.get())
        alarming = [event for event in new_events if intensity_rank(event.max_intensity) >= threshold]
        if alarming and not self.first_load and self.sound_enabled.get():
            self.status.set(f"新しい地震情報を検知: {alarming[0].hypocenter}")
            threading.Thread(target=beep, daemon=True).start()
            self.bell()
        self.first_load = False
        self._schedule_quake_refresh()

    def _handle_eew(self, event: EarthquakeEvent | None) -> None:
        previous_id = self.known_eew_id
        self.current_eew = event
        self.known_eew_id = event.event_id if event else ""
        self.draw_map(event or (self.events[0] if self.events else None))

        if event:
            self._show_event(event)
            label = "取消" if event.is_eew_canceled else f"最大震度 {event.max_intensity}"
            self.status.set(f"緊急地震速報: {event.hypocenter} {label}")
            if previous_id != event.event_id and self.sound_enabled.get():
                threading.Thread(target=beep, daemon=True).start()
                self.bell()
        self._schedule_eew_refresh()

    def _schedule_quake_refresh(self) -> None:
        if self.polling_enabled.get():
            self.after(QUAKE_POLL_SECONDS * 1000, self.refresh_quakes)

    def _schedule_eew_refresh(self) -> None:
        if self.polling_enabled.get():
            self.after(EEW_POLL_SECONDS * 1000, self.refresh_eew)

    def _populate_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for event in self.events:
            item = self.tree.insert(
                "",
                "end",
                iid=event.event_id,
                values=(
                    self._short_time(event.origin_time),
                    event.hypocenter,
                    event.magnitude,
                    event.max_intensity,
                ),
            )
            color = INTENSITY_COLORS.get(event.max_intensity)
            if color:
                tag = f"int-{event.max_intensity}"
                self.tree.tag_configure(tag, foreground=color)
                self.tree.item(item, tags=(tag,))

    def _select_from_tree(self, _event: tk.Event) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        event = next((item for item in self.events if item.event_id == selection[0]), None)
        if event:
            self._show_event(event)
            self.draw_map(event)

    def _show_event(self, event: EarthquakeEvent) -> None:
        prefix = "緊急地震速報" if event.event_type == "eew" else "地震情報"
        status = ""
        if event.event_type == "eew":
            if event.is_eew_canceled:
                status = " 取消"
            elif event.is_eew_final:
                status = " 最終報"
            elif event.is_eew_warning:
                status = " 警報"
        self.summary_label.config(text=f"{prefix}{status}: {event.hypocenter}  最大震度 {event.max_intensity}")
        self.detail_label.config(
            text=(
                f"発生時刻: {self._format_time(event.origin_time)}\n"
                f"発表時刻: {self._format_time(event.report_time)}\n"
                f"マグニチュード: {event.magnitude}\n"
                f"深さ: {event.depth_km}\n"
                f"津波: {event.tsunami or '情報なし'}"
            )
        )

    def draw_map(self, selected: EarthquakeEvent | None = None) -> None:
        self.canvas.delete("all")
        width = max(self.canvas.winfo_width(), 500)
        height = max(self.canvas.winfo_height(), 500)
        self.canvas.create_rectangle(0, 0, width, height, fill="#eef5fb", outline="")

        self._draw_japan_outline(width, height)
        self.canvas.create_text(18, 18, anchor="nw", text="観測震度・緊急地震速報", fill="#34495e", font=("", 13, "bold"))

        event = selected or self.current_eew or (self.events[0] if self.events else None)
        pref_intensity: dict[str, str] = {}
        if event:
            for observation in event.observations:
                pref = observation.pref or self._guess_pref(observation.name)
                if not pref:
                    continue
                current = pref_intensity.get(pref)
                if current is None or intensity_rank(observation.intensity) > intensity_rank(current):
                    pref_intensity[pref] = observation.intensity

        for pref, (lat, lon) in PREF_POINTS.items():
            x, y = self._project(lat, lon, width, height)
            intensity = pref_intensity.get(pref, "0")
            color = INTENSITY_COLORS.get(intensity, "#9aa7b2")
            radius = 5 + max(0, intensity_rank(intensity)) * 1.8
            self.canvas.create_oval(x - radius, y - radius, x + radius, y + radius, fill=color, outline="#ffffff", width=1)
            if intensity != "0":
                self.canvas.create_text(x, y - radius - 10, text=intensity, fill="#1c2730", font=("", 9, "bold"))

        if event and event.latitude and event.longitude:
            x, y = self._project(event.latitude, event.longitude, width, height)
            self.canvas.create_oval(x - 12, y - 12, x + 12, y + 12, outline="#111827", width=3)
            self.canvas.create_line(x - 18, y, x + 18, y, fill="#111827", width=2)
            self.canvas.create_line(x, y - 18, x, y + 18, fill="#111827", width=2)
            self.canvas.create_text(x + 18, y - 18, anchor="w", text=event.hypocenter, fill="#111827", font=("", 11, "bold"))

        self._draw_legend(width, height)

    def _draw_japan_outline(self, width: int, height: int) -> None:
        outlines = [
            [
                (45.5, 141.9),
                (44.6, 141.7),
                (43.8, 141.0),
                (43.2, 140.5),
                (42.2, 140.2),
                (41.5, 140.8),
                (42.0, 142.0),
                (42.7, 143.2),
                (43.5, 144.4),
                (44.3, 145.4),
                (45.1, 144.5),
                (45.5, 141.9),
            ],
            [
                (41.5, 140.9),
                (40.8, 140.2),
                (39.6, 139.9),
                (38.5, 139.5),
                (37.5, 138.8),
                (36.9, 137.5),
                (36.6, 136.2),
                (35.7, 135.4),
                (35.4, 134.3),
                (34.6, 133.5),
                (34.3, 132.1),
                (33.8, 131.0),
                (33.2, 130.4),
                (32.4, 130.5),
                (31.7, 130.7),
                (31.2, 130.4),
                (31.4, 131.3),
                (32.4, 131.8),
                (33.4, 132.6),
                (34.1, 134.0),
                (33.8, 135.1),
                (34.4, 136.3),
                (35.0, 137.1),
                (35.0, 138.5),
                (35.6, 139.7),
                (36.6, 140.7),
                (37.9, 141.0),
                (39.2, 141.5),
                (40.3, 141.7),
                (41.5, 140.9),
            ],
            [(34.7, 134.2), (34.2, 133.6), (33.8, 132.8), (33.4, 132.0), (33.8, 133.0), (34.4, 134.2), (34.7, 134.2)],
            [(34.7, 135.0), (34.1, 135.4), (33.5, 135.7), (33.8, 136.5), (34.5, 136.0), (34.7, 135.0)],
            [(30.8, 130.0), (30.3, 130.6), (29.8, 130.4), (30.2, 129.8), (30.8, 130.0)],
            [(26.7, 127.8), (26.2, 127.6), (25.8, 127.9), (26.2, 128.3), (26.7, 127.8)],
        ]
        for outline in outlines:
            points: list[float] = []
            for lat, lon in outline:
                x, y = self._project(lat, lon, width, height)
                points.extend([x, y])
            self.canvas.create_line(*points, fill="#748494", width=2, smooth=True)

    def _draw_legend(self, width: int, height: int) -> None:
        labels = ["1", "2", "3", "4", "5-", "5+", "6-", "6+", "7"]
        start_x = 22
        y = height - 32
        for idx, label in enumerate(labels):
            x = start_x + idx * 48
            self.canvas.create_oval(x, y - 8, x + 16, y + 8, fill=INTENSITY_COLORS[label], outline="")
            self.canvas.create_text(x + 22, y, anchor="w", text=label, fill="#34495e", font=("", 9))

    def _project(self, lat: float, lon: float, width: int, height: int) -> tuple[float, float]:
        min_lat, max_lat = 24.0, 46.5
        min_lon, max_lon = 122.0, 146.5
        x = 44 + (lon - min_lon) / (max_lon - min_lon) * (width - 88)
        y = 42 + (max_lat - lat) / (max_lat - min_lat) * (height - 92)
        return x, y

    def _guess_pref(self, name: str) -> str:
        for pref in PREF_POINTS:
            if pref in name:
                return pref
        return ""

    def _format_time(self, text: str) -> str:
        if not text:
            return "不明"
        try:
            value = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if value.tzinfo:
                value = value.astimezone()
            return value.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return text

    def _short_time(self, text: str) -> str:
        formatted = self._format_time(text)
        return formatted[5:16] if len(formatted) >= 16 else formatted


def main() -> None:
    try:
        app = EarthquakeMonitor()
        app.mainloop()
    except tk.TclError as exc:
        messagebox.showerror("起動エラー", str(exc))


if __name__ == "__main__":
    main()
