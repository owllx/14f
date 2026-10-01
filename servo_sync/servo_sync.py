#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Servo Trim Sync — компактна панель поверх Mission Planner для шести виходів SERVO.

TRIM (середнє) і відступи «MIN = TRIM −» / «MAX = TRIM +» змінюються кнопками − / +,
коліщатком або введенням числа. Коли TRIM змінюється (тут чи в Mission Planner),
програма сама виставляє SERVOn_MIN = TRIM − відступ і SERVOn_MAX = TRIM + відступ.

Підключення: Mission Planner → Ctrl+F → «Mavlink» (MAVLink Mirror) → UDP Client,
127.0.0.1, порт 14551, галочка «Write access» → Connect. У програмі — «udpin:0.0.0.0:14551».
"""

import json
import os
import queue
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
CONNECTION_PRESETS = (
    "udpin:0.0.0.0:14551",
    "udpin:0.0.0.0:14550",
    "tcp:127.0.0.1:5760",
)
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
    "connection": CONNECTION_PRESETS[0],
    "baud": "57600",
    "only_disarmed": True,
    "low": 800,
    "high": 2200,
    "outputs": [{"enabled": True, "channel": channel, "below": 400, "above": 400, "name": ""}
                for channel in range(7, 13)],
    "auto": True,
    "step": 10,
    "topmost": True,
    "alpha": 1.0,
    "compact": False,
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
        return config
    except Exception:
        return json.loads(json.dumps(DEFAULT_CONFIG))


def save_config(config):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


class MavWorker(threading.Thread):
    """Весь обмін MAVLink в окремому потоці; з вікном спілкується через черги."""

    def __init__(self, events):
        super().__init__(daemon=True)
        self.events = events
        self.commands = queue.Queue()
        self.master = None
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
            if self.master is not None:
                try:
                    self._pump()
                except Exception as exc:  # обрив порту тощо
                    self.log(f"Помилка зв'язку: {exc}", "error")
                    self._close()

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

    def _connect(self, connection, baud):
        self._close()
        try:
            self.master = mavutil.mavlink_connection(
                connection, baud=int(baud), source_system=254, source_component=191,
                autoreconnect=True,
            )
        except Exception as exc:
            self.master = None
            self.log(f"Не вдалося відкрити «{connection}»: {exc}", "error")
            self.emit("link", False, "Не підключено")
            return
        self.params.clear()
        self.pending.clear()
        self.last_vehicle_heartbeat = 0.0
        self.link_ok = False
        self.log(f"Слухаю {connection} — чекаю на політник…")
        self.emit("link", False, "Чекаю на політник…")

    def _close(self):
        if self.master is not None:
            try:
                self.master.close()
            except Exception:
                pass
        self.master = None
        self.link_ok = False
        self.pending.clear()
        self.emit("link", False, "Не підключено")

    # ---- основний цикл -----------------------------------------------------------------
    def _pump(self):
        now = time.monotonic()
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
            self.link_ok = False
            self.emit("link", False, "Немає зв'язку з політником")
            self.log("Зв'язок з політником втрачено", "error")
        if self.link_ok and now - self.last_refresh > REFRESH_SOURCES_S:
            self._request_sources()
        self._retry_writes(now)

    def _on_message(self, message):
        kind = message.get_type()
        if kind == "HEARTBEAT":
            if (message.type == mavutil.mavlink.MAV_TYPE_GCS
                    or message.autopilot == mavutil.mavlink.MAV_AUTOPILOT_INVALID):
                return
            self.target = (message.get_srcSystem(), message.get_srcComponent())
            self.last_vehicle_heartbeat = time.monotonic()
            armed = bool(message.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
            if not self.link_ok:
                self.link_ok = True
                self.emit("link", True, f"Політник #{self.target[0]} на зв'язку")
                self.log(f"Підключено до політника #{self.target[0]}", "ok")
                self._request_sources()
            if armed != self.armed:
                self.armed = armed
                self.emit("armed", armed)
        elif kind == "PARAM_VALUE":
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
        if previous is None or abs(previous - value) < 1e-6:
            return  # перше читання або без змін — нічого не робимо
        rules = [rule for rule in self.rules if rule["enabled"] and rule["source"] == name]
        if rules:
            self.log(f"{name}: {previous:g} → {value:g}")
            self._apply_rules(rules, value)

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


# ---- Вигляд панелі (темна, щоб не сліпила поруч із Mission Planner) ----------------------
PANEL = "#0f1217"
CARD = "#181d25"
CARD_OFF = "#13171d"
BORDER = "#262d38"
TEXT = "#e8ecf2"
MUTED = "#8a93a3"
ACCENT = "#38bdf8"
ACCENT_DIM = "#123247"
OK = "#4ade80"
WARN = "#fbbf24"
ERR = "#f87171"
BTN = "#232a35"
BTN_HOVER = "#2e3746"
BTN_PRESS = "#3a4556"
FONT = "Segoe UI"
STEPS = (1, 5, 10, 25, 50)
ALPHAS = (1.0, 0.9, 0.78)


class Tip:
    """Підказка при наведенні."""

    def __init__(self, widget, text):
        self.widget, self.text, self.window, self.job = widget, text, None, None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        self.job = self.widget.after(550, self._show)

    def _show(self):
        if self.window or not self.text:
            return
        x = self.widget.winfo_rootx() + 8
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.window = tk.Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        self.window.attributes("-topmost", True)
        tk.Label(self.window, text=self.text, bg=TEXT, fg=PANEL, font=(FONT, 9), padx=8, pady=4,
                 justify="left").pack()
        self.window.geometry(f"+{x}+{y}")

    def _hide(self, _event=None):
        if self.job:
            self.widget.after_cancel(self.job)
            self.job = None
        if self.window:
            self.window.destroy()
            self.window = None


class FlatButton(tk.Label):
    """Пласка кнопка; repeat=True — при утриманні повторює дію (зручно для − / +)."""

    def __init__(self, parent, text, command, repeat=False, tip=None, bg=BTN, fg=TEXT, hover=BTN_HOVER,
                 font=(FONT, 10), padx=9, pady=2, width=0):
        super().__init__(parent, text=text, bg=bg, fg=fg, font=font, padx=padx, pady=pady, cursor="hand2",
                         width=width)
        self.command, self.repeat = command, repeat
        self.colors = [bg, hover]
        self.job = None
        self.inside = False
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        if tip:
            Tip(self, tip)

    def set_colors(self, bg, fg=None, hover=None):
        self.colors = [bg, hover or self.colors[1]]
        self.configure(bg=self.colors[1] if self.inside else bg, **({"fg": fg} if fg else {}))

    def _enter(self, _event):
        self.inside = True
        self.configure(bg=self.colors[1])

    def _leave(self, _event):
        self.inside = False
        self.configure(bg=self.colors[0])
        self._stop()

    def _press(self, _event):
        self.configure(bg=BTN_PRESS)
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
        self.configure(bg=self.colors[1] if inside else self.colors[0])
        if inside and not self.repeat:
            self.command()


class App(tk.Tk):
    """Компактна панель поверх Mission Planner: TRIM і відступи MIN/MAX кнопками − / +."""

    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.configure(bg=PANEL)
        self.resizable(False, False)
        self.config_data = load_config()
        self.params = {}
        self.events = queue.Queue()
        self.worker = MavWorker(self.events)
        self.worker.start()
        self.connected_request = False
        self.link_ok = False
        self.armed = False
        self.save_job = None
        self.cards = []
        self.settings_window = None
        self._style()
        self._build()
        self._apply_window_options()
        geometry = str(self.config_data.get("geometry") or "")
        if geometry.startswith("+"):
            self.geometry(geometry)
        self._send_rules()
        self._refresh_values()
        self.after(50, self._poll_events)
        self.protocol("WM_DELETE_WINDOW", self._close)

    # ---- вигляд ------------------------------------------------------------------------
    def _style(self):
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure(".", background=PANEL, foreground=TEXT, font=(FONT, 10))
        style.configure("TFrame", background=PANEL)
        style.configure("TLabel", background=PANEL, foreground=TEXT)
        style.configure("Muted.TLabel", background=PANEL, foreground=MUTED, font=(FONT, 9))
        style.configure("Head.TLabel", background=PANEL, foreground=ACCENT, font=(FONT, 9, "bold"))
        style.configure("TCheckbutton", background=PANEL, foreground=TEXT, indicatorbackground=CARD,
                        indicatorforeground=ACCENT)
        style.map("TCheckbutton", background=[("active", PANEL)])
        for name in ("TEntry", "TCombobox", "TSpinbox"):
            style.configure(name, fieldbackground=CARD, foreground=TEXT, background=BTN, bordercolor=BORDER,
                            lightcolor=CARD, darkcolor=CARD, arrowcolor=MUTED, insertcolor=TEXT, padding=3)
            style.map(name, fieldbackground=[("readonly", CARD)], foreground=[("readonly", TEXT)],
                      bordercolor=[("focus", ACCENT)])
        self.option_add("*TCombobox*Listbox.background", CARD)
        self.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT)

    def _label(self, parent, text="", fg=TEXT, font=(FONT, 10), bg=None, **kw):
        return tk.Label(parent, text=text, fg=fg, bg=bg or parent.cget("bg"), font=font, **kw)

    def _build(self):
        # ---- шапка: стан зв'язку + перемикачі вікна
        head = tk.Frame(self, bg=PANEL, padx=10, pady=8)
        head.pack(fill="x")
        self.link_label = self._label(head, "● Не підключено", ERR, (FONT, 10, "bold"))
        self.link_label.pack(side="left")
        self.link_tip = Tip(self.link_label, "")
        self.settings_button = FlatButton(head, "⚙", self._open_settings, tip="Підключення, канали, назви, межі PWM",
                                          font=(FONT, 11), padx=7)
        self.settings_button.pack(side="right", padx=(3, 0))
        self.compact_button = FlatButton(head, "▤", self._toggle_compact,
                                         tip="Компактно: лише TRIM / докладно: відступи та шкала", padx=7)
        self.compact_button.pack(side="right", padx=(3, 0))
        self.alpha_button = FlatButton(head, "100%", self._cycle_alpha, tip="Прозорість вікна (клік — змінити)",
                                       font=(FONT, 9, "bold"), padx=6)
        self.alpha_button.pack(side="right", padx=(3, 0))
        self.top_button = FlatButton(head, "Поверх", self._toggle_topmost,
                                     tip="Тримати панель поверх Mission Planner", font=(FONT, 9, "bold"))
        self.top_button.pack(side="right", padx=(3, 0))
        self.armed_label = self._label(head, "", MUTED, (FONT, 9, "bold"))
        self.armed_label.pack(side="right", padx=(0, 8))

        # ---- керування: підключення, крок, авто
        bar = tk.Frame(self, bg=PANEL, padx=10)
        bar.pack(fill="x", pady=(0, 6))
        self.connect_button = FlatButton(bar, "Підключитися", self._toggle_connection, bg=ACCENT, fg=PANEL,
                                         hover="#7dd3fc", font=(FONT, 10, "bold"), padx=12, pady=3,
                                         tip="Mission Planner: Ctrl+F → «Mavlink» → UDP Client, 127.0.0.1,\n"
                                             "порт 14551, галочка «Write access» → Connect")
        self.connect_button.pack(side="left")
        self._label(bar, "Крок", MUTED, (FONT, 9)).pack(side="left", padx=(12, 4))
        self.step_buttons = {}
        for step in STEPS:
            button = FlatButton(bar, str(step), lambda s=step: self._set_step(s), font=(FONT, 9, "bold"), padx=6,
                                tip="На скільки змінюють кнопки − / + і коліщатко\n(Shift + коліщатко — ×5)")
            button.pack(side="left", padx=1)
            self.step_buttons[step] = button
        self.auto_button = FlatButton(bar, "", self._toggle_auto, font=(FONT, 9, "bold"), padx=8,
                                      tip="Авто: змінили TRIM у Mission Planner — MIN/MAX\nвиставляються самі")
        self.auto_button.pack(side="right")

        # ---- картки виходів
        self.cards_frame = tk.Frame(self, bg=PANEL, padx=8)
        self.cards_frame.pack(fill="x")
        for index in range(6):
            self.cards.append(self._build_card(index))

        # ---- низ: остання подія + журнал
        foot = tk.Frame(self, bg=PANEL, padx=10, pady=6)
        foot.pack(fill="x")
        FlatButton(foot, "⟳ Всім", self._apply_all, font=(FONT, 9, "bold"), padx=8,
                   tip="Виставити MIN/MAX усім увімкненим виходам зараз").pack(side="right")
        self.log_button = FlatButton(foot, "Журнал", self._toggle_log, font=(FONT, 9), padx=8)
        self.log_button.pack(side="right", padx=(0, 4))
        self.last_log = self._label(foot, "Готово", MUTED, (FONT, 9), anchor="w", width=28)
        self.last_log.pack(side="left", fill="x", expand=True)
        self.log_frame = tk.Frame(self, bg=PANEL, padx=10)
        self.log_text = tk.Text(self.log_frame, height=7, width=1, font=("Consolas", 9), relief="flat",
                                state="disabled", bg=CARD, fg=TEXT, highlightthickness=0, padx=6, pady=4)
        self.log_text.pack(fill="both", expand=True, pady=(0, 10))
        for level, color in (("ok", OK), ("warn", WARN), ("error", ERR), ("info", TEXT)):
            self.log_text.tag_configure(level, foreground=color)
        self._update_header_buttons()

    def _stepper(self, parent, caption, on_step, on_set, big=False, prefix=""):
        """[−] значення [+]: клік, утримання, коліщатко; подвійний клік — ввести число."""
        frame = tk.Frame(parent, bg=parent.cget("bg"))
        caption_label = self._label(frame, caption, MUTED, (FONT, 8))
        caption_label.grid(row=0, column=0, columnspan=3)
        minus = FlatButton(frame, "−", lambda: on_step(-1), repeat=True, font=(FONT, 11, "bold"), padx=7)
        minus.grid(row=1, column=0)
        value = self._label(frame, "—", TEXT, (FONT, 14 if big else 11, "bold"), width=5 if big else 4,
                            cursor="sb_h_double_arrow")
        value.grid(row=1, column=1, padx=2)
        plus = FlatButton(frame, "+", lambda: on_step(1), repeat=True, font=(FONT, 11, "bold"), padx=7)
        plus.grid(row=1, column=2)

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
        return {"frame": frame, "value": value, "caption": caption_label, "prefix": prefix}

    def _inline_edit(self, label, on_set, prefix):
        current = label.cget("text").lstrip(prefix).strip()
        entry = tk.Entry(label.master, font=label.cget("font"), width=6, justify="center", bg=PANEL, fg=TEXT,
                         insertbackground=TEXT, relief="flat", highlightthickness=1, highlightcolor=ACCENT)
        entry.insert(0, "" if current == "—" else current)
        entry.select_range(0, "end")
        entry.place(in_=label, relx=0.5, rely=0.5, anchor="center")
        entry.focus_set()

        def finish(commit):
            text = entry.get().strip()
            entry.destroy()
            if commit:
                try:
                    on_set(int(float(text.replace(",", "."))))
                except ValueError:
                    self._log(f"«{text}» — не число", "warn")

        entry.bind("<Return>", lambda _e: finish(True))
        entry.bind("<KP_Enter>", lambda _e: finish(True))
        entry.bind("<Escape>", lambda _e: finish(False))
        entry.bind("<FocusOut>", lambda _e: finish(True) if entry.winfo_exists() else None)

    def _build_card(self, index):
        card = {"index": index, "pending_trim": None, "pending_time": 0.0, "trim_job": None, "apply_job": None}
        frame = tk.Frame(self.cards_frame, bg=CARD, highlightthickness=1, highlightbackground=BORDER,
                         padx=8, pady=5)
        frame.pack(fill="x", pady=3)
        card["frame"] = frame
        top = tk.Frame(frame, bg=CARD)
        top.pack(fill="x")
        card["top"] = top
        card["toggle"] = FlatButton(top, "", lambda: self._toggle_output(index), font=(FONT, 9, "bold"),
                                    padx=6, tip="Увімкнути / вимкнути цей вихід")
        card["toggle"].pack(side="left")
        card["title"] = self._label(top, "", TEXT, (FONT, 11, "bold"), bg=CARD)
        card["title"].pack(side="left", padx=(8, 4))
        card["name"] = self._label(top, "", MUTED, (FONT, 9), bg=CARD)
        card["name"].pack(side="left")
        card["status"] = self._label(top, "", MUTED, (FONT, 9, "bold"), bg=CARD)
        card["status"].pack(side="right")
        body = tk.Frame(frame, bg=CARD)
        card["body"] = body
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        body.columnconfigure(2, weight=1)
        card["below"] = self._stepper(body, "MIN = TRIM −", lambda d: self._offset_step(index, "below", d),
                                      lambda v: self._offset_set(index, "below", v), prefix="−")
        card["below"]["frame"].grid(row=0, column=0, sticky="w")
        card["trim"] = self._stepper(body, "TRIM (середнє)", lambda d: self._trim_step(index, d),
                                     lambda v: self._trim_set(index, v), big=True)
        card["trim"]["frame"].grid(row=0, column=1)
        card["above"] = self._stepper(body, "MAX = TRIM +", lambda d: self._offset_step(index, "above", d),
                                      lambda v: self._offset_set(index, "above", v), prefix="+")
        card["above"]["frame"].grid(row=0, column=2, sticky="e")
        card["bar"] = tk.Canvas(frame, height=24, bg=CARD, highlightthickness=0, width=400)
        card["bar"].bind("<Configure>", lambda _e: self._schedule_refresh())
        return card

    def _schedule_refresh(self):
        if not getattr(self, "refresh_job", None):
            self.refresh_job = self.after_idle(self._run_refresh)

    def _run_refresh(self):
        self.refresh_job = None
        self._refresh_values()

    # ---- вікно -----------------------------------------------------------------------------
    def _apply_window_options(self):
        try:
            self.attributes("-topmost", bool(self.config_data["topmost"]))
            self.attributes("-alpha", float(self.config_data["alpha"]))
        except tk.TclError:
            pass

    def _toggle_topmost(self):
        self.config_data["topmost"] = not self.config_data["topmost"]
        self._apply_window_options()
        self._update_header_buttons()
        self._save_later()

    def _cycle_alpha(self):
        current = float(self.config_data.get("alpha", 1.0))
        index = min(range(len(ALPHAS)), key=lambda i: abs(ALPHAS[i] - current))
        self.config_data["alpha"] = ALPHAS[(index + 1) % len(ALPHAS)]
        self._apply_window_options()
        self._update_header_buttons()
        self._save_later()

    def _toggle_compact(self):
        self.config_data["compact"] = not self.config_data["compact"]
        self._refresh_values()
        self._update_header_buttons()
        self._save_later()

    def _toggle_log(self):
        self.config_data["show_log"] = not self.config_data["show_log"]
        self._update_header_buttons()
        self._save_later()

    def _toggle_auto(self):
        self.config_data["auto"] = not self.config_data["auto"]
        self._send_rules()
        self._update_header_buttons()
        self._log("Авто увімкнено: зміни TRIM у Mission Planner підхоплюються" if self.config_data["auto"]
                  else "Авто вимкнено: MIN/MAX змінюються лише з цієї панелі")
        self._save_later()

    def _set_step(self, step):
        self.config_data["step"] = step
        self._update_header_buttons()
        self._save_later()

    def _update_header_buttons(self):
        self.alpha_button.configure(text=f"{round(float(self.config_data['alpha']) * 100)}%")
        on = self.config_data["topmost"]
        self.top_button.set_colors(ACCENT_DIM if on else BTN, ACCENT if on else MUTED)
        self.compact_button.set_colors(ACCENT_DIM if self.config_data["compact"] else BTN)
        for step, button in self.step_buttons.items():
            active = step == self.config_data["step"]
            button.set_colors(ACCENT if active else BTN, PANEL if active else TEXT,
                              "#7dd3fc" if active else BTN_HOVER)
        auto = self.config_data["auto"]
        self.auto_button.configure(text="● Авто" if auto else "○ Авто")
        self.auto_button.set_colors(ACCENT_DIM if auto else BTN, OK if auto else MUTED)
        if self.config_data["show_log"]:
            self.log_frame.pack(fill="both", expand=True)
            self.log_button.set_colors(ACCENT_DIM)
        else:
            self.log_frame.pack_forget()
            self.log_button.set_colors(BTN)

    # ---- зміни з панелі ----------------------------------------------------------------------
    def _output(self, index):
        return self.config_data["outputs"][index]

    def _blocked(self):
        if self.config_data["only_disarmed"] and self.armed:
            self._log("Апарат заармлений — зміни заблоковано (див. ⚙)", "warn")
            return True
        return False

    def _current_trim(self, index):
        card = self.cards[index]
        if card["pending_trim"] is not None:
            return card["pending_trim"]
        value = self.params.get(f"SERVO{self._output(index)['channel']}_TRIM")
        return None if value is None else int(round(value))

    def _trim_step(self, index, direction):
        self._trim_set(index, None, direction * self.config_data["step"])

    def _trim_set(self, index, value, delta=0):
        output = self._output(index)
        if not output["enabled"] or self._blocked():
            return
        if not self.link_ok:
            self._log("Немає зв'язку з політником — спершу «Підключитися»", "warn")
            return
        base = self._current_trim(index)
        if value is None:
            if base is None:
                self._log(f"SERVO{output['channel']}_TRIM ще не прочитано", "warn")
                return
            value = base + delta
        low, high = self.config_data["low"], self.config_data["high"]
        value = int(max(low, min(high, value)))
        card = self.cards[index]
        card["pending_trim"] = value
        card["pending_time"] = time.monotonic()
        if card["trim_job"]:
            self.after_cancel(card["trim_job"])
        # Записуємо, коли перестали клацати, — одна зміна замість десятка.
        card["trim_job"] = self.after(450, lambda: self._commit_trim(index))
        self._refresh_values()

    def _commit_trim(self, index):
        card = self.cards[index]
        card["trim_job"] = None
        value = card["pending_trim"]
        if value is None:
            return
        output = self._output(index)
        rules = channel_rules(output["channel"], output["below"], output["above"],
                              self.config_data["low"], self.config_data["high"])
        card["pending_time"] = time.monotonic()
        self.worker.commands.put(("write_trim", f"SERVO{output['channel']}_TRIM", value, rules))

    def _offset_step(self, index, key, direction):
        self._offset_set(index, key, self._output(index)[key] + direction * self.config_data["step"])

    def _offset_set(self, index, key, value):
        output = self._output(index)
        if not output["enabled"]:
            return
        output[key] = int(max(0, min(1000, value)))
        self._send_rules()
        self._refresh_values()
        self._save_later()
        card = self.cards[index]
        if card["apply_job"]:
            self.after_cancel(card["apply_job"])
        card["apply_job"] = self.after(500, lambda: self._apply_output(index))

    def _apply_output(self, index):
        self.cards[index]["apply_job"] = None
        output = self._output(index)
        if not self.link_ok or not output["enabled"]:
            return
        rules = channel_rules(output["channel"], output["below"], output["above"],
                              self.config_data["low"], self.config_data["high"])
        self.worker.commands.put(("apply_all", rules, True))

    def _toggle_output(self, index):
        output = self._output(index)
        output["enabled"] = not output["enabled"]
        self._send_rules()
        self._refresh_values()
        self._save_later()

    def _apply_all(self):
        self._send_rules()
        self.worker.commands.put(("apply_all", self._rules()))

    # ---- правила ------------------------------------------------------------------------------
    def _rules(self):
        rules = []
        for output in self.config_data["outputs"]:
            if output["enabled"]:
                rules += channel_rules(output["channel"], output["below"], output["above"],
                                       self.config_data["low"], self.config_data["high"])
        return rules

    def _send_rules(self):
        # Без «Авто» правила не реагують на зміни з Mission Planner, але «⟳ Всім» і кнопки панелі працюють.
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
            self.config_data["geometry"] = f"+{self.winfo_x()}+{self.winfo_y()}"
        except tk.TclError:
            pass
        return self.config_data

    # ---- показ значень -----------------------------------------------------------------------
    def _refresh_values(self):
        low, high = self.config_data["low"], self.config_data["high"]
        compact = self.config_data["compact"]
        for card in self.cards:
            index = card["index"]
            output = self._output(index)
            channel = output["channel"]
            enabled = output["enabled"]
            card["title"].configure(text=f"SERVO{channel}", fg=TEXT if enabled else MUTED)
            card["name"].configure(text=output.get("name") or "")
            card["toggle"].configure(text="ON" if enabled else "OFF")
            card["toggle"].set_colors(ACCENT_DIM if enabled else BTN, OK if enabled else MUTED)
            bg = CARD if enabled else CARD_OFF
            for widget in (card["frame"], card["top"], card["title"], card["name"], card["status"]):
                widget.configure(bg=bg)
            if not enabled:
                card["body"].pack_forget()
                card["bar"].pack_forget()
                card["status"].configure(text="вимкнено", fg=MUTED)
                continue
            card["body"].pack(fill="x", pady=(4, 0))
            for key in ("below", "trim", "above"):
                if compact:
                    card[key]["caption"].grid_remove()
                else:
                    card[key]["caption"].grid()
            if compact:
                card["bar"].pack_forget()
            else:
                card["bar"].pack(fill="x", pady=(4, 0))
            trim_param = self.params.get(f"SERVO{channel}_TRIM")
            trim = self._current_trim(index)
            pending = card["pending_trim"] is not None
            if pending and trim_param is not None and abs(trim_param - card["pending_trim"]) < 0.5 \
                    and not card["trim_job"]:
                card["pending_trim"] = None
                pending = False
            card["trim"]["value"].configure(text="—" if trim is None else str(trim), fg=WARN if pending else TEXT)
            card["below"]["value"].configure(text=f"−{output['below']}")
            card["above"]["value"].configure(text=f"+{output['above']}")
            current_min = self.params.get(f"SERVO{channel}_MIN")
            current_max = self.params.get(f"SERVO{channel}_MAX")
            expected_min = expected_max = None
            if trim is not None:
                expected_min = max(low, min(high, round(trim - output["below"])))
                expected_max = max(low, min(high, round(trim + output["above"])))
            ok_min = current_min is not None and expected_min is not None and abs(current_min - expected_min) < 0.5
            ok_max = current_max is not None and expected_max is not None and abs(current_max - expected_max) < 0.5
            if pending:
                status, color = "✎ запис…", WARN
            elif trim is None:
                status, color = "чекаю дані" if self.link_ok else "", MUTED
            elif ok_min and ok_max:
                status, color = f"✓ {current_min:g} … {current_max:g}", OK
            else:
                status, color = "⚠ MIN/MAX не відповідають", WARN
            card["status"].configure(text=status, fg=color)
            if not compact:
                self._draw_bar(card, trim, expected_min, expected_max, current_min, current_max, ok_min, ok_max)

    def _draw_bar(self, card, trim, expected_min, expected_max, current_min, current_max, ok_min, ok_max):
        canvas = card["bar"]
        canvas.delete("all")
        width = max(200, canvas.winfo_width())
        low, high = self.config_data["low"], self.config_data["high"]
        pad = 18

        def x(value):
            return pad + (width - 2 * pad) * (float(value) - low) / max(1, high - low)

        y = 7
        canvas.create_line(x(low), y, x(high), y, fill=BORDER, width=4, capstyle="round")
        if expected_min is not None and expected_max is not None:
            canvas.create_line(x(expected_min), y, x(expected_max), y, fill=ACCENT_DIM, width=8, capstyle="round")
        for value, ok in ((current_min, ok_min), (current_max, ok_max)):
            if value is not None:
                canvas.create_line(x(value), y - 6, x(value), y + 6, fill=OK if ok else WARN, width=2)
                canvas.create_text(x(value), y + 13, text=f"{value:g}", fill=OK if ok else WARN, font=(FONT, 8))
        if trim is not None:
            canvas.create_polygon(x(trim) - 5, y - 7, x(trim) + 5, y - 7, x(trim), y - 1, fill=TEXT, outline="")
            canvas.create_line(x(trim), y - 2, x(trim), y + 5, fill=TEXT, width=2)

    # ---- налаштування ------------------------------------------------------------------------
    def _connection_choices(self):
        choices = list(CONNECTION_PRESETS)
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
        window.configure(bg=PANEL)
        window.resizable(False, False)
        window.transient(self)
        window.attributes("-topmost", True)
        body = ttk.Frame(window, padding=14)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="ПІДКЛЮЧЕННЯ", style="Head.TLabel").grid(row=0, column=0, columnspan=4, sticky="w")
        connection = tk.StringVar(value=self.config_data["connection"])
        baud = tk.StringVar(value=self.config_data["baud"])
        ttk.Label(body, text="Адреса").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Combobox(body, textvariable=connection, values=self._connection_choices(), width=24).grid(
            row=1, column=1, columnspan=2, sticky="ew", pady=3)
        ttk.Label(body, text="Швидкість").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Combobox(body, textvariable=baud, values=BAUD_RATES, width=10).grid(row=2, column=1, sticky="w", pady=3)
        ttk.Label(body, text=("Mission Planner: Ctrl+F → «Mavlink» → UDP Client, 127.0.0.1,\n"
                              "порт 14551, галочка «Write access» → Connect.\n"
                              "Тут адреса: udpin:0.0.0.0:14551"),
                  style="Muted.TLabel", justify="left").grid(row=3, column=0, columnspan=4, sticky="w", pady=(2, 10))

        ttk.Label(body, text="ВИХОДИ", style="Head.TLabel").grid(row=4, column=0, columnspan=4, sticky="w")
        ttk.Label(body, text="SERVO №", style="Muted.TLabel").grid(row=5, column=1, sticky="w")
        ttk.Label(body, text="Назва (необов'язково)", style="Muted.TLabel").grid(row=5, column=2, sticky="w")
        rows = []
        for index, output in enumerate(self.config_data["outputs"]):
            enabled = tk.BooleanVar(value=output["enabled"])
            channel = tk.StringVar(value=str(output["channel"]))
            name = tk.StringVar(value=output.get("name", ""))
            ttk.Checkbutton(body, variable=enabled).grid(row=6 + index, column=0, sticky="w")
            ttk.Spinbox(body, from_=1, to=32, textvariable=channel, width=5).grid(row=6 + index, column=1,
                                                                                    sticky="w", pady=2)
            ttk.Entry(body, textvariable=name, width=20).grid(row=6 + index, column=2, sticky="ew", pady=2)
            rows.append((enabled, channel, name))

        ttk.Label(body, text="БЕЗПЕКА", style="Head.TLabel").grid(row=12, column=0, columnspan=4, sticky="w",
                                                                   pady=(10, 0))
        limits = ttk.Frame(body)
        limits.grid(row=13, column=0, columnspan=4, sticky="w", pady=3)
        low = tk.StringVar(value=str(self.config_data["low"]))
        high = tk.StringVar(value=str(self.config_data["high"]))
        ttk.Label(limits, text="MIN/MAX не виходять за").pack(side="left")
        ttk.Entry(limits, textvariable=low, width=6).pack(side="left", padx=4)
        ttk.Label(limits, text="…").pack(side="left")
        ttk.Entry(limits, textvariable=high, width=6).pack(side="left", padx=4)
        only_disarmed = tk.BooleanVar(value=self.config_data["only_disarmed"])
        ttk.Checkbutton(body, text="Змінювати лише коли апарат роззброєний", variable=only_disarmed).grid(
            row=14, column=0, columnspan=4, sticky="w", pady=3)

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
                messagebox.showerror(APP_NAME, text if not text.startswith("invalid") else "Введіть цілі числа",
                                     parent=window)
                return
            reconnect = (connection.get().strip() != self.config_data["connection"]
                         or baud.get().strip() != self.config_data["baud"])
            self.config_data.update({"connection": connection.get().strip(), "baud": baud.get().strip(),
                                     "low": low_value, "high": high_value, "outputs": outputs,
                                     "only_disarmed": only_disarmed.get()})
            save_config(self._collect_config())
            self._send_rules()
            self._refresh_values()
            if reconnect and self.connected_request:
                self.worker.commands.put(("connect", self.config_data["connection"], self.config_data["baud"]))
            window.destroy()

        buttons = ttk.Frame(body)
        buttons.grid(row=15, column=0, columnspan=4, sticky="e", pady=(12, 0))
        FlatButton(buttons, "Скасувати", window.destroy, padx=10, pady=3).pack(side="left", padx=4)
        FlatButton(buttons, "Зберегти", save, bg=ACCENT, fg=PANEL, hover="#7dd3fc", font=(FONT, 10, "bold"),
                   padx=12, pady=3).pack(side="left")
        window.bind("<Escape>", lambda _e: window.destroy())
        window.bind("<Return>", lambda _e: save())
        self.update_idletasks()
        window.geometry(f"+{self.winfo_rootx() + 20}+{self.winfo_rooty() + 40}")

    # ---- підключення ---------------------------------------------------------------------
    def _toggle_connection(self):
        if self.connected_request:
            self.connected_request = False
            self.worker.commands.put(("disconnect",))
            self.connect_button.configure(text="Підключитися")
            self.connect_button.set_colors(ACCENT, PANEL, "#7dd3fc")
            return
        config = self._collect_config()
        save_config(config)
        if not config["connection"]:
            messagebox.showerror(APP_NAME, "Вкажіть адресу підключення (⚙)")
            return
        try:
            int(config["baud"])
        except ValueError:
            messagebox.showerror(APP_NAME, "Швидкість має бути числом (⚙)")
            return
        self.connected_request = True
        self.connect_button.configure(text="Відключитися")
        self.connect_button.set_colors(BTN, TEXT, BTN_HOVER)
        self.worker.commands.put(("connect", config["connection"], config["baud"]))
        self._send_rules()

    def _poll_events(self):
        refresh = False
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "log":
                    self._log(event[1], event[2])
                elif kind == "link":
                    self.link_ok = event[1]
                    self.link_label.configure(text="● На зв'язку" if event[1] else f"● {event[2]}",
                                              fg=OK if event[1] else ERR)
                    self.link_tip.text = event[2]
                    if not event[1]:
                        self.armed_label.configure(text="")
                    refresh = True
                elif kind == "armed":
                    self.armed = event[1]
                    self.armed_label.configure(text="ЗААРМЛЕНИЙ" if event[1] else "", fg=ERR)
                elif kind == "param":
                    self.params[event[1]] = event[2]
                    refresh = True
        except queue.Empty:
            pass
        now = time.monotonic()
        for card in self.cards:
            if card["pending_trim"] is not None and not card["trim_job"] and now - card["pending_time"] > 6:
                card["pending_trim"] = None
                refresh = True
        if refresh:
            self._refresh_values()
        self.after(100, self._poll_events)

    def _log(self, text, level="info"):
        self.last_log.configure(text=text, fg={"ok": OK, "warn": WARN, "error": ERR}.get(level, MUTED))
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
