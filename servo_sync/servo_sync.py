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
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

# Одразу MAVLink 2 (ним говорять ArduPilot і Mission Planner; пакети MAVLink 1 теж читаються).
# Інакше pymavlink перемикає діалект «на льоту» і в exe шукає XML-файли, яких там немає.
os.environ["MAVLINK20"] = "1"
os.environ["MAVLINK_DIALECT"] = "ardupilotmega"
from pymavlink import mavutil  # noqa: E402
from pymavlink.dialects.v10 import ardupilotmega as _dialect_v10  # noqa: E402,F401  (щоб потрапили в exe)
from pymavlink.dialects.v20 import ardupilotmega as _dialect_v20  # noqa: E402,F401

APP_NAME = "Servo Trim Sync"
APP_VERSION = "1.8"
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
SILENT_REOPEN_S = 6.0
MIDDLE_SETTLE_S = 1.5
CONFIG_VERSION = 3
SERVO_CHANNELS = (6, 7, 8, 9, 10)  # програма працює лише з цими виходами
BAUD_RATES = ("57600", "115200", "921600")
HEARTBEAT_TIMEOUT = 5.0
WRITE_RETRY_S = 1.5
WRITE_ATTEMPTS = 3
REFRESH_SOURCES_S = 10.0

PLUGIN_FILE = "ServoTrimSyncRefresh.cs"


