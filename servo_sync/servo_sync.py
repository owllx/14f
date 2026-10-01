#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Servo Trim Sync — працює у фоні поруч із Mission Planner і тримає MIN/MAX виходів SERVO від TRIM.

Для кожного вибраного виходу: SERVOn_MIN = TRIM − відступ, SERVOn_MAX = TRIM + відступ.
«Авто» — змінили TRIM у Mission Planner, і MIN/MAX виставляються самі, нічого не натискаючи.
Без «Авто» — кнопка «Застосувати до вибраних» робить те саме для всіх виходів з галочкою.

Підключення — «Автоматично»: програма бере MAVLink із вбудованого сервера Mission Planner
(порт 56781), тож працює незалежно від того, як MP підключений до політника (USB/COM,
радіомодем, UDP, TCP). Нічого в Mission Planner вмикати не треба. Запасний шлях —
MAVLink Mirror на UDP 14551, якщо його ввімкнено.
"""

import base64
import json
import os
import queue
import socket
import tempfile
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from pymavlink import mavutil

APP_NAME = "Servo Trim Sync"
CONFIG_DIR = Path(os.environ.get("APPDATA", tempfile.gettempdir())) / "ServoTrimSync"
CONFIG_PATH = CONFIG_DIR / "config.json"
AUTO_SOURCE = "auto"
MP_SOURCE = "missionplanner"
MIRROR_SOURCE = "udpin:0.0.0.0:14551"
MP_WEB_PORT = 56781
# Порт 14550 навмисно не слухаємо: якщо Mission Planner підключений по UDP 14550,
# спільний сокет міг би «перехоплювати» його пакети.
AUTO_SOURCES = (MP_SOURCE, MIRROR_SOURCE)
SOURCE_TITLES = {
    AUTO_SOURCE: "Автоматично (рекомендовано)",
    MP_SOURCE: "Через Mission Planner (будь-яке підключення)",
    MIRROR_SOURCE: "MAVLink Mirror · UDP 14551",
}
CONNECTION_PRESETS = (AUTO_SOURCE, MP_SOURCE, MIRROR_SOURCE, "tcp:127.0.0.1:5760")
RETRY_OPEN_S = 2.0
CONFIG_VERSION = 2
BAUD_RATES = ("57600", "115200", "921600")
HEARTBEAT_TIMEOUT = 5.0
WRITE_RETRY_S = 1.5
WRITE_ATTEMPTS = 3
REFRESH_SOURCES_S = 10.0

def channel_rules(channel, below, above, low=800, high=2200):
    """Пара правил «MIN/MAX від TRIM» для одного виходу SERVOn."""
    source = f"SERVO{channel}_TRIM"
    return [
        {"enabled": True, "source": source, "target": f"SERVO{channel}_MIN", "mode": "offset",
         "value": -abs(int(below)), "low": low, "high": high},
        {"enabled": True, "source": source, "target": f"SERVO{channel}_MAX", "mode": "offset",
         "value": abs(int(above)), "low": low, "high": high},
    ]


DEFAULT_CONFIG = {
    "config_version": CONFIG_VERSION,
    "connection": AUTO_SOURCE,
    "baud": "57600",
    "only_disarmed": True,
    "low": 800,
    "high": 2200,
    "outputs": [{"enabled": True, "channel": channel, "below": 400, "above": 400, "name": ""}
                for channel in range(7, 13)],
    "auto": True,
    "autoconnect": True,
    "step": 10,
    "topmost": False,
    "show_log": False,
    "geometry": "",
}


def compute_target(rule, source_value):
    """Нове значення цільового параметра за правилом (з обмеженням меж)."""
    if rule["mode"] == "fixed":
        value = float(rule["value"])
    else:
        value = float(source_value) + float(rule["value"])
    low, high = rule.get("low"), rule.get("high")
    if low not in (None, ""):
        value = max(float(low), value)
    if high not in (None, ""):
        value = min(float(high), value)
    return int(round(value))


def load_config():
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        config = json.loads(json.dumps(DEFAULT_CONFIG))
        config.update({key: data[key] for key in DEFAULT_CONFIG if key in data})
        outputs = [dict(output) for output in config["outputs"]][:6]
        while len(outputs) < 6:
            outputs.append({"enabled": False, "channel": 7 + len(outputs), "below": 400, "above": 400})
        for output in outputs:
            output.setdefault("name", "")
        config["outputs"] = outputs
        if int(data.get("config_version", 1)) < CONFIG_VERSION:
            # Старі версії підключалися лише через MAVLink Mirror — тепер «Автоматично».
            if config["connection"] in ("udpin:0.0.0.0:14551", "udpin:0.0.0.0:14550", ""):
                config["connection"] = AUTO_SOURCE
            config["config_version"] = CONFIG_VERSION
        return config
    except Exception:
        return json.loads(json.dumps(DEFAULT_CONFIG))


def save_config(config):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


class MissionPlannerLink(mavutil.mavfile):
    """MAVLink через вбудований сервер Mission Planner: ws://127.0.0.1:56781/websocket/raw.

    Mission Planner сам віддає сюди кожен пакет від політника і пересилає наші пакети політнику —
    незалежно від того, як він підключений (USB/COM, радіомодем, UDP, TCP). Нічого вмикати не треба.
    """

    def __init__(self, host="127.0.0.1", port=MP_WEB_PORT, source_system=254, source_component=191):
        sock = socket.create_connection((host, port), timeout=1.5)
        try:
            key = base64.b64encode(os.urandom(16)).decode()
            sock.sendall((
                f"GET /websocket/raw HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
                f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
            ).encode())
            response = b""
            while b"\r\n\r\n" not in response and b"\n\n" not in response:
                chunk = sock.recv(4096)
                if not chunk or len(response) > 16384:
                    raise ConnectionError("Mission Planner не відповів на запит")
                response += chunk
            separator = b"\r\n\r\n" if b"\r\n\r\n" in response else b"\n\n"
            head, _, rest = response.partition(separator)
            if b" 101" not in head.split(b"\n", 1)[0]:
                raise ConnectionError("ця версія Mission Planner не віддає MAVLink (оновіть MP)")
        except Exception:
            sock.close()
            raise
        sock.setblocking(False)
        self.sock = sock
        self.frames = bytearray(rest)
        mavutil.mavfile.__init__(self, sock.fileno(), "missionplanner", source_system=source_system,
                                 source_component=source_component)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass

    def recv(self, n=None):
        try:
            data = self.sock.recv(65536)
            if not data:
                raise ConnectionError("Mission Planner закрито")
            self.frames.extend(data)
        except (BlockingIOError, InterruptedError):
            pass
        out = bytearray()
        buffer = self.frames
        while len(buffer) >= 2:
            opcode, length, position = buffer[0] & 0x0F, buffer[1] & 0x7F, 2
            masked = buffer[1] & 0x80
            if length == 126:
                if len(buffer) < 4:
                    break
                length, position = int.from_bytes(buffer[2:4], "big"), 4
            elif length == 127:
                if len(buffer) < 10:
                    break
                length, position = int.from_bytes(buffer[2:10], "big"), 10
            mask = None
            if masked:
                mask = buffer[position:position + 4]
                position += 4
            if len(buffer) < position + length:
                break
            payload = bytearray(buffer[position:position + length])
            del buffer[:position + length]
            if mask:
                for index in range(len(payload)):
                    payload[index] ^= mask[index % 4]
            if opcode == 0x8:
                raise ConnectionError("Mission Planner закрив з'єднання")
            if opcode in (0x0, 0x1, 0x2):
                out.extend(payload)
        return bytes(out)

    def write(self, buf):
        # Кадр WebSocket від клієнта: FIN + binary, маска обов'язкова.
        payload = bytes(buf)
        length = len(payload)
        header = bytearray([0x82])
        if length < 126:
            header.append(0x80 | length)
        else:
            header.append(0x80 | 126)
            header += length.to_bytes(2, "big")
        mask = os.urandom(4)
        header += mask
        frame = bytes(header) + bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self.sock.setblocking(True)
        try:
            self.sock.sendall(frame)
        finally:
            self.sock.setblocking(False)


def open_source(source, baud):
    if source == MP_SOURCE:
        return MissionPlannerLink()
    return mavutil.mavlink_connection(source, baud=int(baud), source_system=254, source_component=191,
                                      autoreconnect=True, retries=0)


def source_title(source):
    return SOURCE_TITLES.get(source, source)


class MavWorker(threading.Thread):
    """Весь обмін MAVLink в окремому потоці; з вікном спілкується через черги.

    «Автоматично» одночасно шукає політник через Mission Planner (вбудований сервер)
    і через MAVLink Mirror; працює з тим, звідки першим прийде heartbeat політника.
    """

    def __init__(self, events):
        super().__init__(daemon=True)
        self.events = events
        self.commands = queue.Queue()
        self.wanted = None            # (джерело, швидкість) або None, якщо відключено
        self.links = {}               # відкриті кандидати, поки шукаємо
        self.next_try = {}
        self.failures = {}
        self.master = None
        self.master_source = None
        self.search_started = 0.0
        self.status = None
        self.target = (1, 1)
        self.params = {}
        self.rules = []
        self.only_disarmed = True
        self.armed = False
        self.last_vehicle_heartbeat = 0.0
        self.last_own_heartbeat = 0.0
        self.last_refresh = 0.0
        self.link_ok = False
        self.pending = {}

    # ---- службове ---------------------------------------------------------------------
    def emit(self, *event):
        self.events.put(event)

    def log(self, text, level="info"):
        self.emit("log", text, level)

    def set_status(self, ok, text):
        if self.status != (ok, text):
            self.status = (ok, text)
            self.emit("link", ok, text)

    def run(self):
        while True:
            try:
                command = self.commands.get(timeout=0.02)
            except queue.Empty:
                command = None
            if command:
                if command[0] == "quit":
                    self._close()
                    return
                self._handle_command(command)
            if self.wanted is not None:
                try:
                    self._pump()
                except Exception as exc:  # обрив зв'язку тощо — шукаємо далі
                    self._drop_master(f"Зв'язок перервано: {exc}")

    def _handle_command(self, command):
        kind = command[0]
        if kind == "connect":
            self._connect(command[1], command[2])
        elif kind == "disconnect":
            self._close()
            self.log("Відключено")
        elif kind == "rules":
            self.rules, self.only_disarmed = command[1], command[2]
            self._request_sources()
        elif kind == "apply_all":
            self._apply_all(command[1] if len(command) > 1 else None, command[2] if len(command) > 2 else False)
        elif kind == "write_trim":
            self._write_trim(command[1], command[2], command[3])

    # ---- пошук і підключення -----------------------------------------------------------
    def _sources(self):
        source = self.wanted[0]
        return AUTO_SOURCES if source == AUTO_SOURCE else (source,)

    def _connect(self, source, baud):
        self._close()
        self.wanted = (source or AUTO_SOURCE, baud)
        self.search_started = time.monotonic()
        self.failures.clear()
        self.next_try.clear()
        if self.wanted[0] == AUTO_SOURCE:
            self.log("Шукаю політник через Mission Planner…")
        else:
            self.log(f"Підключаюся: {source_title(self.wanted[0])}…")

    def _close_links(self, keep=None):
        for source, link in list(self.links.items()):
            if link is keep:
                continue
            try:
                link.close()
            except Exception:
                pass
            del self.links[source]

    def _close(self):
        self._close_links()
        if self.master is not None:
            try:
                self.master.close()
            except Exception:
                pass
        self.master = None
        self.master_source = None
        self.wanted = None
        self.link_ok = False
        self.pending.clear()
        self.set_status(False, "Не підключено")

    def _drop_master(self, reason):
        """Втратили зв'язок: закрити джерело й знову шукати (через секунду)."""
        if self.master is not None:
            try:
                self.master.close()
            except Exception:
                pass
            self.next_try[self.master_source] = time.monotonic() + 1.0
            self.links.pop(self.master_source, None)
        self.master = None
        self.master_source = None
        if self.link_ok:
            self.log(reason, "error")
        self.link_ok = False
        self.pending.clear()
        self.search_started = time.monotonic()

    def _scan(self, now):
        baud = self.wanted[1]
        for source in self._sources():
            link = self.links.get(source)
            if link is None:
                if now < self.next_try.get(source, 0.0):
                    continue
                try:
                    link = open_source(source, baud)
                except Exception as exc:
                    self.next_try[source] = now + RETRY_OPEN_S
                    self.failures[source] = exc
                    continue
                self.links[source] = link
                self.failures.pop(source, None)
            try:
                for _ in range(200):
                    message = link.recv_match(blocking=False)
                    if message is None:
                        break
                    if self._vehicle_heartbeat(message):
                        self._lock(source, link)
                        self._on_message(message)
                        return
            except Exception as exc:
                self.failures[source] = exc
                self.next_try[source] = now + RETRY_OPEN_S
                try:
                    link.close()
                except Exception:
                    pass
                self.links.pop(source, None)
        self.set_status(False, self._search_text(now))

    def _search_text(self, now):
        auto = self.wanted[0] == AUTO_SOURCE
        if MP_SOURCE in self.links:
            return "Mission Planner відкрито — чекаю політник"
        if not auto:
            failure = self.failures.get(self.wanted[0])
            if failure is not None:
                return f"Не вдалося відкрити: {failure}"
            return "Чекаю на політник…"
        failure = self.failures.get(MP_SOURCE)
        if isinstance(failure, ConnectionRefusedError) or (
                isinstance(failure, OSError) and getattr(failure, "errno", None) in (10061, 111)):
            return "Чекаю на Mission Planner…"
        if failure is not None:
            return f"Mission Planner: {failure}"
        return "Шукаю Mission Planner…"

    @staticmethod
    def _vehicle_heartbeat(message):
        return (message.get_type() == "HEARTBEAT"
                and message.type != mavutil.mavlink.MAV_TYPE_GCS
                and message.autopilot != mavutil.mavlink.MAV_AUTOPILOT_INVALID)

    def _lock(self, source, link):
        self._close_links(keep=link)
        self.links.clear()
        self.master = link
        self.master_source = source
        self.params.clear()
        self.pending.clear()
        self.link_ok = False
        self.last_own_heartbeat = 0.0

    # ---- основний цикл -----------------------------------------------------------------
    def _pump(self):
        now = time.monotonic()
        if self.master is None:
            self._scan(now)
            return
        if now - self.last_own_heartbeat >= 1.0:
            self.last_own_heartbeat = now
            self.master.mav.heartbeat_send(
                mavutil.mavlink.MAV_TYPE_GCS, mavutil.mavlink.MAV_AUTOPILOT_INVALID, 0, 0, 0
            )
        for _ in range(200):
            message = self.master.recv_match(blocking=False)
            if message is None:
                break
            self._on_message(message)
        if self.link_ok and now - self.last_vehicle_heartbeat > HEARTBEAT_TIMEOUT:
            self._drop_master("Зв'язок з політником втрачено — шукаю знову…")
            return
        if self.link_ok and now - self.last_refresh > REFRESH_SOURCES_S:
            self._request_sources()
        self._retry_writes(now)

    def _on_message(self, message):
        kind = message.get_type()
        if kind == "HEARTBEAT":
            if not self._vehicle_heartbeat(message):
                return
            source = (message.get_srcSystem(), message.get_srcComponent())
            if self.link_ok and source != self.target:
                return  # інший апарат у тій самій мережі — працюємо з першим
            self.target = source
            self.last_vehicle_heartbeat = time.monotonic()
            armed = bool(message.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
            if not self.link_ok:
                self.link_ok = True
                route = source_title(self.master_source) if self.master_source != MP_SOURCE else "через Mission Planner"
                self.set_status(True, f"Політник #{self.target[0]} · {route}")
                self.log(f"Підключено до політника #{self.target[0]} ({route})", "ok")
                self._request_sources()
            if armed != self.armed:
                self.armed = armed
                self.emit("armed", armed)
        elif kind == "PARAM_VALUE":
            if (message.get_srcSystem(), message.get_srcComponent()) != self.target:
                return
            name = message.param_id
            if isinstance(name, bytes):
                name = name.decode(errors="ignore")
            self._on_param(name.rstrip("\x00"), float(message.param_value))

    def _on_param(self, name, value):
        previous = self.params.get(name)
        self.params[name] = value
        self.emit("param", name, value)
        pending = self.pending.get(name)
        if pending and abs(value - pending["value"]) < 0.5:
            del self.pending[name]
            self.log(f"{name} = {value:g} ✓", "ok")
        changed = previous is not None and abs(previous - value) >= 1e-6
        rules = [rule for rule in self.rules if rule["enabled"] and name in (rule["source"], rule["target"])]
        if not rules:
            return
        if changed and any(rule["source"] == name for rule in rules):
            self.log(f"{name}: {previous:g} → {value:g}")
        # «Авто» тримає MIN/MAX узгодженими з TRIM: після зміни TRIM, а також одразу після
        # підключення, якщо в політнику вже стоять інші значення.
        for rule in rules:
            source = self.params.get(rule["source"])
            if source is None:
                continue
            if rule["target"] not in self.params and not (changed and rule["source"] == name):
                continue  # чекаємо, поки прочитаємо поточне значення MIN/MAX
            self._apply_rules([rule], source)

    # ---- правила й запис ---------------------------------------------------------------
    def _request_sources(self):
        if self.master is None or not self.link_ok:
            return
        self.last_refresh = time.monotonic()
        names = {rule["source"] for rule in self.rules if rule["enabled"]}
        names |= {rule["target"] for rule in self.rules if rule["enabled"]}
        for name in sorted(names):
            self.master.mav.param_request_read_send(self.target[0], self.target[1], name.encode(), -1)

    def _apply_all(self, rules=None, quiet=False):
        if not self.link_ok:
            if not quiet:
                self.log("Немає зв'язку з політником", "error")
            return
        applied = 0
        for rule in (self.rules if rules is None else rules):
            if not rule["enabled"]:
                continue
            source = self.params.get(rule["source"])
            if source is None and rule["mode"] == "offset":
                self.log(f"{rule['source']} ще не прочитано — пропускаю", "warn")
                continue
            applied += self._apply_rules([rule], source)
        if not applied and not quiet:
            self.log("Усе вже відповідає правилам")

    def _write_trim(self, name, value, rules):
        """Користувач ввів TRIM у програмі: записати TRIM і одразу MIN/MAX."""
        if not self.link_ok:
            self.log("Немає зв'язку з політником", "error")
            return
        if self.only_disarmed and self.armed:
            self.log("Апарат заармлений — параметри не змінюю", "warn")
            return
        self._write(name, value)
        self._apply_rules(rules, value)

    def _apply_rules(self, rules, source_value):
        if self.only_disarmed and self.armed:
            now = time.monotonic()
            if now - getattr(self, "last_armed_warning", -100.0) > 20:
                self.last_armed_warning = now
                self.log("Апарат заармлений — параметри не змінюю", "warn")
            return 0
        written = 0
        for rule in rules:
            value = compute_target(rule, source_value)
            current = self.params.get(rule["target"])
            if current is not None and abs(current - value) < 0.5:
                continue
            pending = self.pending.get(rule["target"])
            if pending and abs(pending["value"] - value) < 0.5:
                continue
            self._write(rule["target"], value)
            written += 1
        return written

    def _write(self, name, value):
        self.pending[name] = {"value": float(value), "attempts": 0, "next": 0.0}
        self.log(f"Записую {name} = {value}")
        self._retry_writes(time.monotonic())

    def _retry_writes(self, now):
        for name, job in list(self.pending.items()):
            if now < job["next"]:
                continue
            if job["attempts"] >= WRITE_ATTEMPTS:
                del self.pending[name]
                self.log(f"{name}: політник не підтвердив значення {job['value']:g}", "error")
                continue
            job["attempts"] += 1
            job["next"] = now + WRITE_RETRY_S
            self.master.mav.param_set_send(
                self.target[0], self.target[1], name.encode(), job["value"],
                mavutil.mavlink.MAV_PARAM_TYPE_REAL32,
            )


# ---- Вигляд: темний мінімалістичний, у фірмових кольорах логотипу -------------------------
BG = "#0d0f13"
SURFACE = "#14171d"
RAISED = "#1b1f27"
LINE = "#222731"
TEXT = "#e8ebf0"
MUTED = "#7f8796"
FAINT = "#4f5664"
ACCENT = "#22d3ee"
OK = "#34d399"
WARN = "#fbbf24"
ERR = "#f87171"
FONT = "Segoe UI"
STEPS = (1, 5, 10, 25, 50)
COLUMNS = (30, 112, 118, 128, 118, 118)  # галочка, вихід, MIN, TRIM, MAX, у політнику


class Tip:
    """Підказка при наведенні."""

    def __init__(self, widget, text):
        self.widget, self.text, self.window, self.job = widget, text, None, None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._hide()
        self.job = self.widget.after(600, self._show)

    def _show(self):
        self.job = None
        if self.window or not self.text:
            return
        x = self.widget.winfo_rootx() + 6
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.window = tk.Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        self.window.attributes("-topmost", True)
        tk.Label(self.window, text=self.text, bg=RAISED, fg=TEXT, font=(FONT, 9), padx=10, pady=6,
                 justify="left", highlightthickness=1, highlightbackground=LINE).pack()
        self.window.geometry(f"+{x}+{y}")

    def _hide(self, _event=None):
        if self.job:
            self.widget.after_cancel(self.job)
            self.job = None
        if self.window:
            self.window.destroy()
            self.window = None


class FlatButton(tk.Label):
    """Тиха текстова кнопка; repeat=True — при утриманні повторює дію (для − / +)."""

    def __init__(self, parent, text, command, repeat=False, tip=None, bg=None, fg=MUTED, hover_bg=RAISED,
                 hover_fg=TEXT, font=(FONT, 10), padx=8, pady=2, width=0):
        bg = bg or parent.cget("bg")
        super().__init__(parent, text=text, bg=bg, fg=fg, font=font, padx=padx, pady=pady, cursor="hand2",
                         width=width)
        self.command, self.repeat = command, repeat
        self.normal = (bg, fg)
        self.hover = (hover_bg, hover_fg)
        self.job = None
        self.inside = False
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        self.tip = Tip(self, tip) if tip else None

    def set_style(self, bg=None, fg=None, hover_bg=None, hover_fg=None):
        self.normal = (bg or self.normal[0], fg or self.normal[1])
        self.hover = (hover_bg or self.hover[0], hover_fg or self.hover[1])
        bg, fg = self.hover if self.inside else self.normal
        self.configure(bg=bg, fg=fg)

    def _enter(self, _event):
        self.inside = True
        self.configure(bg=self.hover[0], fg=self.hover[1])

    def _leave(self, _event):
        self.inside = False
        self.configure(bg=self.normal[0], fg=self.normal[1])
        self._stop()

    def _press(self, _event):
        if self.repeat:
            self.command()
            self.job = self.after(420, self._repeat)

    def _repeat(self):
        self.command()
        self.job = self.after(70, self._repeat)

    def _stop(self):
        if self.job:
            self.after_cancel(self.job)
            self.job = None

    def _release(self, event):
        self._stop()
        inside = 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height()
        if inside and not self.repeat:
            self.command()


class ImageButton(tk.Label):
    """Кнопка-картинка (згладжена графіка з assets.py): звичайна / під курсором / натиснута."""

    def __init__(self, parent, images, command, text="", tip=None, fg="#ffffff", font=(FONT, 10, "bold")):
        super().__init__(parent, image=images.get("normal", ""), text=text, compound="center", fg=fg, font=font,
                         bg=parent.cget("bg"), bd=0, highlightthickness=0, padx=0, pady=0, cursor="hand2")
        self.images, self.command, self.enabled, self.inside = images, command, True, False
        self.colors = (fg, MUTED)
        self.bind("<Enter>", lambda _e: self._state(True))
        self.bind("<Leave>", lambda _e: self._state(False))
        self.bind("<ButtonPress-1>", lambda _e: self.enabled and self.configure(
            image=self.images.get("down", self.images["normal"])))
        self.bind("<ButtonRelease-1>", self._release)
        self.tip = Tip(self, tip) if tip else None

    def set_images(self, images):
        self.images = images
        self._state(self.inside)

    def set_enabled(self, enabled):
        self.enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow", fg=self.colors[0] if enabled else self.colors[1])
        self._state(self.inside)

    def _state(self, inside):
        self.inside = inside
        if not self.enabled:
            key = "disabled"
        else:
            key = "hover" if inside else "normal"
        self.configure(image=self.images.get(key, self.images.get("normal", "")))

    def _release(self, event):
        inside = 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height()
        self._state(inside)
        if inside and self.enabled:
            self.command()


class App(tk.Tk):
    """Панель для шести виходів SERVO: MIN = TRIM − відступ, MAX = TRIM + відступ."""

    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.configure(bg=BG)
        self.resizable(False, False)
        self.config_data = load_config()
        self.config_data.setdefault("autoconnect", True)
        self.params = {}
        self.events = queue.Queue()
        self.worker = MavWorker(self.events)
        self.worker.start()
        self.connected_request = False
        self.link_ok = False
        self.link_text = "Не підключено"
        self.armed = False
        self.save_job = None
        self.refresh_job = None
        self.rows = []
        self.settings_window = None
        self._load_images()
        self._style()
        self._build()
        self._apply_window_options()
        geometry = str(self.config_data.get("geometry") or "")
        if geometry.startswith("+"):
            self.geometry(geometry)
        self._send_rules()
        self._refresh_values()
        self.after(50, self._poll_events)
        if self.config_data.get("autoconnect"):
            self.after(400, self._toggle_connection)
        self.protocol("WM_DELETE_WINDOW", self._close)

    # ---- графіка й шрифти ----------------------------------------------------------------
    def _load_images(self):
        from assets import ASSETS
        self.img = {name: tk.PhotoImage(data="".join(chunks)) for name, chunks in ASSETS.items()}
        try:
            self.iconphoto(True, self.img["icon64"], self.img["icon32"], self.img["icon16"])
        except tk.TclError:
            pass
        from tkinter import font as tkfont
        families = set(tkfont.families(self))
        semibold = "Segoe UI Semibold" if "Segoe UI Semibold" in families else None
        self.font_title = (semibold, 13) if semibold else (FONT, 13, "bold")
        self.font_strong = (semibold, 10) if semibold else (FONT, 10, "bold")
        self.font_value = (semibold, 11) if semibold else (FONT, 11, "bold")
        self.font_trim = (semibold, 13) if semibold else (FONT, 13, "bold")

    def _style(self):
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure(".", background=BG, foreground=TEXT, font=(FONT, 10))
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=TEXT)
        for name in ("TEntry", "TCombobox", "TSpinbox"):
            style.configure(name, fieldbackground=SURFACE, foreground=TEXT, background=RAISED, bordercolor=LINE,
                            lightcolor=SURFACE, darkcolor=SURFACE, arrowcolor=MUTED, insertcolor=TEXT, padding=4)
            style.map(name, fieldbackground=[("readonly", SURFACE)], foreground=[("readonly", TEXT)],
                      bordercolor=[("focus", ACCENT)])
        self.option_add("*TCombobox*Listbox.background", SURFACE)
        self.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.option_add("*TCombobox*Listbox.selectForeground", BG)

    def _label(self, parent, text="", fg=TEXT, font=(FONT, 10), **kw):
        return tk.Label(parent, text=text, fg=fg, bg=parent.cget("bg"), font=font, **kw)

    def _line(self, parent, **pack):
        line = tk.Frame(parent, bg=LINE, height=1)
        line.pack(fill="x", **pack)
        return line

    # ---- побудова вікна ------------------------------------------------------------------
    def _build(self):
        head = tk.Frame(self, bg=BG, padx=18, pady=14)
        head.pack(fill="x")
        tk.Label(head, image=self.img["logo"], bg=BG).pack(side="left")
        titles = tk.Frame(head, bg=BG)
        titles.pack(side="left", padx=(10, 0))
        self._label(titles, APP_NAME, TEXT, self.font_title).pack(anchor="w")
        self._label(titles, "MIN / MAX від TRIM · ArduPilot", MUTED, (FONT, 9)).pack(anchor="w")
        self.settings_button = ImageButton(head, {"normal": self.img["tune"], "hover": self.img["tune_hover"]},
                                           self._open_settings, tip="Налаштування: підключення, номери SERVO,\n"
                                                                     "назви, межі PWM, безпека")
        self.settings_button.pack(side="right", padx=(6, 0))
        self.pin_button = ImageButton(head, {}, self._toggle_topmost, tip="Тримати вікно поверх Mission Planner")
        self.pin_button.pack(side="right", padx=(6, 0))
        self.connect_button = FlatButton(head, "", self._toggle_connection, font=self.font_strong, padx=12, pady=5,
                                         bg=SURFACE, hover_bg=RAISED,
                                         tip="Програма бачить усе, що бачить Mission Planner —\n"
                                             "хоч як він підключений (USB, COM, радіо, UDP, TCP).\n"
                                             "У Mission Planner нічого вмикати не треба.")
        self.connect_button.pack(side="right", padx=(0, 6))
        self.armed_label = self._label(head, "", ERR, (FONT, 9, "bold"))
        self.armed_label.pack(side="right", padx=(0, 10))
        self._line(self)

        table = tk.Frame(self, bg=BG, padx=18, pady=10)
        table.pack(fill="x")
        header = self._row_frame(table)
        for column, text in enumerate(("", "ВИХІД", "MIN = TRIM −", "TRIM", "MAX = TRIM +", "У ПОЛІТНИКУ")):
            anchor = "w" if column in (1,) else ("e" if column == 5 else "center")
            self._label(header, text, FAINT, (FONT, 8, "bold"), anchor=anchor).grid(
                row=0, column=column, sticky="ew", pady=(0, 6))
        for index in range(6):
            tk.Frame(table, bg=LINE, height=1).pack(fill="x")
            self.rows.append(self._build_row(table, index))
        self._line(self)

        foot = tk.Frame(self, bg=BG, padx=18, pady=14)
        foot.pack(fill="x")
        top = tk.Frame(foot, bg=BG)
        top.pack(fill="x")
        self.auto_switch = ImageButton(top, {}, self._toggle_auto,
                                       tip="Увімкнено — працює у фоні: змінили TRIM у Mission Planner,\n"
                                           "і MIN/MAX вибраних виходів виставляються самі.\n"
                                           "Вимкнено — лише кнопкою «Застосувати».")
        self.auto_switch.pack(side="left")
        auto_text = tk.Frame(top, bg=BG)
        auto_text.pack(side="left", padx=(10, 0))
        self._label(auto_text, "Авто", TEXT, self.font_strong).pack(anchor="w")
        self.auto_hint = self._label(auto_text, "", MUTED, (FONT, 8))
        self.auto_hint.pack(anchor="w")
        steps = tk.Frame(top, bg=BG)
        steps.pack(side="right")
        self._label(steps, "Крок", FAINT, (FONT, 8, "bold")).pack(side="left", padx=(0, 6))
        self.step_buttons = {}
        for step in STEPS:
            button = FlatButton(steps, str(step), lambda s=step: self._set_step(s), font=(FONT, 9, "bold"),
                                padx=7, tip="На скільки змінюють − / + і коліщатко (Shift — ×5)")
            button.pack(side="left", padx=1)
            self.step_buttons[step] = button

        bottom = tk.Frame(foot, bg=BG)
        bottom.pack(fill="x", pady=(14, 0))
        self.apply_button = ImageButton(
            bottom,
            {key: self.img[f"primary_{key}"] for key in ("normal", "hover", "down", "disabled")},
            self._apply_selected, text="Застосувати до вибраних", font=self.font_strong,
            tip="Виставити MIN і MAX усім виходам з галочкою\nза поточним TRIM і вашими відступами",
        )
        self.apply_button.pack(side="right")
        status = tk.Frame(bottom, bg=BG)
        status.pack(side="left", fill="x", expand=True)
        self.summary = self._label(status, "", MUTED, self.font_strong, anchor="w", justify="left", wraplength=320)
        self.summary.pack(anchor="w")
        line = tk.Frame(status, bg=BG)
        line.pack(anchor="w", fill="x")
        self.last_log = self._label(line, "Готово", FAINT, (FONT, 8), anchor="w", width=34)
        self.last_log.pack(side="left")
        self.log_button = FlatButton(line, "журнал", self._toggle_log, font=(FONT, 8, "underline"), padx=2, pady=0,
                                     fg=MUTED, hover_bg=BG, hover_fg=ACCENT)
        self.log_button.pack(side="left")

        self.log_frame = tk.Frame(self, bg=BG, padx=18)
        self.log_text = tk.Text(self.log_frame, height=7, width=1, font=("Consolas", 9), relief="flat",
                                state="disabled", bg=SURFACE, fg=TEXT, highlightthickness=0, padx=8, pady=6)
        self.log_text.pack(fill="both", expand=True, pady=(0, 16))
        for level, color in (("ok", OK), ("warn", WARN), ("error", ERR), ("info", MUTED)):
            self.log_text.tag_configure(level, foreground=color)
        self._update_controls()

    def _row_frame(self, parent):
        frame = tk.Frame(parent, bg=BG)
        frame.pack(fill="x")
        for column, width in enumerate(COLUMNS):
            frame.columnconfigure(column, minsize=width)
        return frame

    def _stepper(self, parent, on_step, on_set, font, prefix=""):
        """− значення +: клік, утримання, коліщатко; подвійний клік по числу — ввести вручну."""
        frame = tk.Frame(parent, bg=BG)
        minus = FlatButton(frame, "−", lambda: on_step(-1), repeat=True, font=(FONT, 12), padx=6, pady=0,
                           fg=FAINT)
        minus.pack(side="left")
        value = self._label(frame, "—", TEXT, font, width=5, cursor="sb_h_double_arrow")
        value.pack(side="left")
        plus = FlatButton(frame, "+", lambda: on_step(1), repeat=True, font=(FONT, 12), padx=6, pady=0, fg=FAINT)
        plus.pack(side="left")

        def wheel(event):
            up = getattr(event, "delta", 0) > 0 or getattr(event, "num", None) == 4
            on_step((1 if up else -1) * (5 if event.state & 0x0001 else 1))
            return "break"

        for widget in (value, minus, plus):
            widget.bind("<MouseWheel>", wheel)
            widget.bind("<Button-4>", wheel)
            widget.bind("<Button-5>", wheel)
        value.bind("<Double-Button-1>", lambda _e: self._inline_edit(value, on_set, prefix))
        Tip(value, "Коліщатко — змінити, подвійний клік — ввести число")
        return {"frame": frame, "value": value, "buttons": (minus, plus)}

    def _inline_edit(self, label, on_set, prefix):
        current = label.cget("text").lstrip(prefix).strip()
        entry = tk.Entry(label.master, font=label.cget("font"), width=6, justify="center", bg=SURFACE, fg=TEXT,
                         insertbackground=TEXT, relief="flat", highlightthickness=1, highlightcolor=ACCENT,
                         highlightbackground=LINE)
        entry.insert(0, "" if current == "—" else current)
        entry.select_range(0, "end")
        entry.place(in_=label, relx=0.5, rely=0.5, anchor="center")
        entry.focus_set()
        done = []

        def finish(commit):
            if done:
                return
            done.append(True)
            text = entry.get().strip()
            entry.destroy()
            if commit and text:
                try:
                    on_set(int(float(text.replace(",", ".").lstrip("+−-"))))
                except ValueError:
                    self._log(f"«{text}» — не число", "warn")

        entry.bind("<Return>", lambda _e: finish(True))
        entry.bind("<KP_Enter>", lambda _e: finish(True))
        entry.bind("<Escape>", lambda _e: finish(False))
        entry.bind("<FocusOut>", lambda _e: finish(True))

    def _build_row(self, parent, index):
        row = {"index": index, "pending_trim": None, "pending_time": 0.0, "trim_job": None, "apply_job": None}
        frame = self._row_frame(parent)
        frame.configure(pady=8)
        row["frame"] = frame
        row["check"] = ImageButton(frame, {}, lambda: self._toggle_output(index),
                                   tip="Галочка — цей вихід керується програмою")
        row["check"].grid(row=0, column=0, sticky="w")
        names = tk.Frame(frame, bg=BG)
        names.grid(row=0, column=1, sticky="w")
        row["title"] = self._label(names, "", TEXT, self.font_strong)
        row["title"].pack(anchor="w")
        row["name"] = self._label(names, "", MUTED, (FONT, 8))
        row["below"] = self._stepper(frame, lambda d: self._offset_step(index, "below", d),
                                     lambda v: self._offset_set(index, "below", v), self.font_value, "−")
        row["below"]["frame"].grid(row=0, column=2)
        row["trim"] = self._stepper(frame, lambda d: self._trim_step(index, d),
                                    lambda v: self._trim_set(index, v), self.font_trim)
        row["trim"]["frame"].grid(row=0, column=3)
        row["above"] = self._stepper(frame, lambda d: self._offset_step(index, "above", d),
                                     lambda v: self._offset_set(index, "above", v), self.font_value, "+")
        row["above"]["frame"].grid(row=0, column=4)
        result = tk.Frame(frame, bg=BG)
        result.grid(row=0, column=5, sticky="e")
        row["result"] = self._label(result, "—", MUTED, self.font_strong, anchor="e")
        row["result"].pack(anchor="e")
        row["state"] = self._label(result, "", FAINT, (FONT, 8), anchor="e")
        return row

    @staticmethod
    def _set_state(row, text, color):
        row["state"].configure(text=text, fg=color)
        if text:
            row["state"].pack(anchor="e")
        else:
            row["state"].pack_forget()

    # ---- вікно й перемикачі ------------------------------------------------------------------
    def _apply_window_options(self):
        try:
            self.attributes("-topmost", bool(self.config_data["topmost"]))
        except tk.TclError:
            pass

    def _toggle_topmost(self):
        self.config_data["topmost"] = not self.config_data["topmost"]
        self._apply_window_options()
        self._update_controls()
        self._save_later()

    def _toggle_log(self):
        self.config_data["show_log"] = not self.config_data["show_log"]
        self._update_controls()
        self._save_later()

    def _toggle_auto(self):
        self.config_data["auto"] = not self.config_data["auto"]
        self._send_rules()
        if self.config_data["auto"]:
            self._log("Авто: MIN/MAX вибраних виходів стежать за TRIM", "ok")
            self.worker.commands.put(("apply_all", self._rules(), True))
        else:
            self._log("Авто вимкнено — застосування лише кнопкою")
        self._update_controls()
        self._refresh_values()
        self._save_later()

    def _set_step(self, step):
        self.config_data["step"] = step
        self._update_controls()
        self._save_later()

    def _update_controls(self):
        pin = self.config_data["topmost"]
        self.pin_button.set_images({"normal": self.img["pin_on" if pin else "pin_off"],
                                    "hover": self.img["pin_on" if pin else "pin_hover"]})
        auto = self.config_data["auto"]
        self.auto_switch.set_images({"normal": self.img["switch_on" if auto else "switch_off"]})
        self.auto_hint.configure(text="працює у фоні — стежить за TRIM у Mission Planner" if auto
                                 else "вимкнено — застосування лише кнопкою")
        for step, button in self.step_buttons.items():
            active = step == self.config_data["step"]
            button.set_style(bg=RAISED if active else BG, fg=TEXT if active else MUTED)
        if self.config_data["show_log"]:
            self.log_frame.pack(fill="both", expand=True)
        else:
            self.log_frame.pack_forget()
        if self.connected_request and self.link_ok:
            self.connect_button.configure(text="●  На зв'язку")
            self.connect_button.set_style(fg=OK, hover_fg=TEXT)
            self.connect_button.tip.text = f"{self.link_text}\nКлік — відключитися"
        elif self.connected_request:
            self.connect_button.configure(text="●  Шукаю…")
            self.connect_button.set_style(fg=WARN, hover_fg=TEXT)
            self.connect_button.tip.text = f"{self.link_text}\nКлік — зупинити пошук"
        else:
            self.connect_button.configure(text="Підключитися")
            self.connect_button.set_style(fg=ACCENT, hover_fg=TEXT)
            self.connect_button.tip.text = ("Програма бачить усе, що бачить Mission Planner —\n"
                                            "хоч як він підключений. Нічого вмикати не треба.")

    # ---- зміни з панелі ----------------------------------------------------------------------
    def _output(self, index):
        return self.config_data["outputs"][index]

    def _blocked(self):
        if self.config_data["only_disarmed"] and self.armed:
            self._log("Апарат заармлений — зміни заблоковано", "warn")
            return True
        return False

    def _current_trim(self, index):
        row = self.rows[index]
        if row["pending_trim"] is not None:
            return row["pending_trim"]
        value = self.params.get(f"SERVO{self._output(index)['channel']}_TRIM")
        return None if value is None else int(round(value))

    def _trim_step(self, index, direction):
        self._trim_set(index, None, direction * self.config_data["step"])

    def _trim_set(self, index, value, delta=0):
        if self._blocked():
            return
        if not self.link_ok:
            self._log("Немає зв'язку з політником — спершу «Підключитися»", "warn")
            return
        output = self._output(index)
        base = self._current_trim(index)
        if value is None:
            if base is None:
                self._log(f"SERVO{output['channel']}_TRIM ще не прочитано", "warn")
                return
            value = base + delta
        value = int(max(self.config_data["low"], min(self.config_data["high"], value)))
        row = self.rows[index]
        row["pending_trim"] = value
        row["pending_time"] = time.monotonic()
        if row["trim_job"]:
            self.after_cancel(row["trim_job"])
        # Записуємо, коли перестали клацати: одна зміна замість десятка.
        row["trim_job"] = self.after(450, lambda: self._commit_trim(index))
        self._refresh_values()

    def _commit_trim(self, index):
        row = self.rows[index]
        row["trim_job"] = None
        value = row["pending_trim"]
        if value is None:
            return
        output = self._output(index)
        # TRIM з панелі: MIN/MAX слідом — лише для виходів з галочкою.
        rules = self._output_rules(output) if output["enabled"] else []
        row["pending_time"] = time.monotonic()
        self.worker.commands.put(("write_trim", f"SERVO{output['channel']}_TRIM", value, rules))

    def _offset_step(self, index, key, direction):
        self._offset_set(index, key, self._output(index)[key] + direction * self.config_data["step"])

    def _offset_set(self, index, key, value):
        output = self._output(index)
        output[key] = int(max(0, min(1000, value)))
        self._send_rules()
        self._refresh_values()
        self._save_later()
        if not (output["enabled"] and self.config_data["auto"]):
            return  # без «Авто» нові відступи підуть у політник кнопкою «Застосувати»
        row = self.rows[index]
        if row["apply_job"]:
            self.after_cancel(row["apply_job"])
        row["apply_job"] = self.after(500, lambda: self._apply_output(index))

    def _apply_output(self, index):
        self.rows[index]["apply_job"] = None
        output = self._output(index)
        if self.link_ok and output["enabled"]:
            self.worker.commands.put(("apply_all", self._output_rules(output), True))

    def _toggle_output(self, index):
        output = self._output(index)
        output["enabled"] = not output["enabled"]
        self._send_rules()
        if output["enabled"] and self.config_data["auto"]:
            self._apply_output(index)
        self._refresh_values()
        self._save_later()

    def _apply_selected(self):
        if not self.link_ok:
            self._log("Немає зв'язку з політником — спершу «Підключитися»", "warn")
            return
        rules = self._rules()
        if not rules:
            self._log("Поставте галочку хоча б одному виходу", "warn")
            return
        self.worker.commands.put(("apply_all", rules))

    # ---- правила ------------------------------------------------------------------------------
    def _output_rules(self, output):
        return channel_rules(output["channel"], output["below"], output["above"],
                             self.config_data["low"], self.config_data["high"])

    def _rules(self):
        rules = []
        for output in self.config_data["outputs"]:
            if output["enabled"]:
                rules += self._output_rules(output)
        return rules

    def _send_rules(self):
        # Без «Авто» робітник не реагує на зміни з Mission Planner (кнопка й − / + працюють).
        rules = self._rules()
        if not self.config_data["auto"]:
            rules = [dict(rule, enabled=False) for rule in rules]
        self.worker.commands.put(("rules", rules, self.config_data["only_disarmed"]))

    def _save_later(self):
        if self.save_job:
            self.after_cancel(self.save_job)
        self.save_job = self.after(600, self._save_now)

    def _save_now(self):
        self.save_job = None
        save_config(self._collect_config())

    def _collect_config(self):
        try:
            if self.state() == "normal":
                self.config_data["geometry"] = f"+{self.winfo_x()}+{self.winfo_y()}"
        except tk.TclError:
            pass
        return self.config_data

    # ---- показ значень -----------------------------------------------------------------------
    def _schedule_refresh(self):
        if not self.refresh_job:
            self.refresh_job = self.after_idle(self._run_refresh)

    def _run_refresh(self):
        self.refresh_job = None
        self._refresh_values()

    def _refresh_values(self):
        low, high = self.config_data["low"], self.config_data["high"]
        selected = mismatched = 0
        for row in self.rows:
            index = row["index"]
            output = self._output(index)
            channel = output["channel"]
            enabled = output["enabled"]
            selected += bool(enabled)
            row["check"].set_images({"normal": self.img["check_on" if enabled else "check_off"],
                                     "hover": self.img["check_on" if enabled else "check_hover"]})
            row["title"].configure(text=f"SERVO{channel}", fg=TEXT if enabled else FAINT)
            name = output.get("name") or ""
            row["name"].configure(text=name, fg=MUTED if enabled else FAINT)
            if name:
                row["name"].pack(anchor="w")
            else:
                row["name"].pack_forget()
            trim_param = self.params.get(f"SERVO{channel}_TRIM")
            pending = row["pending_trim"] is not None
            if pending and trim_param is not None and abs(trim_param - row["pending_trim"]) < 0.5 \
                    and not row["trim_job"]:
                row["pending_trim"] = None
                pending = False
            trim = self._current_trim(index)
            value_color = TEXT if enabled else FAINT
            row["trim"]["value"].configure(text="—" if trim is None else str(trim),
                                           fg=WARN if pending else value_color)
            row["below"]["value"].configure(text=f"−{output['below']}", fg=value_color)
            row["above"]["value"].configure(text=f"+{output['above']}", fg=value_color)
            current_min = self.params.get(f"SERVO{channel}_MIN")
            current_max = self.params.get(f"SERVO{channel}_MAX")
            if current_min is None or current_max is None:
                row["result"].configure(text="—", fg=FAINT)
                self._set_state(row, "чекаю дані" if self.link_ok and enabled else "", FAINT)
                continue
            row["result"].configure(text=f"{current_min:g} – {current_max:g}")
            if trim is None:
                row["result"].configure(fg=MUTED)
                self._set_state(row, "", FAINT)
                continue
            expected_min = max(low, min(high, round(trim - output["below"])))
            expected_max = max(low, min(high, round(trim + output["above"])))
            ok = abs(current_min - expected_min) < 0.5 and abs(current_max - expected_max) < 0.5
            if not enabled:
                row["result"].configure(fg=FAINT)
                self._set_state(row, "не керується", FAINT)
            elif pending:
                row["result"].configure(fg=WARN)
                self._set_state(row, "записую…", WARN)
            elif ok:
                row["result"].configure(fg=OK)
                self._set_state(row, "✓ збігається", OK)
            else:
                mismatched += 1
                row["result"].configure(fg=WARN)
                self._set_state(row, f"треба {expected_min} – {expected_max}", WARN)
        if not self.link_ok:
            waiting = self.connected_request
            summary, color = (self.link_text, WARN) if waiting else ("Не підключено", FAINT)
            title = "чекаю на політник" if waiting else "не підключено"
        elif not selected:
            summary, color, title = "Жоден вихід не вибрано", MUTED, "нічого не вибрано"
        elif mismatched:
            summary, color = f"Не збігається: {mismatched}", WARN
            title = f"⚠ не збігається {mismatched}"
        else:
            summary, color, title = f"✓ Усе збігається · {selected} вих.", OK, "✓ усе збігається"
        self.summary.configure(text=summary, fg=color)
        self.title(f"{APP_NAME} — {title}")
        self.apply_button.set_enabled(self.link_ok and selected > 0)
        self.apply_button.configure(text=f"Застосувати до вибраних · {selected}" if selected else
                                    "Застосувати до вибраних")

    # ---- налаштування ------------------------------------------------------------------------
    def _connection_choices(self):
        choices = [source_title(source) for source in CONNECTION_PRESETS]
        try:
            from serial.tools import list_ports
            choices += [port.device for port in list_ports.comports()]
        except Exception:
            pass
        return choices

    def _open_settings(self):
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.lift()
            return
        window = tk.Toplevel(self)
        self.settings_window = window
        window.title("Налаштування")
        window.configure(bg=BG)
        window.resizable(False, False)
        window.transient(self)
        window.attributes("-topmost", True)
        body = tk.Frame(window, bg=BG, padx=20, pady=16)
        body.pack(fill="both", expand=True)

        def section(row, text):
            self._label(body, text, FAINT, (FONT, 8, "bold")).grid(row=row, column=0, columnspan=4, sticky="w",
                                                                    pady=(12 if row else 0, 6))

        def check(row, text, value):
            variable = tk.BooleanVar(value=value)
            button = ImageButton(body, {}, lambda: (variable.set(not variable.get()), paint()))

            def paint():
                on = variable.get()
                button.set_images({"normal": self.img["check_on" if on else "check_off"],
                                   "hover": self.img["check_on" if on else "check_hover"]})

            paint()
            button.grid(row=row, column=0, sticky="w", pady=3)
            if text:
                self._label(body, text, TEXT).grid(row=row, column=1, columnspan=3, sticky="w", padx=(8, 0))
            return variable

        section(0, "ПІДКЛЮЧЕННЯ")
        connection = tk.StringVar(value=source_title(self.config_data["connection"]))
        baud = tk.StringVar(value=self.config_data["baud"])
        self._label(body, "Адреса", MUTED).grid(row=1, column=0, columnspan=2, sticky="w", pady=3)
        ttk.Combobox(body, textvariable=connection, values=self._connection_choices(), width=40).grid(
            row=1, column=2, columnspan=2, sticky="ew", pady=3)
        self._label(body, "Швидкість (COM)", MUTED).grid(row=2, column=0, columnspan=2, sticky="w", pady=3)
        ttk.Combobox(body, textvariable=baud, values=BAUD_RATES, width=10).grid(row=2, column=2, sticky="w", pady=3)
        autoconnect = check(3, "Підключатися одразу при запуску", self.config_data.get("autoconnect", True))
        self._label(body, ("«Автоматично» — бере дані з Mission Planner, хоч як він підключений\n"
                           "до політника. Без Mission Planner виберіть COM-порт напряму."),
                    FAINT, (FONT, 8), justify="left").grid(row=4, column=0, columnspan=4, sticky="w", pady=(4, 0))

        section(5, "ВИХОДИ")
        self._label(body, "SERVO №", MUTED, (FONT, 8)).grid(row=6, column=1, sticky="w", padx=(8, 0))
        self._label(body, "Назва (необов'язково)", MUTED, (FONT, 8)).grid(row=6, column=2, columnspan=2,
                                                                         sticky="w")
        rows = []
        for index, output in enumerate(self.config_data["outputs"]):
            enabled = check(7 + index, "", output["enabled"])
            channel = tk.StringVar(value=str(output["channel"]))
            name = tk.StringVar(value=output.get("name", ""))
            ttk.Spinbox(body, from_=1, to=32, textvariable=channel, width=5).grid(
                row=7 + index, column=1, sticky="w", padx=(8, 8), pady=2)
            ttk.Entry(body, textvariable=name, width=22).grid(row=7 + index, column=2, columnspan=2, sticky="ew",
                                                              pady=2)
            rows.append((enabled, channel, name))

        section(13, "БЕЗПЕКА")
        limits = tk.Frame(body, bg=BG)
        limits.grid(row=14, column=0, columnspan=4, sticky="w", pady=3)
        low = tk.StringVar(value=str(self.config_data["low"]))
        high = tk.StringVar(value=str(self.config_data["high"]))
        self._label(limits, "MIN/MAX не виходять за", MUTED).pack(side="left")
        ttk.Entry(limits, textvariable=low, width=6).pack(side="left", padx=6)
        self._label(limits, "…", MUTED).pack(side="left")
        ttk.Entry(limits, textvariable=high, width=6).pack(side="left", padx=6)
        only_disarmed = check(15, "Змінювати лише коли апарат роззброєний", self.config_data["only_disarmed"])

        def save():
            try:
                low_value, high_value = int(low.get()), int(high.get())
                if not 500 <= low_value < high_value <= 2500:
                    raise ValueError("Межі PWM мають бути в 500…2500, і перша менша за другу")
                int(baud.get())
                outputs, seen = [], set()
                for (enabled_var, channel_var, name_var), old in zip(rows, self.config_data["outputs"]):
                    number = int(channel_var.get())
                    if not 1 <= number <= 32:
                        raise ValueError("Номер SERVO має бути 1…32")
                    if enabled_var.get():
                        if number in seen:
                            raise ValueError(f"SERVO{number} вказано двічі")
                        seen.add(number)
                    outputs.append(dict(old, enabled=enabled_var.get(), channel=number,
                                        name=name_var.get().strip()[:24]))
            except ValueError as exc:
                text = str(exc)
                messagebox.showerror(APP_NAME, "Введіть цілі числа" if text.startswith("invalid") else text,
                                     parent=window)
                return
            chosen = connection.get().strip()
            chosen = next((key for key, title in SOURCE_TITLES.items() if title == chosen), chosen) or AUTO_SOURCE
            reconnect = (chosen != self.config_data["connection"]
                         or baud.get().strip() != self.config_data["baud"])
            self.config_data.update({"connection": chosen, "baud": baud.get().strip(),
                                     "low": low_value, "high": high_value, "outputs": outputs,
                                     "only_disarmed": only_disarmed.get(), "autoconnect": autoconnect.get()})
            save_config(self._collect_config())
            self._send_rules()
            self._refresh_values()
            if reconnect and self.connected_request:
                self.worker.commands.put(("connect", self.config_data["connection"], self.config_data["baud"]))
            window.destroy()

        buttons = tk.Frame(body, bg=BG)
        buttons.grid(row=16, column=0, columnspan=4, sticky="e", pady=(16, 0))
        FlatButton(buttons, "Скасувати", window.destroy, padx=12, pady=6, bg=SURFACE).pack(side="left", padx=6)
        FlatButton(buttons, "Зберегти", save, padx=14, pady=6, bg=SURFACE, fg=ACCENT,
                   font=self.font_strong).pack(side="left")
        window.bind("<Escape>", lambda _e: window.destroy())
        window.bind("<Return>", lambda _e: save())
        self.update_idletasks()
        window.geometry(f"+{self.winfo_rootx() + 40}+{self.winfo_rooty() + 60}")

    # ---- підключення ---------------------------------------------------------------------
    def _toggle_connection(self):
        if self.connected_request:
            self.connected_request = False
            self.worker.commands.put(("disconnect",))
            self._update_controls()
            return
        config = self._collect_config()
        if not config["connection"]:
            self._open_settings()
            return
        try:
            int(config["baud"])
        except ValueError:
            self._open_settings()
            return
        self.connected_request = True
        self.worker.commands.put(("connect", config["connection"], config["baud"]))
        self._send_rules()
        self._update_controls()

    def _poll_events(self):
        refresh = False
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "log":
                    self._log(event[1], event[2])
                elif kind == "link":
                    self.link_ok, self.link_text = event[1], event[2]
                    if not event[1]:
                        self.armed_label.configure(text="")
                    self._update_controls()
                    refresh = True
                elif kind == "armed":
                    self.armed = event[1]
                    self.armed_label.configure(text="ЗААРМЛЕНИЙ" if event[1] else "")
                elif kind == "param":
                    self.params[event[1]] = event[2]
                    refresh = True
        except queue.Empty:
            pass
        now = time.monotonic()
        for row in self.rows:
            if row["pending_trim"] is not None and not row["trim_job"] and now - row["pending_time"] > 6:
                row["pending_trim"] = None
                refresh = True
        if refresh:
            self._refresh_values()
        self.after(100, self._poll_events)

    def _log(self, text, level="info"):
        self.last_log.configure(text=text, fg={"ok": OK, "warn": WARN, "error": ERR}.get(level, FAINT))
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"{time.strftime('%H:%M:%S')}  {text}\n", level)
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > 500:
            self.log_text.delete("1.0", f"{lines - 500}.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _close(self):
        save_config(self._collect_config())
        self.worker.commands.put(("quit",))
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
