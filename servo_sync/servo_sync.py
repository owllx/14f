#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Servo Trim Sync — працює поруч із Mission Planner і за правилами змінює параметри ArduPilot.

Типовий приклад: коли в Mission Planner змінюється SERVO7_TRIM (середнє положення),
програма сама виставляє SERVO7_MIN = TRIM − 400 і SERVO7_MAX = TRIM + 400.

Підключення: Mission Planner → Ctrl+F → «Mavlink» (MAVLink Mirror) → UDP Client,
127.0.0.1, порт 14551, галочка «Write access» → Connect. У програмі — «udpin:0.0.0.0:14551».
"""

import json
import os
import queue
import re
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
PARAM_NAME_RE = re.compile(r"^[A-Z0-9_]{1,16}$")
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

MODES = {
    "offset": "Джерело + число",
    "fixed": "Фіксоване значення",
}


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
    "rules": channel_rules(7, 400, 400) + channel_rules(8, 400, 400),
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


def rule_text(rule):
    if rule["mode"] == "fixed":
        how = f"= {float(rule['value']):g}"
    else:
        value = float(rule["value"])
        sign = "+" if value >= 0 else "−"
        how = f"= {rule['source']} {sign} {abs(value):g}"
    return how


def load_config():
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        config = dict(DEFAULT_CONFIG)
        config.update({key: data[key] for key in DEFAULT_CONFIG if key in data})
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


class RuleDialog(tk.Toplevel):
    """Створення або зміна одного правила."""

    def __init__(self, parent, rule=None):
        super().__init__(parent)
        self.title("Правило")
        self.transient(parent)
        self.resizable(False, False)
        self.result = None
        rule = rule or {"enabled": True, "source": "SERVO7_TRIM", "target": "SERVO7_MIN",
                        "mode": "offset", "value": -400, "low": 800, "high": 2200}
        frame = ttk.Frame(self, padding=14)
        frame.pack(fill="both", expand=True)
        self.source = tk.StringVar(value=rule["source"])
        self.target = tk.StringVar(value=rule["target"])
        self.mode = tk.StringVar(value=rule["mode"])
        self.value = tk.StringVar(value=f"{float(rule['value']):g}")
        self.low = tk.StringVar(value="" if rule.get("low") in (None, "") else f"{float(rule['low']):g}")
        self.high = tk.StringVar(value="" if rule.get("high") in (None, "") else f"{float(rule['high']):g}")
        self.enabled = tk.BooleanVar(value=rule["enabled"])
        rows = (
            ("Коли змінюється параметр", ttk.Entry(frame, textvariable=self.source, width=22)),
            ("Змінювати параметр", ttk.Entry(frame, textvariable=self.target, width=22)),
        )
        for row, (label, widget) in enumerate(rows):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=4, padx=(0, 10))
            widget.grid(row=row, column=1, sticky="ew", pady=4)
        modes = ttk.Frame(frame)
        modes.grid(row=2, column=1, sticky="w", pady=4)
        ttk.Label(frame, text="Як рахувати").grid(row=2, column=0, sticky="w", pady=4)
        for key, title in MODES.items():
            ttk.Radiobutton(modes, text=title, value=key, variable=self.mode).pack(anchor="w")
        ttk.Label(frame, text="Число (напр. -400 або 400)").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=self.value, width=10).grid(row=3, column=1, sticky="w", pady=4)
        limits = ttk.Frame(frame)
        limits.grid(row=4, column=1, sticky="w", pady=4)
        ttk.Label(frame, text="Не менше / не більше").grid(row=4, column=0, sticky="w", pady=4)
        ttk.Entry(limits, textvariable=self.low, width=8).pack(side="left")
        ttk.Label(limits, text=" … ").pack(side="left")
        ttk.Entry(limits, textvariable=self.high, width=8).pack(side="left")
        ttk.Checkbutton(frame, text="Правило увімкнене", variable=self.enabled).grid(
            row=5, column=1, sticky="w", pady=(6, 0)
        )
        buttons = ttk.Frame(frame)
        buttons.grid(row=6, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(buttons, text="Скасувати", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Зберегти", command=self._save).pack(side="right", padx=(0, 6))
        self.bind("<Return>", lambda _e: self._save())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.grab_set()

    def _save(self):
        source = self.source.get().strip().upper()
        target = self.target.get().strip().upper()
        try:
            for name in (source, target):
                if not PARAM_NAME_RE.match(name):
                    raise ValueError(f"Некоректна назва параметра: «{name}» (A–Z, 0–9, _, до 16 символів)")
            if source == target:
                raise ValueError("Параметр не може змінювати сам себе")
            value = float(self.value.get().replace(",", "."))
            low = float(self.low.get().replace(",", ".")) if self.low.get().strip() else None
            high = float(self.high.get().replace(",", ".")) if self.high.get().strip() else None
            if low is not None and high is not None and low > high:
                raise ValueError("«Не менше» більше за «не більше»")
        except ValueError as exc:
            messagebox.showerror("Правило", str(exc), parent=self)
            return
        self.result = {"enabled": self.enabled.get(), "source": source, "target": target,
                       "mode": self.mode.get(), "value": value, "low": low, "high": high}
        self.destroy()


class ChannelDialog(tk.Toplevel):
    """Швидке додавання «MIN/MAX від TRIM» для каналу SERVOn."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Додати канал")
        self.transient(parent)
        self.resizable(False, False)
        self.result = None
        frame = ttk.Frame(self, padding=14)
        frame.pack(fill="both", expand=True)
        self.channel = tk.StringVar(value="7")
        self.below = tk.StringVar(value="400")
        self.above = tk.StringVar(value="400")
        self.low = tk.StringVar(value="800")
        self.high = tk.StringVar(value="2200")
        fields = (
            ("Вихід SERVO №", self.channel),
            ("MIN = TRIM −", self.below),
            ("MAX = TRIM +", self.above),
            ("Межі PWM від", self.low),
            ("до", self.high),
        )
        for row, (label, variable) in enumerate(fields):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=4, padx=(0, 10))
            ttk.Entry(frame, textvariable=variable, width=10).grid(row=row, column=1, sticky="w", pady=4)
        buttons = ttk.Frame(frame)
        buttons.grid(row=len(fields), column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(buttons, text="Скасувати", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Додати", command=self._save).pack(side="right", padx=(0, 6))
        self.bind("<Return>", lambda _e: self._save())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.grab_set()

    def _save(self):
        try:
            channel = int(self.channel.get())
            if not 1 <= channel <= 32:
                raise ValueError("Номер виходу — від 1 до 32")
            below, above = abs(int(self.below.get())), abs(int(self.above.get()))
            low, high = int(self.low.get()), int(self.high.get())
            if low >= high:
                raise ValueError("Неправильні межі PWM")
        except ValueError as exc:
            messagebox.showerror("Канал", str(exc) if str(exc)[0].isupper() else "Введіть цілі числа",
                                 parent=self)
            return
        self.result = channel_rules(channel, below, above, low, high)
        self.destroy()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} — ArduPilot")
        self.geometry("980x640")
        self.minsize(820, 520)
        self.config_data = load_config()
        self.params = {}
        self.events = queue.Queue()
        self.worker = MavWorker(self.events)
        self.worker.start()
        self.connected_request = False
        self._style()
        self._build()
        self._refresh_rules()
        self._send_rules()
        self.after(50, self._poll_events)
        self.protocol("WM_DELETE_WINDOW", self._close)

    # ---- вигляд ------------------------------------------------------------------------
    def _style(self):
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        self.configure(bg="#f4f6f9")
        style.configure(".", background="#f4f6f9", font=("Segoe UI", 10))
        style.configure("TLabelframe", background="#f4f6f9")
        style.configure("TLabelframe.Label", foreground="#2563eb", font=("Segoe UI", 9, "bold"))
        style.configure("Accent.TButton", background="#2563eb", foreground="#ffffff",
                        font=("Segoe UI", 10, "bold"), padding=(12, 5))
        style.map("Accent.TButton", background=[("active", "#1d4fd8"), ("disabled", "#a8bff3")])
        style.configure("Treeview", rowheight=26, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
        style.configure("Hint.TLabel", foreground="#6a7587", font=("Segoe UI", 9))

    def _build(self):
        top = ttk.LabelFrame(self, text="Підключення", padding=10)
        top.pack(fill="x", padx=10, pady=(10, 6))
        self.armed_label = tk.Label(top, text="", font=("Segoe UI", 10, "bold"), bg="#f4f6f9")
        self.armed_label.pack(side="right")
        self.link_label = tk.Label(top, text="● Не підключено", fg="#c62828", bg="#f4f6f9",
                                   font=("Segoe UI", 10, "bold"))
        self.link_label.pack(side="right", padx=(0, 16))
        ttk.Label(top, text="Адреса").pack(side="left")
        self.connection_var = tk.StringVar(value=self.config_data["connection"])
        connection = ttk.Combobox(top, textvariable=self.connection_var, width=24,
                                  values=self._connection_choices())
        connection.pack(side="left", padx=(6, 10))
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
            style="Hint.TLabel",
        ).pack(fill="x", padx=14)

        middle = ttk.LabelFrame(self, text="Правила: коли змінюється одне — змінювати інше", padding=10)
        middle.pack(fill="both", expand=True, padx=10, pady=6)
        buttons = ttk.Frame(middle, padding=(10, 0, 0, 0))
        buttons.pack(side="right", fill="y")
        for text, command in (
            ("+ Канал MIN/MAX", self._add_channel),
            ("+ Правило", self._add_rule),
            ("Змінити", self._edit_rule),
            ("Увімк. / вимк.", self._toggle_rule),
            ("Видалити", self._delete_rule),
        ):
            ttk.Button(buttons, text=text, command=command).pack(fill="x", pady=2)
        ttk.Separator(buttons).pack(fill="x", pady=8)
        self.apply_button = ttk.Button(buttons, text="Застосувати зараз", style="Accent.TButton",
                                       command=self._apply_now)
        self.apply_button.pack(fill="x", pady=2)
        self.only_disarmed_var = tk.BooleanVar(value=self.config_data["only_disarmed"])
        ttk.Checkbutton(buttons, text="Лише коли\nроззброєний", variable=self.only_disarmed_var,
                        command=self._options_changed).pack(anchor="w", pady=(10, 0))

        columns = ("on", "source", "target", "how", "limits", "source_now", "target_now")
        self.tree = ttk.Treeview(middle, columns=columns, show="headings", height=8, selectmode="browse")
        for column, title, width in (
            ("on", "Увімк.", 50), ("source", "Коли змінюється", 130), ("target", "Змінювати", 120),
            ("how", "Як", 180), ("limits", "Межі", 90), ("source_now", "Зараз", 60),
            ("target_now", "Ціль", 60),
        ):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, anchor="center" if column != "how" else "w")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda _e: self._edit_rule())
        scroll = ttk.Scrollbar(middle, orient="vertical", command=self.tree.yview)
        scroll.pack(side="left", fill="y")
        self.tree.configure(yscrollcommand=scroll.set)

        bottom = ttk.LabelFrame(self, text="Журнал", padding=6)
        bottom.pack(fill="both", padx=10, pady=(0, 10))
        self.log_text = tk.Text(bottom, height=9, font=("Consolas", 9), relief="flat", state="disabled",
                                bg="#ffffff")
        self.log_text.pack(fill="both", expand=True)
        for level, color in (("ok", "#15803d"), ("warn", "#a16207"), ("error", "#c62828"), ("info", "#1f2933")):
            self.log_text.tag_configure(level, foreground=color)

    def _connection_choices(self):
        choices = list(CONNECTION_PRESETS)
        try:
            from serial.tools import list_ports
            choices += [port.device for port in list_ports.comports()]
        except Exception:
            pass
        return choices

    # ---- правила -----------------------------------------------------------------------
    def _refresh_rules(self):
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        for index, rule in enumerate(self.config_data["rules"]):
            limits = f"{self._number(rule.get('low'))} … {self._number(rule.get('high'))}"
            source_now = self.params.get(rule["source"])
            target_now = self.params.get(rule["target"])
            self.tree.insert("", "end", iid=str(index), values=(
                "✓" if rule["enabled"] else "—", rule["source"], rule["target"], rule_text(rule), limits,
                "" if source_now is None else f"{source_now:g}",
                "" if target_now is None else f"{target_now:g}",
            ))
        for iid in selected:
            if self.tree.exists(iid):
                self.tree.selection_set(iid)

    @staticmethod
    def _number(value):
        return "" if value in (None, "") else f"{float(value):g}"

    def _selected_index(self):
        selection = self.tree.selection()
        return int(selection[0]) if selection else None

    def _rules_changed(self):
        save_config(self._collect_config())
        self._refresh_rules()
        self._send_rules()

    def _send_rules(self):
        rules = json.loads(json.dumps(self.config_data["rules"]))
        self.worker.commands.put(("rules", rules, self.only_disarmed_var.get()))

    def _add_channel(self):
        dialog = ChannelDialog(self)
        self.wait_window(dialog)
        if dialog.result:
            self.config_data["rules"].extend(dialog.result)
            self._rules_changed()

    def _add_rule(self):
        dialog = RuleDialog(self)
        self.wait_window(dialog)
        if dialog.result:
            self.config_data["rules"].append(dialog.result)
            self._rules_changed()

    def _edit_rule(self):
        index = self._selected_index()
        if index is None:
            return
        dialog = RuleDialog(self, self.config_data["rules"][index])
        self.wait_window(dialog)
        if dialog.result:
            self.config_data["rules"][index] = dialog.result
            self._rules_changed()

    def _toggle_rule(self):
        index = self._selected_index()
        if index is not None:
            rule = self.config_data["rules"][index]
            rule["enabled"] = not rule["enabled"]
            self._rules_changed()

    def _delete_rule(self):
        index = self._selected_index()
        if index is not None and messagebox.askyesno("Видалити", "Видалити вибране правило?"):
            del self.config_data["rules"][index]
            self._rules_changed()

    def _options_changed(self):
        self._rules_changed()

    def _apply_now(self):
        self.worker.commands.put(("apply_all",))

    # ---- підключення -------------------------------------------------------------------
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
            messagebox.showerror("Підключення", "Вкажіть адресу підключення")
            return
        try:
            int(config["baud"])
        except ValueError:
            messagebox.showerror("Підключення", "Швидкість має бути числом")
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
                    ok, text = event[1], event[2]
                    self.link_label.configure(text=f"● {text}", fg="#15803d" if ok else "#c62828")
                    if not ok:
                        self.armed_label.configure(text="")
                elif kind == "armed":
                    self.armed_label.configure(
                        text="ЗААРМЛЕНИЙ" if event[1] else "роззброєний",
                        fg="#c62828" if event[1] else "#15803d",
                    )
                elif kind == "param":
                    self.params[event[1]] = event[2]
                    refresh = True
        except queue.Empty:
            pass
        if refresh:
            self._refresh_rules()
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
        save_config(self._collect_config())
        self.worker.commands.put(("quit",))
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