def plugin_source_path():
    """Файл плагіна для Mission Planner (у exe — поруч із розпакованою програмою)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return Path(base) / "mp_plugin" / PLUGIN_FILE


def running_program_paths():
    """Шляхи до exe запущених програм (Windows, без сторонніх бібліотек)."""
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes
    psapi, kernel32 = ctypes.WinDLL("psapi"), ctypes.WinDLL("kernel32")
    pids = (wintypes.DWORD * 4096)()
    needed = wintypes.DWORD()
    if not psapi.EnumProcesses(pids, ctypes.sizeof(pids), ctypes.byref(needed)):
        return []
    paths = []
    for pid in pids[:needed.value // ctypes.sizeof(wintypes.DWORD)]:
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            continue
        try:
            buffer = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                paths.append(buffer.value)
        finally:
            kernel32.CloseHandle(handle)
    return paths


def is_mission_planner_dir(folder):
    folder = Path(folder)
    return (folder / "plugins").is_dir() and (
        (folder / "MissionPlanner.exe").is_file() or (folder / "MAVLink.dll").is_file())


def find_mission_planner_dirs():
    """Папки Mission Planner: спершу запущений (зокрема збірки з іншою назвою), потім стандартні."""
    found = []
    for path in running_program_paths():
        folder = Path(path).parent
        if folder not in found and is_mission_planner_dir(folder):
            found.append(folder)
    for env in ("ProgramFiles(x86)", "ProgramFiles"):
        root = os.environ.get(env)
        if root:
            folder = Path(root) / "Mission Planner"
            if folder not in found and is_mission_planner_dir(folder):
                found.append(folder)
    return found


# ЄДИНІ параметри, які програма взагалі може записати в політник. Перевіряється безпосередньо
# перед відправкою PARAM_SET — будь-яке інше ім'я відкидається, хоч би звідки воно взялося.
WRITABLE_PARAMS = frozenset(f"SERVO{channel}_{kind}" for channel in SERVO_CHANNELS for kind in ("MIN", "TRIM", "MAX"))
MODE_OFFSETS = "offsets"   # MIN = TRIM − відступ, MAX = TRIM + відступ
MODE_MIDDLE = "middle"     # TRIM = середнє між MIN і MAX


def channel_rules(channel, below, above, low=800, high=2200, mode=MODE_OFFSETS):
    """Правила для одного виходу SERVOn (за режимом)."""
    trim, minimum, maximum = f"SERVO{channel}_TRIM", f"SERVO{channel}_MIN", f"SERVO{channel}_MAX"
    if mode == MODE_MIDDLE:
        return [{"enabled": True, "sources": [minimum, maximum], "target": trim, "mode": "middle",
                 "low": low, "high": high}]
    return [
        {"enabled": True, "sources": [trim], "target": minimum, "mode": "offset",
         "value": -abs(int(below)), "low": low, "high": high},
        {"enabled": True, "sources": [trim], "target": maximum, "mode": "offset",
         "value": abs(int(above)), "low": low, "high": high},
    ]


# ---- Перевірки налаштувань (лише читання й звірка) -------------------------------------------
# Назви значень SERVOn_FUNCTION і SERIALn_PROTOCOL з документації ArduPilot.
SERVO_FUNCTIONS = {
    -1: "GPIO", 0: "Disabled", 1: "RCPassThru", 2: "Flap", 3: "FlapAuto", 4: "Aileron", 6: "MountPan",
    7: "MountTilt", 8: "MountRoll", 9: "MountOpen", 10: "CameraTrigger", 12: "Mount2Pan", 13: "Mount2Tilt",
    14: "Mount2Roll", 15: "Mount2Open", 16: "DiffSpoilerLeft1", 17: "DiffSpoilerRight1", 19: "Elevator",
    21: "Rudder", 22: "SprayerPump", 23: "SprayerSpinner", 24: "FlaperonLeft", 25: "FlaperonRight",
    26: "GroundSteering", 27: "Parachute", 28: "Gripper", 29: "LandingGear", 30: "EngineRunEnable",
    31: "HeliRSC", 32: "HeliTailRSC", **{32 + n: f"Motor{n}" for n in range(1, 9)}, 41: "TiltMotorsFront",
    45: "TiltMotorsRearLeft", 46: "TiltMotorsRearRight", **{50 + n: f"RCIN{n}" for n in range(1, 17)},
    67: "Ignition", 69: "Starter", 70: "Throttle", 73: "ThrottleLeft", 74: "ThrottleRight",
    75: "TiltMotorFrontLeft", 76: "TiltMotorFrontRight", 77: "ElevonLeft", 78: "ElevonRight", 79: "VTailLeft",
    80: "VTailRight", 81: "BoostThrottle", **{73 + n: f"Motor{n}" for n in range(9, 13)},
    86: "DiffSpoilerLeft2", 87: "DiffSpoilerRight2", 88: "Winch", 89: "MainSail", 90: "CameraISO",
    91: "CameraAperture", 92: "CameraFocus", 93: "CameraShutterSpeed", **{93 + n: f"Script{n}" for n in range(1, 17)},
    120: "NeoPixel1", 121: "NeoPixel2", 122: "NeoPixel3", 123: "NeoPixel4", 124: "RateRoll", 125: "RatePitch",
    126: "RateThrust", 127: "RateYaw", 129: "ProfiLED1", 130: "ProfiLED2", 131: "ProfiLED3",
    132: "ProfiLEDClock", 133: "WinchClutch", 134: "SERVOn_MIN", 135: "SERVOn_TRIM", 136: "SERVOn_MAX",
    137: "SailMastRotation", **{139 + n: f"RCIN{n}Scaled" for n in range(1, 17)},
    156: "LightsBrightness",
}
SERIAL_PROTOCOLS = {
    -1: "None", 1: "MAVLink1", 2: "MAVLink2", 3: "Frsky D", 4: "Frsky SPort", 5: "GPS", 7: "Alexmos Gimbal",
    8: "Gimbal", 9: "Rangefinder", 10: "FrSky SPort Passthrough", 11: "Lidar360", 13: "Beacon", 14: "Volz servo",
    15: "SBus servo", 16: "ESC Telemetry", 17: "Devo Telemetry", 18: "OpticalFlow", 19: "RobotisServo",
    20: "NMEA Output", 21: "WindVane", 22: "SLCAN", 23: "RCIN", 24: "EFI Serial", 25: "LTM", 26: "RunCam",
    27: "HottTelem", 28: "Scripting", 29: "Crossfire VTX", 30: "Generator", 31: "Winch", 32: "MSP",
    33: "DJI FPV", 34: "AirSpeed", 35: "ADSB", 36: "AHRS", 37: "SmartAudio", 38: "FETtecOneWire",
    39: "Torqeedo", 40: "AIS", 41: "CoDevESC", 42: "DisplayPort", 43: "MAVLink High Latency", 44: "IRC Tramp",
    45: "DDS XRCE", 46: "IMUDATA",
}
TUNING_PARAMS = (
    "RLL_RATE_P", "RLL_RATE_I", "RLL_RATE_D", "RLL_RATE_FF", "RLL_RATE_IMAX",
    "PTCH_RATE_P", "PTCH_RATE_I", "PTCH_RATE_D", "PTCH_RATE_FF", "PTCH_RATE_IMAX",
    "YAW_RATE_P", "YAW_RATE_I", "YAW_RATE_D", "YAW_RATE_FF",
    "RLL2SRV_TCONST", "RLL2SRV_RMAX", "PTCH2SRV_TCONST", "PTCH2SRV_RMAX_UP", "PTCH2SRV_RMAX_DN",
    "YAW2SRV_DAMP", "YAW2SRV_INT", "YAW2SRV_RLL", "NAVL1_PERIOD", "NAVL1_DAMPING",
    "ATC_RAT_RLL_P", "ATC_RAT_RLL_I", "ATC_RAT_RLL_D", "ATC_RAT_RLL_FF",
    "ATC_RAT_PIT_P", "ATC_RAT_PIT_I", "ATC_RAT_PIT_D", "ATC_RAT_PIT_FF",
    "ATC_RAT_YAW_P", "ATC_RAT_YAW_I", "ATC_RAT_YAW_D", "ATC_RAT_YAW_FF",
    "ATC_ANG_RLL_P", "ATC_ANG_PIT_P", "ATC_ANG_YAW_P",
)
CHECK_GROUPS = (
    ("servo", "Servo Output", "FUNCTION виходів"),
    ("serial", "Serial Ports", "PROTOCOL портів"),
    ("tuning", "Basic Tuning", "PID і налаштування"),
    ("custom", "Інше", "будь-які параметри"),
)
CHECK_PARAM_RE = re.compile(r"^[A-Z0-9_]{1,16}$")
CHECK_MISSING_S = 12.0   # не відповів стільки після підключення — параметра, певно, немає


def check_suggestions(group):
    if group == "servo":
        return [f"SERVO{n}_FUNCTION" for n in range(1, 17)]
    if group == "serial":
        return [f"SERIAL{n}_PROTOCOL" for n in range(0, 9)]
    if group == "tuning":
        return list(TUNING_PARAMS)
    return []


def check_choices(group):
    table = SERVO_FUNCTIONS if group == "servo" else SERIAL_PROTOCOLS if group == "serial" else None
    return [f"{value} — {name}" for value, name in sorted(table.items())] if table else []


def value_label(group, value):
    """Число → «70 — Throttle» для FUNCTION/PROTOCOL, інакше просто число."""
    if value is None:
        return "—"
    table = SERVO_FUNCTIONS if group == "servo" else SERIAL_PROTOCOLS if group == "serial" else None
    number = float(value)
    if table is not None and number.is_integer() and int(number) in table:
        return f"{int(number)} — {table[int(number)]}"
    return f"{number:g}"


def parse_number(text):
    """«70 — Throttle» → 70.0; «0,15» → 0.15; порожньо/не число → None."""
    match = re.match(r"\s*([-+]?\d+(?:[.,]\d+)?)", str(text or ""))
    return float(match.group(1).replace(",", ".")) if match else None


def clean_checks(items):
    checks = []
    for item in items if isinstance(items, list) else []:
        try:
            param = str(item.get("param", "")).strip().upper()
            group = item.get("group") if item.get("group") in {g[0] for g in CHECK_GROUPS} else "custom"
            expected = str(item.get("expected", "")).strip()
            tolerance = str(item.get("tolerance", "0")).strip() or "0"
        except AttributeError:
            continue
        if CHECK_PARAM_RE.match(param) and parse_number(expected) is not None:
            checks.append({"group": group, "param": param, "expected": expected, "tolerance": tolerance})
    return checks


def parse_param_file(text):
    """Файл параметрів Mission Planner (.param): «NAME,VALUE» або «NAME VALUE» у кожному рядку."""
    values = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = re.split(r"[,\s\t]+", line)
        if len(parts) >= 2 and CHECK_PARAM_RE.match(parts[0].upper()):
            number = parse_number(parts[1])
            if number is not None:
                values[parts[0].upper()] = number
    return values


DEFAULT_CONFIG = {
    "config_version": CONFIG_VERSION,
    "connection": AUTO_SOURCE,
    "baud": "57600",
    "only_disarmed": True,
    "low": 800,
    "high": 2200,
    "outputs": [{"enabled": True, "channel": channel, "below": 400, "above": 400, "name": "", "mode": "offsets"}
                for channel in SERVO_CHANNELS],
    "auto": True,
    "autoconnect": True,
    "step": 10,
    "topmost": False,
    "show_log": False,
    "geometry": "",
    "overlay": False,
    "overlay_pos": "",
    "checks": [],
}


def compute_target(rule, values):
    """Нове значення цільового параметра за правилом (з обмеженням меж) або None, якщо даних бракує."""
    sources = [values.get(name) for name in rule["sources"]]
    if any(value is None for value in sources):
        return None
    if rule["mode"] == "middle":
        value = (float(sources[0]) + float(sources[1])) / 2.0
    else:
        value = float(sources[0]) + float(rule["value"])
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
        # Рівно SERVO6…SERVO10; налаштування старих рядків з тими самими номерами зберігаються.
        saved = {}
        for output in config["outputs"]:
            try:
                saved.setdefault(int(output["channel"]), dict(output))
            except (KeyError, TypeError, ValueError):
                pass
        outputs = []
        for channel in SERVO_CHANNELS:
            output = saved.get(channel) or {"enabled": True, "below": 400, "above": 400}
            output["channel"] = channel
            output.setdefault("name", "")
            output.setdefault("enabled", True)
            output.setdefault("below", 400)
            output.setdefault("above", 400)
            if output.get("mode") not in (MODE_OFFSETS, MODE_MIDDLE):
                output["mode"] = MODE_OFFSETS
            outputs.append(output)
        config["outputs"] = outputs
        config["checks"] = clean_checks(config.get("checks"))
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
        self.link_heard = {}
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
        self.param_types = {}
        self.deferred = {}
        self.watch = set()

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
        elif kind == "watch":
            self.watch = set(command[1])
            self._request_sources()
        elif kind == "apply_all":
            self._apply_all(command[1] if len(command) > 1 else None, command[2] if len(command) > 2 else False)
        elif kind == "write_values":
            self._write_values(command[1], command[2])

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
        self.deferred.clear()
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
        self.deferred.clear()
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
                self.link_heard[source] = now
                self.failures.pop(source, None)
            try:
                for _ in range(200):
                    message = link.recv_match(blocking=False)
                    if message is None:
                        break
                    self.link_heard[source] = now
                    if self._vehicle_heartbeat(message):
                        self._lock(source, link)
                        self._on_message(message)
                        return
                if source == MP_SOURCE and now - self.link_heard.get(source, now) > SILENT_REOPEN_S:
                    # Mission Planner прив'язує потік до свого поточного підключення. Якщо MP
                    # перепідключився (новий порт/об'єкт), старий потік мовчить — відкриваємо заново.
                    raise ConnectionError("Mission Planner мовчить — перепідключаюся")
            except Exception as exc:
                self.failures[source] = exc
                self.next_try[source] = now + (0.3 if isinstance(exc, ConnectionError) else RETRY_OPEN_S)
                try:
                    link.close()
                except Exception:
                    pass
                self.links.pop(source, None)
        self.set_status(False, self._search_text(now))

    def _search_text(self, now):
        auto = self.wanted[0] == AUTO_SOURCE
        if MP_SOURCE in self.links or "мовчить" in str(self.failures.get(MP_SOURCE, "")):
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
        self.deferred.clear()
        self.link_ok = False
        self.last_own_heartbeat = 0.0

    # ---- основний цикл -----------------------------------------------------------------
    def _pump(self):
        now = time.monotonic()
        if self.master is None:
            self._scan(now)
            return
        # Власний heartbeat не надсилаємо: для читання/запису параметрів він не потрібен,
        # тож політнику не йде нічого, крім запитів параметрів SERVO і запису дозволених значень.
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
        for target, (rule, due) in list(self.deferred.items()):
            if now >= due:
                del self.deferred[target]
                if any(rule is active or rule == active for active in self.rules if active["enabled"]):
                    self._apply_rules([rule])
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
            name = name.rstrip("\x00")
            self.param_types[name] = message.param_type
            self._on_param(name, float(message.param_value))

    def _on_param(self, name, value):
        previous = self.params.get(name)
        self.params[name] = value
        self.emit("param", name, value)
        pending = self.pending.get(name)
        if pending and abs(value - pending["value"]) < 0.5:
            del self.pending[name]
            self.log(f"{name} = {value:g} ✓", "ok")
        changed = previous is not None and abs(previous - value) >= 1e-6
        rules = [rule for rule in self.rules
                 if rule["enabled"] and (name in rule["sources"] or name == rule["target"])]
        if not rules:
            return
        if changed and any(name in rule["sources"] for rule in rules):
            self.log(f"{name}: {previous:g} → {value:g}")
        # «Авто» тримає параметри узгодженими: після зміни джерела, а також одразу після
        # підключення, якщо в політнику вже стоять інші значення.
        for rule in rules:
            if any(source not in self.params for source in rule["sources"]):
                continue
            if rule["target"] not in self.params and not (changed and name in rule["sources"]):
                continue  # чекаємо, поки прочитаємо поточне значення цільового параметра
            if rule["mode"] == "middle" and changed and name in rule["sources"]:
                # MIN і MAX зазвичай міняють один за одним — чекаємо 1,5 с, щоб не писати проміжний TRIM.
                self.deferred[rule["target"]] = (rule, time.monotonic() + MIDDLE_SETTLE_S)
                continue
            self._apply_rules([rule])

    # ---- правила й запис ---------------------------------------------------------------
    def _request_sources(self):
        if self.master is None or not self.link_ok:
            return
        self.last_refresh = time.monotonic()
        names = {source for rule in self.rules if rule["enabled"] for source in rule["sources"]}
        names |= {rule["target"] for rule in self.rules if rule["enabled"]}
        names &= WRITABLE_PARAMS  # свої параметри SERVO
        names |= self.watch       # параметри для перевірок — ЛИШЕ читання, у запис не потрапляють ніколи
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
            missing = [source for source in rule["sources"] if source not in self.params]
            if missing:
                self.log(f"{', '.join(missing)} ще не прочитано — пропускаю", "warn")
                continue
            applied += self._apply_rules([rule])
        if not applied and not quiet:
            self.log("Усе вже відповідає правилам")

    def _write_values(self, values, rules):
        """Користувач змінив значення в програмі: записати їх і одразу залежні параметри."""
        if not self.link_ok:
            self.log("Немає зв'язку з політником", "error")
            return
        if self.only_disarmed and self.armed:
            self.log("Апарат заармлений — параметри не змінюю", "warn")
            return
        for name, value in values.items():
            self._write(name, value)
        self._apply_rules(rules, values)

    def _apply_rules(self, rules, overrides=None):
        if self.only_disarmed and self.armed:
            now = time.monotonic()
            if now - getattr(self, "last_armed_warning", -100.0) > 20:
                self.last_armed_warning = now
                self.log("Апарат заармлений — параметри не змінюю", "warn")
            return 0
        written = 0
        values = dict(self.params)
        values.update(overrides or {})
        for rule in rules:
            value = compute_target(rule, values)
            if value is None:
                continue
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
        if name not in WRITABLE_PARAMS:
            self.log(f"Заблоковано: {name} — програма змінює лише MIN/TRIM/MAX SERVO6–10", "error")
            return
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
            if name not in WRITABLE_PARAMS:  # друга, остання перевірка просто перед відправкою
                del self.pending[name]
                continue
            self.master.mav.param_set_send(
                self.target[0], self.target[1], name.encode(), job["value"],
                self.param_types.get(name, mavutil.mavlink.MAV_PARAM_TYPE_REAL32),
            )


# ---- Вигляд: темний мінімалістичний, у фірмових кольорах логотипу -------------------------
# Стримана «інструментальна» палітра: графіт, білий для активного, червоний — лише фірмовий знак.
BG = "#0b0d10"
SURFACE = "#12151a"
RAISED = "#1a1e24"
LINE = "#1f242b"
TEXT = "#e6e8eb"
MUTED = "#8a919b"
FAINT = "#4d545e"
ACCENT = "#e6e8eb"
BRAND = "#e5484d"
OK = "#3fb950"
WARN = "#d29922"
ERR = "#f85149"
FONT = "Segoe UI"
STEPS = (1, 5, 10, 25, 50)
COLUMNS = (30, 112, 118, 128, 118, 118)  # галочка, вихід, MIN, TRIM, MAX, у політнику


PLUGIN_STATUS_FILE = "ServoTrimSyncRefresh.status"


def plugin_runtime_status():
    """Що пише про себе плагін у Mission Planner (файл у TEMP) або None, якщо файлу немає."""
    path = Path(tempfile.gettempdir()) / PLUGIN_STATUS_FILE
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        age = time.time() - path.stat().st_mtime
    except OSError:
        return None
    data = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
    data["age"] = age
    return data


def plugin_report(installed):
    """Зрозумілий стан плагіна: (текст, колір)."""
    where = ", ".join(str(folder) for folder in installed)
    status = plugin_runtime_status()
    if status is None or status["age"] > 8:
        if count_mission_planner_processes():
            return (f"Плагін скопійовано ({where}), але Mission Planner його не запустив.\n"
                    "Закрийте MP повністю й відкрийте знову. Якщо не допоможе — ця збірка MP\n"
                    "не завантажує плагіни (див. Help → Plugins).", ERR)
        return (f"Плагін скопійовано ({where}).\nЗапустіть Mission Planner — він підхопить плагін.", MUTED)
    packets = int(status.get("packets", "0") or 0)
    refreshed = int(status.get("refreshed", "0") or 0)
    seen = int(status.get("controls_seen", "-1") or -1)
    error = status.get("error", "").strip()
    if packets == 0:
        text, color = "Плагін працює, але ще не бачить пакетів від політника", WARN
    elif seen == 0:
        pages = status.get("pages", "").strip()
        text, color = ("Плагін працює, але зараз не бачить полів SERVO —\nвідкрийте в MP сторінку Servo Output"
                       f" (переглянуто елементів: {status.get('scanned', '?')}"
                       + (f"; сторінки: {pages}" if pages else "") + ")", WARN)
    else:
        text, color = (f"✓ Плагін працює · бачить полів SERVO: {seen} · оновлено: {refreshed}\n"
                       f"({status.get('types', '')})", OK)
    if error:
        text += f"\nПомилка плагіна: {error[:120]}"
        color = ERR
    return text, color


def dark_title_bar(window):
    """Windows 10/11: темний заголовок вікна в кольорі програми (замість світлої смуги)."""
    if os.name != "nt":
        return
    try:
        import ctypes
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        dark = ctypes.c_int(1)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (нові / старі збірки)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(dark),
                                                          ctypes.sizeof(dark)) == 0:
                break
        for attribute, color in ((35, BG), (36, TEXT), (34, LINE)):  # колір заголовка, тексту, рамки (Win 11)
            value = ctypes.c_int(int(color[5:7] + color[3:5] + color[1:3], 16))
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value))
    except Exception:
        pass


def count_mission_planner_processes():
    count = 0
    for path in running_program_paths():
        path = Path(path)
        if "planner" in path.name.lower() and is_mission_planner_dir(path.parent):
            count += 1
    return count


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


class MiniMonitor(tk.Toplevel):
    """Маленьке вікно поверх усіх: вибрані виходи, MIN · TRIM · MAX, зелене — збігається, червоне — ні."""

    def __init__(self, app):
        super().__init__(app, bg=LINE)
        self.app = app
        self.drag = None
        self.signature = None
        self.cells = {}
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        try:
            self.attributes("-alpha", 0.96)
        except tk.TclError:
            pass
        self.body = tk.Frame(self, bg=SURFACE, padx=12, pady=8)
        self.body.pack(fill="both", expand=True, padx=1, pady=1)
        head = tk.Frame(self.body, bg=SURFACE)
        head.pack(fill="x")
        self.dot = tk.Label(head, text="●", bg=SURFACE, fg=FAINT, font=(FONT, 8))
        self.dot.pack(side="left")
        self.caption = tk.Label(head, text="SERVO TRIM SYNC", bg=SURFACE, fg=FAINT, font=(FONT, 7, "bold"))
        self.caption.pack(side="left", padx=(5, 0))
        self.close_button = ImageButton(head, {"normal": app.img["close"], "hover": app.img["close_hover"]},
                                        app._toggle_overlay, tip="Сховати міні-вікно")
        self.close_button.pack(side="right")
        self.table = tk.Frame(self.body, bg=SURFACE)
        self.table.pack(fill="x", pady=(6, 0))
        self.checks_line = tk.Frame(self.body, bg=SURFACE)
        tk.Frame(self.checks_line, bg=LINE, height=1).pack(fill="x", pady=(7, 5))
        line = tk.Frame(self.checks_line, bg=SURFACE)
        line.pack(fill="x")
        caption = tk.Label(line, text="НАЛАШТУВАННЯ", bg=SURFACE, fg=FAINT, font=(FONT, 7, "bold"))
        caption.pack(side="left")
        self.checks_label = tk.Label(line, text="", bg=SURFACE, fg=FAINT, font=app.font_strong, cursor="hand2")
        self.checks_label.pack(side="right")
        self.checks_tip = Tip(self.checks_label, "")
        for widget in (self.checks_line, line, caption):
            self._bind_drag(widget)
        self.checks_label.bind("<Button-1>", lambda _e: app._open_checks())
        for widget in (self, self.body, head, self.dot, self.caption, self.table):
            self._bind_drag(widget)
        position = str(app.config_data.get("overlay_pos") or "")
        if position.startswith("+"):
            self.geometry(position)
        else:
            self.update_idletasks()
            self.geometry(f"+{max(0, self.winfo_screenwidth() - 340)}+{90}")

    def _bind_drag(self, widget):
        widget.bind("<ButtonPress-1>", self._press)
        widget.bind("<B1-Motion>", self._move)
        widget.bind("<ButtonRelease-1>", self._release)
        widget.bind("<Double-Button-1>", lambda _e: self.app._show_main())

    def _press(self, event):
        self.drag = (event.x_root - self.winfo_x(), event.y_root - self.winfo_y())

    def _move(self, event):
        if self.drag:
            self.geometry(f"+{event.x_root - self.drag[0]}+{event.y_root - self.drag[1]}")

    def _release(self, _event):
        if self.drag:
            self.drag = None
            self.app.config_data["overlay_pos"] = f"+{self.winfo_x()}+{self.winfo_y()}"
            self.app._save_later()

    def _rebuild(self, states):
        for widget in self.table.winfo_children():
            widget.destroy()
        self.cells = {}
        font_value = self.app.font_value
        if not states:
            label = tk.Label(self.table, text="Немає вибраних виходів", bg=SURFACE, fg=MUTED, font=(FONT, 9))
            label.grid(row=0, column=0, sticky="w")
            self._bind_drag(label)
            return
        for column, text in ((2, "MIN"), (3, "TRIM"), (4, "MAX")):
            label = tk.Label(self.table, text=text, bg=SURFACE, fg=FAINT, font=(FONT, 7, "bold"), anchor="e")
            label.grid(row=0, column=column, sticky="e", padx=(14, 0))
            self._bind_drag(label)
        for row, state in enumerate(states, start=1):
            strip = tk.Frame(self.table, bg=FAINT, width=3, height=20)
            strip.grid(row=row, column=0, sticky="ns", pady=2, padx=(0, 8))
            name = tk.Label(self.table, text=state["name"], bg=SURFACE, fg=TEXT, font=self.app.font_strong,
                            anchor="w")
            name.grid(row=row, column=1, sticky="w", pady=2)
            values = []
            for column in (2, 3, 4):
                value = tk.Label(self.table, text="—", bg=SURFACE, fg=FAINT, font=font_value, anchor="e", width=5)
                value.grid(row=row, column=column, sticky="e", padx=(14, 0), pady=2)
                values.append(value)
            for widget in [strip, name] + values:
                self._bind_drag(widget)
            self.cells[state["index"]] = (strip, name, values)

    def show(self, states, link_color, checks=None):
        if checks is None:
            self.checks_line.pack_forget()
        else:
            _state, text, color, details = checks
            self.checks_label.configure(text=text, fg=color)
            self.checks_tip.text = details
            self.checks_line.pack(fill="x")
        signature = tuple((state["index"], state["name"]) for state in states)
        if signature != self.signature:
            self.signature = signature
            self._rebuild(states)
        self.dot.configure(fg=link_color)
        colors = {"ok": OK, "bad": ERR, "unknown": FAINT}
        for state in states:
            strip, name, values = self.cells[state["index"]]
            color = colors[state["status"]]
            strip.configure(bg=color)
            for label, value in zip(values, (state["min"], state["trim"], state["max"])):
                label.configure(text="—" if value is None else f"{value:g}", fg=color)


class ChecksWindow(tk.Toplevel):
    """Перевірки налаштувань: що має стояти в політнику → що стоїть насправді. Лише читання."""

    def __init__(self, app):
        super().__init__(app, bg=BG)
        self.app = app
        self.title("Перевірки налаштувань")
        self.resizable(False, False)
        self.group = "servo"
        self.rows = {group: [] for group, _title, _hint in CHECK_GROUPS}
        self.protocol("WM_DELETE_WINDOW", self._close)
        body = tk.Frame(self, bg=BG, padx=18, pady=14)
        body.pack(fill="both", expand=True)

        top = tk.Frame(body, bg=BG)
        top.pack(fill="x")
        titles = tk.Frame(top, bg=BG)
        titles.pack(side="left")
        tk.Label(titles, text="Перевірки налаштувань", bg=BG, fg=TEXT, font=app.font_title).pack(anchor="w")
        tk.Label(titles, text="Лише читання й звірка — у політнику нічого не змінюється", bg=BG, fg=MUTED,
                 font=(FONT, 9)).pack(anchor="w")
        self.summary = tk.Label(top, text="", bg=BG, fg=MUTED, font=app.font_strong)
        self.summary.pack(side="right")

        tabs = tk.Frame(body, bg=BG)
        tabs.pack(fill="x", pady=(14, 8))
        self.tab_buttons = {}
        for group, title, hint in CHECK_GROUPS:
            button = FlatButton(tabs, title, lambda g=group: self._show_group(g), font=(FONT, 9, "bold"),
                                padx=12, pady=5, tip=hint)
            button.pack(side="left", padx=(0, 4))
            self.tab_buttons[group] = button

        header = tk.Frame(body, bg=BG, padx=1)
        header.pack(fill="x")
        self._columns(header)
        for column, text in enumerate(("ПАРАМЕТР", "МАЄ БУТИ", "ДОПУСК", "У ПОЛІТНИКУ", "")):
            tk.Label(header, text=text, bg=BG, fg=FAINT, font=(FONT, 8, "bold"), anchor="w").grid(
                row=0, column=column, sticky="w", pady=(0, 4))

        holder = tk.Frame(body, bg=BG, highlightthickness=1, highlightbackground=LINE)
        holder.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(holder, bg=BG, highlightthickness=0, height=330, width=760)
        scroll = ttk.Scrollbar(holder, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.frames = {}
        for group, _title, _hint in CHECK_GROUPS:
            frame = tk.Frame(self.canvas, bg=BG)
            self.frames[group] = frame
        self.window_id = self.canvas.create_window(0, 0, anchor="nw", window=self.frames["servo"])
        self.canvas.bind("<Configure>", lambda _e: self._sync_scroll())
        self.bind_all("<MouseWheel>", self._wheel, add="+")

        actions = tk.Frame(body, bg=BG)
        actions.pack(fill="x", pady=(10, 0))
        FlatButton(actions, "+ Додати", self._add_row, padx=12, pady=5, bg=SURFACE, fg=ACCENT,
                   font=app.font_strong).pack(side="left")
        FlatButton(actions, "Взяти з політника", self._take_current, padx=12, pady=5, bg=SURFACE,
                   tip="Записати поточні значення з політника як еталон\n(для всіх рядків; у політнику нічого "
                       "не змінюється)").pack(side="left", padx=(6, 0))
        FlatButton(actions, "З .param файлу…", self._from_param_file, padx=12, pady=5, bg=SURFACE,
                   tip="Взяти еталон з файлу параметрів Mission Planner\n(Config → Full Parameter List → Save)"
                   ).pack(side="left", padx=(6, 0))
        FlatButton(actions, "Експорт конфігу…", lambda: app._export_config(self), padx=12, pady=5,
                   bg=SURFACE, tip="Зберегти назви серв, режими, відступи й перевірки у файл").pack(side="right")
        FlatButton(actions, "Імпорт конфігу…", self._import, padx=12, pady=5, bg=SURFACE,
                   tip="Завантажити конфіг з файлу").pack(side="right", padx=(0, 6))

        bottom = tk.Frame(body, bg=BG)
        bottom.pack(fill="x", pady=(14, 0))
        self.hint = tk.Label(bottom, text="", bg=BG, fg=MUTED, font=(FONT, 9), anchor="w")
        self.hint.pack(side="left")
        FlatButton(bottom, "Зберегти", self._save, padx=16, pady=6, bg=SURFACE, fg=ACCENT,
                   font=app.font_strong).pack(side="right")
        FlatButton(bottom, "Закрити", self._close, padx=12, pady=6, bg=SURFACE).pack(side="right", padx=(0, 6))

        self._load(app.config_data["checks"])
        self._show_group("servo")
        self.update_live()
        app.update_idletasks()
        self.geometry(f"+{app.winfo_rootx() + 30}+{app.winfo_rooty() + 30}")
        self.after(10, lambda: dark_title_bar(self))

    CHECK_COLUMNS = (200, 238, 74, 226, 30)

    def _columns(self, frame):
        for column, width in enumerate(self.CHECK_COLUMNS):
            frame.columnconfigure(column, minsize=width)

    # ---- рядки ---------------------------------------------------------------------------
    def _load(self, checks):
        for group in self.rows:
            for row in self.rows[group]:
                row["frame"].destroy()
            self.rows[group] = []
        for check in checks:
            self._add_row(check["group"], check)
        self._sync_scroll()

    def _add_row(self, group=None, check=None):
        group = group or self.group
        frame = tk.Frame(self.frames[group], bg=BG, pady=3)
        frame.pack(fill="x")
        self._columns(frame)
        row = {"group": group, "frame": frame}
        row["param"] = tk.StringVar(value=(check or {}).get("param", ""))
        row["expected"] = tk.StringVar(value=(check or {}).get("expected", ""))
        row["tolerance"] = tk.StringVar(value=(check or {}).get("tolerance", "0"))
        if check is None and group in ("servo", "serial"):
            used = {r["param"].get() for r in self.rows[group]}
            free = [name for name in check_suggestions(group) if name not in used]
            row["param"].set(free[0] if free else "")
        param = ttk.Combobox(frame, textvariable=row["param"], values=check_suggestions(group), width=20)
        param.grid(row=0, column=0, sticky="w")
        expected = ttk.Combobox(frame, textvariable=row["expected"], values=check_choices(group), width=24)
        expected.grid(row=0, column=1, sticky="w")
        tolerance = ttk.Entry(frame, textvariable=row["tolerance"], width=7)
        tolerance.grid(row=0, column=2, sticky="w")
        if group in ("servo", "serial"):
            tolerance.state(["disabled"])
        row["current"] = tk.Label(frame, text="—", bg=BG, fg=FAINT, font=self.app.font_strong, anchor="w")
        row["current"].grid(row=0, column=3, sticky="w")
        FlatButton(frame, "×", lambda: self._remove_row(row), padx=6, pady=0, fg=FAINT,
                   tip="Прибрати перевірку").grid(row=0, column=4)
        for variable in (row["param"], row["expected"], row["tolerance"]):
            variable.trace_add("write", lambda *_a: self.update_live())
        self.rows[group].append(row)
        self._sync_scroll()
        self.update_live()
        return row

    def _remove_row(self, row):
        row["frame"].destroy()
        self.rows[row["group"]].remove(row)
        self._sync_scroll()
        self.update_live()

    def _collect(self):
        checks = []
        for group, _title, _hint in CHECK_GROUPS:
            for row in self.rows[group]:
                checks.append({"group": group, "param": row["param"].get().strip().upper(),
                               "expected": row["expected"].get().strip(),
                               "tolerance": row["tolerance"].get().strip() or "0"})
        return checks

    # ---- вкладки й прокрутка ---------------------------------------------------------------
    def _show_group(self, group):
        self.group = group
        self.canvas.itemconfigure(self.window_id, window=self.frames[group])
        for key, button in self.tab_buttons.items():
            count = len(self.rows[key])
            title = next(title for g, title, _hint in CHECK_GROUPS if g == key)
            button.configure(text=f"{title} · {count}" if count else title)
            button.set_style(bg=RAISED if key == group else BG, fg=TEXT if key == group else MUTED)
        self.canvas.yview_moveto(0)
        self._sync_scroll()

    def _sync_scroll(self):
        frame = self.frames[self.group]
        frame.update_idletasks()
        self.canvas.configure(scrollregion=(0, 0, frame.winfo_reqwidth(), frame.winfo_reqheight()))

    def _wheel(self, event):
        try:
            if str(event.widget).startswith(str(self)):
                self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        except tk.TclError:
            pass

    # ---- живі значення -----------------------------------------------------------------------
    def update_live(self):
        app = self.app
        late = app.link_ok and time.monotonic() - app.link_since > CHECK_MISSING_S
        bad = ok = 0
        for group, _title, _hint in CHECK_GROUPS:
            for row in self.rows[group]:
                param = row["param"].get().strip().upper()
                expected = parse_number(row["expected"].get())
                tolerance = abs(parse_number(row["tolerance"].get()) or 0.0)
                value = app.params.get(param)
                if not param or expected is None:
                    text, color = "заповніть параметр і значення", WARN
                elif not app.link_ok:
                    text, color = "немає зв'язку", FAINT
                elif value is None:
                    text, color = ("✕ немає такого параметра?" if late else "читаю…"), (ERR if late else FAINT)
                    bad += late
                elif abs(value - expected) <= tolerance + 1e-6:
                    text, color = f"✓ {value_label(group, value)}", OK
                    ok += 1
                else:
                    text, color = f"✕ {value_label(group, value)}", ERR
                    bad += 1
                row["current"].configure(text=text, fg=color)
        total = sum(len(rows) for rows in self.rows.values())
        if not total:
            self.summary.configure(text="Перевірок ще немає", fg=MUTED)
        elif bad:
            self.summary.configure(text=f"Щось не так: {bad}", fg=ERR)
        elif ok == total:
            self.summary.configure(text="✓ Налаштування OK", fg=OK)
        else:
            self.summary.configure(text=f"Перевірено {ok} з {total}", fg=MUTED)
        dirty = clean_checks(self._collect()) != self.app.config_data["checks"]
        self.hint.configure(text="Є незбережені зміни" if dirty else "", fg=WARN)
        for key, button in self.tab_buttons.items():
            count = len(self.rows[key])
            title = next(title for g, title, _hint in CHECK_GROUPS if g == key)
            button.configure(text=f"{title} · {count}" if count else title)

    # ---- дії -----------------------------------------------------------------------------------
    def _take_current(self):
        rows = [row for rows in self.rows.values() for row in rows
                if self.app.params.get(row["param"].get().strip().upper()) is not None]
        if not rows:
            messagebox.showinfo(APP_NAME, "Немає прочитаних значень — підключіться до політника\n"
                                "й додайте рядки з параметрами.", parent=self)
            return
        if not messagebox.askyesno(APP_NAME, f"Записати поточні значення з політника як еталон для {len(rows)} "
                                   "рядків?\n\n(Змінюється лише конфіг програми, політник — ні.)", parent=self):
            return
        for row in rows:
            value = self.app.params[row["param"].get().strip().upper()]
            row["expected"].set(value_label(row["group"], value))
        self.update_live()

    def _from_param_file(self):
        path = filedialog.askopenfilename(parent=self, title="Файл параметрів Mission Planner",
                                          filetypes=[("Параметри", "*.param *.parm *.txt"), ("Усі файли", "*.*")])
        if not path:
            return
        try:
            values = parse_param_file(Path(path).read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)
            return
        patterns = {"servo": re.compile(r"^SERVO\d{1,2}_FUNCTION$"), "serial": re.compile(r"^SERIAL\d_PROTOCOL$"),
                    "tuning": re.compile("^(" + "|".join(TUNING_PARAMS) + ")$")}
        updated = added = 0
        existing = {row["param"].get().strip().upper(): row for rows in self.rows.values() for row in rows}
        for name, value in values.items():
            if name in existing:
                row = existing[name]
                row["expected"].set(value_label(row["group"], value))
                updated += 1
                continue
            group = next((g for g, pattern in patterns.items() if pattern.match(name)), None)
            if group:
                self._add_row(group, {"param": name, "expected": value_label(group, value), "tolerance": "0"})
                added += 1
        self._show_group(self.group)
        messagebox.showinfo(APP_NAME, f"З файлу: оновлено {updated}, додано {added} перевірок\n"
                            "(FUNCTION, PROTOCOL і PID). Перегляньте й натисніть «Зберегти».", parent=self)

    def _import(self):
        if self.app._import_config(self):
            self._load(self.app.config_data["checks"])
            self._show_group(self.group)

    def _save(self):
        checks = self._collect()
        broken = [c["param"] or "(порожньо)" for c in checks
                  if not CHECK_PARAM_RE.match(c["param"]) or parse_number(c["expected"]) is None]
        if broken:
            messagebox.showerror(APP_NAME, "Заповніть правильно (назва параметра й число):\n" + ", ".join(broken[:8]),
                                 parent=self)
            return
        self.app._set_checks(checks)
        self.app._log(f"Перевірки збережено: {len(checks)}", "ok")
        self.update_live()

    def _close(self):
        if clean_checks(self._collect()) != self.app.config_data["checks"]:
            answer = messagebox.askyesnocancel(APP_NAME, "Зберегти зміни в перевірках?", parent=self)
            if answer is None:
                return
            if answer:
                self._save()
        try:
            self.unbind_all("<MouseWheel>")
        except tk.TclError:
            pass
        self.app.checks_window = None
        self.destroy()


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
        self.overlay = None
        self.mp_copies = 0
        self.next_mp_check = 0.0
        self.link_since = 0.0
        self.next_checks_ui = 0.0
        self.checks_window = None
        self._load_images()
        self._style()
        self._build()
        self._apply_window_options()
        geometry = str(self.config_data.get("geometry") or "")
        if geometry.startswith("+"):
            self.geometry(geometry)
        self._send_rules()
        self._send_watch()
        self._refresh_values()
        self.after(10, lambda: dark_title_bar(self))
        if self.config_data.get("overlay"):
            self.after(300, self._toggle_overlay)
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
        titles.pack(side="left", padx=(12, 0))
        title_line = tk.Frame(titles, bg=BG)
        title_line.pack(anchor="w")
        self._label(title_line, APP_NAME, TEXT, self.font_title).pack(side="left")
        tk.Label(title_line, text=f"v{APP_VERSION}", bg=RAISED, fg=MUTED, font=(FONT, 7, "bold"), padx=5).pack(
            side="left", padx=(8, 0), pady=(3, 0))
        self._label(titles, "ArduPilot · MIN / MAX від TRIM", MUTED, (FONT, 9)).pack(anchor="w")
        self.settings_button = ImageButton(head, {"normal": self.img["tune"], "hover": self.img["tune_hover"]},
                                           self._open_settings, tip="Налаштування: підключення, номери SERVO,\n"
                                                                     "назви, межі PWM, безпека")
        self.settings_button.pack(side="right", padx=(6, 0))
        self.overlay_button = ImageButton(head, {}, self._toggle_overlay,
                                          tip="Міні-вікно поверх усіх: вибрані виходи\n"
                                              "зеленим — збігається, червоним — ні")
        self.overlay_button.pack(side="right", padx=(8, 0))
        self.pin_button = ImageButton(head, {}, self._toggle_topmost, tip="Тримати це вікно поверх Mission Planner")
        self.pin_button.pack(side="right", padx=(8, 0))
        self.connect_button = FlatButton(head, "", self._toggle_connection, font=self.font_strong, padx=12, pady=5,
                                         bg=SURFACE, hover_bg=RAISED,
                                         tip="Програма бачить усе, що бачить Mission Planner —\n"
                                             "хоч як він підключений (USB, COM, радіо, UDP, TCP).\n"
                                             "У Mission Planner нічого вмикати не треба.")
        self.connect_button.pack(side="right", padx=(0, 6))
        self.checks_button = FlatButton(head, "Перевірки", self._open_checks, font=self.font_strong, padx=12,
                                        pady=5, bg=SURFACE, hover_bg=RAISED, fg=MUTED,
                                        tip="Перевірка налаштувань політника (лише читання)")
        self.checks_button.pack(side="right", padx=(0, 6))
        self.armed_label = self._label(head, "", ERR, (FONT, 9, "bold"))
        self.armed_label.pack(side="right", padx=(0, 10))
        self._line(self)

        table = tk.Frame(self, bg=BG, padx=18, pady=10)
        table.pack(fill="x")
        header = self._row_frame(table)
        for column, text in enumerate(("", "ВИХІД", "MIN", "TRIM", "MAX", "У ПОЛІТНИКУ")):
            anchor = "w" if column in (1,) else ("e" if column == 5 else "center")
            self._label(header, text, FAINT, (FONT, 8, "bold"), anchor=anchor).grid(
                row=0, column=column, sticky="ew", pady=(0, 6))
        for index in range(len(SERVO_CHANNELS)):
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
            self._apply_selected, text="Застосувати до вибраних", font=self.font_strong, fg=BG,
            tip="Виставити MIN і MAX усім виходам з галочкою\nза поточним TRIM і вашими відступами",
        )
        self.apply_button.colors = (BG, FAINT)
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
        row = {"index": index, "pending": {}, "pending_time": 0.0, "write_job": None, "apply_job": None}
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
        row["mode"] = FlatButton(names, "", lambda: self._toggle_mode(index), font=(FONT, 8), padx=4, pady=0,
                                 fg=MUTED, hover_fg=TEXT,
                                 tip="Режим виходу (клік — змінити):\n"
                                     "«± від TRIM» — MIN = TRIM − відступ, MAX = TRIM + відступ\n"
                                     "«TRIM = середнє» — TRIM = (MIN + MAX) / 2")
        row["mode"].pack(anchor="w")
        for column, cell in ((2, "below"), (3, "trim"), (4, "above")):
            row[cell] = self._stepper(frame, lambda d, c=cell: self._cell_step(index, c, d),
                                      lambda v, c=cell: self._cell_set(index, c, v),
                                      self.font_trim if cell == "trim" else self.font_value,
                                      {"below": "−", "above": "+"}.get(cell, ""))
            row[cell]["frame"].grid(row=0, column=column)
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

    def _toggle_overlay(self):
        if self.overlay is not None:
            try:
                self.config_data["overlay_pos"] = f"+{self.overlay.winfo_x()}+{self.overlay.winfo_y()}"
                self.overlay.destroy()
            except tk.TclError:
                pass
            self.overlay = None
            self.config_data["overlay"] = False
        else:
            self.overlay = MiniMonitor(self)
            self.config_data["overlay"] = True
            self._refresh_values()
        self._update_controls()
        self._save_later()

    def _show_main(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def _output_status(self, index):
        """(MIN, TRIM, MAX, стан) для міні-вікна: ok — за правилом, bad — ні, unknown — немає даних."""
        current_min, trim, current_max = (self._value(index, kind) for kind in ("MIN", "TRIM", "MAX"))
        if not self.link_ok or None in (current_min, trim, current_max):
            return current_min, trim, current_max, "unknown"
        if self.rows[index]["pending"]:
            return current_min, trim, current_max, "bad"
        return current_min, trim, current_max, "ok" if not self._mismatches(index) else "bad"

    def _mismatches(self, index):
        """{параметр: потрібне значення} для параметрів, що не відповідають правилу."""
        output = self._output(index)
        values = {name: value for name, value in self.params.items()}
        values.update(self.rows[index]["pending"])
        wrong = {}
        for rule in self._output_rules(output):
            expected = compute_target(rule, values)
            current = self.params.get(rule["target"])
            if expected is not None and current is not None and abs(current - expected) >= 0.5:
                wrong[rule["target"]] = expected
        return wrong

    def _update_overlay(self):
        if self.overlay is None:
            return
        try:
            self.overlay.show(*self._overlay_args())
        except tk.TclError:
            self.overlay = None

    def _send_watch(self):
        names = sorted({check["param"] for check in self.config_data["checks"]})
        self.worker.commands.put(("watch", names))

    def _check_results(self):
        """[(перевірка, поточне значення, стан)]; стан: ok · bad · missing · unknown."""
        results = []
        for check in self.config_data["checks"]:
            value = self.params.get(check["param"])
            expected = parse_number(check["expected"])
            tolerance = abs(parse_number(check["tolerance"]) or 0.0)
            if value is None:
                late = self.link_ok and time.monotonic() - self.link_since > CHECK_MISSING_S
                status = "missing" if late else "unknown"
            else:
                status = "ok" if abs(value - expected) <= tolerance + 1e-6 else "bad"
            results.append((check, value, status))
        return results

    def _checks_summary(self):
        """(стан, текст, колір, подробиці) або None, якщо перевірок немає."""
        results = self._check_results()
        if not results:
            return None
        problems = []
        for check, value, status in results:
            if status == "bad":
                problems.append(f"✕ {check['param']}: {value_label(check['group'], value)}"
                                f" (має бути {value_label(check['group'], parse_number(check['expected']))})")
            elif status == "missing":
                problems.append(f"? {check['param']}: політник не відповідає (немає такого параметра?)")
        if not self.link_ok:
            return "none", "немає зв'язку", FAINT, "Перевірка почнеться після підключення"
        if problems:
            details = "\n".join(problems[:12]) + (f"\n…і ще {len(problems) - 12}" if len(problems) > 12 else "")
            return "bad", f"Щось не так ({len(problems)})", ERR, details
        if any(status == "unknown" for _c, _v, status in results):
            return "wait", "перевіряю…", MUTED, "Читаю параметри з політника"
        return "ok", "OK", OK, f"Усі {len(results)} перевірок збігаються"

    def _update_checks_ui(self):
        summary = self._checks_summary()
        if summary is None:
            self.checks_button.configure(text="Перевірки")
            self.checks_button.set_style(fg=MUTED)
            self.checks_button.tip.text = "Перевірка налаштувань політника (лише читання)\nНаразі перевірок немає"
        else:
            state, text, color, details = summary
            label = {"ok": "✓ Налаштування OK", "bad": f"✕ {text}", "wait": "Перевіряю…",
                     "none": "Перевірки"}[state]
            self.checks_button.configure(text=label)
            self.checks_button.set_style(fg=color)
            self.checks_button.tip.text = details
        if self.overlay is not None:
            try:
                self.overlay.show(*self._overlay_args())
            except tk.TclError:
                self.overlay = None
        if self.checks_window is not None:
            try:
                self.checks_window.update_live()
            except tk.TclError:
                self.checks_window = None

    def _overlay_args(self):
        states = []
        for index, output in enumerate(self.config_data["outputs"]):
            if not output["enabled"]:
                continue
            current_min, trim, current_max, status = self._output_status(index)
            states.append({"index": index, "name": output.get("name") or f"SERVO {output['channel']}",
                           "min": current_min, "trim": trim, "max": current_max, "status": status})
        link_color = OK if self.link_ok else (WARN if self.connected_request else FAINT)
        return states, link_color, self._checks_summary()

    def _open_checks(self):
        if self.checks_window is not None:
            try:
                self.checks_window.deiconify()
                self.checks_window.lift()
                return
            except tk.TclError:
                self.checks_window = None
        self.checks_window = ChecksWindow(self)

    def _set_checks(self, checks):
        self.config_data["checks"] = clean_checks(checks)
        self._send_watch()
        self._update_checks_ui()
        self._save_now()

    # ---- конфіг: експорт / імпорт ---------------------------------------------------------
    EXPORT_KEYS = ("outputs", "checks", "low", "high", "only_disarmed", "auto", "step")

    def _export_config(self, parent):
        path = filedialog.asksaveasfilename(
            parent=parent, title="Експорт конфігу Servo Trim Sync", defaultextension=".json",
            initialfile="servo_trim_sync_config.json", filetypes=[("Конфіг Servo Trim Sync", "*.json")])
        if not path:
            return
        data = {"app": APP_NAME, "format": 1, "version": APP_VERSION}
        data.update({key: self.config_data[key] for key in self.EXPORT_KEYS})
        try:
            Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Не вдалося зберегти:\n{exc}", parent=parent)
            return
        self._log(f"Конфіг експортовано: {path}", "ok")
        messagebox.showinfo(APP_NAME, "Конфіг збережено.\nНазви серв, режими, відступи й перевірки — у файлі.",
                            parent=parent)

    def _import_config(self, parent):
        path = filedialog.askopenfilename(parent=parent, title="Імпорт конфігу Servo Trim Sync",
                                          filetypes=[("Конфіг Servo Trim Sync", "*.json"), ("Усі файли", "*.*")])
        if not path:
            return False
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(data, dict) or "outputs" not in data:
                raise ValueError("це не конфіг Servo Trim Sync")
            low, high = int(data.get("low", 800)), int(data.get("high", 2200))
            if not 500 <= low < high <= 2500:
                raise ValueError("неправильні межі PWM")
        except (OSError, ValueError, TypeError) as exc:
            messagebox.showerror(APP_NAME, f"Не вдалося прочитати конфіг:\n{exc}", parent=parent)
            return False
        saved = {}
        for output in data.get("outputs") or []:
            try:
                saved[int(output["channel"])] = output
            except (KeyError, TypeError, ValueError):
                continue
        outputs = []
        for current in self.config_data["outputs"]:
            item = saved.get(current["channel"], {})
            outputs.append({
                "channel": current["channel"],
                "enabled": bool(item.get("enabled", current["enabled"])),
                "below": int(max(0, min(1000, int(item.get("below", current["below"]))))),
                "above": int(max(0, min(1000, int(item.get("above", current["above"]))))),
                "name": str(item.get("name", current.get("name", "")))[:24],
                "mode": item.get("mode") if item.get("mode") in (MODE_OFFSETS, MODE_MIDDLE) else current["mode"],
            })
        if not messagebox.askyesno(
                APP_NAME, f"Завантажити конфіг «{Path(path).name}»?\n\nЗамінить назви серв, режими, відступи "
                          "й перевірки.\nЯкщо «Авто» увімкнено, MIN/MAX/TRIM вибраних SERVO6–10 будуть "
                          "вирівняні за новими відступами.", parent=parent):
            return False
        self.config_data.update({"outputs": outputs, "checks": clean_checks(data.get("checks")),
                                 "low": low, "high": high,
                                 "only_disarmed": bool(data.get("only_disarmed", self.config_data["only_disarmed"])),
                                 "auto": bool(data.get("auto", self.config_data["auto"])),
                                 "step": data.get("step") if data.get("step") in STEPS else self.config_data["step"]})
        self._save_now()
        self._send_rules()
        self._send_watch()
        self._update_controls()
        self._refresh_values()
        self._log(f"Конфіг завантажено: {path}", "ok")
        return True

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
        overlay = self.overlay is not None
        self.overlay_button.set_images({"normal": self.img["pip_on" if overlay else "pip_off"],
                                        "hover": self.img["pip_on" if overlay else "pip_hover"]})
        pin = self.config_data["topmost"]
        self.pin_button.set_images({"normal": self.img["pin_on" if pin else "pin_off"],
                                    "hover": self.img["pin_on" if pin else "pin_hover"]})
        auto = self.config_data["auto"]
        self.auto_switch.set_images({"normal": self.img["switch_on" if auto else "switch_off"]})
        self.auto_hint.configure(text="працює у фоні — стежить за змінами SERVO6–10 у Mission Planner" if auto
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

    def _param(self, index, kind):
        return f"SERVO{self._output(index)['channel']}_{kind}"

    def _value(self, index, kind):
        """Значення параметра з урахуванням ще не підтвердженого запису (для показу)."""
        name = self._param(index, kind)
        pending = self.rows[index]["pending"]
        if name in pending:
            return pending[name]
        value = self.params.get(name)
        return None if value is None else int(round(value))

    def _current_trim(self, index):
        return self._value(index, "TRIM")

    def _cell_step(self, index, cell, direction):
        delta = direction * self.config_data["step"]
        output = self._output(index)
        if output.get("mode") == MODE_MIDDLE:
            if cell == "trim":
                self._log("У режимі «TRIM = середнє» TRIM рахується сам — змінюйте MIN і MAX", "warn")
                return
            self._param_set(index, "MIN" if cell == "below" else "MAX", None, delta)
        elif cell == "trim":
            self._param_set(index, "TRIM", None, delta)
        else:
            self._offset_set(index, cell, output[cell] + delta)

    def _cell_set(self, index, cell, value):
        output = self._output(index)
        if output.get("mode") == MODE_MIDDLE:
            if cell == "trim":
                self._log("У режимі «TRIM = середнє» TRIM рахується сам — змінюйте MIN і MAX", "warn")
                return
            self._param_set(index, "MIN" if cell == "below" else "MAX", value)
        elif cell == "trim":
            self._param_set(index, "TRIM", value)
        else:
            self._offset_set(index, cell, value)

    def _param_set(self, index, kind, value, delta=0):
        """Змінити SERVOn_<kind> з панелі (записується, коли перестали клацати)."""
        if self._blocked():
            return
        if not self.link_ok:
            self._log("Немає зв'язку з політником — спершу «Підключитися»", "warn")
            return
        base = self._value(index, kind)
        if value is None:
            if base is None:
                self._log(f"{self._param(index, kind)} ще не прочитано", "warn")
                return
            value = base + delta
        value = int(max(self.config_data["low"], min(self.config_data["high"], value)))
        row = self.rows[index]
        row["pending"][self._param(index, kind)] = value
        row["pending_time"] = time.monotonic()
        if row["write_job"]:
            self.after_cancel(row["write_job"])
        # Записуємо, коли перестали клацати: одна зміна замість десятка.
        row["write_job"] = self.after(450, lambda: self._commit(index))
        self._refresh_values()

    def _commit(self, index):
        row = self.rows[index]
        row["write_job"] = None
        if not row["pending"]:
            return
        output = self._output(index)
        # Залежні параметри слідом — лише для виходів з галочкою.
        rules = self._output_rules(output) if output["enabled"] else []
        row["pending_time"] = time.monotonic()
        self.worker.commands.put(("write_values", dict(row["pending"]), rules))

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

    def _toggle_mode(self, index):
        output = self._output(index)
        output["mode"] = MODE_OFFSETS if output.get("mode") == MODE_MIDDLE else MODE_MIDDLE
        self._log(f"SERVO{output['channel']}: " + ("TRIM = середнє між MIN і MAX" if output["mode"] == MODE_MIDDLE
                                                   else "MIN/MAX = TRIM ± відступи"))
        self._send_rules()
        if output["enabled"] and self.config_data["auto"]:
            self._apply_output(index)
        self._refresh_values()
        self._save_later()

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
                             self.config_data["low"], self.config_data["high"], output.get("mode", MODE_OFFSETS))

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
            middle = output.get("mode") == MODE_MIDDLE
            row["mode"].configure(text="TRIM = середнє" if middle else "± від TRIM")
            # Запис підтверджено — прибираємо «очікування».
            for name, value in list(row["pending"].items()):
                current = self.params.get(name)
                if current is not None and abs(current - value) < 0.5 and not row["write_job"]:
                    del row["pending"][name]
            pending = bool(row["pending"])
            value_color = TEXT if enabled else FAINT
            current_min, trim, current_max = (self._value(index, kind) for kind in ("MIN", "TRIM", "MAX"))

            def show(cell, value, param=None, color=value_color):
                waiting = param is not None and param in row["pending"]
                row[cell]["value"].configure(text="—" if value is None else str(value), fg=WARN if waiting else color)

            if middle:
                show("below", current_min, self._param(index, "MIN"))
                show("trim", trim, self._param(index, "TRIM"), MUTED if enabled else FAINT)
                show("above", current_max, self._param(index, "MAX"))
            else:
                row["below"]["value"].configure(text=f"−{output['below']}", fg=value_color)
                show("trim", trim, self._param(index, "TRIM"))
                row["above"]["value"].configure(text=f"+{output['above']}", fg=value_color)
            if None in (current_min, trim, current_max):
                row["result"].configure(text="—", fg=FAINT)
                self._set_state(row, "чекаю дані" if self.link_ok and enabled else "", FAINT)
                continue
            row["result"].configure(text=f"TRIM {trim}" if middle else f"{current_min:g} – {current_max:g}")
            wrong = self._mismatches(index)
            if not enabled:
                row["result"].configure(fg=FAINT)
                self._set_state(row, "не керується", FAINT)
            elif pending:
                row["result"].configure(fg=WARN)
                self._set_state(row, "записую…", WARN)
            elif not wrong:
                row["result"].configure(fg=OK)
                self._set_state(row, "✓ збігається", OK)
            else:
                mismatched += 1
                row["result"].configure(fg=ERR)
                if middle:
                    need = f"треба TRIM {next(iter(wrong.values()))}"
                else:
                    need = "треба " + " – ".join(str(wrong.get(self._param(index, kind), self._value(index, kind)))
                                                 for kind in ("MIN", "MAX"))
                self._set_state(row, need, ERR)
        self._update_overlay()
        self._update_checks_ui()
        if not self.link_ok:
            waiting = self.connected_request
            summary, color = (self.link_text, WARN) if waiting else ("Не підключено", FAINT)
            if waiting and self.mp_copies >= 2:
                summary = f"Відкрито {self.mp_copies} копії Mission Planner — закрийте зайву"
            title = "чекаю на політник" if waiting else "не підключено"
        elif not selected:
            summary, color, title = "Жоден вихід не вибрано", MUTED, "нічого не вибрано"
        elif mismatched:
            summary, color = f"Не збігається: {mismatched}", ERR
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
        self._label(body, "Вихід", MUTED, (FONT, 8)).grid(row=6, column=1, sticky="w", padx=(8, 0))
        self._label(body, "Назва (необов'язково)", MUTED, (FONT, 8)).grid(row=6, column=2, columnspan=2,
                                                                         sticky="w")
        rows = []
        for index, output in enumerate(self.config_data["outputs"]):
            enabled = check(7 + index, "", output["enabled"])
            channel = tk.StringVar(value=str(output["channel"]))
            name = tk.StringVar(value=output.get("name", ""))
            self._label(body, f"SERVO{output['channel']}", TEXT, self.font_strong).grid(
                row=7 + index, column=1, sticky="w", padx=(8, 12), pady=2)
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

        section(19, "ПЕРЕВІРКИ ТА КОНФІГ")
        checks_row = tk.Frame(body, bg=BG)
        checks_row.grid(row=20, column=0, columnspan=4, sticky="ew", pady=3)
        count = len(self.config_data["checks"])
        self._label(checks_row, f"Перевірок налаштувань: {count}. Лише читання й звірка —\n"
                                "програма нічого не змінює в політнику.", MUTED, (FONT, 9), justify="left",
                    anchor="w").pack(side="left", fill="x", expand=True)
        FlatButton(checks_row, "Відкрити", lambda: (window.destroy(), self._open_checks()), padx=12, pady=5,
                   bg=SURFACE, fg=ACCENT, font=self.font_strong).pack(side="right")

        section(16, "MISSION PLANNER")
        plugin_row = tk.Frame(body, bg=BG)
        plugin_row.grid(row=17, column=0, columnspan=4, sticky="ew", pady=3)
        plugin_status = self._label(plugin_row, "", MUTED, (FONT, 9), justify="left", anchor="w", wraplength=380)
        plugin_status.pack(side="left", fill="x", expand=True)

        plugin_button = FlatButton(plugin_row, "", lambda: None, padx=12, pady=5, bg=SURFACE, fg=ACCENT,
                                   font=self.font_strong)
        plugin_button.pack(side="right")

        def refresh_plugin_status():
            folders = find_mission_planner_dirs()
            installed = [folder for folder in folders if (folder / "plugins" / PLUGIN_FILE).is_file()]
            try:
                bundled = plugin_source_path().read_bytes()
            except OSError:
                bundled = None
            outdated = [folder for folder in installed
                        if bundled is not None and (folder / "plugins" / PLUGIN_FILE).read_bytes() != bundled]
            if outdated:
                plugin_status.configure(text="Встановлено стару версію плагіна — натисніть «Оновити»,\n"
                                             "потім перезапустіть Mission Planner", fg=WARN)
                plugin_button.configure(text="Оновити")
                plugin_button.command = lambda: self._install_plugin(window, refresh_plugin_status)
            elif installed:
                text, color = plugin_report(installed)
                plugin_status.configure(text=text, fg=color)
                plugin_button.configure(text="Видалити")
                plugin_button.command = lambda: self._remove_plugin(window, installed, refresh_plugin_status)
            else:
                plugin_status.configure(text="Необов'язково: плагін, щоб Servo Output і\n"
                                             "Full Parameter List у MP оновлювалися самі", fg=MUTED)
                plugin_button.configure(text="Встановити")
                plugin_button.command = lambda: self._install_plugin(window, refresh_plugin_status)

        refresh_plugin_status()

        def keep_refreshing():
            if window.winfo_exists():
                refresh_plugin_status()
                window.after(2000, keep_refreshing)

        window.after(2000, keep_refreshing)

        buttons = tk.Frame(body, bg=BG)
        buttons.grid(row=21, column=0, columnspan=4, sticky="e", pady=(16, 0))
        FlatButton(buttons, "Скасувати", window.destroy, padx=12, pady=6, bg=SURFACE).pack(side="left", padx=6)
        FlatButton(buttons, "Зберегти", save, padx=14, pady=6, bg=SURFACE, fg=ACCENT,
                   font=self.font_strong).pack(side="left")
        window.bind("<Escape>", lambda _e: window.destroy())
        window.bind("<Return>", lambda _e: save())
        self.update_idletasks()
        window.geometry(f"+{self.winfo_rootx() + 40}+{self.winfo_rooty() + 60}")
        window.after(10, lambda: dark_title_bar(window))

    def _install_plugin(self, parent, done):
        """Покласти плагін у папку plugins Mission Planner (з правами адміністратора, якщо треба)."""
        source = plugin_source_path()
        if not source.is_file():
            messagebox.showerror(APP_NAME, f"Не знайдено файл плагіна: {source}", parent=parent)
            return
        folders = find_mission_planner_dirs()
        if not folders:
            chosen = filedialog.askdirectory(
                parent=parent, title="Виберіть папку Mission Planner (де MissionPlanner.exe)")
            if not chosen:
                return
            if not (Path(chosen) / "plugins").is_dir() and not (Path(chosen) / "MissionPlanner.exe").is_file():
                messagebox.showerror(APP_NAME, "Це не схоже на папку Mission Planner", parent=parent)
                return
            folders = [Path(chosen)]
        failed = []
        for folder in folders:
            target = folder / "plugins" / PLUGIN_FILE
            try:
                target.parent.mkdir(exist_ok=True)
                shutil.copyfile(source, target)
                self._log(f"Плагін встановлено: {target}", "ok")
            except OSError:
                failed.append(target)
        for target in failed:
            # Program Files — потрібні права адміністратора: Windows спитає дозвіл.
            staged = Path(tempfile.gettempdir()) / PLUGIN_FILE
            shutil.copyfile(source, staged)
            try:
                import ctypes
                arguments = f'/c mkdir "{target.parent}" 2>nul & copy /Y "{staged}" "{target}"'
                result = ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd.exe", arguments, None, 0)
            except Exception:
                result = 0
            if result <= 32:
                messagebox.showerror(APP_NAME, f"Не вдалося скопіювати плагін у\n{target.parent}\n\n"
                                     "Скопіюйте вручну файл з папки програми.", parent=parent)
                return
        self._wait_plugin([folder / "plugins" / PLUGIN_FILE for folder in folders], parent, done, 30)

    def _remove_plugin(self, parent, folders, done):
        failed = []
        for folder in folders:
            target = folder / "plugins" / PLUGIN_FILE
            try:
                target.unlink()
                self._log(f"Плагін видалено: {target}", "ok")
            except FileNotFoundError:
                pass
            except OSError:
                failed.append(target)
        for target in failed:
            try:
                import ctypes
                ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd.exe", f'/c del /F /Q "{target}"', None, 0)
            except Exception:
                pass
        self._wait_removed([folder / "plugins" / PLUGIN_FILE for folder in folders], parent, done, 30)

    def _wait_removed(self, targets, parent, done, attempts):
        if not any(target.is_file() for target in targets) or attempts <= 0:
            done()
            if not any(target.is_file() for target in targets):
                messagebox.showinfo(APP_NAME, "Плагін видалено. Перезапустіть Mission Planner.", parent=parent)
            return
        self.after(500, lambda: self._wait_removed(targets, parent, done, attempts - 1))

    def _wait_plugin(self, targets, parent, done, attempts):
        if all(target.is_file() for target in targets):
            done()
            self._log("Плагін для Mission Planner встановлено — перезапустіть Mission Planner", "ok")
            messagebox.showinfo(APP_NAME, "Плагін встановлено.\n\nПерезапустіть Mission Planner — після цього "
                                "сторінки Servo Output і Full Parameter List оновлюватимуться самі.", parent=parent)
            return
        if attempts <= 0:
            done()
            self._log("Плагін не встановлено (немає дозволу?)", "warn")
            return
        self.after(500, lambda: self._wait_plugin(targets, parent, done, attempts - 1))

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
                    if event[1] and not self.link_ok:
                        self.link_since = time.monotonic()
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
        if self.config_data["checks"] and now >= self.next_checks_ui:
            self.next_checks_ui = now + 1.0
            self._update_checks_ui()
        if os.name == "nt" and self.connected_request and not self.link_ok and now >= self.next_mp_check:
            # Дві копії MP (стара «зависла» після перезапуску) — частa причина, чому даних немає.
            self.next_mp_check = now + 5.0
            try:
                copies = count_mission_planner_processes()
            except Exception:
                copies = 0
            if copies != self.mp_copies:
                self.mp_copies = copies
                refresh = True
        for row in self.rows:
            if row["pending"] and not row["write_job"] and now - row["pending_time"] > 6:
                row["pending"].clear()
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
        if self.overlay is not None:
            try:
                self.config_data["overlay_pos"] = f"+{self.overlay.winfo_x()}+{self.overlay.winfo_y()}"
            except tk.TclError:
                pass
        save_config(self._collect_config())
        self.worker.commands.put(("quit",))
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
