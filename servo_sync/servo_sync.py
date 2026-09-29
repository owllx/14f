#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Servo Trim Sync — працює поруч із Mission Planner і виставляє MIN/MAX шести виходів SERVO від TRIM.

Типовий приклад: коли в Mission Planner змінюється SERVO7_TRIM (середнє положення),
програма сама виставляє SERVO7_MIN = TRIM − 400 і SERVO7_MAX = TRIM + 400.

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
BG = "#f4f6f9"
GREEN, ORANGE, RED = "#15803d", "#b45309", "#c62828"
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
    "outputs": [{"enabled": True, "channel": channel, "below": 400, "above": 400} for channel in range(7, 13)],
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
            self._apply_all()
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

    def _apply_all(self):
        if not self.link_ok:
            self.log("Немає зв'язку з політником", "error")
            return
        applied = 0
        for rule in self.rules:
            if not rule["enabled"]:
                continue
            source = self.params.get(rule["source"])
            if source is None and rule["mode"] == "offset":
                self.log(f"{rule['source']} ще не прочитано — пропускаю", "warn")
                continue
            applied += self._apply_rules([rule], source)
        if not applied:
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


class App(tk.Tk):
    """Проста таблиця на 6 виходів: номер SERVO, TRIM і відступи для MIN/MAX."""

    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} — ArduPilot")
        self.geometry("1000x640")
        self.minsize(980, 600)
        self.config_data = load_config()
        self.params = {}
        self.events = queue.Queue()
        self.worker = MavWorker(self.events)
        self.worker.start()
        self.connected_request = False
        self.link_ok = False
        self._style()
        self._build()
        self._send_rules()
        self.after(50, self._poll_events)
        self.protocol("WM_DELETE_WINDOW", self._close)

    # ---- вигляд ------------------------------------------------------------------------
    def _style(self):
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        self.configure(bg=BG)
        style.configure(".", background=BG, font=("Segoe UI", 10))
        style.configure("TLabelframe", background=BG)
        style.configure("TLabelframe.Label", foreground="#2563eb", font=("Segoe UI", 9, "bold"))
        style.configure("Head.TLabel", foreground="#6a7587", font=("Segoe UI", 9, "bold"))
        style.configure("Hint.TLabel", foreground="#6a7587", font=("Segoe UI", 9))
        style.configure("Accent.TButton", background="#2563eb", foreground="#ffffff",
                        font=("Segoe UI", 10, "bold"), padding=(12, 5))
        style.map("Accent.TButton", background=[("active", "#1d4fd8"), ("disabled", "#a8bff3")])
        style.configure("Trim.TEntry", padding=4)

    def _build(self):
        top = ttk.LabelFrame(self, text="Підключення", padding=10)
        top.pack(fill="x", padx=10, pady=(10, 4))
        self.armed_label = tk.Label(top, text="", font=("Segoe UI", 10, "bold"), bg=BG)
        self.armed_label.pack(side="right")
        self.link_label = tk.Label(top, text="● Не підключено", fg=RED, bg=BG, font=("Segoe UI", 10, "bold"))
        self.link_label.pack(side="right", padx=(0, 16))
        ttk.Label(top, text="Адреса").pack(side="left")
        self.connection_var = tk.StringVar(value=self.config_data["connection"])
        ttk.Combobox(top, textvariable=self.connection_var, width=22,
                     values=self._connection_choices()).pack(side="left", padx=(6, 10))
        ttk.Label(top, text="Швидкість").pack(side="left")
        self.baud_var = tk.StringVar(value=self.config_data["baud"])
        ttk.Combobox(top, textvariable=self.baud_var, values=BAUD_RATES, width=8).pack(side="left", padx=(6, 10))
        self.connect_button = ttk.Button(top, text="Підключитися", style="Accent.TButton", width=14,
                                         command=self._toggle_connection)
        self.connect_button.pack(side="left")
        ttk.Label(
            self,
            text=("Mission Planner: Ctrl+F → «Mavlink» → UDP Client, 127.0.0.1, порт 14551, "
                  "галочка «Write access» → Connect. Тут — адреса udpin:0.0.0.0:14551."),
            style="Hint.TLabel", wraplength=960,
        ).pack(fill="x", padx=14)

        table = ttk.LabelFrame(self, text="Виходи SERVO", padding=10)
        table.pack(fill="x", padx=10, pady=6)
        headers = ("", "Вихід", "TRIM (середнє)", "", "MIN = TRIM −", "MAX = TRIM +",
                   "MIN зараз", "MAX зараз", "")
        for column, text in enumerate(headers):
            ttk.Label(table, text=text, style="Head.TLabel").grid(row=0, column=column, padx=4, pady=(0, 4))
        self.rows = []
        for index, output in enumerate(self.config_data["outputs"]):
            self.rows.append(self._build_row(table, index + 1, output))

        options = ttk.Frame(self, padding=(14, 2))
        options.pack(fill="x")
        ttk.Label(options, text="Межі PWM:").pack(side="left")
        self.low_var = tk.StringVar(value=str(self.config_data["low"]))
        self.high_var = tk.StringVar(value=str(self.config_data["high"]))
        for variable in (self.low_var, self.high_var):
            entry = ttk.Entry(options, textvariable=variable, width=6)
            entry.pack(side="left", padx=4)
            entry.bind("<FocusOut>", lambda _e: self._settings_changed())
            entry.bind("<Return>", lambda _e: self._settings_changed())
        self.only_disarmed_var = tk.BooleanVar(value=self.config_data["only_disarmed"])
        ttk.Checkbutton(options, text="Змінювати лише коли апарат роззброєний",
                        variable=self.only_disarmed_var, command=self._settings_changed).pack(side="left", padx=16)
        self.apply_button = ttk.Button(options, text="Виставити MIN/MAX усім", style="Accent.TButton",
                                       command=self._apply_all)
        self.apply_button.pack(side="right")

        ttk.Label(
            self,
            text=("Змінили TRIM тут (Enter) або в Mission Planner — MIN і MAX виставляться самі. "
                  "Прошивку програма не змінює: лише параметри, як у Full Parameter List."),
            style="Hint.TLabel", wraplength=960,
        ).pack(fill="x", padx=14, pady=(2, 0))

        bottom = ttk.LabelFrame(self, text="Журнал", padding=6)
        bottom.pack(fill="both", expand=True, padx=10, pady=(6, 10))
        self.log_text = tk.Text(bottom, height=7, font=("Consolas", 9), relief="flat", state="disabled",
                                bg="#ffffff")
        self.log_text.pack(fill="both", expand=True)
        for level, color in (("ok", GREEN), ("warn", ORANGE), ("error", RED), ("info", "#1f2933")):
            self.log_text.tag_configure(level, foreground=color)

    def _build_row(self, parent, row, output):
        widgets = {}
        widgets["enabled"] = tk.BooleanVar(value=output["enabled"])
        ttk.Checkbutton(parent, variable=widgets["enabled"], command=self._settings_changed).grid(
            row=row, column=0, padx=4, pady=3)
        widgets["channel"] = tk.StringVar(value=str(output["channel"]))
        channel = ttk.Spinbox(parent, from_=1, to=32, width=5, textvariable=widgets["channel"],
                              command=self._settings_changed)
        channel.grid(row=row, column=1, padx=4, pady=3)
        channel.bind("<FocusOut>", lambda _e: self._settings_changed())
        channel.bind("<Return>", lambda _e: self._settings_changed())
        widgets["trim"] = tk.StringVar()
        trim = ttk.Entry(parent, textvariable=widgets["trim"], width=9, style="Trim.TEntry",
                         font=("Segoe UI", 11, "bold"))
        trim.grid(row=row, column=2, padx=4, pady=3)
        trim.bind("<Return>", lambda _e, w=widgets: self._write_trim(w))
        trim.bind("<Key>", lambda event, w=widgets: self._trim_edited(event, w), add="+")
        widgets["trim_entry"] = trim
        widgets["editing"] = False
        write = ttk.Button(parent, text="Записати", command=lambda w=widgets: self._write_trim(w))
        write.grid(row=row, column=3, padx=4, pady=3)
        for key, column in (("below", 4), ("above", 5)):
            widgets[key] = tk.StringVar(value=str(output[key]))
            spin = ttk.Spinbox(parent, from_=0, to=1000, increment=10, width=7, textvariable=widgets[key],
                               command=self._settings_changed)
            spin.grid(row=row, column=column, padx=4, pady=3)
            spin.bind("<FocusOut>", lambda _e: self._settings_changed())
            spin.bind("<Return>", lambda _e: self._settings_changed())
        for key, column in (("min_now", 6), ("max_now", 7)):
            widgets[key] = tk.Label(parent, text="—", width=7, bg=BG, font=("Segoe UI", 10, "bold"))
            widgets[key].grid(row=row, column=column, padx=4, pady=3)
        widgets["state"] = tk.Label(parent, text="", width=16, anchor="w", bg=BG, font=("Segoe UI", 9))
        widgets["state"].grid(row=row, column=8, padx=4, pady=3, sticky="w")
        return widgets

    def _connection_choices(self):
        choices = list(CONNECTION_PRESETS)
        try:
            from serial.tools import list_ports
            choices += [port.device for port in list_ports.comports()]
        except Exception:
            pass
        return choices

    # ---- налаштування та правила -----------------------------------------------------------
    def _read_outputs(self, report=False):
        """Прочитати таблицю; повертає список виходів або None, якщо є помилка."""
        outputs, seen = [], set()
        try:
            low, high = int(self.low_var.get()), int(self.high_var.get())
            if not 500 <= low < high <= 2500:
                raise ValueError("Межі PWM мають бути в 500…2500 і «від» менше «до»")
            for number, widgets in enumerate(self.rows, start=1):
                channel = int(widgets["channel"].get())
                if not 1 <= channel <= 32:
                    raise ValueError(f"Рядок {number}: номер виходу 1…32")
                below, above = int(widgets["below"].get()), int(widgets["above"].get())
                if below < 0 or above < 0:
                    raise ValueError(f"Рядок {number}: відступи не можуть бути від'ємними")
                enabled = widgets["enabled"].get()
                if enabled and channel in seen:
                    raise ValueError(f"SERVO{channel} вказано двічі")
                if enabled:
                    seen.add(channel)
                outputs.append({"enabled": enabled, "channel": channel, "below": below, "above": above})
        except ValueError as exc:
            if report:
                text = str(exc) if str(exc)[:1].isupper() or "SERVO" in str(exc) else "Введіть цілі числа"
                messagebox.showerror(APP_NAME, text)
            return None, None, None
        return outputs, low, high

    def _settings_changed(self):
        outputs, low, high = self._read_outputs()
        if outputs is None:
            return
        self.config_data.update({"outputs": outputs, "low": low, "high": high,
                                 "only_disarmed": self.only_disarmed_var.get()})
        save_config(self._collect_config())
        self._send_rules()
        self._refresh_values()

    def _rules(self):
        rules = []
        for output in self.config_data["outputs"]:
            if output["enabled"]:
                rules += channel_rules(output["channel"], output["below"], output["above"],
                                       self.config_data["low"], self.config_data["high"])
        return rules

    def _send_rules(self):
        self.worker.commands.put(("rules", self._rules(), self.config_data["only_disarmed"]))

    @staticmethod
    def _trim_edited(event, widgets):
        if event.keysym not in ("Return", "KP_Enter", "Tab"):
            widgets["editing"] = True
            widgets["trim_entry"].configure(foreground=ORANGE)

    def _write_trim(self, widgets):
        typed = widgets["trim"].get().strip()
        outputs, low, high = self._read_outputs(report=True)
        if outputs is None:
            return
        self._settings_changed()
        widgets["trim"].set(typed)
        channel = int(widgets["channel"].get())
        if not widgets["enabled"].get():
            messagebox.showinfo(APP_NAME, f"SERVO{channel} вимкнено в таблиці")
            return
        try:
            trim = int(widgets["trim"].get())
        except ValueError:
            messagebox.showerror(APP_NAME, "TRIM має бути цілим числом, напр. 1500")
            return
        if not low <= trim <= high:
            messagebox.showerror(APP_NAME, f"TRIM має бути в межах {low}…{high}")
            return
        if not self.link_ok:
            messagebox.showwarning(APP_NAME, "Немає зв'язку з політником")
            return
        rules = channel_rules(channel, int(widgets["below"].get()), int(widgets["above"].get()), low, high)
        self.worker.commands.put(("write_trim", f"SERVO{channel}_TRIM", trim, rules))
        widgets["editing"] = False
        widgets["trim_entry"].configure(foreground="#1f2933")
        self.focus_set()

    def _apply_all(self):
        if self._read_outputs(report=True)[0] is None:
            return
        self._settings_changed()
        self.worker.commands.put(("apply_all",))

    def _refresh_values(self):
        low, high = self.config_data["low"], self.config_data["high"]
        for widgets in self.rows:
            try:
                channel = int(widgets["channel"].get())
                below, above = int(widgets["below"].get()), int(widgets["above"].get())
            except ValueError:
                continue
            trim = self.params.get(f"SERVO{channel}_TRIM")
            current_min = self.params.get(f"SERVO{channel}_MIN")
            current_max = self.params.get(f"SERVO{channel}_MAX")
            if trim is not None and not widgets["editing"]:
                widgets["trim"].set(f"{trim:g}")
            enabled = widgets["enabled"].get()
            for key, value, expected in (
                ("min_now", current_min, None if trim is None else max(low, min(high, round(trim - below)))),
                ("max_now", current_max, None if trim is None else max(low, min(high, round(trim + above)))),
            ):
                ok = value is not None and expected is not None and abs(value - expected) < 0.5
                widgets[key].configure(text="—" if value is None else f"{value:g}",
                                       fg=GREEN if ok or not enabled else ORANGE)
            if not enabled:
                state, color = "вимкнено", "#6a7587"
            elif trim is None:
                state, color = "", "#6a7587"
            elif widgets["min_now"].cget("fg") == GREEN and widgets["max_now"].cget("fg") == GREEN:
                state, color = "✓ відповідає", GREEN
            else:
                state, color = "не відповідає", ORANGE
            widgets["state"].configure(text=state, fg=color)

    # ---- підключення ---------------------------------------------------------------------
    def _collect_config(self):
        self.config_data["connection"] = self.connection_var.get().strip()
        self.config_data["baud"] = self.baud_var.get().strip() or "57600"
        self.config_data["only_disarmed"] = self.only_disarmed_var.get()
        return self.config_data

    def _toggle_connection(self):
        if self.connected_request:
            self.connected_request = False
            self.worker.commands.put(("disconnect",))
            self.connect_button.configure(text="Підключитися")
            return
        config = self._collect_config()
        save_config(config)
        if not config["connection"]:
            messagebox.showerror(APP_NAME, "Вкажіть адресу підключення")
            return
        try:
            int(config["baud"])
        except ValueError:
            messagebox.showerror(APP_NAME, "Швидкість має бути числом")
            return
        self.connected_request = True
        self.connect_button.configure(text="Відключитися")
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
                    self.link_label.configure(text=f"● {event[2]}", fg=GREEN if event[1] else RED)
                    if not event[1]:
                        self.armed_label.configure(text="")
                elif kind == "armed":
                    self.armed_label.configure(text="ЗААРМЛЕНИЙ" if event[1] else "роззброєний",
                                               fg=RED if event[1] else GREEN)
                elif kind == "param":
                    self.params[event[1]] = event[2]
                    refresh = True
        except queue.Empty:
            pass
        if refresh:
            self._refresh_values()
        self.after(100, self._poll_events)

    def _log(self, text, level="info"):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"{time.strftime('%H:%M:%S')}  {text}\n", level)
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > 500:
            self.log_text.delete("1.0", f"{lines - 500}.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _close(self):
        self._settings_changed()
        save_config(self._collect_config())
        self.worker.commands.put(("quit",))
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
