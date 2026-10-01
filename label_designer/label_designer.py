#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Редактор та друк наліпок (типово 50x30 мм, є власні пресети) для Windows/Xprinter."""

import base64
import bisect
import copy
import csv
import ctypes
import hashlib
import io
import ipaddress
import json
import math
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import uuid
import zlib
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter import font as tkfont

if os.name == "nt":
    import winreg

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageMath, ImageOps, ImageTk
import barcode
import qrcode
from barcode.writer import ImageWriter


LABEL_WIDTH_MM = 50.0
LABEL_HEIGHT_MM = 30.0
BASE_PX_PER_MM = 14
PX_PER_MM = BASE_PX_PER_MM
CANVAS_WIDTH = round(LABEL_WIDTH_MM * PX_PER_MM)
CANVAS_HEIGHT = round(LABEL_HEIGHT_MM * PX_PER_MM)
DEFAULT_PRINTER = "Xprinter XP-420B"
DRIVER_NAME = "Xprinter XP-420B"
DEFAULT_NETWORK_IP = "192.168.0.29"
NETWORK_PORT = 9100
DRAG_THRESHOLD_PX = 5
STATUS_REFRESH_MS = 4000
SAFE_MARGIN_MM = 1.5
MAX_HISTORY = 100
IMAGE_FILETYPES = "*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff"
# Межі розміру наліпки: XP-420B друкує смугу шириною до 108 мм.
MIN_LABEL_MM = 5.0
MAX_LABEL_WIDTH_MM = 108.0
MAX_LABEL_HEIGHT_MM = 300.0
BUILTIN_SIZE_PRESETS = (
    (30.0, 20.0),
    (40.0, 25.0),
    (40.0, 30.0),
    (50.0, 30.0),
    (58.0, 40.0),
    (60.0, 40.0),
    (70.0, 50.0),
    (100.0, 50.0),
    (100.0, 100.0),
    (100.0, 150.0),
)
ZOOM_LEVELS = ("50%", "75%", "100%", "125%", "150%", "200%", "300%")
UI_FONT = "Segoe UI"
APP_NAME = "Xprinter Label Studio"
THEMES = {
    "light": {
        "title": "Світла",
        "dark": False,
        "panel": "#ffffff",
        "header": "#e7ebf1",
        "soft": "#f1f4f9",
        "border": "#dde3ec",
        "text": "#151b28",
        "muted": "#6a7587",
        "field": "#ffffff",
        "accent": "#2563eb",
        "accent_hover": "#1d4fd8",
        "accent_pressed": "#1a43b8",
        "accent_soft": "#e7eefe",
        "accent_text": "#ffffff",
        "accent_disabled": "#a8bff3",
        "pressed": "#d5e2fc",
        "workspace": "#e4e8ef",
        "shadow": "#c3cad6",
        "glow": (),
        "status": "#f5f7fa",
        "ok": "#15803d",
        "error": "#c62828",
        "checking": "#a16207",
        "ok_hint": "#3f7d4f",
        "selection": "#2563eb",
        "tooltip_bg": "#151b28",
        "tooltip_fg": "#ffffff",
        "logo_gear": "#151b28",
        "logo_bolt": "#2563eb",
    },
    "purple": {
        "title": "Фіолетова",
        "dark": True,
        "panel": "#1a1430",
        "header": "#120d24",
        "soft": "#251c43",
        "border": "#3b2e66",
        "text": "#efe9ff",
        "muted": "#a193cf",
        "field": "#120d24",
        "accent": "#a855f7",
        "accent_hover": "#b872fb",
        "accent_pressed": "#8f3ce6",
        "accent_soft": "#2e2356",
        "accent_text": "#ffffff",
        "accent_disabled": "#4c3a78",
        "pressed": "#3a2a6b",
        "workspace": "#0d0919",
        "shadow": "#000000",
        "glow": ("#170f2c", "#211343", "#2f175f", "#43207f"),
        "status": "#120d24",
        "ok": "#4ade80",
        "error": "#f87171",
        "checking": "#fbbf24",
        "ok_hint": "#86efac",
        "selection": "#9333ea",
        "tooltip_bg": "#efe9ff",
        "tooltip_fg": "#1a1430",
        "logo_gear": "#efe9ff",
        "logo_bolt": "#c084fc",
    },
    "black": {
        "title": "Чорна",
        "dark": True,
        "panel": "#0f1115",
        "header": "#08090b",
        "soft": "#181b21",
        "border": "#2a2f38",
        "text": "#e8ecf2",
        "muted": "#8a93a3",
        "field": "#08090b",
        "accent": "#22d3ee",
        "accent_hover": "#4fdcf2",
        "accent_pressed": "#0fb5cf",
        "accent_soft": "#10262c",
        "accent_text": "#031014",
        "accent_disabled": "#1d4a52",
        "pressed": "#123a42",
        "workspace": "#050608",
        "shadow": "#000000",
        "glow": ("#07161a", "#092128", "#0b3039", "#0e4450"),
        "status": "#08090b",
        "ok": "#4ade80",
        "error": "#f87171",
        "checking": "#fbbf24",
        "ok_hint": "#86efac",
        "selection": "#0891b2",
        "tooltip_bg": "#e8ecf2",
        "tooltip_fg": "#0f1115",
        "logo_gear": "#e8ecf2",
        "logo_bolt": "#22d3ee",
    },
    "cyber": {
        "title": "Кіберпанк",
        "dark": True,
        "panel": "#140b22",
        "header": "#0c0616",
        "soft": "#1f1233",
        "border": "#3a1f5c",
        "text": "#f7f0ff",
        "muted": "#a68bc9",
        "field": "#0c0616",
        "accent": "#ff2a6d",
        "accent_hover": "#ff4f87",
        "accent_pressed": "#e0125a",
        "accent_soft": "#2d1238",
        "accent_text": "#ffffff",
        "accent_disabled": "#5a2246",
        "pressed": "#4a1a3f",
        "workspace": "#07030d",
        "shadow": "#000000",
        "glow": ("#061219", "#071d26", "#082b36", "#0a3f4d"),
        "status": "#0c0616",
        "ok": "#39ff88",
        "error": "#ff5470",
        "checking": "#ffd23f",
        "ok_hint": "#7dffb2",
        "selection": "#ff2a6d",
        "tooltip_bg": "#05d9e8",
        "tooltip_fg": "#0c0616",
        "logo_gear": "#f7f0ff",
        "logo_bolt": "#05d9e8",
    },
    "synthwave": {
        "title": "Синтвейв",
        "dark": True,
        "panel": "#241734",
        "header": "#1a1029",
        "soft": "#2f1f45",
        "border": "#4b2f6b",
        "text": "#fdf3ff",
        "muted": "#b79fd6",
        "field": "#1a1029",
        "accent": "#ff71ce",
        "accent_hover": "#ff8fd9",
        "accent_pressed": "#e655b5",
        "accent_soft": "#3a2152",
        "accent_text": "#1a1029",
        "accent_disabled": "#6b3d63",
        "pressed": "#4d2b66",
        "workspace": "#130b1e",
        "shadow": "#000000",
        "glow": ("#1c1030", "#2a1340", "#3d1652", "#561a63"),
        "status": "#1a1029",
        "ok": "#72f1b8",
        "error": "#fe4450",
        "checking": "#fede5d",
        "ok_hint": "#a0f5cf",
        "selection": "#e040a0",
        "tooltip_bg": "#fede5d",
        "tooltip_fg": "#241734",
        "logo_gear": "#fdf3ff",
        "logo_bolt": "#fede5d",
    },
    "acid": {
        "title": "Кислота",
        "dark": True,
        "panel": "#151515",
        "header": "#0d0d0d",
        "soft": "#202020",
        "border": "#333333",
        "text": "#f2f2f2",
        "muted": "#9a9a9a",
        "field": "#0d0d0d",
        "accent": "#c6ff00",
        "accent_hover": "#d4ff3d",
        "accent_pressed": "#a8d900",
        "accent_soft": "#252e05",
        "accent_text": "#0d0d0d",
        "accent_disabled": "#4a5a10",
        "pressed": "#3a4a00",
        "workspace": "#080808",
        "shadow": "#000000",
        "glow": ("#0e1203", "#141b04", "#1d2805", "#283a06"),
        "status": "#0d0d0d",
        "ok": "#c6ff00",
        "error": "#ff4d4d",
        "checking": "#ffd400",
        "ok_hint": "#e0ff80",
        "selection": "#7cb300",
        "tooltip_bg": "#c6ff00",
        "tooltip_fg": "#0d0d0d",
        "logo_gear": "#f2f2f2",
        "logo_bolt": "#c6ff00",
    },
    "bubblegum": {
        "title": "Бабл-гам",
        "dark": False,
        "panel": "#ffffff",
        "header": "#ffe4f1",
        "soft": "#fff0f7",
        "border": "#ffd0e6",
        "text": "#2b1020",
        "muted": "#9a6a84",
        "field": "#ffffff",
        "accent": "#ff3d9a",
        "accent_hover": "#ff1f8a",
        "accent_pressed": "#e01577",
        "accent_soft": "#ffe1ef",
        "accent_text": "#ffffff",
        "accent_disabled": "#ffb3d6",
        "pressed": "#ffcfe5",
        "workspace": "#fbe3ee",
        "shadow": "#efbcd3",
        "glow": (),
        "status": "#fff5fa",
        "ok": "#0f9d58",
        "error": "#d62d4f",
        "checking": "#b7791f",
        "ok_hint": "#3f8f5f",
        "selection": "#ff3d9a",
        "tooltip_bg": "#2b1020",
        "tooltip_fg": "#ffffff",
        "logo_gear": "#2b1020",
        "logo_bolt": "#ff3d9a",
    },
    "mint": {
        "title": "М’ята",
        "dark": False,
        "panel": "#ffffff",
        "header": "#dff7ee",
        "soft": "#effbf6",
        "border": "#c8eedf",
        "text": "#0f2a22",
        "muted": "#5b7d70",
        "field": "#ffffff",
        "accent": "#00b37e",
        "accent_hover": "#009e6f",
        "accent_pressed": "#008a61",
        "accent_soft": "#dcf6ec",
        "accent_text": "#ffffff",
        "accent_disabled": "#99dcc4",
        "pressed": "#c2eedd",
        "workspace": "#e2f3ec",
        "shadow": "#b9dccd",
        "glow": (),
        "status": "#f3fbf8",
        "ok": "#0a8f5a",
        "error": "#d14343",
        "checking": "#a16207",
        "ok_hint": "#2e7d5b",
        "selection": "#00a372",
        "tooltip_bg": "#0f2a22",
        "tooltip_fg": "#ffffff",
        "logo_gear": "#0f2a22",
        "logo_bolt": "#00b37e",
    },
}
DEFAULT_THEME = "light"
# Колір ліній-підказок вирівнювання під час перетягування (видно на білій наліпці в усіх темах).
GUIDE_COLOR = "#ff2d95"
MAX_RECENT_FILES = 8
# Поточна палітра; оновлюється на місці при зміні теми.
COLORS = dict(THEMES[DEFAULT_THEME])
# Ctrl+літера має працювати й в українській/російській розкладці:
# у Windows беремо віртуальний код клавіші, інакше — кириличний keysym.
CTRL_KEY_VK = {
    65: "a", 67: "c", 68: "d", 78: "n", 79: "o", 80: "p", 82: "r", 83: "s", 84: "t", 86: "v", 87: "w",
    88: "x", 89: "y", 90: "z",
}
# Linux/X11: фізичні коди клавіш стандартної PC-клавіатури.
CTRL_KEY_X11 = {
    38: "a", 54: "c", 40: "d", 57: "n", 32: "o", 33: "p", 27: "r", 39: "s", 28: "t", 55: "v", 25: "w",
    53: "x", 29: "y", 52: "z",
}
CTRL_KEY_CYRILLIC = {
    "Cyrillic_ef": "a", "Cyrillic_es": "c", "Cyrillic_ve": "d", "Cyrillic_te": "n",
    "Cyrillic_shcha": "o", "Cyrillic_ka": "r", "Cyrillic_yeru": "s", "Ukrainian_i": "s",
    "Cyrillic_em": "v", "Cyrillic_che": "x", "Cyrillic_en": "y", "Cyrillic_ya": "z",
    "Cyrillic_ie": "t", "Cyrillic_tse": "w", "Cyrillic_ze": "p",
}
# Логотип: дві маски (шестерня і блискавка), щоб фарбувати їх під тему.
LOGO_GEAR_MASK = (
    "iVBORw0KGgoAAAANSUhEUgAAAKAAAACgCAAAAACupDjxAAAM1UlEQVR42u1ca5CU1Zl+3nO+uTTMOENgRm4D4SIoBFEQXVm1"
    "NAF0gzHl1iYbL8ledC1XcTcmIRvKJFsWG6ti1BgrViVruTEom8u6WpEUqbiJhgSybFAEJAiiw3B1howwBmamu8/l2R99me6G"
    "6enu6e8bqzLvn6npb+Y7Tz/v9bznAozKqIzKqIzKqIzKSIoM//8FAgEIEATfPwBFCbw/y6ckOdIAlYhLgWgYG5swrqFOaPtO"
    "dZ/q600AALT4KoGsBKBSjgBa58ybN6utpSFWk3ngE309xzr2/n5vRwKAhucIAFTKAph22RVL5ozPAiMAiGReZjp2btmyKwFo"
    "+mgBiqIHFl173aIGANZRaQ14lX5MgCREA8D+X23cdDJij9cA5q75nSeZjCdJkjx14CA988XbeNyRPPLk1aIiY1CUQ83Kv1tR"
    "DyalFsDpt994fd/BzhMTdzQUvIYCwFlVg1WPaxcde8337CJpLcn+//36DdMyqL5Hk0eg5eY7//sISa6Hiki7Gmhe3UE66+l7"
    "Xrh9durTQCsVyBLr8hXcfyFwzvJHtm0YoyQSeEqh9q520jiShl8EIIHODK7wC9o8Au9HnapOoiqZvpWvkmmeDO9W9Srv8Y3M"
    "odBxd0wJRAcR4ROFac9k4ZGG/4wg7w+k7g2bMMYYY5331n8YOsLYooHbuugGKDL8p3yACHBvroL/var4gqGe20mPfhJO5wam"
    "wgTm8JSn1MaaJ5w76dzWY2uUjy40K6zoYL6Tnslg7j+MX3xBRJ6R8k98yRcEORq36gyAEgRBEARaVd9zi6lYuzHf+TSd+Fyf"
    "dT4Yc8Zf0mb1L/DRucekzUxaMifKWUduWxpZfhiC2/P30hh2fmEnfcoKnSNfvF6A9we+i4/SGL46F03fIg3pDfnL5SnTfD/g"
    "u+Q4jeVPmlADXLeX3pB7PpWuuUZeNC4+TmP4pIKGaDQ/TvY/0PB+gQeNuUdpDL+JVMGpgeufX4KqpojhmLLCxH20hg9CSzZi"
    "I/tLlWJs5W8TFdtMY/hoLiStquobajgcaqxn0vI/qktZAb5l2+dXajEBVtMYbgzCK4eVLH2XrzdVphSNj1hruatZQot3Ig1d"
    "7OePKqJQSetBWn/w/DCrTiVP0BjeXckYGs/SWr44ZKU4PArHvkbj+haUn5U0/paGdPxMqHW7wodOO8OtNeX6oZKpf3COdL5z"
    "ooSZcwPcQWu4plwaNH6YKlAN14c79QnwEyZd79zylKxxbab4s7whXCVLW7cz3FDWIKJqd2amuM53NIVaWGncSWt5fTkINf5x"
    "oHo2fEKFSaFovZVJ7qorPR+INB3ybmCC+1qdhFk9a1mSdJa3lU6hxuqBKZy3ycvCdpPPWuf8W2NLpUGk6UgugQ+Fje9Wek/L"
    "O0rNCQHuGbBA5w80hdo7C7Ai4Tzp/O7a0igUqXsjl8BPh0qgxqKe1GiOf1naSHldNMutOkwCFWYeSqvL8helhTOFjTka5kfD"
    "rWam7coO5s3CUsZSmBv33jnnvPfWb1HhZuKbBuKF4TdKARjgKzSZ9QRfql1UXG7FBuzdsX1sCfMT0TtouX/73vaj3T2J7bWh"
    "xmho3JtrT38xNB0alzjv+2aiLnbO+ImzW0Nv7E08mV3/Mf6JoQEG+FfG+T8RrmsMrK44dgyu4wwgJ9dCsEGUiIhEsLwh/5kd"
    "W3H6JUMxozCjlz55QWQMCurf4sDCwf2DpjuV+XH5GCO/f1MYEUDq+MZsL1ZwNVxxgMSVcPiN01EBBPCzrN0pXNhKKQrQqyXQ"
    "2BJhA81j6x/EWus8Ib554WDGpdJmOmkOahKvRtcAB9XJrSoIUkt+HosHc+MgbQRzG53uOIgINUz8w4emTP9g25SW8QJcVHzo"
    "APcwzudHpPtcM/42Gu6QQSjMQLoAwJ6IAYoOAq3suy8lNdpainqxw2wI9kbMHZ21zot0d4kfN3UQdlKLVwymQKMjShPMcedT"
    "R+FkavE42NwCnexSIwEQCkfg0VbMBgVjG4ET7V6NhJcIjoFoK85gfS3Y9LXZ1osegZWuLgimD2JfaYB1ChL74o7vL6Vj9Cx2"
    "QzCpOMCUL4/9zJaf3xj4iDkkTkFQXwJATccVz72yInIOewHUDFGwpiOnuOTCZZEnlCQEehAGC+tETf/HEQiFGLLkz/lEj0Cg"
    "waA54izarIscX12RzblnmQqMjRScEnEAYHH26cZZADZFlpJF0zmg5XJwKIAuOyUQjI8MIC3GXHjV1UsmQKOnuIrjpjYLsTUi"
    "gMKJK69ZOgOpHZuHB/HjIBXL+/prswy2xvojmX1qu+o+AFaUgDhYvKLuOZGhTfy5k6PZfUXMdAmPIFWeHCkWZij976QBeq/q"
    "G6MKz7N0TbZxcLhoLlaZatopveWKneIjMcEPzMioiipxuKjlB/gqDekcO++OKhErXOp9tr91cExRGyT2QAClvrfkca+jArgo"
    "qyni7b6ijqkw39D5bcsR6kJ7vhNjXbZDaPit4gMLYgdouBy1kVVagrr92f6b5d8MBjCtYtW/Cw5Xau+jA7hgZqYypnbbB2sL"
    "Zd18C4ArnYsKHxSWKZc1wQNvDpG+NC73jn+cHF0xLcHr3mZNcN2gXfRMfxC7DinbeJVEV+3zecnsZxb8csjkpfF9Hw97I0WB"
    "Ed5mUmslnr3Th1RdgL+m4fHxEW6C1rjuXab28L48tGkJJnTT8lMSWRwEAizcT0da3ltC+FX4gY/zp5FOOQNM3kzn2T+rhGE1"
    "Pkbr+86LFKFG7A0m/cZSBhWMaWeCDyBCHUOp2Se95U0lDRpgLZM82iwCQJTWkTD4EBM81FiSayrM6vWGd6EuiKoFp6TlOJMl"
    "a01hPRN+Xw0AjJl17a3hryhq3EfjT88o0e41LrXO8cvLPv/dlw/0M+RtHwBExr/jk3yy5HEUNgzsCjD2WGvIiU9jLY2Pzyt5"
    "GI3LnaN3xljnabk+XI9WMu2kS/KpMhSl8Wzu2RHeEipCjadpXO95ZehJyfx+lz1J6lzPnOqHbZUJERpXO2f4cFmWrvFIDoWO"
    "26p+9lEB0FoA0fU7afyxCWWNIOoDh33uEbqnq1zdaFz20RgArWuwlras3YPpF9yUewrM8KtV9WSNee/yre9+fByApUlr+HLZ"
    "/VyN53KU7C3/vIrRUGPqWzQk3/nhTfP20Lre+WUbuZKpxweU7Pyb46q3BUmjdScNnXEkE/SWn6vg22t8ckDJlndVj0CN1m3p"
    "V3tr6S1frGjhLcB3MgidP9RYNQIDtL2WY9/e+a62iqKYqNjvsnsP/6VqoVphwZu5/ueNr3SLosLs43Qkve+cUC0CVWaGNBAg"
    "7qv4y2ssM86Tlv9WLQvUuNLnHuNmks8M490BbqfxnsebqhUFFeb2OZ/L36/qh5OkAnyZxrueG6uWiwWbcjRsuHvC8F4d4CGa"
    "JL9ZNR8JsCY3er09fZjGIxqPkftihT6iKt0yoLA4q2LLA3OGbdyi8Oh7iwvVkDpHWVHwQu0eZu5BeHtOFZxPBB8sLGUCXPSb"
    "NS05NV1ZfvztzFmfXTOqExzkTHzLusljD87L1HRFLCQofBzoq1zqootNrdUKXiqPUNG4OU5ryfiGmycUsQ0d6NT3yycQKxOe"
    "zvGZWBizRQVofJZ0qYPa7Fx/y1n2MkkQpL5U41Vr82YLSiP2NeO9Jb8SyglvhaAOX2faD721JNvPOWu1raZcs/q5w+T2xmyQ"
    "lwBYsYPeG3Z9HGH0AxSm/3b7s8xJBd7YxOwCKgQtqx97Yfd7JOkT/K/04VrRwHnrUvfUvDgzlHmiyOx9JG3ezUGeSwtsKcCd"
    "qSLNWEcmuRZBaovGpAfeozOGfV+ScJoVSjYxbiwLruX5RAEbCs+auM3Q7A1vQa0Cpt7fRRpDvnwxQmpViCx4IX0bTm66L+jb"
    "Cpo6c69n8q7vz4CFj3WTJkkeuwuh9nr+amcBRMOH8wFqrMgrqOjYfsdGk4LX/9hkhNnpUYL6VfvTFzJlVPzjoFbl5OoADxXc"
    "W+JJMpkkk08vQNitMg00rtpD+gyNlpvTIS51UUoQBK/lM0h6Ezdk31OLgPB7oqKB+ptf8qSzjqRnz8M3L27N4WVm0uf7uTUk"
    "Dz14foX1Rca0yyhwHHDprTdMB7xPK/dE59F3uk709CaosOLvc+598YQGEpvWbzhZjRvgSmZRgMYb1x0lSWviycI71VLea1Mh"
    "qW/TFy7AsNgrk8G0uzig+bLlV8xvAADvPCipGxxFgZCUr7L9/1769X5AyXCv+CvfdkXBAZi28JKL5k5pOPO56Wrf/cr2fXFA"
    "qrE+XpFziRILADXntk1rm9wyriFWo2ASp3u6O48ePnTsNABoqc7qvVQeGgdFIBq+ardgDis8iYik7kQkIMj8NiL72UdlVEZl"
    "VEZlVEblT1T+HwfSvL+armHtAAAAAElFTkSuQmCC"
)
LOGO_BOLT_MASK = (
    "iVBORw0KGgoAAAANSUhEUgAAAKAAAACgCAAAAACupDjxAAAMWklEQVR42s2ceZRdVZXG9znnvpdU5omQxCIhaCwMC6cQIygu"
    "ggIBumkVgpimncBIWhvadi0B0XYJRluCGGmHtAFxgASFQNAITZrBSAbQQKOGRGRQwtSYqaiEenufve/9+o/7Eoqk3ivs1nvv"
    "/fuudX/17f3ts88+5xVRgY//P7zriuTLXnXGIR11DwDk6nWPVAzOuUBpbNBgh6RWTyCS+e5lv3Gggp9AM5/DK31efGdTQ1eg"
    "foesndzwA+viiCgd8tAsLVZC5zvWg01fgXpmpumeybmESWF86dVHS4JkhwHOOUKaZs6H4ACAfFIjg8vMKA4bBwLt2EkoEjDY"
    "pfMksUEL/z0zePKUWQrnQs0hAxBqdTJQFjOZcMu4zGe1xXtCWmCAE/ogRBnLB3xz/P2Ixrjqz6pJfwEDH8eqjPVDfHD9P947"
    "52o0ZRPEGN8kX2gBpK7/yaLgyc72ugSa/DDYGNcWzTd2M0St5y0U2vNN2Qw2wdJi+Vyo3QHWiDPaW7LJx1hMrkg+SujbYGN8"
    "aiC+yTnfV4vVjxI6H2yMqyhxr0S/Kyi4Yg18gkVlrPKhPd+rHgab4MrC+Y7Ykarg16Ocb/vaxJzv8oL5PI3fAonptq62Bg40"
    "6SGICRYVnH+OBq+BqKRz2hrE0/gHwcZYVLB+Lgy/FWyCjw/AN+aXpfjD1WgpGtbA4gH4Rt3brH/F8hHRe8HGuNm1+7Cn4WvA"
    "xvhG0Xxh7Mm7owruG9nOwJ5G3AM2xneK5vP0xj3QmG6b0s7Anobfg4YxlhXsX/Ju2LpMNGantUvAQKPWoGGMG0LBfC7QjRAT"
    "LGiv36hfgI3xo8T5Yu2R0CKwMb7QTj9Po9eDjXFDKJ7vPIgxrmvXIQQak/MtL4FvjkZl3Nvh/UD6NbAsuILrX6Dp29MY8egE"
    "asc3Yh3YGD/wRevn3djNkJh2v6GNQTwNvSvnc0XzuRBuB2tMT23L13EHGuXwJXQV2BjntzGwp8GrwMa4zhedf5TQBRBlXNWW"
    "b9BPwdbAjaEEvnenUQW3h9ZLq6dBPwEbY2W9cL5AM3pMBZvHtjawp6G3g62BVYOo4PwjT51/RIzZ9umtDeJpyGo0jPGzjsL5"
    "nBu6AaIaT2jD5zr+E2yM20rgS+gGsAk+0togzoUfo2ENbCiejxL6AtgYl7bm8y4sBxvjjoPIFc83r9khtDSwc2EZ2BhrRpah"
    "3zG9qoK1g1v2ns6769AwweMHtx90/XUKzLRnsxizra2HgM7Td3P9Jg8qPL6Oxv4Gotr7tpbauEDfRsMY946iwh8XktVgi3h/"
    "S4O4QF8DG2PtqOLjSwl9HWyCi1rzJfQliAnuG1O8PyihD4GV8Y12fJ+DGOPBg0rhO7ahKljVukNI6EKICR6eVEJ8A73m2SxG"
    "PDK2ZfOZ0L9AVPDY1BL4PI3bhKi2Z0bLjyd0PkQFW7tK4HOhdifYIs5qmYAJ/SOiCp45sgQ+SmgJ2ASfbsP3UUQVPP+mcvgW"
    "7J3it3zjw1AV/GlmYWeXL/v68aIqWNlyCJjQ2ZmqYMesMvgCTd+WRsGmlkPAhM4y1Yidx5TB52nC7yBqL7y+VXYl9HfRNGa7"
    "ji2Dz/nBvwBrxHta881ppDFmu2eXwUcJfRdsgn9u9fWEZu9JY0x7TyyJ74Lm8UHSKkOP6c5iNPmbkvhO0aiMH7Vq8QO9eTui"
    "mp5eCl+gN+xKVfDgMNeKb/rTEI3ZPKpRGQbufAIS051dLdqnQFOfgGjEOaXwOT/0frBGO6WFgQNN2gxRwT+VEl+X0HKwCha0"
    "+Lyngx6AmODCUvgooYvQMMHClnwj14NNcGn7g/a/Ht9paVTGD1oYeN+A98riDwjz/Hpjt6lgQ4dvwdccEC0ph89T5+OIMXv+"
    "sP4N7H19FRrG+GHRB3B7ByzD7gdrjCf1b2DvKB8Q/bj4AW9zA34T2AQf698gnvy1YGPcWi+FjxL6Eti4lYEDDbsFbIyfDS5h"
    "A9zco4sxvt9//geauBYNi9gw0gcqxcBva2gUrOl/yJZQ12Y0LGb3JkTkkv+3if/cP9JnU24fnVH9jyd393eP2KdH3zY1JkSu"
    "+/CJaa9kIF/oXVnnh/wSorZ7Zr9/mqczetLeqGYGwLbefeVJrtBEdIGWgS3izH4N4mnGUwCAyCIsBgA/n1bkLHrvlPxfWzUA"
    "tZEz5l/9qxcAIBVmZsbjE4orNoHOhhjjhwM0AJPnfHblowoAqb2IrxY2TvDNKfn6Ia0XMOeTHKfjyH9YvPoptZhtqhUUZOdG"
    "P4YYsbVzoLz3IcnfqK8DY+vwggAT+jhYU377K+pAna8HuoxVsgeLWpED3ZyxNk4lopAMfA8n0JDrYdZofzPlLxpiWgNJX1h4"
    "+rR6Hsi2lAkdmt+Z2DK2KBcHuiVly4DeLSs+d9pr6k3qVgXzHU+CjfHo4YVV6kBzETmyZADQu2XFZ089LLTyE32kATbGus4C"
    "VxJPXwSgzMxNyp7fXd6fhoHcIljUiOuHFjpTdfSuG5/J17KoUVgMf9sPQKDRP4FoTPF5Krgl9ESjj1mw9L5tgJkZ45J++BKa"
    "/hDYBD3vL35LEnJBJhx7iZoJ7jrw4MYFOvH53B4zyxkphCQ4osWI0bYdekAEnaMFClHG6gnljBTyIJ6DaIL3HRBgT+4KaFTF"
    "khqFsvg8dXWbCpYcIFGgUSshKhk+Rc6XxedCbR0k4rfD9rdAQl3/DTbGrneXM1Hos+3U2HvUfjF0Cb3zObAxHnlTeelHFOjE"
    "LBofMDh3nj4WISr4rwll8nk3/sk0Cm7dL4ie6CuwqIqlJdqDiALdBInp0xNeboJAI1bk9riQyJfKdx5EJdvvdmVC0zaCjdF9"
    "epn2IPI0vUdVsOhlWeYCzX4GbA38fkaZ6UfkQn0DJOJXg/rK5DydyxAV3D2xXD5KaCFEdc+RfdNsrz0irh5Uqj2IAs1OozI+"
    "0VenQCNugmjMcHG59iDyfsxj2f4VJqFpG9EwwQtzy7UHESX0PXBMn530UoVxCc1+tszm6uUBngdR6Xs27Dydk9vjnkml83k6"
    "dLtFwbdeIvFEC3N7XFO2PYhccKsRIza91MMEGnI9RCXFZ8q2BxEFugiiUd66T6qEJq/L9x5nlm4PokCzJKrgkn0BTuitfwAb"
    "44lZpacfkfPDfosouHvvLsl5mrs7v67YWQG+/PqT2o5XN3PNE12MNGrE9UNKtwcRBToTYoKzmzCB6ksRNRouq4A9iDxN3WbK"
    "uLbJl9DBd4JNwB8u57TwgAoT7oJI9shI3/z3G0dsARvjudnlnFYfmICfB2uMzQvICR2Xjw42TquCPYgCHadRBZ/JcRKa2wtR"
    "wc0jq2APIu/GPJ5FwWofHJELNB9pVMXXKmEPIgq0HBLt+UPIEzlPF0M1pvgk+arwzYeo4L0UiJynf0PUmDXmVWB1a1aYI3ar"
    "Cr5OCZEntwSsgp0nVMMeRC4Muh8S8UCHd+SpfgPYGE/NrApf/itc1RdfT54CjbgtH710VYYv0Jwsav4r5kAT7wMb44FDKsPn"
    "6aCtWRSsoIQS6sqXjzXjqlH+iMiF5GZIzLaOdz6htzwDNsZPh1aGjxL6JNgkO5lCQifsgpjg2lpFynPew/SoCq6gJKH3NCAa"
    "cUWJk90DAlwbsR4SsXFwqNHfq4kqLqxEd7VPwGUQjTqL6vShzGK09NyqLB95hTmqV1XwZUroAmiMGc+tTHkhIu/HPYYoWD/Y"
    "Jf8B1Yg9p1SJjxK6DqzW/VpKbgSbpLuOrxRfoA9AVHAOjV4FNoGeXCk+T6/dZSr4Fo1fm2/Oj60Unwu1tYiCLbXOjWBr4NGp"
    "1SnPeQJ+BaxRX3fYI2AT/OHNpdwlb5OAJyEq49zD85OtZaN8pfTz7uCn0ij43tvz5fcaR46qFeAVEMWvF/QgasQiqpZ+FOg8"
    "sKY9K/cgquLiKi2/eYV5XY+qZT0CjamdW43hRt8KU18HMbPMVLLe0ytV/vIAXwY2MzMT7Dy+gnzv0KhNvqdnVo7P+ZG/R7T8"
    "0k6Fdpd9Ksw1kCbfA53V2R29FOD37eOr0O6yT4WZ8ieLOd+qodXjc8HfkQvIWF6vVvvSDPCnwWamgu/4SvIdLVHNNOLyqi1v"
    "eYUZ/jCiWYzVW36bFWYpxCym6fwq7X77BPgsiJmg98zqled8DrPdogl2vquSfM4nd0KM8fRRleSjQJ+AGGNLVzX5yNNd2tvA"
    "xkkV5aNAXwRwdwWX371EbkOH//783T6jSj++gkz/C76LFxB/RZ/wAAAAAElFTkSuQmCC"
)


def _logo_mask(data):
    return Image.open(io.BytesIO(base64.b64decode("".join(data)))).convert("L")


def render_logo(size, gear_color, bolt_color, background=None):
    """Логотип заданого розміру у кольорах теми (RGBA)."""
    logo = Image.new("RGBA", (size, size), background or (0, 0, 0, 0))
    for data, color in ((LOGO_GEAR_MASK, gear_color), (LOGO_BOLT_MASK, bolt_color)):
        mask = _logo_mask(data).resize((size, size), Image.Resampling.LANCZOS)
        layer = Image.new("RGBA", (size, size), color)
        layer.putalpha(mask)
        logo.alpha_composite(layer)
    return logo


def render_app_icon(size, start="#7c3aed", end="#06b6d4"):
    """Іконка програми: закруглена плитка з градієнтом і білим логотипом."""
    from PIL import ImageDraw

    scale = 4
    big = size * scale
    start_rgb = Image.new("RGB", (1, 1), start).getpixel((0, 0))
    end_rgb = Image.new("RGB", (1, 1), end).getpixel((0, 0))
    gradient = Image.new("RGBA", (big, big))
    pixels = gradient.load()
    for y in range(big):
        for x in range(big):
            t = (x + y) / (2 * big - 2)
            pixels[x, y] = tuple(round(s + (e - s) * t) for s, e in zip(start_rgb, end_rgb)) + (255,)
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, big - 1, big - 1), radius=big // 5, fill=255)
    tile = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    tile.paste(gradient, (0, 0), mask)
    inner = round(big * 0.78)
    logo = render_logo(inner, "#ffffff", "#ffffff")
    tile.alpha_composite(logo, ((big - inner) // 2, (big - inner) // 2))
    return tile.resize((size, size), Image.Resampling.LANCZOS)


def render_icon(name, color, size=18):
    """Векторні іконки інтерфейсу (малюються в 4× і зменшуються — рівні згладжені краї).

    Координати задано в сітці 24×24, як у звичних наборах іконок.
    """
    scale = 4
    big = size * scale
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    unit = big / 24.0
    stroke = max(1, round(2.1 * unit))

    def pt(x, y):
        return x * unit, y * unit

    def line(*points):
        draw.line([pt(x, y) for x, y in points], fill=color, width=stroke, joint="curve")
        for x, y in (points[0], points[-1]):
            radius = stroke / 2
            cx, cy = pt(x, y)
            draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=color)

    def bar(x0, y0, x1, y1):
        draw.rounded_rectangle((*pt(x0, y0), *pt(x1, y1)), radius=1.3 * unit, fill=color)

    if name in ("undo", "redo"):
        # Стрілка-розворот: вістря ліворуч, хвіст загинається праворуч донизу.
        radius = 4.6
        cx, cy = 14.4, 13.4
        line((8.2, cy - radius), (cx, cy - radius))
        half = stroke / 2
        draw.arc(
            (cx * unit - radius * unit - half, cy * unit - radius * unit - half,
             cx * unit + radius * unit + half, cy * unit + radius * unit + half),
            start=-90, end=90, fill=color, width=stroke,
        )
        line((cx, cy + radius), (8.5, cy + radius))
        draw.polygon([pt(2.6, cy - radius), pt(9.4, cy - radius - 4.6), pt(9.4, cy - radius + 4.6)], fill=color)
        if name == "redo":
            image = ImageOps.mirror(image)
    elif name.startswith("align_"):
        kind = name[6:]
        if kind in ("left", "hcenter", "right"):
            x = {"left": 3.5, "hcenter": 12.0, "right": 20.5}[kind]
            line((x, 2.8), (x, 21.2))
            for top, length in ((5.5, 13.0), (13.5, 8.0)):
                if kind == "left":
                    left = 6.0
                elif kind == "right":
                    left = 18.0 - length
                else:
                    left = 12.0 - length / 2
                bar(left, top, left + length, top + 5.0)
        else:
            y = {"top": 3.5, "vcenter": 12.0, "bottom": 20.5}[kind]
            line((2.8, y), (21.2, y))
            for left, length in ((5.5, 13.0), (13.5, 8.0)):
                if kind == "top":
                    top = 6.0
                elif kind == "bottom":
                    top = 18.0 - length
                else:
                    top = 12.0 - length / 2
                bar(left, top, left + 5.0, top + length)
    elif name == "dist_h":
        line((3.0, 4.0), (3.0, 20.0))
        line((21.0, 4.0), (21.0, 20.0))
        bar(9.0, 6.5, 15.0, 17.5)
    elif name == "dist_v":
        line((4.0, 3.0), (20.0, 3.0))
        line((4.0, 21.0), (20.0, 21.0))
        bar(6.5, 9.0, 17.5, 15.0)
    return image.resize((size, size), Image.Resampling.LANCZOS)


def render_theme_swatch(theme, width=46, height=26):
    """Мініатюра теми: шапка, панель, кнопка акценту і біла наліпка зі сяйвом."""
    scale = 4
    w, h = width * scale, height * scale
    image = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    radius = 5 * scale
    draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=theme["workspace"],
                           outline=theme["border"], width=scale)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=255)
    top = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    top_draw = ImageDraw.Draw(top)
    top_draw.rectangle((0, 0, w, 6 * scale), fill=theme["header"])
    top_draw.rectangle((w - 15 * scale, 6 * scale, w, h), fill=theme["panel"])
    top_draw.rounded_rectangle((w - 12 * scale, 10 * scale, w - 3 * scale, 15 * scale),
                               radius=2 * scale, fill=theme["accent"])
    top_draw.rounded_rectangle((w - 12 * scale, 18 * scale, w - 5 * scale, 20 * scale),
                               radius=scale, fill=theme["muted"])
    image.paste(top, (0, 0), ImageChops.multiply(top.getchannel("A"), mask))
    label = (5 * scale, 10 * scale, 27 * scale, 21 * scale)
    for index, color in enumerate(theme.get("glow") or ()):
        spread = (len(theme["glow"]) - index) * scale // 2 + scale // 2
        draw.rounded_rectangle((label[0] - spread, label[1] - spread, label[2] + spread, label[3] + spread),
                               radius=2 * scale, fill=color)
    draw.rectangle(label, fill="#ffffff")
    draw.rectangle((8 * scale, 13 * scale, 18 * scale, 15 * scale), fill="#151515")
    draw.rectangle((8 * scale, 17 * scale, 22 * scale, 18 * scale), fill="#9aa0a8")
    draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, outline=theme["border"], width=scale)
    return image.resize((width, height), Image.Resampling.LANCZOS)


# ---- Обробка фото ------------------------------------------------------------------------
# XP-420B друкує з роздільністю 203 dpi ≈ 8 точок на міліметр.
PRINTER_DOTS_PER_MM = 8
ADJUST_DEFAULTS = {
    "mode": "color",        # color | gray | bw
    "brightness": 0,        # -100…100
    "contrast": 0,          # -100…100
    "saturation": 100,      # 0…200 %
    "black": 0,             # точка чорного 0…254
    "white": 255,           # точка білого 1…255
    "midtones": 0,          # -100…100 (середні тони / гамма)
    "sharpen": 0,           # 0…300 % різкості
    "threshold": 128,       # поріг для режиму «Чорно-білий»
    "denoise": False,       # прибрати шум / зерно
    "invert": False,        # негатив
    "white_transparent": False,  # білий фон стає прозорим
}
def normalized_adjustments(adjust):
    """Повні налаштування або None, якщо фото не змінено."""
    if not adjust:
        return None
    values = dict(ADJUST_DEFAULTS)
    values.update({key: value for key, value in adjust.items() if key in ADJUST_DEFAULTS})
    if values == ADJUST_DEFAULTS:
        return None
    return values


def apply_tone_adjustments(image, adjust):
    """Яскравість, контраст, насиченість, рівні, різкість (RGBA → RGBA)."""
    alpha = image.getchannel("A")
    work = image.convert("RGB")
    if adjust["denoise"]:
        work = work.filter(ImageFilter.MedianFilter(3))
    if adjust["mode"] == "color":
        if adjust["saturation"] != 100:
            work = ImageEnhance.Color(work).enhance(max(0.0, adjust["saturation"] / 100.0))
    else:
        work = work.convert("L")
    if adjust["brightness"]:
        work = ImageEnhance.Brightness(work).enhance(max(0.0, 1.0 + adjust["brightness"] / 100.0))
    if adjust["contrast"]:
        work = ImageEnhance.Contrast(work).enhance(max(0.0, 1.0 + adjust["contrast"] / 100.0))
    black = max(0, min(254, int(adjust["black"])))
    white = max(black + 1, min(255, int(adjust["white"])))
    gamma = 2.0 ** (adjust["midtones"] / 50.0)
    if black > 0 or white < 255 or adjust["midtones"]:
        span = float(white - black)
        table = [
            round(255 * (min(1.0, max(0.0, (value - black) / span)) ** (1.0 / gamma)))
            for value in range(256)
        ]
        work = work.point(table * len(work.getbands()))
    if adjust["sharpen"] > 0:
        work = work.filter(
            ImageFilter.UnsharpMask(radius=2, percent=int(adjust["sharpen"]), threshold=2)
        )
    if adjust["invert"]:
        work = ImageOps.invert(work)
    result = work.convert("RGBA")
    result.putalpha(alpha)
    return result


def apply_bit_mode(image, adjust):
    """Режим «Ч/Б» (після масштабування до роздільності друку)."""
    alpha = image.getchannel("A")
    gray = Image.alpha_composite(Image.new("RGBA", image.size, "white"), image).convert("L")
    if adjust["mode"] == "bw":
        threshold = int(adjust["threshold"])
        bits = gray.point(lambda value: 255 if value >= threshold else 0)
    else:
        bits = gray.convert("1").convert("L")
    alpha = alpha.point(lambda value: 255 if value > 127 else 0)
    return Image.merge("RGBA", (bits, bits, bits, alpha))


def apply_white_transparent(image):
    gray = Image.alpha_composite(Image.new("RGBA", image.size, "white"), image).convert("L")
    keep = gray.point(lambda value: 0 if value >= 235 else 255)
    image = image.copy()
    image.putalpha(ImageChops.multiply(image.getchannel("A"), keep))
    return image


def fit_image(image, box, preserve_aspect):
    """Вписати (або розтягнути) картинку в рамку заданого розміру в пікселях."""
    box = (max(1, int(round(box[0]))), max(1, int(round(box[1]))))
    if not preserve_aspect:
        return image.resize(box, Image.Resampling.LANCZOS)
    scale = min(box[0] / max(1, image.width), box[1] / max(1, image.height))
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    if size == image.size:
        return image
    return image.resize(size, Image.Resampling.LANCZOS)


# ---- Редагування фото для чорно-білого друку (лише Pillow, без важких бібліотек) -------------
PHOTO_WORK_MAX = 2000
PHOTO_DEFAULTS = {
    "mode": "bw",           # bw — чисто чорне й біле | original — без перетворення кольорів
    "shadows": False,       # вирівняти нерівне світло й тіні
    "level": 0,             # більше (+) / менше (−) чорного — зсув від автопорогу
    "clean": 1,             # прибрати дрібне сміття: 0 — ні, 1 — мало, 2 — середньо, 3 — багато
    "edges": False,         # прибрати чорне, що торкається країв
    "smooth": True,         # згладити нерівні краї
    "weight": 0,            # тонше (−) / жирніше (+) лінії
    "invert": False,        # негатив
    "transparent": False,   # білий фон прозорий
    "crop": None,           # [x0, y0, x1, y1] у частках 0…1
    "seeds": [],            # [[x, y], …] у частках 0…1 — плями, прибрані «чарівною гумкою»
    "paint": None,          # PNG-шар пензля/гумки (у координатах усього фото)
}
PHOTO_MODES = (
    ("bw", "Ч/Б", "Лише чисто чорне й біле — для логотипів і тексту"),
    ("original", "Оригінал", "Без перетворення кольорів (лише обрізка й очистка)"),
)
PHOTO_CLEAN_LEVELS = (("Ні", 0), ("Мало", 14), ("Сер.", 45), ("Багато", 160))
PHOTO_LEVEL_STEP = 8
_INK_RUN = re.compile(rb"[^\x00]+")


def photo_available():
    return True


def photo_params(photo):
    params = copy.deepcopy(PHOTO_DEFAULTS)
    if photo:
        for key in PHOTO_DEFAULTS:
            if key in photo:
                params[key] = copy.deepcopy(photo[key])
    if not isinstance(params["seeds"], list):
        params["seeds"] = []
    if params["mode"] not in ("bw", "original"):
        params["mode"] = "bw"   # колишній режим «Точки» → Ч/Б
    return params


def image_orientation(path):
    try:
        with Image.open(path) as image:
            return int(image.getexif().get(0x0112, 1) or 1)
    except Exception:
        return 1


def oriented_size(path):
    """Розмір зображення з урахуванням EXIF-повороту (фото з телефона)."""
    with Image.open(path) as image:
        width, height = image.size
        try:
            orientation = int(image.getexif().get(0x0112, 1) or 1)
        except Exception:
            orientation = 1
    if orientation in (5, 6, 7, 8):
        width, height = height, width
    return width, height


def mm_text(value):
    return f"{value:.1f}".rstrip("0").rstrip(".")


def plural_objects(count):
    if count % 10 == 1 and count % 100 != 11:
        return "об’єкт"
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return "об’єкти"
    return "об’єктів"


_SOURCE_CACHE = {}


def load_oriented_image(path, max_side=None):
    """Відкрити картинку з урахуванням EXIF-повороту (кілька останніх — з кешу, щоб поворот
    коліщатком і перетягування були плавними навіть із великими фото)."""
    try:
        stat = os.stat(path)
        key = (os.path.abspath(str(path)), stat.st_mtime_ns, stat.st_size, max_side)
    except OSError:
        key = None
    cached = _SOURCE_CACHE.get(key) if key else None
    if cached is not None:
        return cached.copy()
    image = _load_oriented_image(path, max_side)
    if key:
        if len(_SOURCE_CACHE) >= 6:
            _SOURCE_CACHE.pop(next(iter(_SOURCE_CACHE)))
        _SOURCE_CACHE[key] = image
        image = image.copy()
    return image


def _load_oriented_image(path, max_side=None):
    with Image.open(path) as source:
        if max_side and source.format == "JPEG":
            source.draft("RGB", (max_side, max_side))
        image = ImageOps.exif_transpose(source)
        image = image.convert("RGBA")
    if max_side and max(image.size) > max_side:
        image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return image


def photo_work_size(path):
    width, height = oriented_size(path)
    if max(width, height) > PHOTO_WORK_MAX:
        scale = PHOTO_WORK_MAX / max(width, height)
        width, height = max(1, round(width * scale)), max(1, round(height * scale))
    return width, height


def crop_fraction(crop):
    try:
        x0, y0, x1, y1 = (min(1.0, max(0.0, float(value))) for value in crop)
    except (TypeError, ValueError):
        return None
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    if x1 - x0 < 0.002 or y1 - y0 < 0.002:
        return None
    return x0, y0, x1, y1


def _shifted(image, dx, dy, fill):
    result = Image.new(image.mode, image.size, fill)
    result.paste(image, (dx, dy))
    return result


def _window_extreme(image, size, combine, fill, horizontal):
    """Максимум/мінімум у вікні size (по одній осі) за O(log size) операцій."""
    if size <= 1:
        return image

    def shift(img, amount):
        return _shifted(img, -amount, 0, fill) if horizontal else _shifted(img, 0, -amount, fill)

    result, span = image, 1
    while span * 2 <= size:
        result = combine(result, shift(result, span))
        span *= 2
    if span < size:
        result = combine(result, shift(result, size - span))
    radius = size // 2
    return _shifted(result, radius, 0, fill) if horizontal else _shifted(result, 0, radius, fill)


def grey_dilate(image, size):
    """Квадратний максимум-фільтр (розширення білого/«чорнила» 255)."""
    image = _window_extreme(image, size, ImageChops.lighter, 0, True)
    return _window_extreme(image, size, ImageChops.lighter, 0, False)


def grey_erode(image, size):
    image = _window_extreme(image, size, ImageChops.darker, 255, True)
    return _window_extreme(image, size, ImageChops.darker, 255, False)


def otsu_threshold(gray):
    histogram = gray.histogram()
    total = sum(histogram)
    weighted_total = sum(index * count for index, count in enumerate(histogram))
    weight_back = sum_back = 0
    best, threshold = -1.0, 127
    for value in range(256):
        weight_back += histogram[value]
        if not weight_back:
            continue
        weight_fore = total - weight_back
        if not weight_fore:
            break
        sum_back += value * histogram[value]
        mean_back = sum_back / weight_back
        mean_fore = (weighted_total - sum_back) / weight_fore
        between = weight_back * weight_fore * (mean_back - mean_fore) ** 2
        if between > best:
            best, threshold = between, value
    return threshold


def even_out_light(gray):
    """Прибрати тіні й нерівне освітлення: поділити на оцінку фону."""
    width, height = gray.size
    factor = 4 if min(width, height) >= 64 else 1
    small = gray.resize((max(1, width // factor), max(1, height // factor)), Image.Resampling.BOX)
    size = max(5, int(min(small.size) / 8) | 1)
    background = grey_erode(grey_dilate(small, size), size)   # «закриття»: прибрати темні деталі
    background = background.filter(ImageFilter.GaussianBlur(size / 2.0))
    background = background.resize((width, height), Image.Resampling.BILINEAR)
    return ImageMath.lambda_eval(
        lambda args: args["convert"](args["min"](args["a"] * 255.0 / args["max"](args["b"], 1), 255), "L"),
        a=gray.convert("F"), b=background.convert("F"),
    )


class InkComponents:
    """Зв'язні плями «чорнила» (8-зв'язність) через відрізки рядків — швидко і без numpy."""

    def __init__(self, ink):
        self.width, self.height = ink.size
        data = ink.tobytes()
        width = self.width
        self.row_first = []
        self.row_starts = []
        run_y, run_s, run_e = [], [], []
        parent = []

        def find(node):
            while parent[node] != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        previous_starts = previous_ends = ()
        previous_first = 0
        finder = _INK_RUN.finditer
        for y in range(self.height):
            offset = y * width
            starts, ends = [], []
            for match in finder(data, offset, offset + width):
                starts.append(match.start() - offset)
                ends.append(match.end() - offset)
            first = len(run_s)
            count = len(starts)
            if count:
                run_s.extend(starts)
                run_e.extend(ends)
                run_y.extend([y] * count)
                parent.extend(range(first, first + count))
                i = j = 0
                while i < len(previous_starts) and j < count:
                    if previous_starts[i] <= ends[j] and starts[j] <= previous_ends[i]:
                        a, b = find(previous_first + i), find(first + j)
                        if a != b:
                            if a < b:
                                parent[b] = a
                            else:
                                parent[a] = b
                    if previous_ends[i] < ends[j]:
                        i += 1
                    else:
                        j += 1
            self.row_first.append(first)
            self.row_starts.append(starts)
            previous_starts, previous_ends, previous_first = starts, ends, first
        self.run_y, self.run_s, self.run_e = run_y, run_s, run_e
        self.root = [find(index) for index in range(len(parent))]
        area, bounds = {}, {}
        for index, root in enumerate(self.root):
            start, end, y = run_s[index], run_e[index], run_y[index]
            area[root] = area.get(root, 0) + end - start
            box = bounds.get(root)
            if box is None:
                bounds[root] = [start, y, end, y + 1]
            else:
                if start < box[0]:
                    box[0] = start
                if end > box[2]:
                    box[2] = end
                box[3] = y + 1
        self.area, self.bounds = area, bounds
        self._members = None

    def root_at(self, x, y):
        """Пляма в точці (x, y) або -1, якщо там немає чорнила."""
        if not (0 <= y < self.height and 0 <= x < self.width):
            return -1
        starts = self.row_starts[y]
        index = bisect.bisect_right(starts, x) - 1
        if index < 0:
            return -1
        run = self.row_first[y] + index
        return self.root[run] if x < self.run_e[run] else -1

    def members(self, root):
        if self._members is None:
            members = {}
            for index, owner in enumerate(self.root):
                members.setdefault(owner, []).append(index)
            self._members = members
        return self._members.get(root, [])

    def small_roots(self, min_area):
        return {root for root, area in self.area.items() if area < min_area}

    def border_roots(self):
        return {root for root, (x0, y0, x1, y1) in self.bounds.items()
                if x0 <= 0 or y0 <= 0 or x1 >= self.width or y1 >= self.height}

    def erase(self, ink, roots):
        """Копія ink без плям roots."""
        if not roots:
            return ink
        buffer = bytearray(ink.tobytes())
        width = self.width
        for root in roots:
            for index in self.members(root):
                offset = self.run_y[index] * width
                start, end = self.run_s[index], self.run_e[index]
                buffer[offset + start:offset + end] = bytes(end - start)
        return Image.frombytes("L", ink.size, bytes(buffer))

    def region_mask(self, root, box):
        """Маска плями root усередині прямокутника box (255 — пляма)."""
        x0, y0, x1, y1 = box
        width, height = max(1, x1 - x0), max(1, y1 - y0)
        buffer = bytearray(width * height)
        for index in self.members(root):
            y = self.run_y[index]
            if not y0 <= y < y1:
                continue
            start, end = max(self.run_s[index], x0), min(self.run_e[index], x1)
            if start < end:
                offset = (y - y0) * width
                buffer[offset + start - x0:offset + end - x0] = b"\xff" * (end - start)
        return Image.frombytes("L", (width, height), bytes(buffer))

    def center(self, root):
        """Найглибша точка плями — стійка «насінина» для чарівної гумки."""
        box = self.bounds.get(root)
        if box is None:
            return None
        x0, y0, x1, y1 = box
        mask = self.region_mask(root, (x0 - 1, y0 - 1, x1 + 1, y1 + 1))
        core = mask
        for _ in range(4096):
            eroded = core.filter(ImageFilter.MinFilter(3))
            if eroded.getbbox() is None:
                break
            core = eroded
        position = core.tobytes().find(b"\xff")
        if position < 0:
            return None
        width = core.size[0]
        return x0 - 1 + position % width, y0 - 1 + position // width


def remove_small_components(ink, min_area):
    if min_area <= 1:
        return ink
    components = InkComponents(ink)
    return components.erase(ink, components.small_roots(min_area))


def remove_border_components(ink):
    components = InkComponents(ink)
    return components.erase(ink, components.border_roots())


def load_paint_masks(path, size):
    """Шар пензля: (біле, чорне) як маски "L" (255 — зафарбовано) або (None, None)."""
    if not path or not os.path.isfile(path):
        return None, None
    with Image.open(path) as image:
        layer = image.convert("RGBA")
    if layer.size != tuple(size):
        layer = layer.resize(tuple(size), Image.Resampling.NEAREST)
    red, _green, _blue, alpha = layer.split()
    painted = alpha.point(lambda value: 255 if value > 127 else 0)
    light = red.point(lambda value: 255 if value > 127 else 0)
    white = ImageChops.multiply(painted, light)
    black = ImageChops.subtract(painted, light)
    return (white if white.getbbox() else None), (black if black.getbbox() else None)


def save_paint_masks(white, black, path):
    layer = Image.new("RGBA", white.size, (0, 0, 0, 0))
    layer.paste((255, 255, 255, 255), (0, 0), white)
    layer.paste((0, 0, 0, 255), (0, 0), black)
    layer.save(path, "PNG")


class PhotoProcessor:
    """Перетворює фото на чисте чорно-біле зображення для термодруку."""

    def __init__(self, path):
        base = load_oriented_image(path, PHOTO_WORK_MAX)
        paper = Image.new("RGBA", base.size, "white")
        paper.alpha_composite(base)
        self.color = paper.convert("RGB")
        self.gray = self.color.convert("L")
        self.width, self.height = self.gray.size
        self.area_scale = max(0.25, self.width * self.height / 3_000_000)
        self.detail = max(1, round(max(self.width, self.height) / 600))
        self._cache = {}

    def _cached(self, key, factory):
        value = self._cache.get(key)
        if value is None:
            value = factory()
            self._cache[key] = value
            while len(self._cache) > 16:
                self._cache.pop(next(iter(self._cache)))
        return value

    def crop_box(self, crop):
        fraction = crop_fraction(crop) if crop else None
        if fraction:
            x0, y0, x1, y1 = fraction
            box = (
                int(round(x0 * self.width)), int(round(y0 * self.height)),
                int(round(x1 * self.width)), int(round(y1 * self.height)),
            )
            if box[2] - box[0] >= 4 and box[3] - box[1] >= 4:
                return box
        return (0, 0, self.width, self.height)

    def tone(self, params, box):
        """Сірий канал, з якого визначається, що буде чорним."""
        key = ("tone", box, bool(params["shadows"]), bool(params["smooth"]))

        def build():
            gray = self.gray.crop(box)
            if params["shadows"]:
                gray = even_out_light(gray)
            if params["smooth"]:
                gray = gray.filter(ImageFilter.GaussianBlur(0.5 + 0.35 * self.detail))
            return gray

        return self._cached(key, build)

    def auto_threshold(self, params, box):
        key = ("otsu", box, bool(params["shadows"]), bool(params["smooth"]))
        return self._cached(key, lambda: otsu_threshold(self.tone(params, box)))

    def threshold(self, params, box):
        return int(min(254, max(1, self.auto_threshold(params, box) + int(params["level"]))))

    def raw_ink(self, params, box):
        """Маска чорнила (255 — чорне) одразу після порогу."""
        key = ("raw", box, bool(params["shadows"]), bool(params["smooth"]), int(params["level"]),
               bool(params["invert"]))

        def build():
            threshold = self.threshold(params, box)
            table = [255 if value < threshold else 0 for value in range(256)]
            if params["invert"]:
                table = [255 - value for value in table]
            return self.tone(params, box).point(table)

        return self._cached(key, build)

    def clean_ink(self, params, box):
        """Чорнило після згладжування, товщини й очистки (без пензля й гумки)."""
        key = ("clean", box, bool(params["shadows"]), bool(params["smooth"]), int(params["level"]),
               bool(params["invert"]), int(params["weight"]), int(params["clean"]), bool(params["edges"]))

        def build():
            ink = self.raw_ink(params, box)
            if params["smooth"]:
                ink = ink.filter(ImageFilter.MedianFilter(3 if self.detail < 3 else 5))
            weight = int(params["weight"])
            if weight:
                size = 2 * abs(weight) * self.detail + 1
                ink = grey_dilate(ink, size) if weight > 0 else grey_erode(ink, size)
            level = max(0, min(len(PHOTO_CLEAN_LEVELS) - 1, int(params["clean"])))
            if level:
                area = PHOTO_CLEAN_LEVELS[level][1] * self.area_scale
                ink = remove_small_components(ink, area)
                ink = ImageOps.invert(remove_small_components(ImageOps.invert(ink), area / 2))
            if params["edges"]:
                ink = remove_border_components(ink)
            return ink

        return self._cached(key, build)

    def seed_params(self, params):
        """Для «Оригіналу» плями шукаємо без згладжування й товщини."""
        if params["mode"] == "bw":
            return params
        return dict(params, smooth=False, weight=0)

    def painted_ink(self, params, box, white=None, black=None):
        """Чорнило з урахуванням пензля й гумки (ще без чарівної гумки)."""
        ink = self.clean_ink(self.seed_params(params), box).copy()
        if white is not None:
            ink.paste(0, (0, 0), white.crop(box))
        if black is not None:
            ink.paste(255, (0, 0), black.crop(box))
        return ink

    def final_ink(self, params, box, white=None, black=None):
        ink = self.painted_ink(params, box, white, black)
        seeds = params["seeds"] or ()
        if not seeds:
            return ink
        components = InkComponents(ink)
        roots = set()
        for seed in seeds:
            try:
                px = int(float(seed[0]) * self.width) - box[0]
                py = int(float(seed[1]) * self.height) - box[1]
            except (TypeError, ValueError, IndexError):
                continue
            root = components.root_at(px, py)
            if root >= 0:
                roots.add(root)
        return components.erase(ink, roots)

    def render(self, params, white=None, black=None):
        """Готове зображення обрізаної частини: L (Ч/Б) або RGB (оригінал)."""
        box = self.crop_box(params["crop"])
        ink = self.final_ink(params, box, white, black)
        if params["mode"] == "bw":
            return ImageOps.invert(ink)
        removed = ImageChops.subtract(self.raw_ink(self.seed_params(params), box), ink)
        if white is not None:
            removed = ImageChops.lighter(removed, white.crop(box))
        if removed.getbbox():
            removed = grey_dilate(removed, 2 * self.detail + 1)
        color = self.color.crop(box)
        if params["invert"]:
            color = ImageOps.invert(color)
        color.paste((255, 255, 255), (0, 0), removed)
        if black is not None:
            color.paste((0, 0, 0), (0, 0), black.crop(box))
        return color


LOCAL_DRIVER_SOURCE = (
    r"C:\Windows\System32\DriverStore\FileRepository"
    r"\xprinter.inf_amd64_a2184ce9ef55d7a6"
)


def mm_to_px(value):
    return round(float(value) * PX_PER_MM)


def px_to_mm(value):
    return round(float(value) / PX_PER_MM, 2)


def size_text(width, height):
    return f"{float(width):g} × {float(height):g} мм"


class ToolTip:
    """Невелика підказка, що з'являється при наведенні на кнопку."""

    def __init__(self, widget, text, delay=500):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.tip = None
        self.job = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._cancel()
        self.job = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self.job:
            try:
                self.widget.after_cancel(self.job)
            except tk.TclError:
                pass
            self.job = None

    def _show(self):
        self.job = None
        if self.tip or not self.widget.winfo_exists():
            return
        x = self.widget.winfo_rootx() + 6
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(
            self.tip,
            text=self.text,
            bg=COLORS["tooltip_bg"],
            fg=COLORS["tooltip_fg"],
            font=(UI_FONT, 9),
            padx=8,
            pady=4,
            justify="left",
        ).pack()

    def _hide(self, _event=None):
        self._cancel()
        if self.tip:
            self.tip.destroy()
            self.tip = None


class BWControls:
    """Керування «Чорно-біле для друку» кнопками, без повзунків (панель і редактор)."""

    FLAGS = (
        ("shadows", "Тіні / нерівне світло", "Увімкніть, якщо на фото тінь або світло падає нерівно"),
        ("smooth", "Згладити краї", "Рівні краї літер і ліній без «сходинок»"),
        ("edges", "Прибрати чорне з країв", "Прибирає темні смуги й тіні, що торкаються краю фото"),
        ("invert", "Негатив", "Біле лого на чорному тлі стане чорним на білому"),
        ("transparent", "Білий — прозорий", "Біле не перекриватиме інші елементи наліпки"),
    )

    def __init__(self, parent, on_change, on_pick=None):
        self.on_change = on_change
        self.params = photo_params(None)
        self.frame = frame = ttk.Frame(parent)
        frame.columnconfigure(1, weight=1)

        def row_label(row, text):
            ttk.Label(frame, text=text).grid(row=row, column=0, sticky="w", pady=3, padx=(0, 8))

        def strip(row):
            holder = ttk.Frame(frame)
            holder.grid(row=row, column=1, sticky="ew", pady=3)
            return holder

        def button(holder, column, text, command, tip, weight=1, width=None):
            widget = ttk.Button(holder, text=text, style="Tool.TButton", command=command)
            if width:
                widget.configure(width=width)
            widget.grid(row=0, column=column, sticky="ew", padx=1)
            holder.columnconfigure(column, weight=weight)
            ToolTip(widget, tip)
            return widget

        row_label(0, "Режим")
        holder = strip(0)
        self.mode_buttons = {
            key: button(holder, index, title, lambda k=key: self.on_change({"mode": k}), tip)
            for index, (key, title, tip) in enumerate(PHOTO_MODES)
        }

        row_label(1, "Чорного")
        holder = strip(1)
        self.less_button = button(
            holder, 0, "−", lambda: self._step("level", -PHOTO_LEVEL_STEP, -160, 160),
            "Менше чорного: сіре й світле стане білим", width=3,
        )
        self.level_label = ttk.Label(holder, width=5, anchor="center", style="Title.TLabel")
        self.level_label.grid(row=0, column=1, padx=2)
        holder.columnconfigure(1, weight=1)
        self.more_button = button(
            holder, 2, "+", lambda: self._step("level", PHOTO_LEVEL_STEP, -160, 160),
            "Більше чорного: світло-чорне стане глибоко чорним", width=3,
        )
        self.auto_button = button(holder, 3, "Авто", lambda: self.on_change({"level": 0}),
                                  "Автоматично визначити межу чорного", weight=2)
        self.pick_buttons = []
        next_row = 2
        if on_pick:
            holder = strip(next_row)
            self.pick_buttons = [
                button(holder, 0, "◉ Це чорне", lambda: on_pick("black"),
                       "Клікніть по фото там, що має стати чорним (напр. блідий текст)"),
                button(holder, 1, "○ Це біле", lambda: on_pick("white"),
                       "Клікніть по фото там, що має стати білим (напр. тінь чи сірий фон)"),
            ]
            next_row += 1

        row_label(next_row, "Сміття")
        holder = strip(next_row)
        self.clean_buttons = [
            button(holder, index, title, lambda i=index: self.on_change({"clean": i}),
                   "Прибирати дрібні точки й крапки такого розміру")
            for index, (title, _area) in enumerate(PHOTO_CLEAN_LEVELS)
        ]
        next_row += 1

        row_label(next_row, "Лінії")
        holder = strip(next_row)
        self.thin_button = button(holder, 0, "−", lambda: self._step("weight", -1, -3, 3),
                                  "Тонше: зробити лінії й літери тоншими", width=3)
        self.weight_label = ttk.Label(holder, width=5, anchor="center", style="Title.TLabel")
        self.weight_label.grid(row=0, column=1, padx=2)
        holder.columnconfigure(1, weight=1)
        self.bold_button = button(holder, 2, "+", lambda: self._step("weight", 1, -3, 3),
                                  "Жирніше: зробити лінії й літери товщими", width=3)

        next_row += 1

        checks = ttk.Frame(frame)
        checks.grid(row=next_row, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self.flag_vars = {}
        self.flag_widgets = {}
        for index, (key, title, tip) in enumerate(self.FLAGS):
            variable = tk.BooleanVar(value=False)
            widget = ttk.Checkbutton(
                checks, text=title, variable=variable,
                command=lambda k=key, v=variable: self.on_change({k: v.get()}),
            )
            widget.grid(row=index, column=0, sticky="w", pady=1)
            ToolTip(widget, tip)
            self.flag_vars[key] = variable
            self.flag_widgets[key] = widget

    def _step(self, key, delta, low, high):
        value = max(low, min(high, int(self.params.get(key, 0)) + delta))
        self.on_change({key: value})

    @staticmethod
    def _enable(widget, enabled):
        widget.state(["!disabled"] if enabled else ["disabled"])

    def refresh(self, params):
        self.params = params
        mode = params["mode"]
        for key, widget in self.mode_buttons.items():
            widget.configure(style="SegOn.TButton" if key == mode else "Tool.TButton")
        level = int(params["level"])
        self.level_label.configure(text=f"{level:+d}" if level else "0")
        weight = int(params["weight"])
        self.weight_label.configure(text=f"{weight:+d}" if weight else "0")
        for index, widget in enumerate(self.clean_buttons):
            widget.configure(style="SegOn.TButton" if index == int(params["clean"]) else "Tool.TButton")
        for key, variable in self.flag_vars.items():
            variable.set(bool(params[key]))
        not_original = mode != "original"
        for widget in (self.less_button, self.more_button, self.auto_button, *self.pick_buttons):
            self._enable(widget, not_original)
        for widget in (self.thin_button, self.bold_button):
            self._enable(widget, mode == "bw")
        self._enable(self.flag_widgets["smooth"], mode == "bw")
        self._enable(self.flag_widgets["shadows"], not_original)


class PhotoEditor(tk.Toplevel):
    """Окреме вікно для очищення фото під чорно-білий друк."""

    TOOLS = (
        ("magic", "✦  Чарівна гумка", "Клік по зайвій чорній плямі — вона зникне.\n"
                                        "Те, що буде прибрано, підсвічується червоним.  (W)"),
        ("lasso", "◌  Ласо-гумка", "Обведіть зайве мишкою — усе всередині стане білим.  (L)"),
        ("erase", "◻  Гумка", "Зафарбувати білим. [ і ] — розмір, Shift+клік — пряма лінія.  (E)"),
        ("brush", "◼  Пензель", "Домалювати чорним. [ і ] — розмір, Shift+клік — пряма лінія.  (B)"),
        ("crop", "✂  Обрізати", "Виділіть рамкою потрібну частину фото.\nПодвійний клік — без обрізки.  (C)"),
        ("hand", "✋  Рука", "Рухати зображення. Також пробіл або середня кнопка миші.  (H)"),
    )
    HINTS = {
        "magic": "Наведіть на зайву пляму (вона стане червоною) і клацніть — пляма зникне",
        "lasso": "Обведіть зайве, не відпускаючи кнопку миші — усе всередині стане білим",
        "erase": "Зафарбовуйте зайве білим. Розмір — кнопки ± або клавіші [ ]",
        "brush": "Домальовуйте чорним. Розмір — кнопки ± або клавіші [ ]",
        "crop": "Виділіть рамкою потрібну частину. Подвійний клік — скинути обрізку",
        "hand": "Перетягуйте зображення. Коліщатко миші — масштаб",
    }
    KEY_TOOLS = {"w": "magic", "l": "lasso", "e": "erase", "b": "brush", "c": "crop", "h": "hand"}
    KEY_VK = {87: "w", 76: "l", 69: "e", 66: "b", 67: "c", 72: "h", 90: "z", 89: "y",
              219: "[", 221: "]", 48: "0", 49: "1"}
    KEY_X11 = {25: "w", 46: "l", 26: "e", 56: "b", 54: "c", 43: "h", 52: "z", 29: "y",
               34: "[", 35: "]", 19: "0", 10: "1"}
    KEY_CYRILLIC = {
        "Cyrillic_tse": "w", "Cyrillic_de": "l", "Cyrillic_u": "e", "Cyrillic_i": "b", "Cyrillic_es": "c",
        "Cyrillic_er": "h", "Cyrillic_ya": "z", "Cyrillic_en": "y", "Cyrillic_ha": "[",
        "Ukrainian_yi": "]", "Cyrillic_hardsign": "]",
    }

    def __init__(self, app, element):
        super().__init__(app)
        self.withdraw()
        self.app = app
        self.element_id = element["id"]
        self.title(f"Редактор фото — {Path(element['path']).name}")
        app._themed(self, bg="panel")
        self.transient(app)
        self.processor = processor = app._photo_processor(element["path"])
        self.params = photo_params(element.get("photo"))
        size = (processor.width, processor.height)
        white, black = app._paint_masks(self.params["paint"], size)
        self.white = white.copy() if white is not None else Image.new("L", size, 0)
        self.black = black.copy() if black is not None else Image.new("L", size, 0)
        self.history = []
        self.future = []
        self.tool = "magic"
        self.brush = max(3, round(min(processor.width, processor.height) * 0.02))
        self.zoom = 1.0
        self.ox = self.oy = 0.0
        self.box = processor.crop_box(self.params["crop"])
        self.result = None
        self.components = None
        self.hover_label = -1
        self.full_image = None
        self.photo = None
        self.stroke_last = None
        self.stroke_active = False
        self.lasso = []
        self.crop_drag = None
        self.pan_start = None
        self.space_pan = False
        self.compare = False
        self.pick = None
        self.recompute_job = None
        self.fitted = False
        self._build()
        self.start_state = self._state_key()
        self._recompute()
        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        width, height = min(1440, int(screen_w * 0.92)), min(940, int(screen_h * 0.88))
        self.geometry(f"{width}x{height}+{max(0, (screen_w - width) // 2)}+{max(0, (screen_h - height) // 3)}")
        self.minsize(960, 620)
        self.deiconify()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<KeyPress>", self._key)
        self.bind("<KeyRelease-space>", self._space_up)
        self.after(60, self._grab)

    # ---- побудова вікна --------------------------------------------------------------------
    def _build(self):
        app = self.app

        def tool_button(parent, text, command, tip, style="Tool.TButton", side="left"):
            widget = ttk.Button(parent, text=text, style=style, command=command)
            widget.pack(side=side, padx=2)
            ToolTip(widget, tip)
            return widget

        top = ttk.Frame(self, padding=(10, 6))
        top.pack(side="top", fill="x")
        tool_button(top, "Готово ✓", self._apply, "Застосувати зміни (Enter)", style="Accent.TButton",
                    side="right")
        tool_button(top, "Скасувати", self._close, "Закрити без змін", side="right")
        app._icon_button(top, "undo", self._undo, "Скасувати дію (Ctrl+Z)", size=20, padx=2)
        app._icon_button(top, "redo", self._redo, "Повторити дію (Ctrl+Y)", size=20, padx=2)
        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Label(top, text="Пензель").pack(side="left", padx=(0, 4))
        tool_button(top, "−", lambda: self._brush_step(-1), "Менший пензель ([)")
        self.brush_label = ttk.Label(top, width=6, anchor="center")
        self.brush_label.pack(side="left")
        tool_button(top, "+", lambda: self._brush_step(1), "Більший пензель (])")
        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=8)
        tool_button(top, "Вписати", self._fit, "Показати фото повністю (0)")
        tool_button(top, "1:1", lambda: self._zoom_at(1.0 / self.zoom), "Реальні пікселі (1)")
        tool_button(top, "−", lambda: self._zoom_at(1 / 1.25), "Зменшити (коліщатко миші)")
        tool_button(top, "+", lambda: self._zoom_at(1.25), "Збільшити (коліщатко миші)")
        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=8)
        compare = tool_button(top, "👁 Оригінал", lambda: None, "Утримуйте, щоб побачити вихідне фото")
        compare.bind("<ButtonPress-1>", lambda _e: self._set_compare(True))
        compare.bind("<ButtonRelease-1>", lambda _e: self._set_compare(False))
        self.uncrop_button = tool_button(top, "Без обрізки", lambda: self._set_params({"crop": None}),
                                         "Скасувати обрізку фото")
        app._line(self, fill="x")

        bottom = ttk.Frame(self, style="Status.TFrame", padding=(10, 4))
        bottom.pack(side="bottom", fill="x")
        self.hint_var = tk.StringVar()
        self.info_var = tk.StringVar()
        ttk.Label(bottom, textvariable=self.hint_var, style="Status.TLabel").pack(side="left")
        ttk.Label(bottom, textvariable=self.info_var, style="Status.TLabel").pack(side="right")

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        tools = ttk.Frame(body, padding=(8, 10))
        tools.pack(side="left", fill="y")
        ttk.Label(tools, text="ІНСТРУМЕНТИ", style="Caption.TLabel").pack(anchor="w", pady=(0, 6))
        self.tool_buttons = {}
        for key, title, tip in self.TOOLS:
            widget = ttk.Button(tools, text=title, style="Tool.TButton", command=lambda k=key: self._set_tool(k))
            widget.pack(fill="x", pady=1)
            ToolTip(widget, tip)
            self.tool_buttons[key] = widget
        app._line(body, orient="vertical", side="left", fill="y")

        right = ttk.Frame(body, width=340, padding=(12, 10))
        right.pack(side="right", fill="y")
        right.pack_propagate(False)
        app._line(body, orient="vertical", side="right", fill="y")
        ttk.Label(right, text="ЧОРНО-БІЛЕ ДЛЯ ДРУКУ", style="Caption.TLabel").pack(anchor="w")
        self.controls = BWControls(right, self._set_params, on_pick=self._start_pick)
        self.controls.frame.pack(fill="x", pady=(6, 0))
        ttk.Label(
            right,
            text=("Швидко й чисто:\n"
                  "1. ✂ Обріжте все зайве навколо.\n"
                  "2. «Більше / Менше» — скільки чорного.\n"
                  "3. ✦ Клацніть по зайвих плямах.\n"
                  "4. ◻ Гумкою підчистіть дрібниці.\n\n"
                  "Колесо миші — масштаб, пробіл — рука.\n"
                  "Ctrl+Z — крок назад."),
            style="Hint.TLabel",
            justify="left",
        ).pack(anchor="w", pady=(16, 0))
        ttk.Button(right, text="↺ Скинути все", style="Tool.TButton", command=self._reset_all).pack(
            side="bottom", fill="x"
        )

        self.canvas = canvas = tk.Canvas(body, highlightthickness=0, borderwidth=0, cursor="crosshair")
        app._themed(canvas, bg="workspace")
        canvas.pack(fill="both", expand=True)
        canvas.bind("<Configure>", self._canvas_resized)
        canvas.bind("<ButtonPress-1>", self._press)
        canvas.bind("<B1-Motion>", self._drag)
        canvas.bind("<ButtonRelease-1>", self._release)
        canvas.bind("<Double-Button-1>", self._double)
        canvas.bind("<ButtonPress-2>", self._pan_begin)
        canvas.bind("<B2-Motion>", self._pan_move)
        canvas.bind("<ButtonRelease-2>", self._pan_end)
        canvas.bind("<Motion>", self._motion)
        canvas.bind("<Leave>", self._leave)
        canvas.bind("<MouseWheel>", self._wheel)
        canvas.bind("<Button-4>", self._wheel)
        canvas.bind("<Button-5>", self._wheel)

    def _grab(self):
        try:
            self.grab_set()
            self.focus_force()
        except tk.TclError:
            pass

    # ---- стан і історія -----------------------------------------------------------------------
    def _state_key(self):
        params = {key: value for key, value in self.params.items() if key != "paint"}
        digest = hashlib.md5(self.white.tobytes() + self.black.tobytes()).hexdigest()
        return json.dumps(params, sort_keys=True) + digest

    def _snapshot(self):
        return (copy.deepcopy(self.params), zlib.compress(self.white.tobytes(), 1),
                zlib.compress(self.black.tobytes(), 1))

    def _restore(self, snapshot):
        params, white, black = snapshot
        size = (self.processor.width, self.processor.height)
        self.params = params
        self.white = Image.frombytes("L", size, zlib.decompress(white))
        self.black = Image.frombytes("L", size, zlib.decompress(black))
        self.full_image = None
        self._recompute()

    def _push_history(self):
        self.history.append(self._snapshot())
        del self.history[:-40]
        self.future.clear()

    def _undo(self):
        if not self.history:
            self.hint_var.set("Немає дій для скасування")
            return
        self.future.append(self._snapshot())
        self._restore(self.history.pop())
        self.hint_var.set("Дію скасовано")

    def _redo(self):
        if not self.future:
            return
        self.history.append(self._snapshot())
        self._restore(self.future.pop())
        self.hint_var.set("Дію повторено")

    def _set_params(self, changes):
        updated = dict(self.params)
        updated.update(changes)
        if updated == self.params:
            return
        self._push_history()
        crop_changed = updated.get("crop") != self.params.get("crop")
        self.params = updated
        self._recompute()
        if crop_changed and self.tool != "crop":
            self._fit()

    def _reset_all(self):
        self._push_history()
        self.params = photo_params(None)
        size = (self.processor.width, self.processor.height)
        self.white = Image.new("L", size, 0)
        self.black = Image.new("L", size, 0)
        self._recompute()
        self._fit()
        self.hint_var.set("Усе скинуто: автоматичне Ч/Б без правок")

    # ---- обчислення й показ --------------------------------------------------------------------
    def _masks(self):
        white = self.white if self.white.getbbox() else None
        black = self.black if self.black.getbbox() else None
        return white, black

    def _preview_paint(self):
        """Швидкий показ мазка без повного перерахунку (повний — коли відпустите мишку)."""
        self.recompute_job = None
        if self.result is None:
            return
        preview = self.result.copy()
        paint_white = (255, 255, 255) if preview.mode == "RGB" else 255
        paint_black = (0, 0, 0) if preview.mode == "RGB" else 0
        preview.paste(paint_white, (0, 0), self.white.crop(self.box))
        preview.paste(paint_black, (0, 0), self.black.crop(self.box))
        self.result = preview
        self._render()

    def _schedule_preview(self):
        if self.recompute_job is None:
            self.recompute_job = self.after(15, self._preview_paint)

    def _schedule_recompute(self):
        if self.recompute_job is None:
            self.recompute_job = self.after(12, self._recompute)

    def _recompute(self):
        self.recompute_job = None
        self.box = self.processor.crop_box(self.params["crop"])
        white, black = self._masks()
        try:
            self.result = self.processor.render(self.params, white, black)
        except Exception as exc:
            messagebox.showerror("Редактор фото", f"Не вдалося обробити фото:\n{exc}", parent=self)
            return
        self.components = None
        self.hover_label = -1
        self.controls.refresh(self.params)
        for key, widget in self.tool_buttons.items():
            widget.configure(style="SegOn.TButton" if key == self.tool else "Tool.TButton")
        self.uncrop_button.state(["!disabled"] if self.params["crop"] else ["disabled"])
        self.brush_label.configure(text=f"{self.brush} px")
        self.hint_var.set(self.HINTS.get(self.tool, "") if not self.pick else self.hint_var.get())
        self._render()

    def _ensure_labels(self):
        if self.components is None:
            white, black = self._masks()
            ink = self.processor.final_ink(self.params, self.box, white, black)
            self.components = InkComponents(ink)
        return self.components

    def _current_image(self):
        if self.tool == "crop":
            if self.full_image is None:
                self.full_image = self.processor.color
            return self.full_image
        if self.compare:
            return self.processor.color.crop(self.box)
        return self.result

    def _canvas_resized(self, _event=None):
        if not self.fitted:
            self.fitted = True
            self._fit()
        else:
            self._render()

    def _fit(self):
        image = self._current_image()
        if image is None:
            return
        width = max(50, self.canvas.winfo_width())
        height = max(50, self.canvas.winfo_height())
        zoom = min((width - 48) / image.width, (height - 48) / image.height)
        self.zoom = max(0.02, min(zoom, 8.0))
        self.ox = (width - image.width * self.zoom) / 2
        self.oy = (height - image.height * self.zoom) / 2
        self._render()

    def _zoom_at(self, factor, x=None, y=None):
        if x is None:
            x, y = self.canvas.winfo_width() / 2, self.canvas.winfo_height() / 2
        image_x, image_y = (x - self.ox) / self.zoom, (y - self.oy) / self.zoom
        self.zoom = max(0.02, min(32.0, self.zoom * factor))
        self.ox = x - image_x * self.zoom
        self.oy = y - image_y * self.zoom
        self._render()

    def _render(self):
        canvas = self.canvas
        canvas.delete("img")
        image = self._current_image()
        if image is None:
            return
        zoom = self.zoom
        width, height = canvas.winfo_width(), canvas.winfo_height()
        x0 = max(0, int(math.floor(-self.ox / zoom)))
        y0 = max(0, int(math.floor(-self.oy / zoom)))
        x1 = min(image.width, int(math.ceil((width - self.ox) / zoom)))
        y1 = min(image.height, int(math.ceil((height - self.oy) / zoom)))
        canvas.create_rectangle(
            self.ox - 1, self.oy - 1, self.ox + image.width * zoom, self.oy + image.height * zoom,
            fill="#ffffff", outline=COLORS["border"], tags="img",
        )
        if x1 > x0 and y1 > y0:
            region = image.crop((x0, y0, x1, y1))
            if (self.tool == "magic" and self.hover_label >= 0 and self.components is not None
                    and not self.compare and not self.pick):
                mask = self.components.region_mask(self.hover_label, (x0, y0, x1, y1))
                if mask.getbbox():
                    region = region.convert("RGB")
                    red = Image.new("RGB", region.size, (235, 40, 70))
                    region = Image.composite(red, region, mask)
            target = (max(1, round((x1 - x0) * zoom)), max(1, round((y1 - y0) * zoom)))
            resample = Image.Resampling.NEAREST if zoom >= 1 else Image.Resampling.BOX
            self.photo = ImageTk.PhotoImage(region.resize(target, resample))
            canvas.create_image(self.ox + x0 * zoom, self.oy + y0 * zoom, image=self.photo, anchor="nw",
                                tags="img")
        canvas.tag_lower("img")
        self._draw_overlays()
        self.info_var.set(f"{image.width} × {image.height} px   •   {round(zoom * 100)}%")

    def _draw_overlays(self):
        canvas = self.canvas
        canvas.delete("overlay")
        accent = COLORS["accent"]
        if self.tool == "crop":
            if self.crop_drag:
                x0, y0, x1, y1 = self.crop_drag
            else:
                x0, y0, x1, y1 = self.box
            left, right = sorted((x0, x1))
            top, bottom = sorted((y0, y1))
            image_w, image_h = self.processor.width, self.processor.height
            to_screen = self._to_screen
            for rect in ((0, 0, image_w, top), (0, bottom, image_w, image_h),
                         (0, top, left, bottom), (right, top, image_w, bottom)):
                ax, ay = to_screen(rect[0], rect[1])
                bx, by = to_screen(rect[2], rect[3])
                if bx - ax > 0 and by - ay > 0:
                    canvas.create_rectangle(ax, ay, bx, by, fill="#000000", stipple="gray50", outline="",
                                            tags="overlay")
            ax, ay = to_screen(left, top)
            bx, by = to_screen(right, bottom)
            canvas.create_rectangle(ax, ay, bx, by, outline=accent, width=2, tags="overlay")
        if len(self.lasso) > 1:
            points = [coordinate for point in self.lasso for coordinate in self._to_screen(*point)]
            canvas.create_line(*points, fill=accent, width=2, dash=(5, 3), tags="overlay")
            canvas.create_line(*points[-2:], *points[:2], fill=accent, width=1, dash=(2, 4), tags="overlay")

    def _to_screen(self, x, y):
        return self.ox + x * self.zoom, self.oy + y * self.zoom

    def _to_image(self, x, y):
        return (x - self.ox) / self.zoom, (y - self.oy) / self.zoom

    def _draw_cursor(self, x, y):
        canvas = self.canvas
        canvas.delete("cursor")
        if self.tool in ("erase", "brush") and not self.space_pan and not self.pick and not self.compare:
            radius = max(2, self.brush * self.zoom / 2)
            canvas.create_oval(x - radius, y - radius, x + radius, y + radius, outline="#000000", width=3,
                               tags="cursor")
            canvas.create_oval(x - radius, y - radius, x + radius, y + radius, outline="#ffffff", width=1,
                               tags="cursor")

    # ---- інструменти ----------------------------------------------------------------------------
    def _set_tool(self, tool):
        previous = self.tool
        self.tool = tool
        self.pick = None
        self.lasso = []
        self.crop_drag = None
        self.hover_label = -1
        self.canvas.configure(cursor="fleur" if tool == "hand" else "crosshair")
        self._recompute()
        if (previous == "crop") != (tool == "crop"):
            self._fit()

    def _brush_step(self, direction):
        factor = 1.25 if direction > 0 else 0.8
        self.brush = int(max(1, min(600, round(self.brush * factor) + (1 if direction > 0 else -1))))
        self.brush_label.configure(text=f"{self.brush} px")
        self.hint_var.set(f"Розмір пензля: {self.brush} px")

    def _start_pick(self, kind):
        if self.tool == "crop":
            self._set_tool("magic")
        self.pick = kind
        self.canvas.configure(cursor="target")
        self.hint_var.set(
            "Клацніть по фото там, що має стати ЧОРНИМ" if kind == "black"
            else "Клацніть по фото там, що має стати БІЛИМ"
        )

    def _set_compare(self, active):
        self.compare = active
        self._render()

    def _image_point(self, event, clamp=True):
        x, y = self._to_image(event.x, event.y)
        image = self._current_image()
        if clamp and image is not None:
            x = min(max(x, 0), image.width - 1)
            y = min(max(y, 0), image.height - 1)
        return x, y

    def _inside(self, event):
        image = self._current_image()
        x, y = self._to_image(event.x, event.y)
        return image is not None and 0 <= x < image.width and 0 <= y < image.height

    def _to_full(self, x, y):
        return int(round(x)) + self.box[0], int(round(y)) + self.box[1]

    def _press(self, event):
        self.canvas.focus_set()
        if self.space_pan or self.tool == "hand":
            self._pan_begin(event)
            return
        if self.pick:
            self._apply_pick(event)
            return
        if self.tool == "crop":
            x, y = self._image_point(event)
            self.crop_drag = [x, y, x, y]
            self._draw_overlays()
        elif self.tool == "magic":
            self._magic_click(event)
        elif self.tool in ("erase", "brush"):
            self._push_history()
            point = self._to_full(*self._image_point(event, clamp=False))
            if event.state & 0x0001 and self.stroke_last:
                self._paint_line(self.stroke_last, point)
            else:
                self._paint_line(point, point)
            self.stroke_last = point
            self.stroke_active = True
            self._schedule_preview()
        elif self.tool == "lasso":
            self.lasso = [self._image_point(event)]

    def _drag(self, event):
        if self.pan_start:
            self._pan_move(event)
            return
        self._draw_cursor(event.x, event.y)
        if self.tool == "crop" and self.crop_drag:
            x, y = self._image_point(event)
            self.crop_drag[2:] = [x, y]
            self._draw_overlays()
        elif self.tool in ("erase", "brush") and self.stroke_active:
            point = self._to_full(*self._image_point(event, clamp=False))
            self._paint_line(self.stroke_last, point)
            self.stroke_last = point
            self._schedule_preview()
        elif self.tool == "lasso" and self.lasso:
            x, y = self._image_point(event)
            last_x, last_y = self.lasso[-1]
            if math.hypot((x - last_x) * self.zoom, (y - last_y) * self.zoom) >= 3:
                self.lasso.append((x, y))
                self._draw_overlays()

    def _release(self, event):
        if self.pan_start:
            self._pan_end(event)
            return
        if self.tool == "crop" and self.crop_drag:
            x0, y0, x1, y1 = self.crop_drag
            self.crop_drag = None
            left, right = sorted((x0, x1))
            top, bottom = sorted((y0, y1))
            if right - left >= 8 and bottom - top >= 8:
                width, height = self.processor.width, self.processor.height
                self._set_params({"crop": [round(left / width, 5), round(top / height, 5),
                                           round(right / width, 5), round(bottom / height, 5)]})
                self.hint_var.set("Обрізано. Перейдіть до ✦ чарівної гумки, щоб прибрати плями")
            else:
                self._draw_overlays()
        elif self.tool in ("erase", "brush") and self.stroke_active:
            self.stroke_active = False
            self._recompute()
        elif self.tool == "lasso" and self.lasso:
            points = self.lasso
            self.lasso = []
            if len(points) >= 3:
                self._push_history()
                polygon = [self._to_full(x, y) for x, y in points]
                ImageDraw.Draw(self.white).polygon(polygon, fill=255)
                ImageDraw.Draw(self.black).polygon(polygon, fill=0)
                self._recompute()
                self.hint_var.set("Обведене стерто")
            else:
                self._draw_overlays()

    def _double(self, event):
        if self.tool == "crop":
            self.crop_drag = None
            self._set_params({"crop": None})
            self._fit()

    def _paint_line(self, start, end):
        thickness = max(1, int(self.brush))
        target, other = (self.white, self.black) if self.tool == "erase" else (self.black, self.white)
        radius = max(1, thickness // 2)
        for image, fill in ((target, 255), (other, 0)):
            draw = ImageDraw.Draw(image)
            if start != end and thickness > 1:
                draw.line([start, end], fill=fill, width=thickness)
            elif start != end:
                draw.line([start, end], fill=fill)
            for x, y in {start, end}:
                draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=fill)

    def _magic_click(self, event):
        if not self._inside(event):
            return
        components = self._ensure_labels()
        x, y = self._image_point(event)
        root = components.root_at(int(x), int(y))
        if root < 0:
            self.hint_var.set("Тут немає чорного — наведіть на пляму, яку треба прибрати")
            return
        center = components.center(root)
        if center is None:
            return
        full_x, full_y = center[0] + self.box[0], center[1] + self.box[1]
        seed = [round((full_x + 0.5) / self.processor.width, 6), round((full_y + 0.5) / self.processor.height, 6)]
        self._set_params({"seeds": list(self.params["seeds"]) + [seed]})
        self.hint_var.set("Пляму прибрано. Ctrl+Z — повернути")

    def _apply_pick(self, event):
        kind = self.pick
        self.pick = None
        self.canvas.configure(cursor="crosshair")
        if not self._inside(event) or self.tool == "crop":
            self.hint_var.set("Клік поза фото — піпетку скасовано")
            return
        x, y = self._image_point(event)
        tone = self.processor.tone(self.params, self.box)
        value = int(tone.getpixel((int(x), int(y))))
        base = self.processor.auto_threshold(self.params, self.box)
        target = value + 14 if kind == "black" else value - 14
        level = int(max(-160, min(160, target - base)))
        self._set_params({"level": level})
        self.hint_var.set("Готово: це тепер чорне" if kind == "black" else "Готово: це тепер біле")

    # ---- перегляд -------------------------------------------------------------------------------
    def _pan_begin(self, event):
        self.pan_start = (event.x, event.y, self.ox, self.oy)
        self.canvas.configure(cursor="fleur")

    def _pan_move(self, event):
        if not self.pan_start:
            return
        start_x, start_y, ox, oy = self.pan_start
        self.ox = ox + event.x - start_x
        self.oy = oy + event.y - start_y
        self._render()

    def _pan_end(self, _event=None):
        self.pan_start = None
        self.canvas.configure(cursor="fleur" if self.tool == "hand" or self.space_pan else "crosshair")

    def _wheel(self, event):
        if getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0:
            self._zoom_at(1.2, event.x, event.y)
        else:
            self._zoom_at(1 / 1.2, event.x, event.y)
        return "break"

    def _motion(self, event):
        self._draw_cursor(event.x, event.y)
        if self.tool == "magic" and not self.pick and not self.compare:
            label = -1
            if self._inside(event):
                x, y = self._image_point(event)
                label = self._ensure_labels().root_at(int(x), int(y))
            if label != self.hover_label:
                self.hover_label = label
                self._render()

    def _leave(self, _event=None):
        self.canvas.delete("cursor")
        if self.hover_label >= 0:
            self.hover_label = -1
            self._render()

    # ---- клавіатура -----------------------------------------------------------------------------
    def _key_name(self, event):
        keysym = str(event.keysym)
        if len(keysym) == 1 and keysym.isascii():
            return keysym.lower()
        named = {"bracketleft": "[", "bracketright": "]"}
        if keysym in named:
            return named[keysym]
        codes = self.KEY_VK if os.name == "nt" else self.KEY_X11
        return codes.get(event.keycode) or self.KEY_CYRILLIC.get(keysym)

    def _key(self, event):
        keysym = str(event.keysym)
        control = bool(event.state & 0x0004)
        shift = bool(event.state & 0x0001)
        if keysym == "space":
            if not self.space_pan:
                self.space_pan = True
                self.canvas.configure(cursor="fleur")
                self.canvas.delete("cursor")
            return "break"
        key = self._key_name(event)
        if control:
            if key == "z":
                self._redo() if shift else self._undo()
                return "break"
            if key == "y":
                self._redo()
                return "break"
            return None
        if keysym == "Escape":
            self.pick = None
            self.lasso = []
            self.crop_drag = None
            self.canvas.configure(cursor="crosshair")
            self._recompute()
            return "break"
        if keysym in ("Return", "KP_Enter"):
            self._apply()
            return "break"
        if keysym in ("plus", "equal", "KP_Add"):
            self._zoom_at(1.25)
            return "break"
        if keysym in ("minus", "KP_Subtract"):
            self._zoom_at(1 / 1.25)
            return "break"
        if key in self.KEY_TOOLS:
            self._set_tool(self.KEY_TOOLS[key])
            return "break"
        if key in ("[", "]"):
            self._brush_step(1 if key == "]" else -1)
            return "break"
        if key == "0":
            self._fit()
            return "break"
        if key == "1":
            self._zoom_at(1.0 / self.zoom)
            return "break"
        return None

    def _space_up(self, _event=None):
        self.space_pan = False
        if not self.pan_start:
            self.canvas.configure(cursor="fleur" if self.tool == "hand" else "crosshair")

    # ---- завершення -----------------------------------------------------------------------------
    def _changed(self):
        return self._state_key() != self.start_state

    def _apply(self):
        if not self._changed():
            self._destroy()
            return
        paint_path = None
        if self.white.getbbox() or self.black.getbbox():
            folder = self.app.data_dir / "photo_paint"
            folder.mkdir(parents=True, exist_ok=True)
            paint_path = folder / f"paint_{uuid.uuid4().hex}.png"
            save_paint_masks(self.white, self.black, paint_path)
        params = copy.deepcopy(self.params)
        params["paint"] = str(paint_path) if paint_path else None
        element_id = self.element_id
        self._destroy()
        self.app._apply_photo_edit(element_id, params)

    def _close(self):
        if self._changed():
            answer = messagebox.askyesnocancel(
                "Редактор фото", "Застосувати зміни до фото на наліпці?", parent=self
            )
            if answer is None:
                return
            if answer:
                self._apply()
                return
        self._destroy()

    def _destroy(self):
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.app.photo_editor = None
        self.destroy()
        try:
            self.app.focus_force()
        except tk.TclError:
            pass


class LabelDocument:
    """Одна вкладка: окремий макет зі своєю історією змін і розміром наліпки."""

    counter = 0

    def __init__(self, width, height):
        LabelDocument.counter += 1
        self.number = LabelDocument.counter
        self.elements = []
        self.selected_id = None
        self.selection_ids = []
        self.layout_locked = False
        self.current_file = None
        self.undo_stack = []
        self.redo_stack = []
        self.width = float(width)
        self.height = float(height)
        self.dirty = False
        self.active_preset_display = None


class LabelDesigner(tk.Tk):
    # ---- Виділення: один головний елемент (selected_id) + кілька вибраних (selection_ids) ----
    @property
    def selected_id(self):
        return self.__dict__.get("_selected_id")

    @selected_id.setter
    def selected_id(self, value):
        # Звичайне присвоєння вибирає рівно один елемент (або нічого).
        self.__dict__["_selected_id"] = value
        self.__dict__["selection_ids"] = [value] if value else []

    def _set_selection(self, ids, primary=None):
        existing = {element["id"] for element in self.elements}
        ids = [element_id for element_id in dict.fromkeys(ids) if element_id in existing]
        if primary not in ids:
            primary = ids[-1] if ids else None
        self.__dict__["_selected_id"] = primary
        self.__dict__["selection_ids"] = ids

    def _selected_elements(self):
        chosen = set(self.selection_ids)
        return [element for element in self.elements if element["id"] in chosen]

    def _multi(self):
        return len(self.selection_ids) > 1

    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1320x860")
        self.minsize(1120, 740)

        self.documents = []
        self.doc = None
        self.doc_dirty = False
        self.elements = []
        self.selected_id = None
        self.canvas_items = {}
        self.photo_refs = {}
        self.drag_start = None
        self.drag_origin = None
        self.drag_started = False
        self.resize_state = None
        self.rotate_state = None
        self.group_origin = None
        self.collapse_on_release = None
        self.marquee = None
        self.wheel_rotate_state = None
        self.icon_buttons = []
        self.icon_photos = {}
        self.history_buttons_state = None
        self.recent_files = []
        self.image_size_cache = {}
        self.bitmap_cache = {}
        self.adjust_compare = False
        self.photo_processors = {}
        self.photo_outputs = {}
        self.paint_cache = {}
        self.photo_editor = None
        self.presets_dialog = None
        self.active_preset_display = None
        self.clipboard_signature = None
        self.inline_editor = None
        self.inline_element_id = None
        self.inline_original_text = None
        self.double_click_guard = None
        self.loading_properties = False
        self.live_apply_job = None
        self.layout_locked = False
        self.printer_infos = {}
        self.connection_ok = False
        self.connection_details = ""
        self.status_check_running = False
        self.connection_after_id = None
        self.printing_active = False
        self.current_file = None
        self.undo_stack = []
        self.redo_stack = []
        self.clipboard_elements = []
        self.history_suspended = False
        self.autosave_suspended = True
        self.autosave_job = None
        self.drag_history_recorded = False
        self.layer_ids = []
        self.generated_images = []
        self.data_dir = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "XprinterLabelDesigner"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.autosave_path = self.data_dir / "autosave.json"
        self.recovery_payload = self._read_autosave_payload()
        self.presets_path = self.data_dir / "size_presets.json"
        self.custom_presets = []
        self.theme_name = DEFAULT_THEME
        last_size = self._load_size_presets()
        COLORS.clear()
        COLORS.update(THEMES[self.theme_name])

        self._build_ui()
        self._apply_window_icon()
        self._set_theme(self.theme_name, save=False)
        try:
            self._set_label_size(*self._validate_label_size(*last_size))
        except (TypeError, ValueError):
            self._set_label_size(LABEL_WIDTH_MM, LABEL_HEIGHT_MM)
        self._refresh_printers()
        self._new_tab()
        self.autosave_suspended = False
        self.after(200, self._ensure_label_fits)
        self.after(250, self._offer_autosave_recovery)
        self.connection_after_id = self.after(500, self._schedule_connection_check)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---- Вкладки (як у браузері) -----------------------------------------------------------

    def _build_tabbar(self, parent):
        self.tabbar = tk.Canvas(parent, height=34, highlightthickness=0, borderwidth=0)
        self._themed(self.tabbar, bg="header")
        self.tabbar.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.tab_font = tkfont.Font(family=UI_FONT, size=9)
        self.tab_regions = []
        self.tab_plus = (0, 0)
        self.tab_hover = None
        self.tab_close_hover = None
        self.tab_drag = None
        self.tab_close_pending = None
        self.tabbar.bind("<Configure>", lambda _e: self._draw_tabs())
        self.tabbar.bind("<Motion>", self._tab_motion)
        self.tabbar.bind("<Leave>", self._tab_leave)
        self.tabbar.bind("<ButtonPress-1>", self._tab_press)
        self.tabbar.bind("<B1-Motion>", self._tab_drag_motion)
        self.tabbar.bind("<ButtonRelease-1>", self._tab_release)
        self.tabbar.bind("<ButtonRelease-2>", self._tab_middle)
        self.tabbar.bind("<Double-Button-1>", self._tab_double)

    def _doc_title(self, doc):
        path = self.current_file if doc is self.doc else doc.current_file
        return Path(path).stem if path else f"Без назви {doc.number}"

    def _doc_dirty(self, doc):
        return self.doc_dirty if doc is self.doc else doc.dirty

    def _elide(self, text, width):
        if self.tab_font.measure(text) <= width:
            return text
        while text and self.tab_font.measure(text + "…") > width:
            text = text[:-1]
        return text + "…"

    def _draw_tabs(self):
        canvas = getattr(self, "tabbar", None)
        if canvas is None:
            return
        canvas.delete("all")
        width = max(200, canvas.winfo_width())
        height = int(canvas.cget("height"))
        count = max(1, len(self.documents))
        tab_width = int(max(92, min(210, (width - 44) / count)))
        top = 5
        middle = (top + height) / 2 + 1
        x = 2
        self.tab_regions = []
        for index, doc in enumerate(self.documents):
            active = doc is self.doc
            hover = self.tab_hover == index
            x0, x1 = x, x + tab_width
            if active or hover:
                fill = COLORS["panel"] if active else COLORS["soft"]
                radius = 8
                canvas.create_polygon(
                    x0, height + 2, x0, top + radius, x0, top, x0 + radius, top, x1 - radius, top, x1, top,
                    x1, top + radius, x1, height + 2, smooth=True, fill=fill, outline="",
                )
                if active:
                    canvas.create_line(x0 + 7, top + 1, x1 - 7, top + 1, fill=COLORS["accent"], width=2)
            else:
                next_active = index + 1 < len(self.documents) and (
                    self.documents[index + 1] is self.doc or self.tab_hover == index + 1
                )
                if index + 1 < len(self.documents) and not next_active:
                    canvas.create_line(x1, top + 9, x1, height - 7, fill=COLORS["border"])
            text_x = x0 + 12
            if self._doc_dirty(doc):
                canvas.create_oval(text_x, middle - 3, text_x + 6, middle + 3, fill=COLORS["accent"], outline="")
                text_x += 11
            title = self._elide(self._doc_title(doc), x1 - text_x - 28)
            canvas.create_text(
                text_x, middle, text=title, anchor="w", font=self.tab_font,
                fill=COLORS["text"] if active else COLORS["muted"],
            )
            close_x = x1 - 15
            if active or hover:
                if self.tab_close_hover == index:
                    canvas.create_oval(close_x - 9, middle - 9, close_x + 9, middle + 9,
                                       fill=COLORS["accent_soft"], outline="")
                canvas.create_text(close_x, middle, text="×", font=(UI_FONT, 12),
                                   fill=COLORS["text"] if self.tab_close_hover == index else COLORS["muted"])
            self.tab_regions.append((x0, x1, doc, close_x - 10, close_x + 10))
            x = x1 + 1
        plus_x = x + 18
        self.tab_plus = (plus_x - 14, plus_x + 14)
        if self.tab_hover == "plus":
            canvas.create_oval(plus_x - 13, middle - 13, plus_x + 13, middle + 13, fill=COLORS["soft"], outline="")
        canvas.create_text(plus_x, middle, text="+", font=(UI_FONT, 15), fill=COLORS["text"])

    def _tab_at(self, x):
        for index, (x0, x1, _doc, close0, close1) in enumerate(self.tab_regions):
            if x0 <= x < x1:
                return index, close0 <= x <= close1
        if self.tab_plus[0] <= x <= self.tab_plus[1]:
            return "plus", False
        return None, False

    def _tab_motion(self, event):
        if self.tab_drag:
            return
        index, on_close = self._tab_at(event.x)
        close = index if on_close else None
        if (index, close) != (self.tab_hover, self.tab_close_hover):
            self.tab_hover, self.tab_close_hover = index, close
            self._draw_tabs()
            if isinstance(index, int):
                doc = self.tab_regions[index][2]
                path = self.current_file if doc is self.doc else doc.current_file
                width, height = (LABEL_WIDTH_MM, LABEL_HEIGHT_MM) if doc is self.doc else (doc.width, doc.height)
                self.status_var.set(f"{path or self._doc_title(doc)}  •  {size_text(width, height)}")
            elif index == "plus":
                self.status_var.set("Нова вкладка (Ctrl+T)")

    def _tab_leave(self, _event=None):
        if self.tab_hover is not None or self.tab_close_hover is not None:
            self.tab_hover = self.tab_close_hover = None
            self._draw_tabs()

    def _tab_press(self, event):
        index, on_close = self._tab_at(event.x)
        if index == "plus":
            self._new_tab()
            return
        if index is None:
            return
        doc = self.tab_regions[index][2]
        if on_close:
            self.tab_close_pending = doc
            return
        self._activate_document(doc)
        self.tab_drag = {"doc": doc, "x": event.x, "moved": False}

    def _tab_drag_motion(self, event):
        drag = self.tab_drag
        if not drag:
            return
        if not drag["moved"] and abs(event.x - drag["x"]) < 8:
            return
        drag["moved"] = True
        target = None
        for index, (x0, x1, _doc, _c0, _c1) in enumerate(self.tab_regions):
            if x0 <= event.x < x1:
                target = index
        if target is None:
            target = 0 if event.x < 0 else len(self.documents) - 1
        current = self.documents.index(drag["doc"])
        if target != current:
            self.documents.insert(target, self.documents.pop(current))
            self._draw_tabs()

    def _tab_release(self, event):
        pending = self.tab_close_pending
        self.tab_close_pending = None
        self.tab_drag = None
        if pending is not None:
            index, on_close = self._tab_at(event.x)
            if on_close and isinstance(index, int) and self.tab_regions[index][2] is pending:
                self._close_tab(pending)

    def _tab_middle(self, event):
        index, _on_close = self._tab_at(event.x)
        if isinstance(index, int):
            self._close_tab(self.tab_regions[index][2])

    def _tab_double(self, event):
        index, _on_close = self._tab_at(event.x)
        if index is None:
            self._new_tab()

    # ---- Документи ---------------------------------------------------------------------------

    def _stash_document(self):
        doc = self.doc
        if doc is None:
            return
        doc.elements = self.elements
        doc.selected_id = self.selected_id
        doc.selection_ids = list(self.selection_ids)
        doc.layout_locked = self.layout_locked
        doc.current_file = self.current_file
        doc.undo_stack = self.undo_stack
        doc.redo_stack = self.redo_stack
        doc.width = LABEL_WIDTH_MM
        doc.height = LABEL_HEIGHT_MM
        doc.dirty = self.doc_dirty
        doc.active_preset_display = self.active_preset_display

    def _activate_document(self, doc):
        if doc is self.doc:
            return
        self._finish_inline_edit(commit=True)
        self._stash_document()
        self.doc = doc
        self.elements = doc.elements
        self._set_selection(getattr(doc, "selection_ids", None) or [doc.selected_id], doc.selected_id)
        self.layout_locked = doc.layout_locked
        self.current_file = doc.current_file
        self.undo_stack = doc.undo_stack
        self.redo_stack = doc.redo_stack
        self.doc_dirty = doc.dirty
        self.active_preset_display = doc.active_preset_display
        self.drag_start = self.drag_origin = self.group_origin = None
        self.resize_state = self.rotate_state = self.marquee = None
        self.wheel_rotate_state = None
        self._set_label_size(doc.width, doc.height)
        self._render_all()
        self._load_properties()
        self._draw_tabs()
        self._ensure_label_fits()

    def _new_tab(self, width=None, height=None):
        doc = LabelDocument(width or LABEL_WIDTH_MM, height or LABEL_HEIGHT_MM)
        index = self.documents.index(self.doc) + 1 if self.doc in self.documents else len(self.documents)
        self.documents.insert(index, doc)
        self._activate_document(doc)
        self.status_var.set(f"Нова вкладка: {self._doc_title(doc)}")
        return doc

    def _new_layout(self, confirm=True):
        """«Новий» відкриває порожню вкладку (попередні роботи лишаються відкритими)."""
        return self._new_tab()

    def _close_tab(self, doc=None):
        doc = doc or self.doc
        if doc not in self.documents:
            return False
        if doc is self.doc:
            self._finish_inline_edit(commit=True)
            self._stash_document()
        if doc.dirty and doc.elements:
            self._activate_document(doc)
            answer = messagebox.askyesnocancel(
                "Закрити вкладку", f"Зберегти зміни у «{self._doc_title(doc)}» перед закриттям?"
            )
            if answer is None:
                return False
            if answer and not self._save_layout():
                return False
        index = self.documents.index(doc)
        self.documents.remove(doc)
        if not self.documents:
            self.documents.append(LabelDocument(doc.width, doc.height))
        if doc is self.doc:
            self.doc = None
            self._activate_document(self.documents[min(index, len(self.documents) - 1)])
        else:
            self._draw_tabs()
        self._schedule_autosave()
        return True

    def _cycle_tab(self, direction, event=None):
        # Лише з головного вікна: у редакторі фото чи діалогах вкладки не перемикаємо.
        try:
            if event is not None and event.widget.winfo_toplevel() is not self:
                return None
        except (AttributeError, tk.TclError):
            return None
        if self.photo_editor is not None:
            return "break"
        if len(self.documents) > 1 and self.doc in self.documents:
            index = (self.documents.index(self.doc) + direction) % len(self.documents)
            self._activate_document(self.documents[index])
        return "break"

    def _mark_dirty(self):
        if not self.doc_dirty:
            self.doc_dirty = True
            self._draw_tabs()

    def _mark_clean(self):
        self.doc_dirty = False
        self._draw_tabs()

    def _update_title(self):
        doc = getattr(self, "doc", None)
        name = self._doc_title(doc) if doc is not None else APP_NAME
        self.title(f"{name} · {size_text(LABEL_WIDTH_MM, LABEL_HEIGHT_MM)} — {APP_NAME}")

    def _print_tabs_dialog(self):
        """Вибрати кілька відкритих вкладок і надрукувати їх одним заходом."""
        self._stash_document()
        dialog = tk.Toplevel(self)
        dialog.title("Друк кількох вкладок")
        self._themed(dialog, bg="panel")
        dialog.transient(self)
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Які вкладки друкувати?", style="Title.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8)
        )
        choices = []
        for row, doc in enumerate(self.documents, start=1):
            variable = tk.BooleanVar(value=doc is self.doc and bool(doc.elements))
            label = (f"{self._doc_title(doc)}   ·   {size_text(doc.width, doc.height)}"
                     f"   ·   елементів: {len(doc.elements)}")
            check = ttk.Checkbutton(frame, text=label, variable=variable)
            check.grid(row=row, column=0, columnspan=3, sticky="w", pady=1)
            if not doc.elements:
                check.state(["disabled"])
            choices.append((doc, variable))
        row = len(self.documents) + 1
        ttk.Label(frame, text="Копій кожної").grid(row=row, column=0, sticky="w", pady=(10, 0))
        copies_var = tk.IntVar(value=self.copies_var.get() or 1)
        ttk.Spinbox(frame, from_=1, to=99, textvariable=copies_var, width=6).grid(
            row=row, column=1, sticky="w", pady=(10, 0), padx=(6, 0)
        )
        buttons = ttk.Frame(frame)
        buttons.grid(row=row + 1, column=0, columnspan=3, sticky="ew", pady=(14, 0))

        def set_all(value):
            for doc, variable in choices:
                if doc.elements:
                    variable.set(value)

        def start():
            selected = [doc for doc, variable in choices if variable.get() and doc.elements]
            if not selected:
                messagebox.showwarning("Друк", "Позначте хоча б одну вкладку з елементами", parent=dialog)
                return
            try:
                copies = int(copies_var.get())
                if not 1 <= copies <= 99:
                    raise ValueError
            except (ValueError, tk.TclError):
                messagebox.showerror("Друк", "Кількість копій має бути від 1 до 99", parent=dialog)
                return
            dialog.destroy()
            self._print_documents(selected, copies)

        ttk.Button(buttons, text="Усі", style="Tool.TButton", command=lambda: set_all(True)).pack(side="left")
        ttk.Button(buttons, text="Жодної", style="Tool.TButton", command=lambda: set_all(False)).pack(
            side="left", padx=(4, 0)
        )
        ttk.Button(buttons, text="Друкувати", style="AccentSmall.TButton", command=start).pack(side="right")
        ttk.Button(buttons, text="Скасувати", command=dialog.destroy).pack(side="right", padx=(0, 6))
        dialog.bind("<Escape>", lambda _e: dialog.destroy())
        dialog.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - dialog.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - dialog.winfo_height()) // 3
        dialog.geometry(f"+{max(0, x)}+{max(0, y)}")
        dialog.grab_set()

    def _print_documents(self, documents, copies):
        printer = self.printer_var.get().strip()
        if not printer or not self.connection_ok:
            messagebox.showwarning("Друк", "Спочатку дочекайтеся зеленого індикатора підключення принтера.")
            return
        layouts = []
        try:
            for doc in documents:
                layout = self._layout_data(doc=doc)
                for element in layout["elements"]:
                    if (element.get("visible", True) and element["type"] == "image"
                            and not os.path.isfile(element["path"])):
                        raise FileNotFoundError(f"«{self._doc_title(doc)}»: не знайдено {element['path']}")
                layouts.append(self._print_ready_layout(layout))
        except Exception as exc:
            messagebox.showerror("Друк", f"Не вдалося підготувати вкладки:\n{exc}")
            return
        self.print_button.configure(state="disabled")
        self.printing_active = True
        self.status_var.set(f"Друк вкладок: 0 із {len(layouts) * copies}")
        threading.Thread(
            target=self._batch_print_worker,
            args=(layouts, printer, copies, self.connection_var.get(), self.network_ip_var.get().strip()),
            daemon=True,
        ).start()

    # ---- Вкладка «Фото»: чорно-біле для друку -----------------------------------------------

    def _build_photo_tab(self, tab):
        tab.columnconfigure(0, weight=1)
        self.photo_title_var = tk.StringVar(value="Фото не вибрано")
        ttk.Label(tab, textvariable=self.photo_title_var, style="Title.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 8)
        )
        self.photo_empty = ttk.Label(
            tab,
            text=("Натисніть на фото на наліпці —\n"
                  "тут можна зробити його ідеально чорно-білим.\n\n"
                  "Подвійний клік по фото відкриває редактор:\n"
                  "обрізка, чарівна гумка, пензель, ласо."),
            style="Hint.TLabel",
            justify="left",
        )
        self.photo_empty.grid(row=1, column=0, sticky="nw")
        body = ttk.Frame(tab)
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        self.photo_body = body
        editor_button = ttk.Button(
            body, text="✏  Редактор фото — очистити", style="Accent.TButton",
            command=self._open_photo_editor,
        )
        editor_button.grid(row=0, column=0, sticky="ew")
        ToolTip(editor_button, "Велике вікно: обрізка, чарівна гумка, гумка, пензель, ласо.\n"
                               "Також — подвійний клік по фото.")
        box = ttk.LabelFrame(body, text="Чорно-біле для друку", padding=8)
        box.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self.photo_controls = BWControls(box, self._set_photo_params)
        self.photo_controls.frame.pack(fill="x")
        buttons = ttk.Frame(body)
        buttons.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)
        compare = ttk.Button(buttons, text="👁 Утримуйте — оригінал", style="Tool.TButton")
        compare.grid(row=0, column=0, sticky="ew", padx=(0, 2))
        compare.bind("<ButtonPress-1>", lambda _e: self._photo_compare(True))
        compare.bind("<ButtonRelease-1>", lambda _e: self._photo_compare(False))
        ttk.Button(buttons, text="↺ Як було", style="Tool.TButton", command=self._reset_photo).grid(
            row=0, column=1, sticky="ew", padx=(2, 0)
        )
        ttk.Label(
            body,
            text=("Зміни видно одразу на наліпці й так само друкуються. "
                  "Вихідний файл не змінюється. Ctrl+Z — скасувати."),
            style="Hint.TLabel",
            justify="left",
            wraplength=320,
        ).grid(row=3, column=0, sticky="w", pady=(10, 0))
        if not photo_available():
            ttk.Label(
                body, text="Для обробки фото потрібні пакети opencv-python-headless і numpy.",
                style="Hint.TLabel", wraplength=320,
            ).grid(row=4, column=0, sticky="w", pady=(10, 0))

    def _selected_photo(self):
        element = self._element()
        if element and element.get("type") == "image":
            return element
        return None

    def _effective_photo_params(self, element):
        if element.get("photo"):
            return photo_params(element["photo"])
        return dict(photo_params(None), mode="original")

    def _load_photo_controls(self):
        if not hasattr(self, "photo_controls"):
            return
        element = self._selected_photo()
        if not element:
            self.photo_title_var.set("Фото не вибрано")
            self.photo_body.grid_remove()
            self.photo_empty.grid()
            return
        self.photo_empty.grid_remove()
        self.photo_body.grid()
        if element.get("source_kind") == "qr":
            self.photo_title_var.set("QR-код")
        elif element.get("source_kind") == "code128":
            self.photo_title_var.set("Штрихкод")
        else:
            self.photo_title_var.set(f"Фото: {Path(element.get('path', '')).name[:30]}")
        self.photo_controls.refresh(self._effective_photo_params(element))

    def _open_photo_tab(self):
        if self._selected_photo():
            self.notebook.select(self.photo_tab)
            self._load_photo_controls()

    def _set_photo_params(self, changes):
        element = self._selected_photo()
        if not element:
            self.status_var.set("Спочатку виберіть фото на наліпці")
            return
        if not photo_available():
            messagebox.showerror("Фото", "Для обробки фото потрібні пакети opencv-python-headless і numpy")
            return
        params = self._effective_photo_params(element)
        if not element.get("photo") and "mode" not in changes:
            changes = dict(changes, mode="bw")
        params.update(changes)
        self._update_photo(element, params)

    def _reset_photo(self):
        element = self._selected_photo()
        if element and (element.get("photo") or element.get("adjust")):
            self._update_photo(element, None)
            self.status_var.set("Фото повернуто до оригіналу")

    def _update_photo(self, element, params):
        """Записати нові налаштування фото (None — оригінал) і підлаштувати рамку після обрізки."""
        self._record_history()
        old_size = self._element_pixel_size(element)
        if params is None:
            element.pop("photo", None)
        else:
            element["photo"] = params
        element.pop("adjust", None)
        new_size = self._element_pixel_size(element)
        old_ratio = old_size[0] / max(1.0, old_size[1])
        new_ratio = new_size[0] / max(1.0, new_size[1])
        if abs(new_ratio - old_ratio) > 0.005 * max(old_ratio, new_ratio):
            self._refit_image_box(element, new_size)
        self._render_all()
        self._load_properties()

    def _refit_image_box(self, element, pixel_size):
        """Нові пропорції картинки (після обрізки) вписати в стару рамку, зберігши центр."""
        angle = self._normalize_angle(element.get("rotation", 0))
        extent_w, extent_h = self._rotated_extent(max(1, pixel_size[0]), max(1, pixel_size[1]), angle)
        box_w = max(0.5, float(element["width"]))
        box_h = max(0.5, float(element["height"]))
        center = (float(element["x"]) + box_w / 2, float(element["y"]) + box_h / 2)
        scale = min(box_w / extent_w, box_h / extent_h)
        new_w, new_h = max(0.5, extent_w * scale), max(0.5, extent_h * scale)
        element["preserve_aspect"] = True
        element["width"] = round(new_w, 2)
        element["height"] = round(new_h, 2)
        element["x"] = round(center[0] - new_w / 2, 2)
        element["y"] = round(center[1] - new_h / 2, 2)

    def _photo_compare(self, active):
        if not self._selected_photo():
            return
        self.adjust_compare = active
        self._render_all()
        self.status_var.set("Показано оригінал — відпустіть кнопку" if active else "Показано з обробкою")

    def _open_photo_editor(self, element=None):
        element = element or self._selected_photo()
        if not element:
            self.status_var.set("Спочатку виберіть фото на наліпці")
            return
        if not photo_available():
            messagebox.showerror("Редактор фото", "Для редактора потрібні пакети opencv-python-headless і numpy")
            return
        if self.photo_editor is not None:
            try:
                self.photo_editor.lift()
                return
            except tk.TclError:
                self.photo_editor = None
        try:
            self.photo_editor = PhotoEditor(self, element)
        except Exception as exc:
            self.photo_editor = None
            messagebox.showerror("Редактор фото", f"Не вдалося відкрити фото:\n{exc}")

    def _apply_photo_edit(self, element_id, params):
        element = self._element(element_id)
        if not element:
            return
        self._update_photo(element, params)
        self.status_var.set("Фото оновлено")

    # ---- Кеші обробки фото ------------------------------------------------------------------

    @staticmethod
    def _file_stamp(path):
        try:
            stat = os.stat(path)
            return stat.st_mtime_ns, stat.st_size
        except (OSError, TypeError):
            return None

    def _photo_processor(self, path):
        key = (os.path.abspath(path), self._file_stamp(path))
        processor = self.photo_processors.pop(key, None)
        if processor is None:
            processor = PhotoProcessor(path)
        self.photo_processors[key] = processor
        while len(self.photo_processors) > 3:
            self.photo_processors.pop(next(iter(self.photo_processors)))
        return processor

    def _paint_masks(self, path, size):
        if not path:
            return None, None
        key = (path, self._file_stamp(path), tuple(size))
        masks = self.paint_cache.get(key)
        if masks is None:
            masks = load_paint_masks(path, size)
            self.paint_cache[key] = masks
            while len(self.paint_cache) > 8:
                self.paint_cache.pop(next(iter(self.paint_cache)))
        return masks

    def _photo_output(self, element):
        params = photo_params(element.get("photo"))
        key = json.dumps([
            element["path"], self._file_stamp(element["path"]), params,
            self._file_stamp(params["paint"]) if params["paint"] else None,
        ], sort_keys=True)
        output = self.photo_outputs.get(key)
        if output is None:
            processor = self._photo_processor(element["path"])
            white, black = self._paint_masks(params["paint"], (processor.width, processor.height))
            output = processor.render(params, white, black)
            self.photo_outputs[key] = output
            while len(self.photo_outputs) > 12:
                self.photo_outputs.pop(next(iter(self.photo_outputs)))
        return output

    def _element_pixel_size(self, element):
        """Розмір картинки в пікселях з урахуванням EXIF і обрізки."""
        try:
            if element.get("photo") and photo_available():
                width, height = photo_work_size(element["path"])
                fraction = crop_fraction(element["photo"].get("crop")) if element["photo"].get("crop") else None
                if fraction:
                    x0, y0, x1, y1 = fraction
                    return max(1.0, (x1 - x0) * width), max(1.0, (y1 - y0) * height)
                return float(width), float(height)
            return self._image_pixel_size(element["path"])
        except Exception:
            return max(1.0, float(element.get("width", 1))), max(1.0, float(element.get("height", 1)))

    # ---- Тема оформлення -----------------------------------------------------------------

    def _setup_style(self):
        """Налаштувати стилі ttk під поточну тему (можна викликати повторно)."""
        c = COLORS
        self.configure(bg=c["panel"])
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            try:
                tkfont.nametofont(name).configure(family=UI_FONT, size=10)
            except tk.TclError:
                pass
        style = ttk.Style(self)
        if "clam" in style.theme_names() and style.theme_use() != "clam":
            style.theme_use("clam")
        panel, border, accent, soft = c["panel"], c["border"], c["accent"], c["soft"]
        style.configure(
            ".",
            background=panel,
            foreground=c["text"],
            font=(UI_FONT, 10),
            bordercolor=border,
            lightcolor=panel,
            darkcolor=panel,
            focuscolor=accent,
            troughcolor=soft,
            selectbackground=accent,
            selectforeground=c["accent_text"],
            insertcolor=c["text"],
            arrowcolor=c["muted"],
            fieldbackground=c["field"],
        )
        style.map(".", foreground=[("disabled", c["muted"])])
        style.configure("TFrame", background=panel)
        style.configure("Header.TFrame", background=c["header"])
        style.configure("TLabel", background=panel, foreground=c["text"])
        style.configure("Header.TLabel", background=c["header"], foreground=c["text"])
        style.configure(
            "Brand.TLabel", background=c["header"], foreground=c["text"], font=(UI_FONT, 13, "bold")
        )
        style.configure(
            "BrandSub.TLabel", background=c["header"], foreground=c["muted"], font=(UI_FONT, 8)
        )
        style.configure("Title.TLabel", font=(UI_FONT, 12, "bold"))
        style.configure("Caption.TLabel", foreground=c["muted"], font=(UI_FONT, 7, "bold"))
        style.configure("Hint.TLabel", foreground=c["muted"], font=(UI_FONT, 9))
        style.configure("Ok.TLabel", foreground=c["ok_hint"], font=(UI_FONT, 9))
        style.configure("Status.TFrame", background=c["status"])
        style.configure("Status.TLabel", background=c["status"], foreground=c["muted"], font=(UI_FONT, 9))
        style.configure(
            "TLabelframe", background=panel, bordercolor=border, relief="solid", borderwidth=1,
            lightcolor=border, darkcolor=border,
        )
        style.configure(
            "TLabelframe.Label", background=panel, foreground=accent, font=(UI_FONT, 8, "bold")
        )
        style.configure("TSeparator", background=border)

        def flat_button(name, padding, bg, fg, hover, pressed, font=(UI_FONT, 10), border_color=None):
            border_color = border_color or bg
            style.configure(
                name, padding=padding, width=0, background=bg, foreground=fg, font=font,
                bordercolor=border_color, lightcolor=bg, darkcolor=bg, focusthickness=0,
                relief="flat", anchor="center",
            )
            style.map(
                name,
                background=[("disabled", c["accent_disabled"] if name.startswith("Accent") else bg),
                            ("pressed", pressed), ("active", hover)],
                lightcolor=[("pressed", pressed), ("active", hover)],
                darkcolor=[("pressed", pressed), ("active", hover)],
                bordercolor=[("active", hover if name.startswith("Accent") else accent)],
                foreground=[("disabled", c["accent_text"] if name.startswith("Accent") else c["muted"])],
            )

        flat_button("TButton", (10, 5), soft, c["text"], c["accent_soft"], c["pressed"], border_color=border)
        flat_button("Tool.TButton", (8, 5), soft, c["text"], c["accent_soft"], c["pressed"])
        flat_button(
            "Accent.TButton", (14, 10), accent, c["accent_text"], c["accent_hover"],
            c["accent_pressed"], font=(UI_FONT, 11, "bold"),
        )
        flat_button("Seg.TButton", (10, 4), c["header"], c["muted"], c["accent_soft"], c["pressed"],
                    font=(UI_FONT, 9), border_color=border)
        flat_button("SegOn.TButton", (10, 4), accent, c["accent_text"], c["accent_hover"],
                    c["accent_pressed"], font=(UI_FONT, 9, "bold"))
        flat_button("AccentSmall.TButton", (12, 5), accent, c["accent_text"], c["accent_hover"],
                    c["accent_pressed"], font=(UI_FONT, 10, "bold"))
        flat_button("Mini.TButton", (7, 1), c["status"], c["text"], c["accent_soft"], c["pressed"],
                    font=(UI_FONT, 9))
        style.configure(
            "Status.TCheckbutton", background=c["status"], foreground=c["text"], font=(UI_FONT, 9),
            indicatorbackground=c["field"], indicatorforeground=accent, focusthickness=0,
        )
        style.map(
            "Status.TCheckbutton", background=[("active", c["status"])],
            indicatorbackground=[("selected", c["field"]), ("active", c["accent_soft"])],
        )
        style.configure(
            "Header.TMenubutton", background=c["header"], foreground=c["text"], padding=(9, 5), width=0,
            bordercolor=c["header"], lightcolor=c["header"], darkcolor=c["header"],
            arrowsize=0, relief="flat", font=(UI_FONT, 10),
        )
        style.map(
            "Header.TMenubutton",
            background=[("pressed", c["pressed"]), ("active", c["accent_soft"])],
            foreground=[("active", accent)],
        )
        style.layout("Header.TMenubutton", [
            ("Menubutton.border", {"sticky": "nswe", "children": [
                ("Menubutton.padding", {"sticky": "nswe", "children": [
                    ("Menubutton.label", {"sticky": ""}),
                ]}),
            ]}),
        ])
        for name in ("TEntry", "TCombobox", "TSpinbox"):
            style.configure(
                name, fieldbackground=c["field"], foreground=c["text"], background=soft,
                bordercolor=border, lightcolor=c["field"], darkcolor=c["field"], padding=4,
                arrowsize=13, arrowcolor=c["muted"], insertcolor=c["text"],
            )
            style.map(
                name,
                bordercolor=[("focus", accent)],
                lightcolor=[("focus", c["field"])],
                fieldbackground=[("readonly", c["field"]), ("disabled", soft)],
                foreground=[("disabled", c["muted"]), ("readonly", c["text"])],
                background=[("active", c["accent_soft"])],
                arrowcolor=[("active", accent)],
                selectbackground=[("readonly", c["field"])],
                selectforeground=[("readonly", c["text"])],
            )
        style.configure("TNotebook", background=panel, borderwidth=0, tabmargins=(0, 0, 0, 0))
        style.configure(
            "TNotebook.Tab", padding=(10, 6), background=panel, foreground=c["muted"],
            bordercolor=panel, lightcolor=panel, darkcolor=panel, font=(UI_FONT, 9, "bold"),
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", c["accent_soft"]), ("active", soft)],
            lightcolor=[("selected", c["accent_soft"])],
            bordercolor=[("selected", accent)],
            foreground=[("selected", accent), ("active", c["text"])],
        )
        for name in ("TCheckbutton", "TRadiobutton"):
            style.configure(
                name, background=panel, foreground=c["text"], indicatorbackground=c["field"],
                indicatorforeground=accent, indicatormargin=(0, 0, 6, 0), focusthickness=0,
            )
            style.map(
                name,
                background=[("active", panel)],
                indicatorbackground=[("selected", c["field"]), ("active", c["accent_soft"])],
                indicatorforeground=[("selected", accent)],
            )
        style.configure(
            "Horizontal.TScale", background=accent, troughcolor=soft, bordercolor=border,
            lightcolor=accent, darkcolor=accent,
        )
        for name in ("Horizontal.TScrollbar", "Vertical.TScrollbar"):
            style.configure(
                name, background=soft, troughcolor=c["workspace"], bordercolor=c["workspace"],
                lightcolor=soft, darkcolor=soft, arrowcolor=c["muted"], gripcount=0,
            )
            style.map(name, background=[("active", c["accent_soft"])])
        # Випадні списки комбобоксів.
        for option, value in (
            ("*TCombobox*Listbox.background", c["field"]),
            ("*TCombobox*Listbox.foreground", c["text"]),
            ("*TCombobox*Listbox.selectBackground", accent),
            ("*TCombobox*Listbox.selectForeground", c["accent_text"]),
        ):
            self.option_add(option, value)

    def _themed(self, widget, **mapping):
        """Запам'ятати звичайний tk-віджет, щоб перефарбовувати його при зміні теми."""
        self.theme_widgets.append((widget, mapping))
        self._paint_widget(widget, mapping)
        return widget

    @staticmethod
    def _paint_widget(widget, mapping):
        options = {}
        for option, key in mapping.items():
            options[option] = COLORS[key] if isinstance(key, str) and key in COLORS else key
        widget.configure(**options)

    def _line(self, parent, orient="horizontal", **pack):
        if orient == "horizontal":
            line = tk.Frame(parent, height=1)
        else:
            line = tk.Frame(parent, width=1)
        self._themed(line, bg="border")
        line.pack(**pack)
        return line

    def _logo_photo(self, size):
        return ImageTk.PhotoImage(render_logo(size, COLORS["logo_gear"], COLORS["logo_bolt"]))

    def _refresh_logos(self):
        self.logo_photos = {}
        for label, size in self.logo_labels:
            try:
                photo = self.logo_photos.get(size) or self._logo_photo(size)
                self.logo_photos[size] = photo
                label.configure(image=photo)
            except tk.TclError:
                pass

    def _apply_window_icon(self):
        try:
            self.icon_photos = [
                ImageTk.PhotoImage(render_app_icon(size)) for size in (16, 32, 48, 64)
            ]
            self.iconphoto(True, *self.icon_photos)
        except Exception:
            pass

    def _apply_title_bar_theme(self):
        """Windows 10/11: темний заголовок вікна для темних тем."""
        if os.name != "nt":
            return
        try:
            self.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            dark = ctypes.c_int(1 if COLORS["dark"] else 0)
            for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (нові / старі збірки)
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attribute, ctypes.byref(dark), ctypes.sizeof(dark)
                ) == 0:
                    break
            color = COLORS["header"].lstrip("#")
            colorref = ctypes.c_int(int(color[4:6] + color[2:4] + color[0:2], 16))
            ctypes.windll.dwmapi.DwmSetWindowAttribute(  # DWMWA_CAPTION_COLOR (Windows 11)
                hwnd, 35, ctypes.byref(colorref), ctypes.sizeof(colorref)
            )
        except Exception:
            pass

    def _set_theme(self, name, save=True):
        if name not in THEMES:
            name = DEFAULT_THEME
        self.theme_name = name
        COLORS.clear()
        COLORS.update(THEMES[name])
        self._setup_style()
        alive = []
        for widget, mapping in self.theme_widgets:
            try:
                self._paint_widget(widget, mapping)
                alive.append((widget, mapping))
            except tk.TclError:
                pass
        self.theme_widgets = alive
        for combo in self.comboboxes:
            try:
                popdown = self.tk.call("ttk::combobox::PopdownWindow", combo)
                self.tk.call(
                    f"{popdown}.f.l", "configure", "-background", COLORS["field"],
                    "-foreground", COLORS["text"], "-selectbackground", COLORS["accent"],
                    "-selectforeground", COLORS["accent_text"],
                )
            except tk.TclError:
                pass
        for key, button in self.theme_buttons.items():
            button.configure(style="SegOn.TButton" if key == name else "Seg.TButton")
        if hasattr(self, "theme_var"):
            self.theme_var.set(name)
        self._refresh_icons()
        self._refresh_logos()
        self._apply_title_bar_theme()
        state, text = getattr(self, "connection_state", ("checking", "Перевірка підключення…"))
        self._set_connection_status(state, text)
        self._center_canvas()
        self._draw_tabs()
        self._render_all()
        if save:
            self._save_size_presets()
            self.status_var.set(f"Тема: {THEMES[name]['title']}")

    def _cycle_theme(self):
        names = list(THEMES)
        self._set_theme(names[(names.index(self.theme_name) + 1) % len(names)])

    def _icon_set(self, name, size):
        """(звичайна, неактивна, під курсором) — іконка у кольорах поточної теми."""
        key = (name, size)
        images = self.icon_photos.get(key)
        if images is None:
            normal = render_icon(name, COLORS["text"], size)
            faded = render_icon(name, COLORS["muted"], size)
            faded.putalpha(faded.getchannel("A").point(lambda value: value * 45 // 100))
            hover = render_icon(name, COLORS["accent"], size)
            images = tuple(ImageTk.PhotoImage(image) for image in (normal, faded, hover))
            self.icon_photos[key] = images
        return images

    def _paint_icon_button(self, button, name, size):
        normal, faded, hover = self._icon_set(name, size)
        button.configure(image=(normal, "disabled", faded, "active", hover))

    def _icon_button(self, parent, name, command, tip=None, pack=True, text=None, side="left",
                     style="Tool.TButton", size=18, padx=1):
        button = ttk.Button(parent, command=command, style=style, text=text or "",
                            compound="left" if text else "image")
        self._paint_icon_button(button, name, size)
        self.icon_buttons.append((button, name, size))
        if pack:
            button.pack(side=side, padx=padx)
        if tip:
            ToolTip(button, tip)
        return button

    def _refresh_icons(self):
        self.icon_photos = {}
        alive = []
        for button, name, size in self.icon_buttons:
            try:
                self._paint_icon_button(button, name, size)
                alive.append((button, name, size))
            except tk.TclError:
                pass
        self.icon_buttons = alive
        button = getattr(self, "theme_menu_button", None)
        if button is not None:
            button.configure(image=self._theme_swatch(self.theme_name, (34, 20)))

    def _poll_history_buttons(self):
        """Стрілки «назад/вперед» неактивні, коли немає що скасувати чи повторити."""
        try:
            state = (bool(self.undo_stack), bool(self.redo_stack))
            if state != self.history_buttons_state:
                self.history_buttons_state = state
                for button, enabled in ((self.undo_button, state[0]), (self.redo_button, state[1])):
                    button.state(["!disabled"] if enabled else ["disabled"])
            self.after(250, self._poll_history_buttons)
        except tk.TclError:
            pass

    @staticmethod
    def _tool_button(parent, text, command, tip=None, style="Tool.TButton", side="left", padx=1):
        button = ttk.Button(parent, text=text, command=command, style=style)
        button.pack(side=side, padx=padx)
        if tip:
            ToolTip(button, tip)
        return button

    @staticmethod
    def _separator(parent, side="left"):
        ttk.Separator(parent, orient="vertical").pack(side=side, fill="y", padx=7, pady=3)

    def _new_menu(self, parent):
        menu = tk.Menu(parent, tearoff=False, relief="flat", borderwidth=1)
        self._themed(
            menu, bg="panel", fg="text", activebackground="accent", activeforeground="accent_text",
            selectcolor="accent",
        )
        return menu

    def _build_menu(self, button):
        """Головне меню ☰ — усе, що не винесено на панель."""
        root = self._new_menu(button)

        def cascade(title):
            menu = self._new_menu(root)
            root.add_cascade(label=title, menu=menu)
            return menu

        menu = cascade("Файл")
        menu.add_command(label="Нова вкладка", accelerator="Ctrl+T", command=self._new_tab)
        menu.add_command(label="Відкрити…", accelerator="Ctrl+O", command=self._load_layout)
        self.recent_menu = self._new_menu(menu)
        self.recent_menu.configure(postcommand=self._fill_recent_menu)
        menu.add_cascade(label="Останні файли", menu=self.recent_menu)
        self._fill_recent_menu()
        menu.add_command(label="Зберегти", accelerator="Ctrl+S", command=self._save_layout)
        menu.add_command(label="Зберегти як…", accelerator="Ctrl+Shift+S", command=self._save_layout_as)
        menu.add_separator()
        menu.add_command(label="Закрити вкладку", accelerator="Ctrl+W", command=self._close_tab)
        menu.add_command(label="Вихід", command=self._on_close)

        menu = cascade("Правка")
        menu.add_command(label="Скасувати", accelerator="Ctrl+Z", command=self._undo)
        menu.add_command(label="Повторити", accelerator="Ctrl+Y", command=self._redo)
        menu.add_separator()
        menu.add_command(label="Копіювати елемент", accelerator="Ctrl+C", command=self._copy_selected)
        menu.add_command(label="Вставити (текст, картинку, елемент)", accelerator="Ctrl+V",
                         command=self._paste_element)
        menu.add_command(label="Дублювати", accelerator="Ctrl+D", command=self._duplicate_selected)
        menu.add_command(label="Видалити", accelerator="Del", command=self._delete_selected)
        menu.add_separator()
        menu.add_command(label="Виділити все", accelerator="Ctrl+A", command=self._select_all)
        menu.add_command(label="Зняти виділення", accelerator="Esc", command=self._deselect)
        align_menu = self._new_menu(menu)
        menu.add_cascade(label="Вирівняти вибрані", menu=align_menu)
        self._fill_align_menu(align_menu)
        menu.add_separator()
        menu.add_command(label="Заблокувати / розблокувати елемент", command=self._toggle_selected_lock)
        menu.add_command(label="Заблокувати / розблокувати макет", command=self._toggle_layout_lock)

        menu = cascade("Вставка")
        menu.add_command(label="Текст", command=self._add_text)
        menu.add_command(label="Фото з файлу…", command=self._add_image)
        menu.add_command(label="QR-код…", command=self._add_qr)
        menu.add_command(label="Штрихкод Code 128…", command=self._add_barcode)
        menu.add_separator()
        menu.add_command(label="З буфера обміну", accelerator="Ctrl+V", command=self._paste_element)

        menu = cascade("Фото")
        menu.add_command(label="Редактор фото (очистка, пензель)…", command=self._open_photo_editor)
        menu.add_separator()
        menu.add_command(label="Повернути ⟳ 90°", accelerator="Ctrl+R", command=lambda: self._rotate_selected(90))
        menu.add_command(label="Повернути ⟲ 90°", accelerator="Ctrl+Shift+R",
                         command=lambda: self._rotate_selected(-90))
        menu.add_command(label="Повернути на 180°", command=lambda: self._rotate_selected(180))
        menu.add_command(label="Віддзеркалити по ширині ⇆", command=lambda: self._flip_selected("h"))
        menu.add_command(label="Віддзеркалити по висоті ⇅", command=lambda: self._flip_selected("v"))
        menu.add_command(label="Скинути поворот", command=self._reset_image_transform)

        menu = cascade("Вигляд")
        menu.add_checkbutton(label="Сітка", variable=self.show_grid_var, command=self._render_all)
        menu.add_checkbutton(label="Прив’язка", variable=self.snap_var)
        menu.add_checkbutton(label="Безпечні поля", variable=self.safe_margin_var, command=self._render_all)
        menu.add_separator()
        menu.add_command(label="Вписати наліпку у вікно", command=self._fit_zoom)
        for level in ZOOM_LEVELS:
            menu.add_radiobutton(label=f"Масштаб {level}", value=level, variable=self.zoom_var,
                                 command=self._set_zoom)
        menu.add_separator()
        menu.add_cascade(label="Тема оформлення", menu=self._build_theme_menu(menu))

        menu = cascade("Наліпка")
        menu.add_command(label="Пресети розміру…", command=self._open_size_presets_dialog)

        menu = cascade("Друк")
        menu.add_command(label="Друкувати поточну вкладку", accelerator="Ctrl+P", command=self._print_layout)
        menu.add_command(label="Друк кількох вкладок…", command=self._print_tabs_dialog)
        menu.add_command(label="Серійний друк із CSV…", command=self._batch_print_csv)

        menu = cascade("Довідка")
        menu.add_command(label="Гарячі клавіші та підказки", accelerator="F1", command=self._show_shortcuts)
        return root

    THEME_ORDER = ("light", "bubblegum", "mint", None, "purple", "black", "cyber", "synthwave", "acid")

    def _theme_swatch(self, key, size=(46, 26)):
        cache = self.__dict__.setdefault("theme_swatches", {})
        photo = cache.get((key, size))
        if photo is None:
            photo = ImageTk.PhotoImage(render_theme_swatch(THEMES[key], *size))
            cache[(key, size)] = photo
        return photo

    def _build_theme_menu(self, parent):
        """Список тем із мініатюрами — видно, як виглядатиме, ще до вибору."""
        menu = self._new_menu(parent)
        order = list(self.THEME_ORDER) + [key for key in THEMES if key not in self.THEME_ORDER]
        for key in order:
            if key is None:
                menu.add_separator()
                continue
            menu.add_radiobutton(
                label=f"  {THEMES[key]['title']}", image=self._theme_swatch(key), compound="left",
                value=key, variable=self.theme_var, command=lambda k=key: self._set_theme(k),
            )
        return menu

    def _fill_align_menu(self, menu):
        menu.delete(0, "end")
        for label, mode in (("По лівому краю", "left"), ("По центру ↔", "hcenter"), ("По правому краю", "right"),
                            ("По верхньому краю", "top"), ("По середині ↕", "vcenter"),
                            ("По нижньому краю", "bottom")):
            menu.add_command(label=label, command=lambda m=mode: self._align_selection(m))
        menu.add_separator()
        menu.add_command(label="Рівні проміжки ↔", command=lambda: self._distribute_selection("h"))
        menu.add_command(label="Рівні проміжки ↕", command=lambda: self._distribute_selection("v"))

    # ---- Останні файли -----------------------------------------------------------------
    def _remember_recent(self, path):
        try:
            path = os.path.abspath(str(path))
        except (TypeError, ValueError):
            return
        key = os.path.normcase(path)
        self.recent_files = [path] + [
            item for item in self.recent_files if os.path.normcase(os.path.abspath(item)) != key
        ]
        del self.recent_files[MAX_RECENT_FILES:]
        self._save_size_presets()
        self._fill_recent_menu()

    def _fill_recent_menu(self):
        menu = getattr(self, "recent_menu", None)
        if menu is None:
            return
        try:
            menu.delete(0, "end")
            existing = [path for path in self.recent_files if os.path.isfile(path)]
            if not existing:
                menu.add_command(label="Поки порожньо", state="disabled")
                return
            for path in existing:
                folder = Path(path).parent.name
                menu.add_command(
                    label=f"{Path(path).name}" + (f"   ·   {folder}" if folder else ""),
                    command=lambda p=path: self._open_layout_file(p),
                )
            menu.add_separator()
            menu.add_command(label="Очистити список", command=self._clear_recent)
        except tk.TclError:
            pass

    def _clear_recent(self):
        self.recent_files = []
        self._save_size_presets()
        self._fill_recent_menu()

    # ---- Довідка ------------------------------------------------------------------------
    SHORTCUTS = (
        ("Ctrl+V", "вставити текст, скриншот чи фото з буфера"),
        ("Ctrl+C · Ctrl+D", "копіювати · дублювати вибране"),
        ("Delete", "видалити вибране"),
        ("Ctrl+Z · Ctrl+Y", "скасувати · повторити"),
        ("Shift/Ctrl + клік", "додати або прибрати елемент з виділення"),
        ("Рамка мишею", "виділити кілька елементів (з порожнього місця)"),
        ("Ctrl+A · Esc", "виділити все · зняти виділення"),
        ("Стрілки", "зсув на 0,1 мм (Shift — 0,5 мм)"),
        ("Коліщатко над фото", "повернути вибране фото на 1° (Shift — 15°)"),
        ("Ctrl+R · Ctrl+Shift+R", "повернути на 90° за / проти годинникової"),
        ("Ctrl + коліщатко", "масштаб перегляду"),
        ("Подвійний клік", "текст — редагувати, фото — редактор фото"),
        ("Правий клік", "меню дій для вибраного"),
        ("Ctrl+T · Ctrl+W · Ctrl+Tab", "нова · закрити · наступна вкладка"),
        ("Ctrl+O · Ctrl+S · Ctrl+P", "відкрити · зберегти · друк"),
    )

    def _show_shortcuts(self, _event=None):
        existing = self.__dict__.get("shortcuts_window")
        if existing is not None:
            try:
                existing.lift()
                existing.focus_force()
                return "break"
            except tk.TclError:
                pass
        window = tk.Toplevel(self)
        self.shortcuts_window = window
        window.title("Гарячі клавіші")
        window.transient(self)
        window.resizable(False, False)
        window.configure(bg=COLORS["panel"])
        body = ttk.Frame(window, padding=(18, 14, 18, 14))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Гарячі клавіші та підказки", style="Title.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 10)
        )
        for row, (keys, text) in enumerate(self.SHORTCUTS, start=1):
            key_label = tk.Label(body, text=keys, font=(UI_FONT, 9, "bold"), padx=7, pady=2,
                                 bg=COLORS["accent_soft"], fg=COLORS["accent"])
            key_label.grid(row=row, column=0, sticky="w", pady=2, padx=(0, 12))
            ttk.Label(body, text=text).grid(row=row, column=1, sticky="w", pady=2)
        ttk.Button(body, text="Зрозуміло", style="AccentSmall.TButton", command=window.destroy).grid(
            row=len(self.SHORTCUTS) + 1, column=0, columnspan=2, sticky="e", pady=(12, 0)
        )

        def closed(_event=None):
            if self.__dict__.get("shortcuts_window") is window:
                self.shortcuts_window = None

        window.bind("<Destroy>", closed, add="+")
        window.bind("<Escape>", lambda _e: window.destroy())
        window.bind("<F1>", lambda _e: window.destroy())
        self.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - window.winfo_reqwidth()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - window.winfo_reqheight()) // 3
        window.geometry(f"+{max(0, x)}+{max(0, y)}")
        window.focus_force()
        return "break"

    # ---- Контекстне меню полотна --------------------------------------------------------
    def _canvas_context_menu(self, event):
        self.canvas.focus_set()
        if self.inline_editor:
            self._finish_inline_edit(commit=True)
        self.marquee = None
        found = None
        for item in reversed(self.canvas.find_overlapping(event.x, event.y, event.x, event.y)):
            tags = self.canvas.gettags(item)
            if "element" in tags:
                found = next((tag for tag in tags if tag not in ("element", "current")), None)
                break
        if found and found not in self.selection_ids:
            self.selected_id = found
        elif not found and not self.selection_ids:
            self.selected_id = None
        self._draw_selection()
        self._load_properties()
        menu = self.context_menu
        menu.delete(0, "end")
        chosen = self._selected_elements()
        if chosen:
            images = [element for element in chosen if element.get("type") == "image"]
            menu.add_command(label="Копіювати", accelerator="Ctrl+C", command=self._copy_selected)
            menu.add_command(label="Дублювати", accelerator="Ctrl+D", command=self._duplicate_selected)
            menu.add_command(label="Видалити", accelerator="Del", command=self._delete_selected)
            menu.add_separator()
            if len(chosen) > 1:
                menu.add_cascade(label="Вирівняти", menu=self.context_align_menu)
            else:
                element = chosen[0]
                menu.add_command(label="Центрувати на наліпці",
                                 command=lambda: self._center_selected(horizontal=True, vertical=True))
                if element.get("type") == "text":
                    menu.add_command(label="Редагувати текст",
                                     command=lambda e=element, ev=event: self._start_inline_edit(e, ev))
            menu.add_command(label="Нагору (над іншими)", command=self._bring_front)
            locked = all(element.get("locked") for element in chosen)
            menu.add_command(label="Розблокувати" if locked else "Заблокувати", command=self._toggle_selected_lock)
            if images:
                menu.add_separator()
                menu.add_command(label="Повернути ⟳ 90°", accelerator="Ctrl+R",
                                 command=lambda: self._rotate_selected(90))
                menu.add_command(label="Повернути ⟲ 90°", accelerator="Ctrl+Shift+R",
                                 command=lambda: self._rotate_selected(-90))
                menu.add_command(label="Віддзеркалити ⇆", command=lambda: self._flip_selected("h"))
                if len(chosen) == 1:
                    menu.add_command(label="Редактор фото…", command=self._open_photo_editor)
        else:
            menu.add_command(label="Вставити", accelerator="Ctrl+V", command=self._paste_element)
            menu.add_command(label="Виділити все", accelerator="Ctrl+A", command=self._select_all)
            menu.add_separator()
            menu.add_command(label="+ Текст", command=self._add_text)
            menu.add_command(label="+ Фото…", command=self._add_image)
            menu.add_command(label="+ QR-код…", command=self._add_qr)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def _build_header(self):
        """Шапка як у браузері: логотип, вкладки, тема, меню."""
        header = ttk.Frame(self, style="Header.TFrame", padding=(8, 4, 8, 0))
        header.pack(side="top", fill="x")
        logo = ttk.Label(header, style="Header.TLabel")
        logo.pack(side="left", padx=(2, 8), pady=(0, 4))
        self.logo_labels.append((logo, 26))
        ToolTip(logo, APP_NAME)
        menu_button = ttk.Menubutton(header, text="☰", style="Header.TMenubutton", width=0)
        menu_button.pack(side="right", padx=(6, 0), pady=(0, 4))
        menu_button.configure(menu=self._build_menu(menu_button))
        ToolTip(menu_button, "Меню: файл, правка, фото, вигляд, друк")
        self.theme_menu_button = ttk.Menubutton(
            header, style="Header.TMenubutton", width=0, image=self._theme_swatch(self.theme_name, (34, 20)),
            text="Тема", compound="left",
        )
        self.theme_menu_button.pack(side="right", pady=(0, 4))
        self.theme_menu_button.configure(menu=self._build_theme_menu(self.theme_menu_button))
        ToolTip(self.theme_menu_button, "Тема оформлення: світлі, темні, неонові")
        self._build_tabbar(header)

    def _build_ui(self):
        self.theme_widgets = []
        self.comboboxes = []
        self.theme_buttons = {}
        self.logo_labels = []
        self.logo_photos = {}
        self.connection_state = ("checking", "Перевірка підключення…")
        self.theme_var = tk.StringVar(value=self.theme_name)
        self._setup_style()
        self.show_grid_var = tk.BooleanVar(value=True)
        self.snap_var = tk.BooleanVar(value=True)
        self.safe_margin_var = tk.BooleanVar(value=True)
        self.zoom_var = tk.StringVar(value="100%")
        self.size_preset_var = tk.StringVar()
        self.size_status_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Готово")
        self.copies_var = tk.IntVar(value=1)
        self._build_header()

        # ---- Панель інструментів (один рядок) --------------------------------------------
        toolbar = ttk.Frame(self, padding=(8, 6, 8, 6))
        toolbar.pack(side="top", fill="x")
        self._tool_button(toolbar, "Друкувати", self._print_layout, "Друкувати поточну вкладку (Ctrl+P)",
                          style="AccentSmall.TButton", side="right", padx=(4, 0))
        copies = ttk.Spinbox(toolbar, from_=1, to=99, textvariable=self.copies_var, width=3, justify="center")
        copies.pack(side="right", padx=(2, 0))
        ToolTip(copies, "Кількість копій")
        ttk.Label(toolbar, text="×", style="Hint.TLabel").pack(side="right")
        self._separator(toolbar, side="right")
        self._tool_button(toolbar, "⚙", self._open_size_presets_dialog, "Власні розміри наліпок",
                          side="right")
        self.size_combo = ttk.Combobox(toolbar, textvariable=self.size_preset_var, state="readonly", width=16)
        self.size_combo.pack(side="right", padx=(0, 2))
        self.size_combo.bind("<<ComboboxSelected>>", self._size_preset_selected)
        self.comboboxes.append(self.size_combo)
        ToolTip(self.size_combo, "Розмір наліпки")
        self._tool_button(toolbar, "Відкрити", self._load_layout, "Відкрити макет у новій вкладці (Ctrl+O)")
        self._tool_button(toolbar, "Зберегти", self._save_layout, "Зберегти вкладку (Ctrl+S)")
        self._separator(toolbar)
        self._tool_button(toolbar, "+ Текст", self._add_text, "Додати текст")
        self._tool_button(toolbar, "+ Фото", self._add_image, "Додати фото чи картинку з файлу")
        self._tool_button(toolbar, "+ QR", self._add_qr, "Додати QR-код")
        self._tool_button(toolbar, "+ Штрихкод", self._add_barcode, "Додати штрихкод Code 128")
        self._tool_button(
            toolbar, "Вставити", self._paste_element,
            "Ctrl+V — вставити текст або картинку з буфера обміну\n(скриншот, фото, текст, файл зображення)",
        )
        self._separator(toolbar)
        self.undo_button = self._icon_button(toolbar, "undo", self._undo, "Скасувати (Ctrl+Z)", size=20)
        self.redo_button = self._icon_button(toolbar, "redo", self._redo, "Повторити (Ctrl+Y)", size=20)
        self._separator(toolbar)
        self._tool_button(toolbar, "Дубль", self._duplicate_selected, "Дублювати елемент (Ctrl+D)")
        self._tool_button(toolbar, "Видалити", self._delete_selected, "Видалити елемент (Delete)")
        self._line(self, fill="x")

        # ---- Нижній рядок: масштаб, перемикачі, стан ---------------------------------------
        bottom = ttk.Frame(self, style="Status.TFrame", padding=(8, 3))
        bottom.pack(side="bottom", fill="x")
        self._tool_button(bottom, "−", lambda: self._zoom_step(-1), "Зменшити (Ctrl + коліщатко)",
                          style="Mini.TButton")
        ttk.Label(bottom, textvariable=self.zoom_var, style="Status.TLabel", width=5, anchor="center").pack(
            side="left"
        )
        self._tool_button(bottom, "+", lambda: self._zoom_step(1), "Збільшити (Ctrl + коліщатко)",
                          style="Mini.TButton")
        self._tool_button(bottom, "Вписати", self._fit_zoom, "Показати наліпку повністю", style="Mini.TButton")
        self._separator(bottom)
        for text, variable, command in (
            ("Сітка", self.show_grid_var, self._render_all),
            ("Прив’язка", self.snap_var, None),
            ("Поля", self.safe_margin_var, self._render_all),
        ):
            ttk.Checkbutton(bottom, text=text, variable=variable, command=command,
                            style="Status.TCheckbutton").pack(side="left", padx=3)
        self._tool_button(bottom, "🔒 Макет", self._toggle_layout_lock,
                          "Заблокувати / розблокувати всі елементи", style="Mini.TButton", padx=(6, 0))
        self.status_conn_label = tk.Label(bottom, text="● Перевірка принтера…", font=(UI_FONT, 9, "bold"))
        self._themed(self.status_conn_label, bg="status", fg="checking")
        self.status_conn_label.pack(side="right", padx=(12, 0))
        ttk.Label(bottom, textvariable=self.size_status_var, style="Status.TLabel").pack(side="right", padx=(12, 0))
        ttk.Label(bottom, textvariable=self.status_var, style="Status.TLabel").pack(
            side="left", fill="x", expand=True, padx=(14, 0)
        )

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)

        # ---- Права панель ------------------------------------------------------------------
        side = ttk.Frame(body, width=360, padding=(10, 8, 10, 8))
        side.pack(side="right", fill="y")
        side.pack_propagate(False)
        self._line(body, orient="vertical", side="right", fill="y")

        # ---- Робоча область ----------------------------------------------------------------
        canvas_holder = self._themed(tk.Frame(body), bg="workspace")
        canvas_holder.pack(side="left", fill="both", expand=True)
        canvas_holder.rowconfigure(0, weight=1)
        canvas_holder.columnconfigure(0, weight=1)
        self.canvas_view = self._themed(tk.Canvas(canvas_holder, highlightthickness=0), bg="workspace")
        canvas_x_scroll = ttk.Scrollbar(canvas_holder, orient="horizontal", command=self.canvas_view.xview)
        canvas_y_scroll = ttk.Scrollbar(canvas_holder, orient="vertical", command=self.canvas_view.yview)
        self.canvas_view.configure(xscrollcommand=canvas_x_scroll.set, yscrollcommand=canvas_y_scroll.set)
        self.canvas_view.grid(row=0, column=0, sticky="nsew")
        canvas_y_scroll.grid(row=0, column=1, sticky="ns")
        canvas_x_scroll.grid(row=1, column=0, sticky="ew")
        self.canvas = tk.Canvas(
            self.canvas_view,
            width=round(LABEL_WIDTH_MM * PX_PER_MM),
            height=round(LABEL_HEIGHT_MM * PX_PER_MM),
            bg="white",
            highlightthickness=1,
        )
        self._themed(self.canvas, highlightbackground="border")
        self.canvas_window = self.canvas_view.create_window(32, 32, window=self.canvas, anchor="nw")
        self.canvas_view.bind("<Configure>", self._center_canvas)
        self.canvas_view.bind("<Button-1>", lambda _event: self.canvas.focus_set())
        self.canvas.bind("<Button-1>", self._canvas_click)
        self.canvas.bind("<Double-Button-1>", self._canvas_double_click)
        self.canvas.bind("<B1-Motion>", self._canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self._canvas_release)
        self.canvas.bind("<Motion>", self._canvas_motion)
        self.canvas.bind("<Button-3>", self._canvas_context_menu)
        self.context_menu = self._new_menu(self.canvas)
        self.context_align_menu = self._new_menu(self.context_menu)
        self._fill_align_menu(self.context_align_menu)
        for widget in (self.canvas_view, self.canvas):
            widget.bind("<MouseWheel>", self._mouse_wheel)
            widget.bind("<Control-MouseWheel>", self._mouse_wheel_zoom)
            widget.bind("<Shift-MouseWheel>", self._mouse_wheel_horizontal)
            widget.bind("<Button-4>", self._mouse_wheel)
            widget.bind("<Button-5>", self._mouse_wheel)

        notebook = ttk.Notebook(side)
        notebook.pack(fill="both", expand=True)
        self.notebook = notebook
        props_tab = ttk.Frame(notebook, padding=(2, 10, 2, 4))
        self.photo_tab = ttk.Frame(notebook, padding=(2, 10, 2, 4))
        layers_tab = ttk.Frame(notebook, padding=(2, 10, 2, 4))
        print_tab = ttk.Frame(notebook, padding=(2, 10, 2, 4))
        notebook.add(props_tab, text="Властивості")
        notebook.add(self.photo_tab, text="Фото")
        notebook.add(layers_tab, text="Шари")
        notebook.add(print_tab, text="Друк")
        self._build_photo_tab(self.photo_tab)
        notebook.bind("<<NotebookTabChanged>>", lambda _e: self._load_photo_controls())

        # ---- Вкладка «Властивості» (компактно) ---------------------------------------------
        props_tab.columnconfigure(0, weight=1)
        self.type_var = tk.StringVar(value="Нічого не вибрано")
        ttk.Label(props_tab, textvariable=self.type_var, style="Title.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 6)
        )
        self.empty_hint = ttk.Frame(props_tab)
        self.empty_hint.grid(row=1, column=0, sticky="nsew", pady=(18, 0))
        empty_logo = ttk.Label(self.empty_hint)
        empty_logo.pack(pady=(0, 12))
        self.logo_labels.append((empty_logo, 80))
        ttk.Label(self.empty_hint, text="Почніть створювати наліпку", style="Title.TLabel").pack()
        ttk.Label(
            self.empty_hint,
            text=("Кнопки «+ Текст», «+ Фото», «+ QR» — вгорі.\n\n"
                  "Або скопіюйте текст чи картинку\nв будь-якій програмі й натисніть Ctrl+V.\n\n"
                  "Кілька наліпок — кілька вкладок (Ctrl+T).\n"
                  "Кілька елементів — рамкою мишею або Shift+клік.\n\n"
                  "F1 — усі гарячі клавіші."),
            style="Hint.TLabel",
            justify="center",
        ).pack(pady=(6, 0))
        self.element_panel = ttk.Frame(props_tab)
        self.element_panel.grid(row=2, column=0, sticky="nsew")
        self.element_panel.columnconfigure(0, weight=1)

        def entry_row(parent, row, first, first_var, second, second_var):
            ttk.Label(parent, text=first).grid(row=row, column=0, sticky="w", padx=(0, 6), pady=2)
            ttk.Entry(parent, textvariable=first_var, width=8).grid(row=row, column=1, sticky="ew", pady=2)
            ttk.Label(parent, text=second).grid(row=row, column=2, sticky="w", padx=(12, 6), pady=2)
            ttk.Entry(parent, textvariable=second_var, width=8).grid(row=row, column=3, sticky="ew", pady=2)

        geometry = ttk.LabelFrame(self.element_panel, text="Розташування, мм", padding=8)
        geometry.grid(row=0, column=0, sticky="ew")
        geometry.columnconfigure(1, weight=1)
        geometry.columnconfigure(3, weight=1)
        self.x_var = tk.StringVar()
        self.y_var = tk.StringVar()
        self.width_var = tk.StringVar()
        self.height_var = tk.StringVar()
        entry_row(geometry, 0, "X", self.x_var, "Y", self.y_var)
        self.size_row = ttk.Frame(geometry)
        self.size_row.grid(row=1, column=0, columnspan=4, sticky="ew")
        self.size_row.columnconfigure(1, weight=1)
        self.size_row.columnconfigure(3, weight=1)
        entry_row(self.size_row, 0, "Ш", self.width_var, "В", self.height_var)

        self.text_frame = ttk.LabelFrame(self.element_panel, text="Текст", padding=8)
        self.text_frame.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        self.text_frame.columnconfigure(0, weight=1)
        self.text_var = tk.StringVar()
        ttk.Entry(self.text_frame, textvariable=self.text_var).grid(
            row=0, column=0, columnspan=3, sticky="ew", pady=(0, 4)
        )
        self.font_var = tk.StringVar(value="Tahoma")
        font_combo = ttk.Combobox(
            self.text_frame, textvariable=self.font_var, width=14,
            values=("Tahoma", "Arial", "Segoe UI", "Calibri", "Times New Roman"),
        )
        font_combo.grid(row=1, column=0, sticky="ew")
        self.comboboxes.append(font_combo)
        ToolTip(font_combo, "Шрифт")
        self.size_var = tk.StringVar(value="12")
        size_spin = ttk.Spinbox(self.text_frame, from_=4, to=200, increment=1, textvariable=self.size_var, width=5)
        size_spin.grid(row=1, column=1, sticky="ew", padx=4)
        ToolTip(size_spin, "Кегль, pt")
        self.bold_var = tk.BooleanVar()
        ttk.Checkbutton(self.text_frame, text="Жирний", variable=self.bold_var).grid(row=1, column=2, sticky="w")

        self.image_frame = ttk.LabelFrame(self.element_panel, text="Фото", padding=8)
        self.image_frame.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        self.image_frame.columnconfigure(0, weight=1)
        self.image_frame.columnconfigure(1, weight=1)
        self.image_path_var = tk.StringVar()
        ttk.Label(self.image_frame, textvariable=self.image_path_var, style="Hint.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        editor_button = ttk.Button(self.image_frame, text="✏ Редактор фото", style="AccentSmall.TButton",
                                   command=self._open_photo_editor)
        editor_button.grid(row=1, column=0, sticky="ew", pady=(6, 0), padx=(0, 2))
        ToolTip(editor_button, "Обрізка, чарівна гумка, пензель, Ч/Б для друку (подвійний клік по фото)")
        ttk.Button(self.image_frame, text="Замінити…", style="Tool.TButton", command=self._replace_image).grid(
            row=1, column=1, sticky="ew", pady=(6, 0), padx=(2, 0)
        )
        turn_row = ttk.Frame(self.image_frame)
        turn_row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        for column, (text, command, tip) in enumerate((
            ("⟲", lambda: self._rotate_selected(-90), "Повернути проти годинникової (Ctrl+Shift+R)"),
            ("⟳", lambda: self._rotate_selected(90), "Повернути за годинниковою (Ctrl+R)"),
            ("180°", lambda: self._rotate_selected(180), "Перевернути догори дном"),
            ("⇆", lambda: self._flip_selected("h"), "Віддзеркалити по ширині"),
            ("⇅", lambda: self._flip_selected("v"), "Віддзеркалити по висоті"),
        )):
            button = ttk.Button(turn_row, text=text, command=command, style="Tool.TButton")
            button.grid(row=0, column=column, sticky="ew", padx=1)
            ToolTip(button, tip)
            turn_row.columnconfigure(column, weight=1)
        angle_row = ttk.Frame(self.image_frame)
        angle_row.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        angle_row.columnconfigure(1, weight=1)
        ttk.Label(angle_row, text="Кут, °").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.rotation_var = tk.StringVar(value="0")
        ttk.Spinbox(angle_row, from_=0, to=359, increment=1, wrap=True, textvariable=self.rotation_var,
                    width=6).grid(row=0, column=1, sticky="ew")
        ttk.Button(angle_row, text="Скинути", command=self._reset_image_transform, style="Tool.TButton").grid(
            row=0, column=2, padx=(6, 0)
        )
        ttk.Label(self.image_frame, text="Будь-який кут — маркер ↻ над фото або коліщатко миші над ним "
                                         "(1°, Shift — 15°)",
                  style="Hint.TLabel", wraplength=310).grid(row=4, column=0, columnspan=2, sticky="w", pady=(4, 0))

        actions = ttk.Frame(self.element_panel)
        actions.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        for index, (text, command, tip) in enumerate((
            ("Центр ↔", lambda: self._center_selected(horizontal=True), "Центрувати по горизонталі"),
            ("Центр ↕", lambda: self._center_selected(vertical=True), "Центрувати по вертикалі"),
            ("Нагору", self._bring_front, "Перемістити над іншими елементами"),
            ("Замок", self._toggle_selected_lock, "Заблокувати / розблокувати від зсуву"),
        )):
            button = ttk.Button(actions, text=text, command=command, style="Tool.TButton")
            button.grid(row=0, column=index, sticky="ew", padx=1)
            ToolTip(button, tip)
            actions.columnconfigure(index, weight=1)
        ttk.Label(self.element_panel, text="✓ Зміни застосовуються автоматично", style="Ok.TLabel").grid(
            row=4, column=0, sticky="w", pady=(8, 0)
        )

        # Кілька вибраних елементів: вирівнювання, розподіл, спільні дії.
        self.group_panel = ttk.Frame(props_tab)
        self.group_panel.grid(row=3, column=0, sticky="nsew")
        self.group_panel.columnconfigure(0, weight=1)
        align = ttk.LabelFrame(self.group_panel, text="Вирівняти між собою", padding=8)
        align.grid(row=0, column=0, sticky="ew")
        for column, (icon, mode, tip) in enumerate((
            ("align_left", "left", "По лівому краю"),
            ("align_hcenter", "hcenter", "По центру (вертикальна вісь)"),
            ("align_right", "right", "По правому краю"),
            ("align_top", "top", "По верхньому краю"),
            ("align_vcenter", "vcenter", "По середині (горизонтальна вісь)"),
            ("align_bottom", "bottom", "По нижньому краю"),
        )):
            button = self._icon_button(align, icon, lambda m=mode: self._align_selection(m), tip, pack=False)
            button.grid(row=0, column=column, sticky="ew", padx=1)
            align.columnconfigure(column, weight=1)
        spread = ttk.Frame(align)
        spread.grid(row=1, column=0, columnspan=6, sticky="ew", pady=(6, 0))
        spread.columnconfigure(0, weight=1)
        spread.columnconfigure(1, weight=1)
        for column, (icon, axis, text) in enumerate((
            ("dist_h", "h", "Проміжки ↔"),
            ("dist_v", "v", "Проміжки ↕"),
        )):
            button = self._icon_button(spread, icon, lambda a=axis: self._distribute_selection(a),
                                       "Рівні проміжки між 3+ елементами", pack=False, text=text)
            button.grid(row=0, column=column, sticky="ew", padx=1)
        on_label = ttk.LabelFrame(self.group_panel, text="Групу на наліпці", padding=8)
        on_label.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        for column, (text, command, tip) in enumerate((
            ("Центр ↔", lambda: self._align_selection("hcenter", to_label=True), "Групу по центру наліпки"),
            ("Центр ↕", lambda: self._align_selection("vcenter", to_label=True), "Групу по середині наліпки"),
            ("Нагору", self._bring_front, "Над іншими елементами"),
            ("Замок", self._toggle_selected_lock, "Заблокувати / розблокувати всі вибрані"),
        )):
            button = ttk.Button(on_label, text=text, command=command, style="Tool.TButton")
            button.grid(row=0, column=column, sticky="ew", padx=1)
            ToolTip(button, tip)
            on_label.columnconfigure(column, weight=1)
        group_actions = ttk.Frame(self.group_panel)
        group_actions.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        group_actions.columnconfigure(0, weight=1)
        group_actions.columnconfigure(1, weight=1)
        ttk.Button(group_actions, text="Дублювати", style="Tool.TButton", command=self._duplicate_selected).grid(
            row=0, column=0, sticky="ew", padx=1
        )
        ttk.Button(group_actions, text="Видалити", style="Tool.TButton", command=self._delete_selected).grid(
            row=0, column=1, sticky="ew", padx=1
        )
        ttk.Label(
            self.group_panel,
            text=("Тягніть будь-який із вибраних — рухається вся група.\n"
                  "Квадратні маркери — пропорційний розмір групи.\n"
                  "Shift/Ctrl+клік — додати чи прибрати елемент, Esc — зняти виділення."),
            style="Hint.TLabel", justify="left", wraplength=310,
        ).grid(row=3, column=0, sticky="w", pady=(10, 0))

        # ---- Вкладка «Шари» ----------------------------------------------------------------
        ttk.Label(layers_tab, text="Верхній рядок — верхній шар · Shift/Ctrl — кілька", style="Hint.TLabel").pack(
            anchor="w", pady=(0, 6)
        )
        self.layers_list = tk.Listbox(
            layers_tab, height=8, exportselection=False, relief="flat", borderwidth=0, selectmode=tk.EXTENDED,
            highlightthickness=1, activestyle="none", font=(UI_FONT, 10),
        )
        self._themed(
            self.layers_list, bg="field", fg="text", highlightbackground="border",
            highlightcolor="accent", selectbackground="accent", selectforeground="accent_text",
        )
        self.layers_list.pack(fill="both", expand=True)
        self.layers_list.bind("<<ListboxSelect>>", self._layer_selected)
        layer_buttons = ttk.Frame(layers_tab)
        layer_buttons.pack(fill="x", pady=(6, 0))
        for text, command in (("▲ Вище", lambda: self._move_layer(1)), ("▼ Нижче", lambda: self._move_layer(-1)),
                              ("Сховати / показати", self._toggle_visibility)):
            ttk.Button(layer_buttons, text=text, command=command, style="Tool.TButton").pack(
                side="left", fill="x", expand=True, padx=1
            )

        # ---- Вкладка «Друк» ----------------------------------------------------------------
        printing = ttk.LabelFrame(print_tab, text="Підключення принтера", padding=10)
        printing.pack(fill="x")

        ttk.Label(printing, text="Підключення").grid(row=0, column=0, sticky="w", pady=3)
        self.connection_var = tk.StringVar(value="USB")
        modes = ttk.Frame(printing)
        modes.grid(row=0, column=1, sticky="e")
        ttk.Radiobutton(
            modes,
            text="USB",
            value="USB",
            variable=self.connection_var,
            command=self._connection_mode_changed,
        ).pack(side="left")
        ttk.Radiobutton(
            modes,
            text="Wi-Fi / LAN",
            value="NETWORK",
            variable=self.connection_var,
            command=self._connection_mode_changed,
        ).pack(side="left", padx=(8, 0))

        ttk.Label(printing, text="IP принтера").grid(row=1, column=0, sticky="w", pady=3)
        self.network_ip_var = tk.StringVar(value=DEFAULT_NETWORK_IP)
        self.network_ip_entry = ttk.Entry(printing, textvariable=self.network_ip_var, width=17)
        self.network_ip_entry.grid(row=1, column=1, sticky="ew", pady=3)
        self.network_ip_var.trace_add("write", self._network_ip_changed)

        ttk.Label(printing, text="Черга Windows").grid(row=2, column=0, sticky="w", pady=3)
        self.printer_var = tk.StringVar(value=DEFAULT_PRINTER)
        self.printer_combo = ttk.Combobox(
            printing, textvariable=self.printer_var, state="readonly"
        )
        self.printer_combo.grid(row=3, column=0, columnspan=2, sticky="ew")
        self.printer_combo.bind("<<ComboboxSelected>>", lambda _event: self._schedule_connection_check())
        self.comboboxes.append(self.printer_combo)

        setup_buttons = ttk.Frame(printing)
        setup_buttons.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 4))
        ttk.Button(
            setup_buttons, text="Встановити драйвер", command=self._install_driver, style="Tool.TButton"
        ).pack(side="left", fill="x", expand=True)
        ttk.Button(
            setup_buttons, text="Налаштувати мережу", command=self._configure_network_printer,
            style="Tool.TButton",
        ).pack(side="left", fill="x", expand=True, padx=(5, 0))

        refresh_buttons = ttk.Frame(printing)
        refresh_buttons.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        ttk.Button(
            refresh_buttons, text="⟳ Оновити черги", command=self._refresh_printers, style="Tool.TButton"
        ).pack(side="left", fill="x", expand=True)
        ttk.Button(
            refresh_buttons, text="Перевірити зараз", command=self._schedule_connection_check,
            style="Tool.TButton",
        ).pack(side="left", fill="x", expand=True, padx=(5, 0))

        self.connection_status_label = tk.Label(
            printing,
            text="● Перевірка підключення…",
            anchor="w",
            justify="left",
            wraplength=310,
            font=(UI_FONT, 10),
        )
        self._themed(self.connection_status_label, bg="panel", fg="checking")
        self.connection_status_label.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        printing.columnconfigure(0, weight=1)
        printing.columnconfigure(1, weight=1)

        job = ttk.LabelFrame(print_tab, text="Друк", padding=10)
        job.pack(fill="x", pady=(10, 0))
        ttk.Label(job, textvariable=self.size_status_var, style="Hint.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 6)
        )
        ttk.Label(job, text="Кількість копій").grid(row=1, column=0, sticky="w")
        ttk.Spinbox(job, from_=1, to=99, textvariable=self.copies_var, width=8).grid(
            row=1, column=1, sticky="e"
        )
        self.print_button = ttk.Button(
            job,
            text="ДРУКУВАТИ",
            command=self._print_layout,
            state="disabled",
            style="Accent.TButton",
        )
        self.print_button.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        ttk.Button(job, text="Друк кількох вкладок…", command=self._print_tabs_dialog).grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=(7, 0)
        )
        ttk.Button(
            job,
            text="Серійний друк CSV…",
            command=self._batch_print_csv,
        ).grid(row=4, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        ttk.Label(
            job,
            text="У тексті, QR або штрихкоді використовуйте поля {serial}, {name} тощо",
            wraplength=310,
            style="Hint.TLabel",
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))
        job.columnconfigure(0, weight=1)
        job.columnconfigure(1, weight=1)

        for variable in (
            self.x_var,
            self.y_var,
            self.text_var,
            self.font_var,
            self.size_var,
            self.bold_var,
            self.width_var,
            self.height_var,
            self.rotation_var,
        ):
            variable.trace_add("write", self._schedule_live_apply)
        self.network_ip_entry.configure(state="disabled")
        self._show_property_frame(None)

        # Усі Ctrl-комбінації йдуть через один обробник, який працює в будь-якій
        # розкладці клавіатури (зокрема українській).
        self.bind_all("<Control-KeyPress>", self._control_key)
        for sequence, direction in (("<Control-Tab>", 1), ("<Control-Shift-Tab>", -1),
                                    ("<Control-ISO_Left_Tab>", -1)):
            try:
                self.bind_all(sequence, lambda event, d=direction: self._cycle_tab(d, event))
            except tk.TclError:
                pass
        self.bind("<Delete>", self._delete_shortcut)
        self.bind("<Escape>", self._deselect)
        self.bind("<F1>", self._show_shortcuts)
        for key in ("<Left>", "<Right>", "<Up>", "<Down>"):
            self.bind(key, self._nudge_selected)
        self.after(300, self._poll_history_buttons)

    def _snapshot(self):
        return {
            "elements": copy.deepcopy(self.elements),
            "layout_locked": bool(self.layout_locked),
            "selected_id": self.selected_id,
            "selection": list(self.selection_ids),
            "label_width_mm": LABEL_WIDTH_MM,
            "label_height_mm": LABEL_HEIGHT_MM,
        }

    @staticmethod
    def _snapshot_key(snapshot):
        return json.dumps(snapshot, ensure_ascii=False, sort_keys=True)

    def _record_history(self):
        if self.history_suspended:
            return
        self.wheel_rotate_state = None
        snapshot = self._snapshot()
        if self.undo_stack and self._snapshot_key(self.undo_stack[-1]) == self._snapshot_key(snapshot):
            return
        self.undo_stack.append(snapshot)
        del self.undo_stack[:-MAX_HISTORY]
        self.redo_stack.clear()
        self._mark_dirty()

    def _restore_snapshot(self, snapshot):
        self.wheel_rotate_state = None
        self.history_suspended = True
        try:
            self._finish_inline_edit(commit=False)
            self.elements = copy.deepcopy(snapshot.get("elements", []))
            size = (
                float(snapshot.get("label_width_mm", LABEL_WIDTH_MM)),
                float(snapshot.get("label_height_mm", LABEL_HEIGHT_MM)),
            )
            if size != (LABEL_WIDTH_MM, LABEL_HEIGHT_MM):
                self._set_label_size(*size)
            self.layout_locked = bool(snapshot.get("layout_locked", False))
            candidate = snapshot.get("selected_id")
            self._set_selection(snapshot.get("selection") or [candidate], candidate)
            self._render_all()
            self._load_properties()
        finally:
            self.history_suspended = False

    def _undo(self, _event=None):
        if self._event_in_text_input(_event):
            return
        if not self.undo_stack:
            self.status_var.set("Немає змін для скасування")
            return "break"
        self.redo_stack.append(self._snapshot())
        self._restore_snapshot(self.undo_stack.pop())
        self._mark_dirty()
        self.status_var.set("Останню зміну скасовано")
        return "break"

    def _redo(self, _event=None):
        if self._event_in_text_input(_event):
            return
        if not self.redo_stack:
            self.status_var.set("Немає змін для повторення")
            return "break"
        self.undo_stack.append(self._snapshot())
        self._restore_snapshot(self.redo_stack.pop())
        self._mark_dirty()
        self.status_var.set("Зміну повторено")
        return "break"

    def _read_autosave_payload(self):
        try:
            if self.autosave_path.is_file():
                with self.autosave_path.open("r", encoding="utf-8") as stream:
                    payload = json.load(stream)
                if payload.get("elements") or payload.get("tabs"):
                    return payload
        except Exception:
            pass
        return None

    def _schedule_autosave(self):
        if self.autosave_suspended or self.history_suspended:
            return
        if self.autosave_job:
            try:
                self.after_cancel(self.autosave_job)
            except tk.TclError:
                pass
        self.autosave_job = self.after(700, self._write_autosave)

    def _write_autosave(self):
        """Зберегти всі відкриті вкладки на випадок збою (посилання на файли, без копій фото)."""
        self.autosave_job = None
        try:
            self._stash_document()
            tabs = [
                {"file": doc.current_file, "dirty": doc.dirty, "layout": self._layout_data(doc=doc)}
                for doc in self.documents if doc.elements
            ]
            active = next((index for index, doc in enumerate(d for d in self.documents if d.elements)
                           if doc is self.doc), 0)
            with self.autosave_path.open("w", encoding="utf-8") as stream:
                json.dump({"version": 5, "tabs": tabs, "active": active}, stream, ensure_ascii=False)
        except Exception:
            pass

    def _offer_autosave_recovery(self):
        payload = self.recovery_payload
        self.recovery_payload = None
        if not payload:
            return
        tabs = payload.get("tabs") or [{"file": None, "dirty": True, "layout": payload}]
        if not messagebox.askyesno(
            "Відновлення роботи",
            f"Знайдено незбережену роботу після попереднього сеансу (вкладок: {len(tabs)}). Відновити?",
        ):
            return
        restored = []
        for tab in tabs:
            try:
                if restored or self.elements or self.current_file:
                    self._new_tab()
                self._load_layout_payload(tab["layout"])
                self.current_file = tab.get("file")
                self.undo_stack.clear()
                self.redo_stack.clear()
                self.doc_dirty = bool(tab.get("dirty", True))
                restored.append(self.doc)
            except Exception as exc:
                messagebox.showerror("Відновлення роботи", f"Не вдалося відновити вкладку:\n{exc}")
        if restored:
            active = restored[min(len(restored) - 1, max(0, int(payload.get("active", 0) or 0)))]
            self._activate_document(active)
            self._draw_tabs()
            self.status_var.set(f"Відновлено вкладок: {len(restored)}")

    def _on_close(self):
        self._finish_inline_edit(commit=True)
        self._stash_document()
        unsaved = [doc for doc in self.documents if doc.dirty and doc.elements]
        if unsaved:
            names = ", ".join(f"«{self._doc_title(doc)}»" for doc in unsaved[:4])
            if len(unsaved) > 4:
                names += " …"
            answer = messagebox.askyesnocancel(
                "Вихід", f"Є незбережені зміни: {names}.\nЗберегти їх перед виходом?"
            )
            if answer is None:
                return
            if answer:
                for doc in unsaved:
                    self._activate_document(doc)
                    if not self._save_layout():
                        return
        try:
            if self.autosave_job:
                self.after_cancel(self.autosave_job)
            self.autosave_path.unlink(missing_ok=True)
        except OSError:
            pass
        self.destroy()

    @staticmethod
    def _is_editable_input(widget):
        """Поле, куди користувач зараз вводить текст (не «лише для читання»)."""
        if not isinstance(widget, (tk.Entry, ttk.Entry, tk.Text)):
            return False
        try:
            return str(widget.cget("state")) not in ("readonly", "disabled")
        except tk.TclError:
            return True

    def _event_in_text_input(self, event):
        if event is None:
            return False
        widget = getattr(event, "widget", None)
        return isinstance(widget, tk.Listbox) or self._is_editable_input(widget)

    def _control_key(self, event):
        """Ctrl-комбінації незалежно від розкладки (англійська, українська, російська)."""
        keysym = str(event.keysym)
        latin = len(keysym) == 1 and keysym.isascii() and keysym.isalpha()
        letter = keysym.lower() if latin else None
        if letter is None:
            codes = CTRL_KEY_VK if os.name == "nt" else CTRL_KEY_X11
            letter = codes.get(event.keycode)
        if letter is None:
            letter = CTRL_KEY_CYRILLIC.get(keysym)
        if letter is None:
            return None
        widget = event.widget
        if self._is_editable_input(widget):
            if not latin and letter in ("a", "c", "v", "x"):
                # У не латинській розкладці стандартні Ctrl+C/V/X полів Tk не спрацьовують.
                widget.event_generate(
                    {"a": "<<SelectAll>>", "c": "<<Copy>>", "v": "<<Paste>>", "x": "<<Cut>>"}[letter]
                )
                return "break"
            if letter in ("a", "c", "v", "x", "z", "y"):
                return None
        try:
            if widget.winfo_toplevel() is not self:
                return None
        except (AttributeError, tk.TclError):
            return None
        shift = bool(event.state & 0x0001)
        actions = {
            "a": self._select_all,
            "z": self._undo,
            "y": self._redo,
            "c": self._copy_selected,
            "v": self._paste_element,
            "d": self._duplicate_selected,
            "n": self._new_tab,
            "t": self._new_tab,
            "w": self._close_tab,
            "p": self._print_layout,
            "o": self._load_layout,
            "s": self._save_layout_as if shift else self._save_layout,
            "r": (lambda: self._rotate_selected(-90)) if shift else (lambda: self._rotate_selected(90)),
        }
        action = actions.get(letter)
        if action is None:
            return None
        action()
        return "break"

    def _copy_selected(self, event=None):
        if self._event_in_text_input(event):
            return
        chosen = self._selected_elements()
        if chosen:
            self.clipboard_elements = copy.deepcopy(chosen)
            # Кладемо «підпис» у системний буфер: так Ctrl+V знає, що вставляти
            # саме скопійовані елементи, а не текст, скопійований деінде пізніше.
            element = chosen[0]
            if len(chosen) > 1:
                signature = "\n".join(
                    str(item.get("text", "")) if item.get("type") == "text"
                    else f"Елемент наліпки: {self._layer_name(item)[2:].strip()}"
                    for item in chosen
                )
            elif element.get("type") == "text":
                signature = str(element.get("text", ""))
            else:
                signature = f"Елемент наліпки: {self._layer_name(element)[2:].strip()}"
            try:
                self.clipboard_clear()
                self.clipboard_append(signature)
                self.clipboard_signature = signature
            except tk.TclError:
                self.clipboard_signature = None
            self.status_var.set("Елемент скопійовано" if len(chosen) == 1 else f"Скопійовано елементів: {len(chosen)}")
        return "break" if event else None

    def _paste_element(self, event=None):
        """Ctrl+V: вставити текст або картинку з буфера обміну чи скопійований елемент."""
        if self._event_in_text_input(event) or self.inline_editor:
            return
        kind, value = self._read_system_clipboard()
        if kind == "files":
            self._paste_image_files(value)
        elif kind == "text" and self.clipboard_elements and value == self.clipboard_signature:
            self._paste_internal_element()
        elif kind == "text":
            self._paste_text(value)
        elif kind == "image":
            self._paste_clipboard_image(value)
        elif self.clipboard_elements:
            self._paste_internal_element()
        else:
            self.status_var.set("Буфер обміну порожній: скопіюйте текст або картинку й натисніть Ctrl+V")
        return "break" if event else None

    def _read_system_clipboard(self):
        grabbed = None
        try:
            from PIL import ImageGrab
            grabbed = ImageGrab.grabclipboard()
        except Exception:
            grabbed = None
        if isinstance(grabbed, list):
            files = [str(path) for path in grabbed if self._is_image_file(path)]
            if files:
                return "files", files
        try:
            text = self.clipboard_get()
        except tk.TclError:
            text = ""
        if text and text.strip():
            candidates = [
                line.strip().strip('"').removeprefix("file://")
                for line in text.strip().splitlines()
                if line.strip()
            ]
            if candidates and all(self._is_image_file(path) for path in candidates):
                return "files", candidates
            return "text", text
        if isinstance(grabbed, Image.Image):
            return "image", grabbed
        return None, None

    @staticmethod
    def _is_image_file(path):
        try:
            candidate = Path(str(path))
            return candidate.is_file() and candidate.suffix.lower() in IMAGE_FILETYPES.replace("*", "").split()
        except (OSError, ValueError):
            return False

    def _pointer_position_mm(self):
        """Позиція курсора на наліпці (мм), якщо курсор зараз над нею."""
        try:
            x = self.winfo_pointerx() - self.canvas.winfo_rootx()
            y = self.winfo_pointery() - self.canvas.winfo_rooty()
        except tk.TclError:
            return None
        if 0 <= x <= self.canvas.winfo_width() and 0 <= y <= self.canvas.winfo_height():
            return px_to_mm(x), px_to_mm(y)
        return None

    def _paste_text(self, text):
        lines = [
            " ".join(line.split())
            for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        ]
        lines = [line[:300] for line in lines if line][:12]
        if not lines:
            return
        self._record_history()
        start = self._pointer_position_mm()
        x, y = start if start else (SAFE_MARGIN_MM + 0.5, SAFE_MARGIN_MM + 0.5)
        max_width = LABEL_WIDTH_MM - 2 * SAFE_MARGIN_MM
        added = []
        for line in lines:
            element = {
                "id": uuid.uuid4().hex,
                "type": "text",
                "x": round(x, 2),
                "y": round(y, 2),
                "text": line,
                "font": "Tahoma",
                "size": 12.0,
                "bold": False,
                "locked": False,
                "visible": True,
            }
            self.elements.append(element)
            self._render_all()
            # Довгий рядок автоматично зменшуємо, щоб він умістився в ширину наліпки.
            width, height = self._element_size_mm(element)
            while width > max_width and element["size"] > 5:
                element["size"] = round(max(5.0, element["size"] * max_width / width - 0.25), 2)
                self._render_all()
                width, height = self._element_size_mm(element)
            if element["x"] + width > LABEL_WIDTH_MM - SAFE_MARGIN_MM:
                element["x"] = round(max(0.0, LABEL_WIDTH_MM - SAFE_MARGIN_MM - width), 2)
            added.append(element)
            y += max(height, 1.0)
        self.selected_id = added[0]["id"]
        self._render_all()
        self._load_properties()
        self.status_var.set(
            "Текст вставлено з буфера обміну" if len(added) == 1
            else f"Вставлено рядків тексту: {len(added)}"
        )

    def _paste_clipboard_image(self, image):
        folder = self.data_dir / "pasted"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"clipboard_{uuid.uuid4().hex}.png"
        try:
            if image.mode not in ("RGB", "RGBA", "L", "LA", "1"):
                image = image.convert("RGBA")
            image.save(target, "PNG")
        except Exception as exc:
            messagebox.showerror("Вставлення", f"Не вдалося вставити картинку:\n{exc}")
            return
        if self._add_image_file(target, self._pointer_position_mm()):
            self.status_var.set("Картинку вставлено з буфера обміну")

    def _paste_image_files(self, paths):
        position = self._pointer_position_mm()
        added = 0
        for index, path in enumerate(paths[:10]):
            offset = None
            if position:
                offset = (position[0] + index, position[1] + index)
            if self._add_image_file(path, offset, offset_index=index):
                added += 1
        if added:
            self.status_var.set(f"Вставлено зображень: {added}")

    def _paste_internal_element(self):
        if not self.clipboard_elements:
            return
        self._record_history()
        added = []
        for source in self.clipboard_elements:
            element = copy.deepcopy(source)
            element["id"] = uuid.uuid4().hex
            element["x"] = round(float(element.get("x", 0)) + 1.0, 2)
            element["y"] = round(float(element.get("y", 0)) + 1.0, 2)
            element["locked"] = False
            self.elements.append(element)
            added.append(element["id"])
        # Наступна вставка зсунеться ще на 1 мм — копії не лягають одна на одну.
        for source in self.clipboard_elements:
            source["x"] = round(float(source.get("x", 0)) + 1.0, 2)
            source["y"] = round(float(source.get("y", 0)) + 1.0, 2)
        self._set_selection(added, added[0])
        self._render_all()
        self._load_properties()
        self.status_var.set("Елемент вставлено" if len(added) == 1 else f"Вставлено елементів: {len(added)}")

    def _duplicate_selected(self, event=None):
        if self._event_in_text_input(event):
            return
        chosen = self._selected_elements()
        if not chosen:
            return "break" if event else None
        saved = self.clipboard_elements
        self.clipboard_elements = copy.deepcopy(chosen)
        self._paste_internal_element()
        self.clipboard_elements = saved
        self.status_var.set("Елемент продубльовано" if len(chosen) == 1 else f"Продубльовано елементів: {len(chosen)}")
        return "break" if event else None

    def _delete_shortcut(self, event=None):
        if self._event_in_text_input(event) or self.inline_editor:
            return
        self._delete_selected()
        return "break"

    def _nudge_selected(self, event):
        if self._event_in_text_input(event) or self.inline_editor:
            return
        movers = [element for element in self._selected_elements() if not element.get("locked")]
        if not movers:
            return
        step = 0.5 if event.state & 0x0001 else 0.1
        dx = {"Left": -step, "Right": step}.get(event.keysym, 0.0)
        dy = {"Up": -step, "Down": step}.get(event.keysym, 0.0)
        self._record_history()
        for element in movers:
            element["x"] = round(float(element["x"]) + dx, 2)
            element["y"] = round(float(element["y"]) + dy, 2)
        self._render_all()
        self._load_properties()
        self.status_var.set(f"Зсув: {step:g} мм")
        return "break"

    def _set_zoom(self, _event=None):
        global PX_PER_MM
        try:
            factor = int(self.zoom_var.get().rstrip("%")) / 100.0
        except ValueError:
            factor = 1.0
        PX_PER_MM = BASE_PX_PER_MM * factor
        self._update_canvas_geometry()
        self._render_all()
        self.status_var.set(f"Масштаб перегляду: {round(factor * 100)}%")

    def _update_canvas_geometry(self):
        self.canvas.configure(
            width=round(LABEL_WIDTH_MM * PX_PER_MM),
            height=round(LABEL_HEIGHT_MM * PX_PER_MM),
        )
        self._center_canvas()

    def _center_canvas(self, _event=None):
        """Розмістити наліпку по центру робочої області з легкою тінню та підписом розміру."""
        view_width = max(1, self.canvas_view.winfo_width())
        view_height = max(1, self.canvas_view.winfo_height())
        label_width = round(LABEL_WIDTH_MM * PX_PER_MM) + 2
        label_height = round(LABEL_HEIGHT_MM * PX_PER_MM) + 2
        pad = 32
        x = max(pad, (view_width - label_width) / 2)
        y = max(pad, (view_height - label_height - 24) / 2)
        self.canvas_view.coords(self.canvas_window, x, y)
        self.canvas_view.delete("decor")
        glow = COLORS.get("glow") or ()
        if glow:
            # Неонове сяйво навколо наліпки в темних темах.
            steps = len(glow)
            for index, color in enumerate(glow):
                spread = (steps - index) * 4
                self.canvas_view.create_rectangle(
                    x - spread, y - spread, x + label_width + spread, y + label_height + spread,
                    fill=color, outline="", tags="decor",
                )
        else:
            self.canvas_view.create_rectangle(
                x + 3, y + 4, x + label_width + 4, y + label_height + 5,
                fill=COLORS["shadow"], outline="", tags="decor",
            )
        self.canvas_view.create_text(
            x + label_width / 2, y + label_height + 18 + (len(glow) * 4 if glow else 0),
            text=size_text(LABEL_WIDTH_MM, LABEL_HEIGHT_MM),
            fill=COLORS["muted"], font=(UI_FONT, 9, "bold"), tags="decor",
        )
        self.canvas_view.configure(
            scrollregion=(
                0,
                0,
                max(view_width, x + label_width + pad),
                max(view_height, y + label_height + pad + 40),
            )
        )

    def _mouse_wheel(self, event):
        if getattr(event, "num", None) == 4:
            step = -1
        elif getattr(event, "num", None) == 5:
            step = 1
        else:
            step = -1 if event.delta > 0 else 1
        if event.state & 0x0004:
            return self._zoom_step(-step)
        # Над вибраним фото коліщатко повертає його (вгору — за годинниковою).
        if self._wheel_rotate(event, -step, big=bool(event.state & 0x0001)):
            return "break"
        self.canvas_view.yview_scroll(step, "units")
        return "break"

    def _mouse_wheel_horizontal(self, event):
        direction = 1 if event.delta > 0 else -1
        if self._wheel_rotate(event, direction, big=True):
            return "break"
        self.canvas_view.xview_scroll(-direction, "units")
        return "break"

    def _mouse_wheel_zoom(self, event):
        return self._zoom_step(1 if event.delta > 0 else -1)

    def _zoom_step(self, direction):
        try:
            current = int(self.zoom_var.get().rstrip("%"))
        except ValueError:
            current = 100
        levels = [int(level.rstrip("%")) for level in ZOOM_LEVELS]
        if direction > 0:
            target = next((level for level in levels if level > current), levels[-1])
        else:
            target = next((level for level in reversed(levels) if level < current), levels[0])
        if target != current:
            self.zoom_var.set(f"{target}%")
            self._set_zoom()
        return "break"

    def _fit_factor(self):
        view_width = self.canvas_view.winfo_width()
        view_height = self.canvas_view.winfo_height()
        if view_width < 50 or view_height < 50:
            return None
        return max(0.2, min(4.0, min(
            (view_width - 72) / (LABEL_WIDTH_MM * BASE_PX_PER_MM),
            (view_height - 96) / (LABEL_HEIGHT_MM * BASE_PX_PER_MM),
        )))

    def _fit_zoom(self):
        """Вписати наліпку у вікно повністю."""
        factor = self._fit_factor()
        if factor is None:
            return
        self.zoom_var.set(f"{max(20, int(factor * 100))}%")
        self._set_zoom()

    def _ensure_label_fits(self):
        """Якщо наліпка не вміщується у вікно — автоматично зменшити масштаб."""
        factor = self._fit_factor()
        if factor is None:
            return
        try:
            current = int(self.zoom_var.get().rstrip("%")) / 100.0
        except ValueError:
            current = 1.0
        if current > factor:
            self._fit_zoom()

    def _generated_path(self, prefix):
        folder = self.data_dir / "generated"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{prefix}_{uuid.uuid4().hex}.png"

    def _add_generated_image(self, path, width, height, source_kind, value):
        self._record_history()
        element = {
            "id": uuid.uuid4().hex,
            "type": "image",
            "x": 3.0,
            "y": 3.0,
            "width": float(width),
            "height": float(height),
            "path": str(path),
            "preserve_aspect": True,
            "locked": False,
            "visible": True,
            "source_kind": source_kind,
            "code_value": value,
        }
        self.elements.append(element)
        self.selected_id = element["id"]
        self._render_all()
        self._load_properties()

    def _generate_code_file(self, source_kind, value):
        if source_kind == "qr":
            path = self._generated_path("qr")
            qr = qrcode.QRCode(version=None, box_size=10, border=1)
            qr.add_data(value)
            qr.make(fit=True)
            qr.make_image(fill_color="black", back_color="white").save(path)
            return path
        if source_kind == "code128":
            target = self._generated_path("code128")
            result = barcode.get("code128", value, writer=ImageWriter()).save(
                str(target.with_suffix("")),
                options={
                    "write_text": True,
                    "module_height": 10.0,
                    "quiet_zone": 1.0,
                    "dpi": 300,
                },
            )
            return Path(result)
        raise ValueError(f"Невідомий тип коду: {source_kind}")

    def _add_qr(self):
        value = simpledialog.askstring("QR-код", "Введіть текст або адресу для QR-коду:", parent=self)
        if value is None or not value.strip():
            return
        try:
            path = self._generate_code_file("qr", value)
            self._add_generated_image(path, 15.0, 15.0, "qr", value)
            self.status_var.set("QR-код додано")
        except Exception as exc:
            messagebox.showerror("QR-код", f"Не вдалося створити QR-код:\n{exc}")

    def _add_barcode(self):
        value = simpledialog.askstring("Штрихкод Code 128", "Введіть значення штрихкоду:", parent=self)
        if value is None or not value.strip():
            return
        try:
            path = self._generate_code_file("code128", value)
            self._add_generated_image(path, 28.0, 12.0, "code128", value)
            self.status_var.set("Штрихкод Code 128 додано")
        except Exception as exc:
            messagebox.showerror("Штрихкод", f"Не вдалося створити штрихкод:\n{exc}")

    def _layer_name(self, element):
        prefix = "👁" if element.get("visible", True) else "×"
        lock = " 🔒" if element.get("locked") else ""
        if element.get("type") == "text":
            name = element.get("text", "") or "Порожній текст"
        elif element.get("source_kind") == "qr":
            name = f"QR: {element.get('code_value', '')}"
        elif element.get("source_kind") == "code128":
            name = f"Code128: {element.get('code_value', '')}"
        else:
            name = Path(element.get("path", "Зображення")).name
        transform = ""
        if element.get("type") == "image":
            angle = self._normalize_angle(element.get("rotation", 0))
            if angle:
                transform += f" ↻{angle:g}°"
            if element.get("flip_h"):
                transform += " ⇆"
            if element.get("flip_v"):
                transform += " ⇅"
            if element.get("photo") or normalized_adjustments(element.get("adjust")):
                transform += " ✦"
        return f"{prefix} {name[:34]}{transform}{lock}"

    def _refresh_layers(self):
        if not hasattr(self, "layers_list"):
            return
        self.layers_list.delete(0, tk.END)
        self.layer_ids = []
        for element in reversed(self.elements):
            self.layer_ids.append(element["id"])
            self.layers_list.insert(tk.END, self._layer_name(element))
        for element_id in self.selection_ids:
            if element_id in self.layer_ids:
                self.layers_list.selection_set(self.layer_ids.index(element_id))
        if self.selected_id in self.layer_ids:
            self.layers_list.see(self.layer_ids.index(self.selected_id))

    def _layer_selected(self, _event=None):
        """Клік — один шар; Shift/Ctrl+клік — кілька шарів одразу."""
        selection = self.layers_list.curselection()
        if not selection:
            return
        ids = [self.layer_ids[index] for index in selection if index < len(self.layer_ids)]
        if not ids:
            return
        try:
            active = self.layer_ids[self.layers_list.index("active")]
        except (tk.TclError, IndexError):
            active = None
        primary = active if active in ids else (self.selected_id if self.selected_id in ids else ids[0])
        self._set_selection(ids, primary)
        self._draw_selection()
        self._load_properties()

    def _move_layer(self, direction):
        element = self._element()
        if not element:
            return
        index = self.elements.index(element)
        target = max(0, min(len(self.elements) - 1, index + direction))
        if target == index:
            return
        self._record_history()
        self.elements.pop(index)
        self.elements.insert(target, element)
        self._render_all()
        self.status_var.set("Порядок шарів змінено")

    def _toggle_visibility(self):
        chosen = self._selected_elements()
        if not chosen:
            return
        self._record_history()
        visible = not all(element.get("visible", True) for element in chosen)
        for element in chosen:
            element["visible"] = visible
        self._render_all()
        self._load_properties()
        self.status_var.set("Видимість шару змінено")

    def _element(self, element_id=None):
        element_id = element_id or self.selected_id
        return next((e for e in self.elements if e["id"] == element_id), None)

    def _add_text(self):
        self._record_history()
        element = {
            "id": uuid.uuid4().hex,
            "type": "text",
            "x": 10.0,
            "y": 10.0,
            "text": "Новий текст",
            "font": "Tahoma",
            "size": 12.0,
            "bold": False,
            "locked": False,
            "visible": True,
        }
        self.elements.append(element)
        self.selected_id = element["id"]
        self._render_all()
        self._load_properties()

    def _add_image(self):
        path = filedialog.askopenfilename(
            title="Виберіть зображення",
            filetypes=[("Зображення", IMAGE_FILETYPES), ("Усі файли", "*.*")],
        )
        if not path:
            return
        self._add_image_file(path)

    def _add_image_file(self, path, position=None, offset_index=0):
        try:
            width, height = oriented_size(path)
            ratio = width / height
        except Exception as exc:
            messagebox.showerror("Зображення", f"Не вдалося відкрити файл:\n{exc}")
            return False
        height = 12.0
        width = min(20.0, height * ratio)
        if width == 20.0:
            height = width / ratio
        x, y = position if position else (2.0 + offset_index, 2.0 + offset_index)
        x = max(0.0, min(x, LABEL_WIDTH_MM - width))
        y = max(0.0, min(y, LABEL_HEIGHT_MM - height))
        element = {
            "id": uuid.uuid4().hex,
            "type": "image",
            "x": round(x, 2),
            "y": round(y, 2),
            "width": round(width, 2),
            "height": round(height, 2),
            "path": os.path.abspath(path),
            "preserve_aspect": True,
            "locked": False,
            "visible": True,
        }
        self._record_history()
        self.elements.append(element)
        self.selected_id = element["id"]
        self._render_all()
        self._load_properties()
        return True

    def _replace_image(self):
        """Замінити файл вибраного зображення, не змінюючи його рамку та позицію."""
        element = self._element()
        if not element or element.get("type") != "image":
            messagebox.showinfo("Заміна зображення", "Спочатку виберіть зображення")
            return
        path = filedialog.askopenfilename(
            title="Виберіть нове зображення",
            filetypes=[
                ("Зображення", IMAGE_FILETYPES),
                ("Усі файли", "*.*"),
            ],
        )
        if not path:
            return
        try:
            with Image.open(path) as image:
                image.verify()
        except Exception as exc:
            messagebox.showerror("Зображення", f"Не вдалося відкрити файл:\n{exc}")
            return
        self._record_history()
        element["path"] = os.path.abspath(path)
        element["preserve_aspect"] = True
        element.pop("photo", None)
        element.pop("adjust", None)
        element.pop("image_data_b64", None)
        element.pop("image_name", None)
        element.pop("image_ext", None)
        self._render_all()
        self._load_properties()
        self.status_var.set("Зображення замінено; позицію та рамку збережено")

    def _delete_selected(self):
        if not self.selection_ids:
            return
        chosen = set(self.selection_ids)
        self._record_history()
        self.elements = [e for e in self.elements if e["id"] not in chosen]
        self.selected_id = None
        self._render_all()
        self._load_properties()
        if len(chosen) > 1:
            self.status_var.set(f"Видалено елементів: {len(chosen)}")

    def _bring_front(self):
        chosen = self._selected_elements()
        if not chosen:
            return
        self._record_history()
        for element in chosen:
            self.elements.remove(element)
        self.elements.extend(chosen)
        self._render_all()

    def _toggle_selected_lock(self):
        chosen = self._selected_elements()
        if not chosen:
            messagebox.showinfo("Блокування", "Спочатку виберіть елемент")
            return
        self._record_history()
        lock = not all(element.get("locked") for element in chosen)
        for element in chosen:
            element["locked"] = lock
        self._render_all()
        self._load_properties()
        if len(chosen) > 1:
            self.status_var.set(f"Заблоковано елементів: {len(chosen)}" if lock else f"Розблоковано елементів: {len(chosen)}")
        else:
            self.status_var.set(
                "Елемент заблоковано від зсуву та зміни розміру" if lock else "Елемент розблоковано"
            )

    def _toggle_layout_lock(self):
        self._record_history()
        self.layout_locked = not self.layout_locked
        for element in self.elements:
            element["locked"] = self.layout_locked
        self._render_all()
        self._load_properties()
        self.status_var.set(
            "Увесь макет заблоковано" if self.layout_locked else "Увесь макет розблоковано"
        )

    def _center_selected(self, horizontal=False, vertical=False):
        """Центрувати вибраний елемент (або всю виділену групу) відносно наліпки."""
        if self._multi():
            self._align_selection("hcenter" if horizontal else "vcenter", to_label=True)
            if horizontal and vertical:
                self._align_selection("vcenter", to_label=True, record=False)
            return
        element = self._element()
        if not element:
            messagebox.showinfo("Центрування", "Спочатку виберіть текст або зображення")
            return
        if element.get("locked"):
            return
        self._record_history()

        if element["type"] == "image":
            element_width_mm = float(element["width"])
            element_height_mm = float(element["height"])
        else:
            item = self.canvas_items.get(element["id"])
            bbox = self.canvas.bbox(item) if item else None
            if not bbox:
                return
            element_width_mm = (bbox[2] - bbox[0]) / PX_PER_MM
            element_height_mm = (bbox[3] - bbox[1]) / PX_PER_MM

        if horizontal:
            element["x"] = round((LABEL_WIDTH_MM - element_width_mm) / 2, 2)
        if vertical:
            element["y"] = round((LABEL_HEIGHT_MM - element_height_mm) / 2, 2)

        self._render_all()
        self._load_properties()
        directions = "горизонталі та вертикалі" if horizontal and vertical else (
            "горизонталі" if horizontal else "вертикалі"
        )
        self.status_var.set(f"Елемент відцентровано по {directions}")

    def _draw_grid_and_guides(self):
        if self.show_grid_var.get():
            for mm in range(1, int(LABEL_WIDTH_MM)):
                x = mm_to_px(mm)
                major = mm % 5 == 0
                self.canvas.create_line(
                    x, 0, x, mm_to_px(LABEL_HEIGHT_MM),
                    fill="#d5dce3" if major else "#edf0f3",
                    width=1,
                    tags="grid",
                )
                if major:
                    self.canvas.create_text(x + 2, 2, text=str(mm), anchor="nw", fill="#8a949e", tags="grid")
            for mm in range(1, int(LABEL_HEIGHT_MM)):
                y = mm_to_px(mm)
                major = mm % 5 == 0
                self.canvas.create_line(
                    0, y, mm_to_px(LABEL_WIDTH_MM), y,
                    fill="#d5dce3" if major else "#edf0f3",
                    width=1,
                    tags="grid",
                )
                if major:
                    self.canvas.create_text(2, y + 2, text=str(mm), anchor="nw", fill="#8a949e", tags="grid")
        if self.safe_margin_var.get():
            margin = mm_to_px(SAFE_MARGIN_MM)
            self.canvas.create_rectangle(
                margin,
                margin,
                mm_to_px(LABEL_WIDTH_MM) - margin,
                mm_to_px(LABEL_HEIGHT_MM) - margin,
                outline="#db4b4b",
                dash=(5, 4),
                width=1,
                tags="grid",
            )

    def _render_all(self):
        self.canvas.delete("all")
        self.canvas_items.clear()
        self.photo_refs.clear()
        self._draw_grid_and_guides()
        for element in self.elements:
            if not element.get("visible", True):
                continue
            try:
                if element["type"] == "text":
                    px_height = max(7, round(element["size"] * (PX_PER_MM * 25.4) / 72))
                    style = "bold" if element.get("bold") else "normal"
                    item = self.canvas.create_text(
                        mm_to_px(element["x"]),
                        mm_to_px(element["y"]),
                        text=element["text"],
                        anchor="nw",
                        fill="black",
                        font=(element.get("font", "Tahoma"), -px_height, style),
                        tags=("element", element["id"]),
                    )
                else:
                    box_size = (
                        max(1, mm_to_px(element["width"])),
                        max(1, mm_to_px(element["height"])),
                    )
                    target = self._display_bitmap(element, box_size)
                    photo = ImageTk.PhotoImage(target)
                    self.photo_refs[element["id"]] = photo
                    item = self.canvas.create_image(
                        mm_to_px(element["x"]),
                        mm_to_px(element["y"]),
                        image=photo,
                        anchor="nw",
                        tags=("element", element["id"]),
                    )
                self.canvas_items[element["id"]] = item
            except Exception as exc:
                self.status_var.set(f"Помилка елемента: {exc}")
        self._draw_selection()
        self._refresh_layers()
        self._schedule_autosave()

    def _draw_selection(self):
        self.canvas.delete("selection")
        if self._multi():
            self._draw_group_selection()
            return
        item = self.canvas_items.get(self.selected_id)
        if not item:
            return
        bbox = self.canvas.bbox(item)
        if bbox:
            element = self._element()
            locked = bool(element and element.get("locked"))
            outline = "#d97706" if locked else COLORS["selection"]
            self.canvas.create_rectangle(
                bbox[0] - 3, bbox[1] - 3, bbox[2] + 3, bbox[3] + 3,
                outline=outline, width=2, dash=(4, 2), tags="selection"
            )
            if locked:
                self.canvas.create_text(
                    bbox[0] - 3,
                    bbox[1] - 8,
                    text="🔒",
                    anchor="sw",
                    fill="#b45309",
                    tags="selection",
                )
                return
            left, top, right, bottom = bbox[0] - 3, bbox[1] - 3, bbox[2] + 3, bbox[3] + 3
            middle_x = (left + right) / 2
            middle_y = (top + bottom) / 2
            handles = {
                "nw": (left, top), "n": (middle_x, top), "ne": (right, top),
                "e": (right, middle_y), "se": (right, bottom),
                "s": (middle_x, bottom), "sw": (left, bottom), "w": (left, middle_y),
            }
            radius = 5
            for direction, (x, y) in handles.items():
                self.canvas.create_rectangle(
                    x - radius, y - radius, x + radius, y + radius,
                    fill="#ffffff", outline=COLORS["selection"], width=2,
                    tags=("selection", "resize_handle", f"resize_{direction}"),
                )
            if element and element.get("type") == "image":
                # Круглий маркер повороту: над рамкою, а якщо там немає місця — під нею.
                canvas_height = self.canvas.winfo_height() or mm_to_px(LABEL_HEIGHT_MM)
                if top - 24 >= 8:
                    anchor_y, handle_y = top, top - 22
                elif bottom + 24 <= canvas_height - 8:
                    anchor_y, handle_y = bottom, bottom + 22
                else:
                    anchor_y, handle_y = top, top + 22
                self.canvas.create_line(
                    middle_x, anchor_y, middle_x, handle_y, fill=COLORS["selection"], width=1,
                    tags="selection",
                )
                rotate_radius = 8
                self.canvas.create_oval(
                    middle_x - rotate_radius, handle_y - rotate_radius,
                    middle_x + rotate_radius, handle_y + rotate_radius,
                    fill=COLORS["selection"], outline="#ffffff", width=2,
                    tags=("selection", "rotate_handle"),
                )
                self.canvas.create_text(
                    middle_x, handle_y, text="↻", fill="#ffffff", font=(UI_FONT, 9, "bold"),
                    tags=("selection", "rotate_handle"),
                )

    def _canvas_click(self, event):
        # Фокус на полотно: тоді Ctrl+V/Ctrl+C діють на наліпку, а не на поле вводу.
        self.canvas.focus_set()
        if self.inline_editor:
            # Натискання поза вбудованим полем завершує редагування так само,
            # як Enter або втрата фокуса. Після цього звичайно обробляємо клік:
            # порожнє місце зніме виділення, а інший елемент буде вибрано.
            self._finish_inline_edit(commit=True)
        additive = bool(event.state & 0x0001) or bool(event.state & 0x0004)
        hits = self.canvas.find_overlapping(event.x, event.y, event.x, event.y)
        for item in reversed(hits):
            tags = self.canvas.gettags(item)
            if "rotate_handle" in tags and self.selected_id and not self._multi():
                self._start_rotate_drag(event)
                return
            if "resize_handle" in tags and self.selected_id:
                direction = next(
                    tag[7:] for tag in tags
                    if tag.startswith("resize_") and tag != "resize_handle"
                )
                if self._multi():
                    self._start_group_resize(direction, event)
                    return
                element = self._element()
                canvas_item = self.canvas_items.get(self.selected_id)
                bbox = self.canvas.bbox(canvas_item) if canvas_item else None
                if element and bbox and not element.get("locked"):
                    self._record_history()
                    self.resize_state = {
                        "direction": direction,
                        "start_x": event.x,
                        "start_y": event.y,
                        "element": dict(element),
                        "bbox": bbox,
                    }
                    self.drag_start = None
                return
        found = None
        for item in reversed(hits):
            tags = self.canvas.gettags(item)
            if "element" in tags:
                found = next((tag for tag in tags if tag not in ("element", "current")), None)
                break
        self.drag_started = False
        self.drag_history_recorded = False
        self.resize_state = None
        self.rotate_state = None
        self.collapse_on_release = None
        self.marquee = None
        self.drag_start = None
        self.drag_origin = None
        self.group_origin = None
        if found is None:
            # Порожнє місце: тягніть рамку, щоб виділити кілька елементів.
            base = list(self.selection_ids) if additive else []
            if not additive:
                self.selected_id = None
            self.marquee = {"x0": event.x, "y0": event.y, "x1": event.x, "y1": event.y, "base": base}
            self._draw_selection()
            self._load_properties()
            return
        if additive:
            ids = list(self.selection_ids)
            if found in ids:
                ids.remove(found)
                self._set_selection(ids)
                self._draw_selection()
                self._load_properties()
                return
            self._set_selection(ids + [found], found)
        elif found in self.selection_ids and self._multi():
            # Клік по елементу групи: можна одразу тягнути всю групу;
            # якщо відпустити без руху — лишиться вибраним лише він.
            self._set_selection(self.selection_ids, found)
            self.collapse_on_release = found
        else:
            self.selected_id = found
        element = self._element(found)
        self.drag_start = (event.x, event.y)
        self.drag_origin = (float(element["x"]), float(element["y"])) if element else None
        self.group_origin = {
            item["id"]: (float(item["x"]), float(item["y"])) for item in self._selected_elements()
        }
        if element and element.get("type") == "text" and not self._multi():
            guard = self.double_click_guard
            if (
                not guard
                or guard["id"] != element["id"]
                or event.time - guard["time"] > 650
            ):
                self.double_click_guard = {
                    "id": element["id"],
                    "time": event.time,
                    "x": float(element["x"]),
                    "y": float(element["y"]),
                }
        self._draw_selection()
        self._load_properties()

    def _element_size_mm(self, element):
        if element.get("type") == "image":
            return float(element.get("width", 0)), float(element.get("height", 0))
        item = self.canvas_items.get(element.get("id"))
        bbox = self.canvas.bbox(item) if item else None
        if not bbox:
            return 0.0, 0.0
        return (bbox[2] - bbox[0]) / PX_PER_MM, (bbox[3] - bbox[1]) / PX_PER_MM

    def _snap_position(self, element, x, y):
        width, height = self._element_size_mm(element)
        x, y, _guides = self._snap_box(x, y, width, height, {element["id"]})
        return x, y

    def _snap_box(self, x, y, width, height, exclude=()):
        """Прив’язати рамку (лівий верхній кут x, y; мм) до сітки 0,5 мм, країв і центру наліпки
        та до країв і центрів інших елементів. Повертає (x, y, лінії-підказки)."""
        if not self.snap_var.get():
            return x, y, []
        x = round(x * 2) / 2
        y = round(y * 2) / 2
        x_lines = [0.0, SAFE_MARGIN_MM, LABEL_WIDTH_MM / 2, LABEL_WIDTH_MM - SAFE_MARGIN_MM, LABEL_WIDTH_MM]
        y_lines = [0.0, SAFE_MARGIN_MM, LABEL_HEIGHT_MM / 2, LABEL_HEIGHT_MM - SAFE_MARGIN_MM, LABEL_HEIGHT_MM]
        for other in self.elements:
            if other["id"] in exclude or not other.get("visible", True):
                continue
            other_w, other_h = self._element_size_mm(other)
            ox, oy = float(other.get("x", 0)), float(other.get("y", 0))
            x_lines.extend((ox, ox + other_w / 2, ox + other_w))
            y_lines.extend((oy, oy + other_h / 2, oy + other_h))
        threshold = 0.65
        guides = []

        def nearest(position, size, lines):
            best = None
            for offset in (0.0, size / 2, size):
                for line in lines:
                    distance = abs(position + offset - line)
                    if distance <= threshold and (best is None or distance < best[0] - 1e-9):
                        best = (distance, line - offset, line)
            return best

        best_x = nearest(x, width, x_lines)
        if best_x:
            x = best_x[1]
            guides.append(("v", best_x[2]))
        best_y = nearest(y, height, y_lines)
        if best_y:
            y = best_y[1]
            guides.append(("h", best_y[2]))
        return x, y, guides

    def _draw_guides(self, guides=()):
        """Рожеві лінії, коли елемент «прилип» до центру, краю чи іншого елемента."""
        self.canvas.delete("guides")
        width_px = mm_to_px(LABEL_WIDTH_MM)
        height_px = mm_to_px(LABEL_HEIGHT_MM)
        for axis, value in guides:
            position = mm_to_px(value)
            if axis == "v":
                self.canvas.create_line(position, 0, position, height_px, fill=GUIDE_COLOR, width=1,
                                        dash=(4, 3), tags="guides")
            else:
                self.canvas.create_line(0, position, width_px, position, fill=GUIDE_COLOR, width=1,
                                        dash=(4, 3), tags="guides")

    # ---- Кілька елементів: рамка, група, вирівнювання ------------------------------------
    def _element_box_mm(self, element):
        width, height = self._element_size_mm(element)
        x, y = float(element.get("x", 0)), float(element.get("y", 0))
        return x, y, x + width, y + height

    def _group_box_mm(self, elements):
        boxes = [self._element_box_mm(element) for element in elements]
        if not boxes:
            return None
        return (min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))

    def _draw_group_selection(self):
        chosen = [element for element in self._selected_elements() if element["id"] in self.canvas_items]
        boxes = []
        color = COLORS["selection"]
        for element in chosen:
            bbox = self.canvas.bbox(self.canvas_items[element["id"]])
            if not bbox:
                continue
            boxes.append(bbox)
            locked = bool(element.get("locked"))
            self.canvas.create_rectangle(
                bbox[0] - 2, bbox[1] - 2, bbox[2] + 2, bbox[3] + 2,
                outline="#d97706" if locked else color, width=1, dash=(3, 2), tags="selection",
            )
        if not boxes:
            return
        left = min(b[0] for b in boxes) - 6
        top = min(b[1] for b in boxes) - 6
        right = max(b[2] for b in boxes) + 6
        bottom = max(b[3] for b in boxes) + 6
        self.canvas.create_rectangle(left, top, right, bottom, outline=color, width=2, tags="selection")
        count = len(self.selection_ids)
        badge = f"{count} {plural_objects(count)}"
        canvas_height = self.canvas.winfo_height() or mm_to_px(LABEL_HEIGHT_MM)
        if top - 20 >= 0:
            text_y, anchor = top - 4, "sw"
        elif bottom + 20 <= canvas_height:
            text_y, anchor = bottom + 4, "nw"
        else:
            text_y, anchor = top + 4, "nw"
        text = self.canvas.create_text(left + 6, text_y, text=badge, anchor=anchor, fill="#ffffff",
                                       font=(UI_FONT, 8, "bold"), tags="selection")
        tb = self.canvas.bbox(text)
        if tb:
            pill = self.canvas.create_rectangle(tb[0] - 5, tb[1] - 1, tb[2] + 5, tb[3] + 1, fill=color,
                                                outline=color, tags="selection")
            self.canvas.tag_lower(pill, text)
        if all(element.get("locked") for element in chosen):
            return
        middle_x = (left + right) / 2
        middle_y = (top + bottom) / 2
        handles = {
            "nw": (left, top), "n": (middle_x, top), "ne": (right, top),
            "e": (right, middle_y), "se": (right, bottom),
            "s": (middle_x, bottom), "sw": (left, bottom), "w": (left, middle_y),
        }
        radius = 5
        for direction, (x, y) in handles.items():
            self.canvas.create_rectangle(
                x - radius, y - radius, x + radius, y + radius,
                fill="#ffffff", outline=color, width=2,
                tags=("selection", "resize_handle", f"resize_{direction}"),
            )

    def _draw_marquee(self):
        self.canvas.delete("marquee")
        state = self.marquee
        if not state:
            return
        x0, y0, x1, y1 = state["x0"], state["y0"], state["x1"], state["y1"]
        if abs(x1 - x0) < 3 and abs(y1 - y0) < 3:
            return
        self.canvas.create_rectangle(x0, y0, x1, y1, outline=COLORS["selection"], width=1, dash=(4, 3),
                                     tags="marquee")

    def _finish_marquee(self):
        state = self.marquee
        self.marquee = None
        self.canvas.delete("marquee")
        if not state:
            return
        x0, x1 = sorted((state["x0"], state["x1"]))
        y0, y1 = sorted((state["y0"], state["y1"]))
        if x1 - x0 < 3 and y1 - y0 < 3:
            return
        hit_ids = []
        for element in self.elements:
            item = self.canvas_items.get(element["id"])
            bbox = self.canvas.bbox(item) if item else None
            if bbox and bbox[0] < x1 and bbox[2] > x0 and bbox[1] < y1 and bbox[3] > y0:
                hit_ids.append(element["id"])
        ids = list(state["base"]) + [element_id for element_id in hit_ids if element_id not in state["base"]]
        self._set_selection(ids, hit_ids[-1] if hit_ids else None)
        self._draw_selection()
        self._load_properties()
        if len(self.selection_ids) > 1:
            self.status_var.set(
                f"Вибрано елементів: {len(self.selection_ids)} — тягніть, масштабуйте або вирівнюйте разом"
            )

    def _start_group_resize(self, direction, event):
        movers = [element for element in self._selected_elements() if not element.get("locked")]
        box = self._group_box_mm(movers)
        if not movers or not box:
            return
        self._record_history()
        self.resize_state = {
            "group": True,
            "direction": direction,
            "box": box,
            "start": (event.x / PX_PER_MM, event.y / PX_PER_MM),
            "items": {element["id"]: copy.deepcopy(element) for element in movers},
        }
        self.drag_start = None
        self.group_origin = None

    def _resize_group(self, event):
        """Пропорційно масштабувати всю групу (тексти — кеглем, фото — рамкою)."""
        state = self.resize_state
        left, top, right, bottom = state["box"]
        direction = state["direction"]
        cursor_x = event.x / PX_PER_MM
        cursor_y = event.y / PX_PER_MM
        start_x, start_y = state["start"]
        anchor_x = right if "w" in direction else left if "e" in direction else (left + right) / 2
        anchor_y = bottom if "n" in direction else top if "s" in direction else (top + bottom) / 2
        # Масштаб рахуємо від точки, де натиснули маркер: без стрибка на початку.
        factors = []
        if "e" in direction or "w" in direction:
            factors.append((cursor_x - anchor_x) / (start_x - anchor_x) if abs(start_x - anchor_x) > 1e-6 else 1.0)
        if "n" in direction or "s" in direction:
            factors.append((cursor_y - anchor_y) / (start_y - anchor_y) if abs(start_y - anchor_y) > 1e-6 else 1.0)
        # Кут: перемагає вісь, яку тягнуть сильніше.
        scale = max(factors, key=lambda value: abs(value - 1.0)) if factors else 1.0
        scale = max(0.05, scale)
        for element in self.elements:
            original = state["items"].get(element["id"])
            if original is None:
                continue
            element["x"] = round(anchor_x + (float(original["x"]) - anchor_x) * scale, 2)
            element["y"] = round(anchor_y + (float(original["y"]) - anchor_y) * scale, 2)
            if element.get("type") == "image":
                element["width"] = round(max(0.5, float(original["width"]) * scale), 2)
                element["height"] = round(max(0.5, float(original["height"]) * scale), 2)
            else:
                element["size"] = round(max(1.0, float(original["size"]) * scale), 2)
        self._render_all()
        self._load_properties()
        self.status_var.set(f"Масштаб групи: {round(scale * 100)}%")

    def _align_selection(self, mode, to_label=None, record=True):
        """Вирівняти вибрані елементи: краї/центри між собою (або по наліпці, якщо елемент один)."""
        chosen = self._selected_elements()
        movers = [element for element in chosen if not element.get("locked")]
        if not movers:
            self.status_var.set("Спочатку виберіть елементи")
            return
        if to_label is None:
            to_label = len(chosen) < 2
        if to_label:
            reference = (0.0, 0.0, LABEL_WIDTH_MM, LABEL_HEIGHT_MM)
        else:
            reference = self._group_box_mm(chosen)
        if record:
            self._record_history()
        ref_left, ref_top, ref_right, ref_bottom = reference
        if to_label and len(movers) > 1:
            # Група цілком: зсуваємо її спільну рамку, взаємне розташування не змінюється.
            box = self._group_box_mm(movers)
            dx = dy = 0.0
            if mode == "left":
                dx = ref_left - box[0]
            elif mode == "right":
                dx = ref_right - box[2]
            elif mode == "hcenter":
                dx = (ref_left + ref_right) / 2 - (box[0] + box[2]) / 2
            elif mode == "top":
                dy = ref_top - box[1]
            elif mode == "bottom":
                dy = ref_bottom - box[3]
            elif mode == "vcenter":
                dy = (ref_top + ref_bottom) / 2 - (box[1] + box[3]) / 2
            for element in movers:
                element["x"] = round(float(element["x"]) + dx, 2)
                element["y"] = round(float(element["y"]) + dy, 2)
        else:
            for element in movers:
                left, top, right, bottom = self._element_box_mm(element)
                width, height = right - left, bottom - top
                if mode == "left":
                    element["x"] = round(ref_left, 2)
                elif mode == "right":
                    element["x"] = round(ref_right - width, 2)
                elif mode == "hcenter":
                    element["x"] = round((ref_left + ref_right) / 2 - width / 2, 2)
                elif mode == "top":
                    element["y"] = round(ref_top, 2)
                elif mode == "bottom":
                    element["y"] = round(ref_bottom - height, 2)
                elif mode == "vcenter":
                    element["y"] = round((ref_top + ref_bottom) / 2 - height / 2, 2)
        self._render_all()
        self._load_properties()
        names = {"left": "ліворуч", "hcenter": "по центру", "right": "праворуч",
                 "top": "догори", "vcenter": "по середині", "bottom": "донизу"}
        target = "наліпки" if to_label else "групи"
        self.status_var.set(f"Вирівняно {names.get(mode, '')} ({target})")

    def _distribute_selection(self, axis):
        """Рівні проміжки між трьома й більше елементами."""
        movers = [element for element in self._selected_elements() if not element.get("locked")]
        if len(movers) < 3:
            self.status_var.set("Щоб розподілити рівномірно, виберіть щонайменше 3 елементи")
            return
        self._record_history()
        index = 0 if axis == "h" else 1
        boxes = sorted(((self._element_box_mm(element), element) for element in movers),
                       key=lambda pair: pair[0][index])
        start = boxes[0][0][index]
        end = max(box[index + 2] for box, _element in boxes)
        total = sum(box[index + 2] - box[index] for box, _element in boxes)
        gap = (end - start - total) / (len(boxes) - 1)
        position = start
        key = "x" if axis == "h" else "y"
        for box, element in boxes:
            element[key] = round(position, 2)
            position += box[index + 2] - box[index] + gap
        self._render_all()
        self._load_properties()
        self.status_var.set("Розподілено рівномірно по ширині" if axis == "h" else "Розподілено рівномірно по висоті")

    def _select_all(self, event=None):
        if self._event_in_text_input(event) or self.inline_editor:
            return None
        ids = [element["id"] for element in self.elements if element.get("visible", True)]
        self._set_selection(ids, self.selected_id)
        self._draw_selection()
        self._load_properties()
        self.status_var.set(f"Вибрано все: {len(ids)}")
        return "break"

    def _deselect(self, event=None):
        if self._event_in_text_input(event) or self.inline_editor:
            return None
        if self.marquee is not None:
            self.marquee = None
            self.canvas.delete("marquee")
        if self.selection_ids:
            self.selected_id = None
            self._draw_selection()
            self._load_properties()
        return "break"

    def _canvas_drag(self, event):
        if self.inline_editor:
            return
        if self.rotate_state:
            self._rotate_drag(event)
            return
        if self.resize_state:
            if self.resize_state.get("group"):
                self._resize_group(event)
            else:
                self._resize_selected(event)
            return
        if self.marquee is not None:
            self.marquee["x1"], self.marquee["y1"] = event.x, event.y
            self._draw_marquee()
            return
        if not self.selected_id or not self.drag_start or not self.group_origin:
            return
        movers = [
            element for element in self._selected_elements()
            if not element.get("locked") and element["id"] in self.group_origin
        ]
        if not movers:
            return
        dx = event.x - self.drag_start[0]
        dy = event.y - self.drag_start[1]
        if not self.drag_started:
            if dx * dx + dy * dy < DRAG_THRESHOLD_PX * DRAG_THRESHOLD_PX:
                return
            self.drag_started = True
            self.collapse_on_release = None
            if not self.drag_history_recorded:
                self._record_history()
                self.drag_history_recorded = True
        # Уся група рухається разом; прив’язуємо її спільну рамку.
        boxes = []
        for element in movers:
            width, height = self._element_size_mm(element)
            ox, oy = self.group_origin[element["id"]]
            boxes.append((ox, oy, ox + width, oy + height))
        left = min(box[0] for box in boxes)
        top = min(box[1] for box in boxes)
        right = max(box[2] for box in boxes)
        bottom = max(box[3] for box in boxes)
        new_left, new_top, guides = self._snap_box(
            left + dx / PX_PER_MM, top + dy / PX_PER_MM, right - left, bottom - top,
            {element["id"] for element in movers},
        )
        shift_x, shift_y = new_left - left, new_top - top
        for element in movers:
            ox, oy = self.group_origin[element["id"]]
            element["x"] = round(ox + shift_x, 2)
            element["y"] = round(oy + shift_y, 2)
            item = self.canvas_items.get(element["id"])
            if item:
                self.canvas.coords(item, mm_to_px(element["x"]), mm_to_px(element["y"]))
        self._draw_selection()
        self._draw_guides(guides)
        self._load_properties()

    def _canvas_release(self, _event):
        if self.marquee is not None:
            self._finish_marquee()
            return
        self.canvas.delete("guides")
        if self.collapse_on_release and not self.drag_started:
            self.selected_id = self.collapse_on_release
            self._draw_selection()
            self._load_properties()
        self.collapse_on_release = None
        if self.rotate_state:
            self.rotate_state = None
            self._load_properties()
            element = self._element()
            if element:
                self.status_var.set(
                    f"Зображення повернуто на {self._normalize_angle(element.get('rotation', 0)):g}°"
                )
        self.drag_start = None
        self.drag_origin = None
        self.group_origin = None
        if self.drag_started:
            count = len(self.selection_ids)
            self.status_var.set("Елемент переміщено" if count <= 1 else f"Переміщено елементів: {count}")
            self._schedule_autosave()
        self.drag_started = False
        self.drag_history_recorded = False
        if self.resize_state:
            group = self.resize_state.get("group")
            self.resize_state = None
            self._load_properties()
            self.status_var.set("Розмір групи змінено" if group else "Розмір елемента змінено")

    def _canvas_double_click(self, event):
        """Редагувати текст безпосередньо на полотні, не змінюючи координати."""
        hits = self.canvas.find_overlapping(event.x, event.y, event.x, event.y)
        element_id = None
        for item in reversed(hits):
            tags = self.canvas.gettags(item)
            if "element" in tags:
                element_id = next(
                    (tag for tag in tags if tag not in ("element", "current")), None
                )
                break
        element = self._element(element_id)
        if element and element.get("type") == "image":
            self.selected_id = element["id"]
            self.drag_start = None
            self.drag_origin = None
            self._draw_selection()
            self._load_properties()
            self._open_photo_editor(element)
            return
        if not element or element.get("type") != "text":
            return

        guard = self.double_click_guard
        if guard and guard["id"] == element["id"] and event.time - guard["time"] <= 650:
            element["x"] = guard["x"]
            element["y"] = guard["y"]
        self.drag_start = None
        self.drag_origin = None
        self.drag_started = False
        self.resize_state = None
        self.rotate_state = None
        self.selected_id = element["id"]
        self._render_all()
        self._start_inline_edit(element, event)

    def _start_inline_edit(self, element, event):
        self._finish_inline_edit(commit=True)
        item = self.canvas_items.get(element["id"])
        bbox = self.canvas.bbox(item) if item else None
        if not bbox:
            return
        px_height = max(7, round(element["size"] * (PX_PER_MM * 25.4) / 72))
        style = "bold" if element.get("bold") else "normal"
        editor = tk.Entry(
            self.canvas,
            font=(element.get("font", "Tahoma"), -px_height, style),
            relief="solid",
            borderwidth=1,
            highlightthickness=2,
            highlightcolor=COLORS["selection"],
            highlightbackground=COLORS["selection"],
        )
        editor.insert(0, element.get("text", ""))
        editor.place(
            x=max(0, bbox[0] - 2),
            y=max(0, bbox[1] - 2),
            width=max(70, bbox[2] - bbox[0] + 16),
            height=max(26, bbox[3] - bbox[1] + 8),
        )
        self.inline_editor = editor
        self.inline_element_id = element["id"]
        self.inline_original_text = element.get("text", "")
        editor.focus_set()
        try:
            editor.icursor(editor.index(f"@{max(0, event.x - bbox[0])}"))
        except tk.TclError:
            editor.icursor(tk.END)
        editor.bind("<Return>", lambda _event: self._finish_inline_edit(commit=True))
        editor.bind("<Escape>", lambda _event: self._finish_inline_edit(commit=False))
        editor.bind("<FocusOut>", lambda _event: self._finish_inline_edit(commit=True))
        self.status_var.set("Редагування тексту: Enter — зберегти, Esc — скасувати")

    def _finish_inline_edit(self, commit=True):
        editor = self.inline_editor
        element_id = self.inline_element_id
        original_text = self.inline_original_text
        if not editor:
            return
        self.inline_editor = None
        self.inline_element_id = None
        self.inline_original_text = None
        value = editor.get()
        editor.destroy()
        element = self._element(element_id)
        if commit and element and element.get("type") == "text":
            if value != original_text:
                self._record_history()
            element["text"] = value
        self._render_all()
        self._load_properties()
        self.status_var.set("Текст змінено" if commit else "Редагування скасовано")

    def _resize_selected(self, event):
        """Зміна розміру за одним із восьми маркерів виділення."""
        state = self.resize_state
        element = self._element()
        if not state or not element or element.get("locked"):
            return
        original = state["element"]
        direction = state["direction"]
        shift = bool(event.state & 0x0001)
        cursor_x = event.x / PX_PER_MM
        cursor_y = event.y / PX_PER_MM

        if element["type"] == "image":
            left = float(original["x"])
            top = float(original["y"])
            right = left + float(original["width"])
            bottom = top + float(original["height"])
            original_width = max(0.1, right - left)
            original_height = max(0.1, bottom - top)

            new_left, new_top, new_right, new_bottom = left, top, right, bottom
            if "w" in direction:
                new_left = min(cursor_x, right - 0.5)
            if "e" in direction:
                new_right = max(cursor_x, left + 0.5)
            if "n" in direction:
                new_top = min(cursor_y, bottom - 0.5)
            if "s" in direction:
                new_bottom = max(cursor_y, top + 0.5)

            if shift:
                scale_x = (new_right - new_left) / original_width
                scale_y = (new_bottom - new_top) / original_height
                if direction in ("e", "w"):
                    scale = scale_x
                elif direction in ("n", "s"):
                    scale = scale_y
                else:
                    scale = scale_x if abs(scale_x - 1) >= abs(scale_y - 1) else scale_y
                scale = max(0.05, scale)
                width = original_width * scale
                height = original_height * scale
                if "w" in direction:
                    new_left, new_right = right - width, right
                elif "e" in direction:
                    new_left, new_right = left, left + width
                else:
                    center_x = (left + right) / 2
                    new_left, new_right = center_x - width / 2, center_x + width / 2
                if "n" in direction:
                    new_top, new_bottom = bottom - height, bottom
                elif "s" in direction:
                    new_top, new_bottom = top, top + height
                else:
                    center_y = (top + bottom) / 2
                    new_top, new_bottom = center_y - height / 2, center_y + height / 2

            element["x"] = round(new_left, 2)
            element["y"] = round(new_top, 2)
            element["width"] = round(max(0.5, new_right - new_left), 2)
            element["height"] = round(max(0.5, new_bottom - new_top), 2)
        else:
            bbox = state["bbox"]
            width_px = max(1, bbox[2] - bbox[0])
            height_px = max(1, bbox[3] - bbox[1])
            factors = []
            if "e" in direction:
                factors.append((event.x - bbox[0]) / width_px)
            if "w" in direction:
                factors.append((bbox[2] - event.x) / width_px)
            if "s" in direction:
                factors.append((event.y - bbox[1]) / height_px)
            if "n" in direction:
                factors.append((bbox[3] - event.y) / height_px)
            scale = max(0.1, max(factors) if factors else 1.0)
            new_size = max(1.0, float(original["size"]) * scale)
            estimated_width_mm = width_px / PX_PER_MM * scale
            estimated_height_mm = height_px / PX_PER_MM * scale
            original_right = float(original["x"]) + width_px / PX_PER_MM
            original_bottom = float(original["y"]) + height_px / PX_PER_MM
            element["size"] = round(new_size, 2)
            element["x"] = round(
                original_right - estimated_width_mm if "w" in direction else float(original["x"]), 2
            )
            element["y"] = round(
                original_bottom - estimated_height_mm if "n" in direction else float(original["y"]), 2
            )

        self._render_all()
        self._load_properties()

    def _show_property_frame(self, element_type):
        if element_type is None:
            self.element_panel.grid_remove()
            self.group_panel.grid_remove()
            self.empty_hint.grid()
            return
        self.empty_hint.grid_remove()
        if element_type == "group":
            self.element_panel.grid_remove()
            self.group_panel.grid()
            return
        self.group_panel.grid_remove()
        self.element_panel.grid()
        if element_type == "text":
            self.text_frame.grid()
            self.image_frame.grid_remove()
            self.size_row.grid_remove()
        else:
            self.text_frame.grid_remove()
            self.image_frame.grid()
            self.size_row.grid()

    def _schedule_live_apply(self, *_args):
        if self.loading_properties:
            return
        if self.live_apply_job:
            try:
                self.after_cancel(self.live_apply_job)
            except tk.TclError:
                pass
        self.live_apply_job = self.after(140, lambda: self._apply_properties(show_error=False))

    def _load_properties(self):
        self._load_photo_controls()
        self.loading_properties = True
        try:
            element = self._element()
            if not element:
                self.type_var.set("Нічого не вибрано")
                self.x_var.set("")
                self.y_var.set("")
                self._show_property_frame(None)
                return
            if self._multi():
                count = len(self.selection_ids)
                box = self._group_box_mm(self._selected_elements())
                size = f" · {mm_text(box[2] - box[0])}×{mm_text(box[3] - box[1])} мм" if box else ""
                self.type_var.set(f"{count} {plural_objects(count)}{size}")
                self._show_property_frame("group")
                return
            self.x_var.set(str(element["x"]))
            self.y_var.set(str(element["y"]))
            if element["type"] == "text":
                lock_suffix = " (заблоковано)" if element.get("locked") else ""
                self.type_var.set("Текст" + lock_suffix)
                self.text_var.set(element["text"])
                self.font_var.set(element.get("font", "Tahoma"))
                self.size_var.set(str(element.get("size", 12)))
                self.bold_var.set(bool(element.get("bold")))
            else:
                lock_suffix = " (заблоковано)" if element.get("locked") else ""
                self.type_var.set("Фото / картинка" + lock_suffix)
                self.width_var.set(str(element["width"]))
                self.height_var.set(str(element["height"]))
                if element.get("source_kind") in ("qr", "code128"):
                    kind = "QR-код" if element["source_kind"] == "qr" else "Штрихкод Code 128"
                    self.image_path_var.set(f"{kind}: {element.get('code_value', '')}")
                else:
                    self.image_path_var.set(f"Файл: {Path(element['path']).name}")
                angle = self._normalize_angle(element.get("rotation", 0))
                self.rotation_var.set(f"{angle:g}")
            self._show_property_frame(element["type"])
        finally:
            self.loading_properties = False

    def _apply_properties(self, show_error=True):
        self.live_apply_job = None
        element = self._element()
        if not element or self._multi():
            return False
        new_rotation = None
        try:
            values = {
                "x": float(self.x_var.get().replace(",", ".")),
                "y": float(self.y_var.get().replace(",", ".")),
            }
            if element["type"] == "text":
                values.update({
                    "text": self.text_var.get(),
                    "font": self.font_var.get().strip() or "Tahoma",
                    "size": float(self.size_var.get().replace(",", ".")),
                    "bold": self.bold_var.get(),
                })
                if values["size"] <= 0:
                    raise ValueError("Кегль має бути більшим за нуль")
            else:
                values.update({
                    "width": float(self.width_var.get().replace(",", ".")),
                    "height": float(self.height_var.get().replace(",", ".")),
                })
                if values["width"] <= 0 or values["height"] <= 0:
                    raise ValueError("Розмір зображення має бути більшим за нуль")
                rotation_text = self.rotation_var.get().strip().replace(",", ".")
                if not rotation_text:
                    raise ValueError("Вкажіть кут повороту")
                new_rotation = self._normalize_angle(float(rotation_text))
        except ValueError as exc:
            if show_error:
                messagebox.showerror("Властивості", str(exc))
            return False
        changed = any(element.get(key) != value for key, value in values.items())
        rotation_changed = (
            new_rotation is not None
            and new_rotation != self._normalize_angle(element.get("rotation", 0))
        )
        if not changed and not rotation_changed:
            return True
        self._record_history()
        element.update(values)
        if rotation_changed:
            self._set_image_rotation(element, new_rotation)
        self._render_all()
        if rotation_changed:
            self._load_properties()
        self.status_var.set("Зміни застосовано автоматично")
        return True

    # ---- Поворот і віддзеркалення зображень ---------------------------------------------

    @staticmethod
    def _normalize_angle(angle):
        try:
            value = round(float(angle) % 360.0, 2)
        except (TypeError, ValueError):
            return 0.0
        return 0.0 if value >= 360.0 else value

    @staticmethod
    def _rotated_extent(width, height, angle):
        radians = math.radians(angle)
        cos_value = abs(math.cos(radians))
        sin_value = abs(math.sin(radians))
        return (
            width * cos_value + height * sin_value,
            width * sin_value + height * cos_value,
        )

    @classmethod
    def _has_transform(cls, element):
        return bool(
            cls._normalize_angle(element.get("rotation", 0))
            or element.get("flip_h")
            or element.get("flip_v")
        )

    @classmethod
    def _apply_image_transform(cls, image, element):
        """Віддзеркалення, потім поворот за годинниковою стрілкою (кути, кратні 90°, — без втрат)."""
        if element.get("flip_h"):
            image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        if element.get("flip_v"):
            image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        angle = cls._normalize_angle(element.get("rotation", 0))
        exact = {
            90.0: Image.Transpose.ROTATE_270,
            180.0: Image.Transpose.ROTATE_180,
            270.0: Image.Transpose.ROTATE_90,
        }
        if angle in exact:
            image = image.transpose(exact[angle])
        elif angle:
            image = image.rotate(
                -angle, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=(255, 255, 255, 0)
            )
        return image

    def _transformed_image(self, element):
        with Image.open(element["path"]) as source:
            image = source.convert("RGBA")
        return self._apply_image_transform(image, element)

    def _comparing(self, element):
        return self.adjust_compare and element.get("id") == self.selected_id

    def _element_adjustments(self, element):
        """Старі налаштування «adjust» (з попередньої версії) — якщо фото ще не оброблене по-новому."""
        if self._comparing(element) or element.get("photo"):
            return None
        return normalized_adjustments(element.get("adjust"))

    def _photo_active(self, element):
        return bool(element.get("photo")) and photo_available() and not self._comparing(element)

    def _output_mode(self, element, adjust):
        """(режим: "bw" | None, поріг, білий прозорий)."""
        if self._photo_active(element):
            params = photo_params(element["photo"])
            return ("bw" if params["mode"] == "bw" else None), 128, bool(params["transparent"])
        if adjust:
            mode = "bw" if adjust["mode"] in ("bw", "dither") else None
            return mode, int(adjust["threshold"]), bool(adjust["white_transparent"])
        return None, 128, False

    def _element_bitmap(self, element, fit_box, adjust):
        """Обробити фото: джерело → поворот/віддзеркалення → вписати в рамку → Ч/Б."""
        max_side = max(8, round(math.hypot(*fit_box)))
        if self._photo_active(element):
            image = self._photo_output(element).convert("RGBA")
            adjust = None
        else:
            image = load_oriented_image(element["path"], max_side)
            if adjust:
                image = apply_tone_adjustments(image, adjust)
        image = self._apply_image_transform(image, element)
        image = fit_image(image, fit_box, bool(element.get("preserve_aspect")))
        bit_mode, threshold, transparent = self._output_mode(element, adjust)
        if bit_mode:
            image = apply_bit_mode(image, {"mode": bit_mode, "threshold": threshold})
        if transparent:
            image = apply_white_transparent(image)
        return image

    def _element_cache_key(self, element, *extra):
        photo = None
        if self._photo_active(element):
            params = photo_params(element["photo"])
            photo = [params, self._file_stamp(params["paint"]) if params["paint"] else None]
        return json.dumps([
            element["path"], self._file_stamp(element["path"]), self._element_adjustments(element), photo,
            self._normalize_angle(element.get("rotation", 0)),
            bool(element.get("flip_h")), bool(element.get("flip_v")),
            bool(element.get("preserve_aspect")), *extra,
        ], sort_keys=True)

    def _display_bitmap(self, element, box_size):
        """Картинка для полотна (з кешем, щоб усе працювало плавно)."""
        adjust = self._element_adjustments(element)
        key = self._element_cache_key(element, list(box_size))
        cached = self.bitmap_cache.get(key)
        if cached is not None:
            return cached
        if self._output_mode(element, adjust)[0]:
            # Показуємо саме так, як надрукує принтер: точки 203 dpi, збільшені без згладжування.
            printer_box = (
                float(element["width"]) * PRINTER_DOTS_PER_MM,
                float(element["height"]) * PRINTER_DOTS_PER_MM,
            )
            image = self._element_bitmap(element, printer_box, adjust)
            scale = min(box_size[0] / max(1, image.width), box_size[1] / max(1, image.height))
            if not element.get("preserve_aspect"):
                image = image.resize(box_size, Image.Resampling.NEAREST)
            else:
                image = image.resize(
                    (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                    Image.Resampling.NEAREST,
                )
        else:
            image = self._element_bitmap(element, box_size, adjust)
        target = Image.new("RGBA", box_size, (255, 255, 255, 0))
        target.alpha_composite(
            image, ((box_size[0] - image.width) // 2, (box_size[1] - image.height) // 2)
        )
        self.bitmap_cache[key] = target
        while len(self.bitmap_cache) > 48:
            self.bitmap_cache.pop(next(iter(self.bitmap_cache)))
        return target

    def _image_pixel_size(self, path):
        key = (str(path), self._file_stamp(path))
        size = self.image_size_cache.get(key)
        if size is None:
            size = oriented_size(path)
            self.image_size_cache[key] = size
        return size

    def _set_image_rotation(self, element, angle, center=None):
        """Задати кут; рамка підлаштовується під повернуту картинку, центр лишається на місці."""
        new_angle = self._normalize_angle(angle)
        old_angle = self._normalize_angle(element.get("rotation", 0))
        box_width = max(0.1, float(element["width"]))
        box_height = max(0.1, float(element["height"]))
        if center is None:
            center = (float(element["x"]) + box_width / 2, float(element["y"]) + box_height / 2)

        def quarter(value):
            return abs(value / 90.0 - round(value / 90.0)) < 1e-6

        old_odd = quarter(old_angle) and round(old_angle / 90.0) % 2 == 1
        if not element.get("preserve_aspect") and quarter(old_angle) and quarter(new_angle):
            turned = round((new_angle - old_angle) / 90.0) % 2 == 1
            new_width, new_height = (box_height, box_width) if turned else (box_width, box_height)
        else:
            try:
                image_width, image_height = self._element_pixel_size(element)
            except Exception:
                image_width, image_height = box_width, box_height
            image_width = max(1, image_width)
            image_height = max(1, image_height)
            if element.get("preserve_aspect"):
                old_width, old_height = self._rotated_extent(image_width, image_height, old_angle)
                scale = min(box_width / old_width, box_height / old_height)
            else:
                # Розтягнуте зображення зі старого шаблону: під довільним кутом
                # показуємо його у власних пропорціях.
                base_width, base_height = (
                    (box_height, box_width) if old_odd else (box_width, box_height)
                )
                scale = min(base_width / image_width, base_height / image_height)
                element["preserve_aspect"] = True
            new_width, new_height = self._rotated_extent(
                image_width * scale, image_height * scale, new_angle
            )
        new_width = max(0.5, new_width)
        new_height = max(0.5, new_height)
        element["rotation"] = new_angle
        element["width"] = round(new_width, 2)
        element["height"] = round(new_height, 2)
        element["x"] = round(center[0] - new_width / 2, 2)
        element["y"] = round(center[1] - new_height / 2, 2)

    def _transform_targets(self):
        """Вибрані зображення, які можна повертати (усі, якщо вибрано кілька)."""
        images = [element for element in self._selected_elements() if element.get("type") == "image"]
        if not images:
            self.status_var.set("Спочатку виберіть зображення, QR-код або штрихкод")
            return []
        unlocked = [element for element in images if not element.get("locked")]
        if not unlocked:
            self.status_var.set("Елемент заблоковано — спочатку розблокуйте його")
        return unlocked

    def _selected_image_for_transform(self):
        targets = self._transform_targets()
        primary = self._element()
        return primary if primary in targets else (targets[0] if targets else None)

    def _rotate_selected(self, delta, event=None):
        if self._event_in_text_input(event) or self.inline_editor:
            return None
        targets = self._transform_targets()
        if targets:
            self._record_history()
            angle = 0.0
            for element in targets:
                angle = self._normalize_angle(element.get("rotation", 0)) + delta
                self._set_image_rotation(element, angle)
            self._render_all()
            self._load_properties()
            self.status_var.set(f"Поворот: {self._normalize_angle(angle):g}°")
        return "break" if event else None

    def _wheel_rotate(self, event, direction, big=False):
        """Коліщатко над вибраним фото повертає його на 1° (Shift — на 15°).

        Повороти поспіль — це одна дія в історії, і рамка рахується від початкового стану,
        тож картинка не «пливе» й не змінює розмір від багатьох дрібних кроків.
        """
        if self.inline_editor or self.drag_started or self.resize_state or self.rotate_state or self.marquee:
            return False
        targets = [
            element for element in self._selected_elements()
            if element.get("type") == "image" and not element.get("locked") and element.get("visible", True)
        ]
        if not targets:
            return False
        try:
            root_x = getattr(event, "x_root", None)
            root_y = getattr(event, "y_root", None)
            if not isinstance(root_x, int) or not isinstance(root_y, int):
                root_x, root_y = self.winfo_pointerx(), self.winfo_pointery()
            x = root_x - self.canvas.winfo_rootx()
            y = root_y - self.canvas.winfo_rooty()
        except tk.TclError:
            return False
        target_ids = {element["id"] for element in targets}
        hovered = False
        for item in self.canvas.find_overlapping(x - 2, y - 2, x + 2, y + 2):
            tags = set(self.canvas.gettags(item))
            if "rotate_handle" in tags or ("element" in tags and tags & target_ids):
                hovered = True
                break
        if not hovered:
            return False
        now = time.monotonic()
        ids = tuple(sorted(target_ids))
        state = self.wheel_rotate_state
        fresh = (
            not state or state["ids"] != ids or state["doc"] is not self.doc or now - state["time"] > 1.5
            or any(
                state["last"].get(element["id"])
                != (element["x"], element["y"], element["width"], element["height"], element.get("rotation"))
                for element in targets
            )
        )
        if fresh:
            self._record_history()
            state = {
                "ids": ids,
                "doc": self.doc,
                "delta": 0.0,
                "base": {element["id"]: copy.deepcopy(element) for element in targets},
                "centers": {
                    element["id"]: (
                        float(element["x"]) + float(element["width"]) / 2,
                        float(element["y"]) + float(element["height"]) / 2,
                    )
                    for element in targets
                },
            }
        state["time"] = now
        state["delta"] += direction * (15.0 if big else 1.0)
        angle = 0.0
        for element in targets:
            base = state["base"][element["id"]]
            for key in ("x", "y", "width", "height", "preserve_aspect", "rotation"):
                if key in base:
                    element[key] = base[key]
                else:
                    element.pop(key, None)
            angle = self._normalize_angle(base.get("rotation", 0)) + state["delta"]
            if big:
                angle = round(angle / 15.0) * 15.0
            self._set_image_rotation(element, angle, center=state["centers"][element["id"]])
            angle = element["rotation"]
        state["last"] = {
            element["id"]: (element["x"], element["y"], element["width"], element["height"], element.get("rotation"))
            for element in targets
        }
        self.wheel_rotate_state = state
        self._render_all()
        self._load_properties()
        self.status_var.set(f"Поворот: {angle:g}° · коліщатко ±1°, Shift+коліщатко ±15°")
        return True

    def _flip_selected(self, axis):
        targets = self._transform_targets()
        if not targets:
            return
        self._record_history()
        key = "flip_h" if axis == "h" else "flip_v"
        for element in targets:
            # Віддзеркалення відносно екрана: для вже поверненої картинки кут змінює знак.
            element[key] = not element.get(key)
            element["rotation"] = self._normalize_angle(-self._normalize_angle(element.get("rotation", 0)))
        self._render_all()
        self._load_properties()
        self.status_var.set(
            "Віддзеркалено по ширині" if axis == "h" else "Віддзеркалено по висоті"
        )

    def _reset_image_transform(self):
        targets = [element for element in self._transform_targets() if self._has_transform(element)]
        if not targets:
            return
        self._record_history()
        for element in targets:
            self._set_image_rotation(element, 0)
            element["flip_h"] = False
            element["flip_v"] = False
        self._render_all()
        self._load_properties()
        self.status_var.set("Поворот і віддзеркалення скинуто")

    def _start_rotate_drag(self, event):
        element = self._element()
        if not element or element.get("type") != "image" or element.get("locked"):
            return
        self._record_history()
        center_x = float(element["x"]) + float(element["width"]) / 2
        center_y = float(element["y"]) + float(element["height"]) / 2
        pointer = math.degrees(
            math.atan2(event.y - center_y * PX_PER_MM, event.x - center_x * PX_PER_MM)
        )
        self.rotate_state = {
            "center": (center_x, center_y),
            "pointer": pointer,
            "rotation": self._normalize_angle(element.get("rotation", 0)),
        }
        self.drag_start = None
        self.resize_state = None

    def _rotate_drag(self, event):
        state = self.rotate_state
        element = self._element()
        if not state or not element:
            return
        center_x, center_y = state["center"]
        pointer = math.degrees(
            math.atan2(event.y - center_y * PX_PER_MM, event.x - center_x * PX_PER_MM)
        )
        angle = state["rotation"] + pointer - state["pointer"]
        if event.state & 0x0001:
            angle = round(angle / 15.0) * 15.0
        else:
            nearest = round(angle / 90.0) * 90.0
            angle = nearest if abs(angle - nearest) <= 3 else round(angle)
        self._set_image_rotation(element, angle, center=state["center"])
        self._render_all()
        self._load_properties()
        self.status_var.set(f"Поворот: {self._normalize_angle(angle):g}° (Shift — крок 15°)")

    def _canvas_motion(self, event):
        """Підказувати курсором, що можна зробити в цій точці."""
        if self.drag_start or self.resize_state or self.rotate_state:
            return
        hits = self.canvas.find_overlapping(event.x - 1, event.y - 1, event.x + 1, event.y + 1)
        cursor = ""
        for item in reversed(hits):
            tags = self.canvas.gettags(item)
            if "rotate_handle" in tags:
                cursor = "exchange"
                break
            if "resize_handle" in tags:
                direction = next(
                    (tag[7:] for tag in tags if tag.startswith("resize_") and tag != "resize_handle"),
                    "",
                )
                cursor = {"n": "sb_v_double_arrow", "s": "sb_v_double_arrow",
                          "e": "sb_h_double_arrow", "w": "sb_h_double_arrow"}.get(direction, "sizing")
                break
            if "element" in tags:
                element_id = next((tag for tag in tags if tag not in ("element", "current")), None)
                element = self._element(element_id)
                cursor = "" if element and element.get("locked") else "fleur"
                break
        if str(self.canvas.cget("cursor")) != cursor:
            self.canvas.configure(cursor=cursor)

    def _layout_data(self, embed_images=False, doc=None):
        if doc is None or doc is self.doc:
            source, width, height, locked = self.elements, LABEL_WIDTH_MM, LABEL_HEIGHT_MM, self.layout_locked
        else:
            source, width, height, locked = doc.elements, doc.width, doc.height, doc.layout_locked
        elements = copy.deepcopy(source)
        for element in elements:
            if element.get("type") != "image":
                continue
            photo = element.get("photo")
            if photo:
                photo.pop("paint_data_b64", None)
                paint = photo.get("paint")
                if embed_images and paint and os.path.isfile(paint):
                    photo["paint_data_b64"] = base64.b64encode(Path(paint).read_bytes()).decode("ascii")
            if not embed_images:
                element.pop("image_data_b64", None)
                element.pop("image_name", None)
                element.pop("image_ext", None)
                continue
            image_path = Path(element.get("path", ""))
            if image_path.is_file():
                image_bytes = image_path.read_bytes()
                try:
                    with Image.open(io.BytesIO(image_bytes)) as image:
                        image.verify()
                except Exception as exc:
                    raise ValueError(f"Пошкоджене зображення {image_path.name}: {exc}") from exc
                suffix = image_path.suffix.lower()
                if suffix not in (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff"):
                    suffix = ".png"
                element["image_data_b64"] = base64.b64encode(image_bytes).decode("ascii")
                element["image_name"] = image_path.name
                element["image_ext"] = suffix
            elif not element.get("image_data_b64"):
                raise FileNotFoundError(f"Не знайдено зображення: {element.get('path', '')}")
        return {
            "version": 5,
            "label_width_mm": width,
            "label_height_mm": height,
            "layout_locked": bool(locked),
            "portable_images": True,
            "elements": elements,
        }

    def _save_layout(self):
        path = self.current_file or filedialog.asksaveasfilename(
            title="Зберегти макет",
            defaultextension=".json",
            filetypes=[("Макет наліпки", "*.json")],
            initialfile=f"{self._doc_title(self.doc)}.json" if self.doc else None,
        )
        if not path:
            return False
        try:
            payload = self._layout_data(embed_images=True)
            Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            messagebox.showerror("Збереження макета", str(exc))
            return False
        self.current_file = path
        self._mark_clean()
        self._update_title()
        self._remember_recent(path)
        self.status_var.set(f"Збережено: {path}")
        return True

    def _save_layout_as(self):
        previous = self.current_file
        self.current_file = None
        if not self._save_layout():
            self.current_file = previous
            return False
        return True

    # ---- Розмір наліпки та пресети ------------------------------------------------------

    @staticmethod
    def _validate_label_size(width, height):
        width = round(float(str(width).replace(",", ".")), 2)
        height = round(float(str(height).replace(",", ".")), 2)
        if not MIN_LABEL_MM <= width <= MAX_LABEL_WIDTH_MM:
            raise ValueError(
                f"Ширина наліпки має бути від {MIN_LABEL_MM:g} до {MAX_LABEL_WIDTH_MM:g} мм"
            )
        if not MIN_LABEL_MM <= height <= MAX_LABEL_HEIGHT_MM:
            raise ValueError(
                f"Висота наліпки має бути від {MIN_LABEL_MM:g} до {MAX_LABEL_HEIGHT_MM:g} мм"
            )
        return width, height

    def _load_size_presets(self):
        last = (LABEL_WIDTH_MM, LABEL_HEIGHT_MM)
        try:
            data = json.loads(self.presets_path.read_text(encoding="utf-8"))
        except Exception:
            return last
        for item in data.get("custom", []):
            try:
                name = str(item["name"]).strip()
                width, height = self._validate_label_size(item["width"], item["height"])
            except (KeyError, TypeError, ValueError):
                continue
            if name:
                self.custom_presets.append({"name": name, "width": width, "height": height})
        if data.get("theme") in THEMES:
            self.theme_name = data["theme"]
        recent = data.get("recent") or []
        if isinstance(recent, list):
            self.recent_files = [str(path) for path in recent if isinstance(path, str)][:MAX_RECENT_FILES]
        saved = data.get("last") or {}
        try:
            last = self._validate_label_size(saved["width"], saved["height"])
        except (KeyError, TypeError, ValueError):
            pass
        return last

    def _save_size_presets(self):
        payload = {
            "custom": self.custom_presets,
            "last": {"width": LABEL_WIDTH_MM, "height": LABEL_HEIGHT_MM},
            "theme": getattr(self, "theme_name", DEFAULT_THEME),
            "recent": getattr(self, "recent_files", []),
        }
        try:
            self.presets_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            self.status_var.set(f"Не вдалося зберегти пресети: {exc}")

    def _remember_last_size(self):
        self._save_size_presets()

    def _preset_entries(self):
        entries = [
            {"name": size_text(width, height), "width": width, "height": height, "custom": False}
            for width, height in BUILTIN_SIZE_PRESETS
        ]
        entries.extend(
            {"name": item["name"], "width": item["width"], "height": item["height"], "custom": True}
            for item in self.custom_presets
        )
        return entries

    @staticmethod
    def _preset_display(entry):
        if entry["custom"]:
            return f"★ {entry['name']} ({size_text(entry['width'], entry['height'])})"
        return entry["name"]

    def _refresh_size_combo(self):
        entries = self._preset_entries()
        self.size_preset_map = {self._preset_display(entry): entry for entry in entries}
        self.size_combo["values"] = list(self.size_preset_map)
        current = (LABEL_WIDTH_MM, LABEL_HEIGHT_MM)
        active = self.size_preset_map.get(self.active_preset_display)
        if active and (active["width"], active["height"]) == current:
            self.size_preset_var.set(self.active_preset_display)
            return
        for display, entry in self.size_preset_map.items():
            if (entry["width"], entry["height"]) == current:
                self.active_preset_display = display
                self.size_preset_var.set(display)
                return
        self.active_preset_display = None
        self.size_preset_var.set(f"Власний: {size_text(*current)}")

    def _set_label_size(self, width, height):
        """Лише змінює розмір полотна/макета, без запису в історію."""
        global LABEL_WIDTH_MM, LABEL_HEIGHT_MM
        LABEL_WIDTH_MM = float(width)
        LABEL_HEIGHT_MM = float(height)
        self._update_canvas_geometry()
        self._update_title()
        self.size_status_var.set(f"Наліпка: {size_text(LABEL_WIDTH_MM, LABEL_HEIGHT_MM)}")
        self._refresh_size_combo()

    def _apply_label_size(self, width, height):
        try:
            width, height = self._validate_label_size(width, height)
        except ValueError as exc:
            messagebox.showerror("Розмір наліпки", str(exc))
            return False
        if (width, height) == (LABEL_WIDTH_MM, LABEL_HEIGHT_MM):
            self._refresh_size_combo()
            return True
        self._record_history()
        self._set_label_size(width, height)
        self._render_all()
        self._remember_last_size()
        self._ensure_label_fits()
        outside = 0
        for element in self.elements:
            if not element.get("visible", True):
                continue
            element_width, element_height = self._element_size_mm(element)
            if (float(element["x"]) + element_width > LABEL_WIDTH_MM + 0.01
                    or float(element["y"]) + element_height > LABEL_HEIGHT_MM + 0.01):
                outside += 1
        message = f"Розмір наліпки: {size_text(width, height)}"
        if outside:
            message += f". Увага: елементів за межами наліпки — {outside}"
        self.status_var.set(message)
        return True

    def _size_preset_selected(self, _event=None):
        entry = self.size_preset_map.get(self.size_preset_var.get())
        if not entry:
            return
        self.active_preset_display = self.size_preset_var.get()
        self._apply_label_size(entry["width"], entry["height"])
        self.size_combo.selection_clear()
        self.canvas.focus_set()

    def _open_size_presets_dialog(self):
        if self.presets_dialog and self.presets_dialog.winfo_exists():
            self.presets_dialog.lift()
            self.presets_dialog.focus_set()
            return
        dialog = tk.Toplevel(self)
        self.presets_dialog = dialog
        dialog.title("Пресети розміру наліпок")
        self._themed(dialog, bg="panel")
        dialog.transient(self)
        dialog.resizable(False, False)

        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Розміри наліпок", style="Title.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 8)
        )
        listbox = tk.Listbox(
            frame,
            height=14,
            width=34,
            exportselection=False,
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            activestyle="none",
            font=(UI_FONT, 10),
        )
        self._themed(
            listbox, bg="field", fg="text", highlightbackground="border",
            highlightcolor="accent", selectbackground="accent", selectforeground="accent_text",
        )
        listbox.grid(row=1, column=0, rowspan=2, sticky="nsew")

        form = ttk.LabelFrame(frame, text="Пресет", padding=10)
        form.grid(row=1, column=1, sticky="new", padx=(14, 0))
        name_var = tk.StringVar()
        width_var = tk.StringVar(value=f"{LABEL_WIDTH_MM:g}")
        height_var = tk.StringVar(value=f"{LABEL_HEIGHT_MM:g}")
        ttk.Label(form, text="Назва").grid(row=0, column=0, sticky="w", pady=3)
        name_entry = ttk.Entry(form, textvariable=name_var, width=22)
        name_entry.grid(row=0, column=1, sticky="ew", pady=3)
        ttk.Label(form, text="Ширина, мм").grid(row=1, column=0, sticky="w", pady=3, padx=(0, 8))
        ttk.Entry(form, textvariable=width_var, width=10).grid(row=1, column=1, sticky="w", pady=3)
        ttk.Label(form, text="Висота, мм").grid(row=2, column=0, sticky="w", pady=3, padx=(0, 8))
        ttk.Entry(form, textvariable=height_var, width=10).grid(row=2, column=1, sticky="w", pady=3)

        buttons = ttk.Frame(form)
        buttons.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)
        ttk.Label(
            frame,
            text=(f"Вбудовані пресети не змінюються. Власні позначено ★.\n"
                  f"Ширина {MIN_LABEL_MM:g}–{MAX_LABEL_WIDTH_MM:g} мм, "
                  f"висота {MIN_LABEL_MM:g}–{MAX_LABEL_HEIGHT_MM:g} мм."),
            style="Hint.TLabel",
            justify="left",
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 0))

        state = {"entries": []}

        def fill_list(select_name=None):
            state["entries"] = self._preset_entries()
            listbox.delete(0, tk.END)
            current = (LABEL_WIDTH_MM, LABEL_HEIGHT_MM)
            select_index = None
            for index, entry in enumerate(state["entries"]):
                marker = "★ " if entry["custom"] else "    "
                text = entry["name"] if not entry["custom"] else (
                    f"{entry['name']}  —  {size_text(entry['width'], entry['height'])}"
                )
                if (entry["width"], entry["height"]) == current:
                    text += "   ✓"
                listbox.insert(tk.END, marker + text)
                if select_name is not None and entry["custom"] and entry["name"] == select_name:
                    select_index = index
            if select_index is not None:
                listbox.selection_clear(0, tk.END)
                listbox.selection_set(select_index)
                listbox.see(select_index)
            self._refresh_size_combo()

        def selected_entry():
            selection = listbox.curselection()
            if not selection:
                return None
            return state["entries"][selection[0]]

        def on_select(_event=None):
            entry = selected_entry()
            if not entry:
                return
            name_var.set(entry["name"] if entry["custom"] else "")
            width_var.set(f"{entry['width']:g}")
            height_var.set(f"{entry['height']:g}")

        def read_form(require_name=True):
            name = " ".join(name_var.get().split())
            if require_name and not name:
                raise ValueError("Вкажіть назву пресету, наприклад «Цінник 58×40»")
            width, height = self._validate_label_size(width_var.get(), height_var.get())
            return name, width, height

        def add_preset():
            try:
                name, width, height = read_form()
            except ValueError as exc:
                messagebox.showerror("Пресет", str(exc), parent=dialog)
                return
            if any(item["name"].casefold() == name.casefold() for item in self.custom_presets):
                messagebox.showerror("Пресет", f"Пресет «{name}» уже існує", parent=dialog)
                return
            self.custom_presets.append({"name": name, "width": width, "height": height})
            self._save_size_presets()
            fill_list(select_name=name)
            self.status_var.set(f"Додано пресет «{name}» ({size_text(width, height)})")

        def update_preset():
            entry = selected_entry()
            if not entry or not entry["custom"]:
                messagebox.showinfo("Пресет", "Виберіть у списку власний пресет (★)", parent=dialog)
                return
            try:
                name, width, height = read_form()
            except ValueError as exc:
                messagebox.showerror("Пресет", str(exc), parent=dialog)
                return
            if any(
                item["name"].casefold() == name.casefold() and item["name"] != entry["name"]
                for item in self.custom_presets
            ):
                messagebox.showerror("Пресет", f"Пресет «{name}» уже існує", parent=dialog)
                return
            for item in self.custom_presets:
                if item["name"] == entry["name"]:
                    item.update({"name": name, "width": width, "height": height})
                    break
            self._save_size_presets()
            fill_list(select_name=name)
            self.status_var.set(f"Пресет «{name}» оновлено")

        def delete_preset():
            entry = selected_entry()
            if not entry or not entry["custom"]:
                messagebox.showinfo("Пресет", "Видаляти можна лише власні пресети (★)", parent=dialog)
                return
            if not messagebox.askyesno(
                "Пресет", f"Видалити пресет «{entry['name']}»?", parent=dialog
            ):
                return
            self.custom_presets = [
                item for item in self.custom_presets if item["name"] != entry["name"]
            ]
            self._save_size_presets()
            fill_list()
            self.status_var.set(f"Пресет «{entry['name']}» видалено")

        def apply_size(_event=None):
            try:
                _name, width, height = read_form(require_name=False)
            except ValueError as exc:
                messagebox.showerror("Розмір наліпки", str(exc), parent=dialog)
                return
            entry = selected_entry()
            if entry and (entry["width"], entry["height"]) == (width, height):
                self.active_preset_display = self._preset_display(entry)
            if self._apply_label_size(width, height):
                selection = listbox.curselection()
                fill_list()
                if selection:
                    listbox.selection_set(selection[0])

        ttk.Button(buttons, text="+ Додати новий", command=add_preset, style="Tool.TButton").grid(
            row=0, column=0, sticky="ew", padx=(0, 2)
        )
        ttk.Button(buttons, text="Зберегти зміни", command=update_preset, style="Tool.TButton").grid(
            row=0, column=1, sticky="ew", padx=(2, 0)
        )
        ttk.Button(buttons, text="Видалити", command=delete_preset, style="Tool.TButton").grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0)
        )
        ttk.Button(
            buttons, text="Застосувати до макета", command=apply_size, style="Accent.TButton"
        ).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        ttk.Button(buttons, text="Закрити", command=dialog.destroy).grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=(4, 0)
        )

        listbox.bind("<<ListboxSelect>>", on_select)
        listbox.bind("<Double-Button-1>", lambda event: (on_select(), apply_size()))
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        fill_list()
        name_entry.focus_set()
        dialog.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - dialog.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - dialog.winfo_height()) // 3
        dialog.geometry(f"+{max(0, x)}+{max(0, y)}")

    def _load_layout(self):
        paths = filedialog.askopenfilenames(
            title="Відкрити макети (можна кілька)",
            filetypes=[("Макет наліпки", "*.json"), ("Усі файли", "*.*")],
        )
        if isinstance(paths, str):
            paths = self.tk.splitlist(paths) if paths else ()
        for path in paths:
            self._open_layout_file(path)

    def _open_layout_file(self, path):
        """Відкрити макет у новій вкладці (або перейти до вже відкритої)."""
        self._stash_document()
        target = os.path.normcase(os.path.abspath(path))
        for doc in self.documents:
            if doc.current_file and os.path.normcase(os.path.abspath(doc.current_file)) == target:
                self._activate_document(doc)
                self.status_var.set(f"Цей макет уже відкрито: {Path(path).name}")
                return True
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as exc:
            messagebox.showerror("Відкриття макета", str(exc))
            return False
        reuse = not self.elements and not self.current_file and not self.doc_dirty
        previous = self.doc
        if not reuse:
            self._new_tab()
        try:
            self._load_layout_payload(data)
        except Exception as exc:
            messagebox.showerror("Відкриття макета", str(exc))
            if not reuse:
                failed = self.doc
                self.doc = None
                self.documents.remove(failed)
                self._activate_document(previous)
            return False
        self.current_file = path
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._mark_clean()
        self._update_title()
        self._ensure_label_fits()
        self._remember_recent(path)
        self.status_var.set(f"Відкрито: {path}")
        return True

    def _load_layout_payload(self, data):
        elements = copy.deepcopy(data["elements"])
        for element in elements:
            element.setdefault("locked", False)
            element.setdefault("visible", True)
            if element["type"] == "image":
                element.setdefault("preserve_aspect", False)
                embedded = element.get("image_data_b64")
                if embedded:
                    element["path"] = str(self._materialize_embedded_image(element))
                    element.pop("image_data_b64", None)
                    element.pop("image_name", None)
                    element.pop("image_ext", None)
                elif not os.path.isfile(element.get("path", "")):
                    missing = element.get("path", "")
                    replacement = filedialog.askopenfilename(
                        title=f"Знайдіть зображення для старого шаблону: {Path(missing).name}",
                        filetypes=[
                            ("Зображення", IMAGE_FILETYPES),
                            ("Усі файли", "*.*"),
                        ],
                    )
                    if not replacement:
                        raise FileNotFoundError(f"Не знайдено зображення: {missing}")
                    try:
                        with Image.open(replacement) as image:
                            image.verify()
                    except Exception as exc:
                        raise ValueError(f"Не вдалося відкрити зображення: {exc}") from exc
                    element["path"] = os.path.abspath(replacement)
                photo = element.get("photo")
                if photo:
                    embedded_paint = photo.pop("paint_data_b64", None)
                    if embedded_paint:
                        photo["paint"] = str(self._materialize_paint_layer(embedded_paint))
                    elif photo.get("paint") and not os.path.isfile(photo["paint"]):
                        photo["paint"] = None
        try:
            size = self._validate_label_size(
                data.get("label_width_mm", 50.0), data.get("label_height_mm", 30.0)
            )
        except (TypeError, ValueError):
            size = (50.0, 30.0)
        self.elements = elements
        self.layout_locked = bool(data.get("layout_locked", False))
        self.selected_id = None
        self._set_label_size(*size)
        self._remember_last_size()
        self._render_all()
        self._load_properties()

    def _materialize_paint_layer(self, data_b64):
        try:
            data = base64.b64decode(data_b64, validate=True)
            with Image.open(io.BytesIO(data)) as image:
                image.verify()
        except Exception as exc:
            raise ValueError("Шар редагування фото в шаблоні пошкоджений") from exc
        folder = self.data_dir / "photo_paint"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{hashlib.sha256(data).hexdigest()}.png"
        if not target.is_file() or target.stat().st_size != len(data):
            target.write_bytes(data)
        return target

    def _materialize_embedded_image(self, element):
        try:
            image_bytes = base64.b64decode(element["image_data_b64"], validate=True)
            with Image.open(io.BytesIO(image_bytes)) as image:
                image.verify()
        except Exception as exc:
            raise ValueError("Вбудоване зображення у шаблоні пошкоджене") from exc
        suffix = str(element.get("image_ext", ".png")).lower()
        if suffix not in (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff"):
            suffix = ".png"
        digest = hashlib.sha256(image_bytes).hexdigest()
        asset_dir = self.data_dir / "template_assets"
        asset_dir.mkdir(parents=True, exist_ok=True)
        target = asset_dir / f"{digest}{suffix}"
        if not target.is_file() or target.stat().st_size != len(image_bytes):
            target.write_bytes(image_bytes)
        return target

    @staticmethod
    def _run_powershell(script, env=None, timeout=60):
        return subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    @staticmethod
    def _ps_literal(value):
        return "'" + str(value).replace("'", "''") + "'"

    @staticmethod
    def _driver_inf_path():
        resource_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        bundled = resource_root / "xprinter_driver" / "Xprinter.inf"
        if bundled.is_file():
            return bundled
        installed = Path(LOCAL_DRIVER_SOURCE) / "Xprinter.inf"
        return installed if installed.is_file() else None

    @classmethod
    def _run_elevated_powershell(cls, script, timeout=300):
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        wrapper = (
            "$args = @('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',"
            f"'-EncodedCommand','{encoded}');"
            "$p = Start-Process -FilePath 'powershell.exe' -Verb RunAs "
            "-ArgumentList $args -Wait -PassThru; exit $p.ExitCode"
        )
        return cls._run_powershell(wrapper, timeout=timeout)

    def _refresh_printers(self):
        script = r'''
$ErrorActionPreference = 'Stop'
$ports = @{}
Get-PrinterPort -ErrorAction SilentlyContinue | ForEach-Object {
    $ports[$_.Name] = $_
}
$items = @(
    Get-Printer -ErrorAction SilentlyContinue | ForEach-Object {
        $port = $ports[$_.PortName]
        [PSCustomObject]@{
            name = [string]$_.Name
            driver = [string]$_.DriverName
            port = [string]$_.PortName
            status = [string]$_.PrinterStatus
            host = if ($port) { [string]$port.PrinterHostAddress } else { '' }
            port_number = if ($port) { [int]$port.PortNumber } else { 0 }
        }
    }
)
@($items) | ConvertTo-Json -Compress
'''
        try:
            result = self._run_powershell(script, timeout=30)
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "Не вдалося прочитати черги")
            payload = json.loads(result.stdout.strip() or "[]")
            if isinstance(payload, dict):
                payload = [payload]
            self.printer_infos = {item["name"]: item for item in payload}
            names = sorted(self.printer_infos, key=str.casefold)
            self.printer_combo["values"] = names
            self._select_printer_for_mode()
            self.status_var.set(f"Знайдено черг принтера: {len(names)}")
        except Exception as exc:
            self.printer_infos = {}
            self.printer_combo["values"] = ()
            self.printer_var.set("")
            self.status_var.set(f"Не вдалося оновити принтери: {exc}")
        self._schedule_connection_check()

    def _select_printer_for_mode(self):
        mode = self.connection_var.get()
        ip = self.network_ip_var.get().strip()
        candidates = []
        for name, info in self.printer_infos.items():
            looks_like_xprinter = (
                "xprinter" in name.casefold()
                or "xp-420" in name.casefold()
                or "xprinter" in info.get("driver", "").casefold()
                or "xp-420" in info.get("driver", "").casefold()
            )
            if not looks_like_xprinter:
                continue
            if mode == "USB" and info.get("port", "").upper().startswith("USB"):
                candidates.append(name)
            elif mode == "NETWORK" and (
                info.get("host") == ip or info.get("port") == f"IP_{ip}"
            ):
                candidates.append(name)
        current = self.printer_var.get()
        if current not in candidates:
            self.printer_var.set(candidates[0] if candidates else "")

    def _connection_mode_changed(self):
        network = self.connection_var.get() == "NETWORK"
        self.network_ip_entry.configure(state="normal" if network else "disabled")
        self._select_printer_for_mode()
        self._schedule_connection_check()

    def _network_ip_changed(self, *_args):
        if getattr(self, "connection_var", None) and self.connection_var.get() == "NETWORK":
            self._select_printer_for_mode()
            self._schedule_connection_check()

    def _set_connection_status(self, state, text):
        self.connection_state = (state, text)
        color, prefix = COLORS[state], "● "
        self.connection_status_label.configure(text=prefix + text, fg=color)
        short = {
            "ok": "Принтер підключено",
            "error": "Принтер недоступний",
            "checking": "Перевірка принтера…",
        }[state]
        self.status_conn_label.configure(text=prefix + short, fg=color)

    def _schedule_connection_check(self, *_args):
        self.connection_after_id = None
        if self.status_check_running:
            return
        self.status_check_running = True
        mode = self.connection_var.get()
        printer = self.printer_var.get().strip()
        ip = self.network_ip_var.get().strip()
        self._set_connection_status("checking", "Перевірка реального підключення…")

        def worker():
            try:
                ok, details = self._probe_connection_values(mode, printer, ip)
            except Exception as exc:
                ok, details = False, f"Помилка перевірки: {exc}"
            self.after(0, self._connection_check_done, ok, details)

        threading.Thread(target=worker, daemon=True).start()

    def _connection_check_done(self, ok, details):
        self.status_check_running = False
        self.connection_ok = bool(ok)
        self.connection_details = details
        self._set_connection_status("ok" if ok else "error", details)
        if not getattr(self, "printing_active", False):
            self.print_button.configure(state="normal" if ok else "disabled")
        if self.connection_after_id:
            try:
                self.after_cancel(self.connection_after_id)
            except tk.TclError:
                pass
        self.connection_after_id = self.after(STATUS_REFRESH_MS, self._schedule_connection_check)

    def _probe_connection_values(self, mode, printer, ip):
        if not printer:
            return False, "Чергу XP-420B для вибраного підключення не знайдено"
        info = self.printer_infos.get(printer, {})
        driver = info.get("driver", "")
        if "xprinter" not in driver.casefold() and "xp-420" not in driver.casefold():
            return False, f"Неправильний драйвер черги: {driver or 'невідомий'}"

        if mode == "NETWORK":
            try:
                address = ipaddress.ip_address(ip)
                if address.version != 4:
                    raise ValueError
            except ValueError:
                return False, "Некоректна IPv4-адреса принтера"
            if info.get("host") != ip and info.get("port") != f"IP_{ip}":
                return False, f"Черга не прив’язана до IP {ip}"
            try:
                with socket.create_connection((ip, NETWORK_PORT), timeout=1.5):
                    pass
            except OSError:
                return False, f"Принтер не відповідає: {ip}:{NETWORK_PORT}"
            return True, f"Підключено: {printer} — {ip}:{NETWORK_PORT}"

        if not info.get("port", "").upper().startswith("USB"):
            return False, "Вибрана черга не використовує USB-порт"
        port_name = info.get("port", "")
        device_id = self._usb_device_id_for_port(port_name)
        if not device_id:
            return False, f"Windows не пов’язав {port_name} з USB-пристроєм"
        if not self._usb_device_present(device_id):
            return False, f"Черга є, але XP-420B фізично не підключений до {port_name}"
        return True, f"Підключено: {printer} — {port_name}"

    @staticmethod
    def _usb_device_id_for_port(port_name):
        if os.name != "nt" or not port_name:
            return ""
        key_path = (
            r"SYSTEM\CurrentControlSet\Control\Print\Monitors\USB Monitor\Ports"
            + "\\"
            + port_name
        )
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
                value, _kind = winreg.QueryValueEx(key, "Device Id")
                return str(value)
        except OSError:
            return ""

    @staticmethod
    def _usb_device_present(device_id):
        """Швидка фізична PnP-перевірка без повільного Get-PnpDevice."""
        if os.name != "nt" or not device_id:
            return False
        cfgmgr32 = ctypes.WinDLL("cfgmgr32", use_last_error=True)
        devinst = ctypes.c_ulong()
        # CM_Locate_DevNodeW з NORMAL повертає CR_NO_SUCH_DEVNODE для від'єднаного USB.
        result = cfgmgr32.CM_Locate_DevNodeW(
            ctypes.byref(devinst), ctypes.c_wchar_p(device_id), 0
        )
        return result == 0

    def _install_driver(self):
        inf_path = self._driver_inf_path()
        if not inf_path:
            messagebox.showerror("Драйвер", "Вбудований Xprinter.inf не знайдено")
            return
        if not messagebox.askyesno(
            "Встановлення драйвера",
            "Windows покаже запит адміністратора. Встановити підписаний драйвер XP-420B?",
        ):
            return
        inf_literal = self._ps_literal(inf_path)
        driver_literal = self._ps_literal(DRIVER_NAME)
        script = f'''
$ErrorActionPreference = 'Stop'
$inf = {inf_literal}
& "$env:SystemRoot\\System32\\pnputil.exe" /add-driver $inf /install
if ($LASTEXITCODE -ne 0) {{ throw "pnputil завершився з кодом $LASTEXITCODE" }}
if (-not (Get-PrinterDriver -Name {driver_literal} -ErrorAction SilentlyContinue)) {{
    Add-PrinterDriver -Name {driver_literal}
}}
'''
        self._run_admin_action("Встановлення драйвера", script)

    def _configure_network_printer(self):
        ip = self.network_ip_var.get().strip()
        try:
            address = ipaddress.ip_address(ip)
            if address.version != 4:
                raise ValueError
        except ValueError:
            messagebox.showerror("Мережевий принтер", "Вкажіть коректну IPv4-адресу")
            return
        inf_path = self._driver_inf_path()
        port_name = f"IP_{ip}"
        queue_name = f"Xprinter XP-420B (Network {ip})"
        driver_literal = self._ps_literal(DRIVER_NAME)
        port_literal = self._ps_literal(port_name)
        queue_literal = self._ps_literal(queue_name)
        ip_literal = self._ps_literal(ip)
        install_driver = ""
        if inf_path:
            install_driver = f'''
if (-not (Get-PrinterDriver -Name {driver_literal} -ErrorAction SilentlyContinue)) {{
    & "$env:SystemRoot\\System32\\pnputil.exe" /add-driver {self._ps_literal(inf_path)} /install
    if ($LASTEXITCODE -ne 0) {{ throw "pnputil завершився з кодом $LASTEXITCODE" }}
    Add-PrinterDriver -Name {driver_literal}
}}
'''
        script = f'''
$ErrorActionPreference = 'Stop'
{install_driver}
if (-not (Get-PrinterDriver -Name {driver_literal} -ErrorAction SilentlyContinue)) {{
    throw 'Драйвер Xprinter XP-420B не встановлено'
}}
if (-not (Get-PrinterPort -Name {port_literal} -ErrorAction SilentlyContinue)) {{
    Add-PrinterPort -Name {port_literal} -PrinterHostAddress {ip_literal} -PortNumber {NETWORK_PORT}
}}
$queue = Get-Printer -Name {queue_literal} -ErrorAction SilentlyContinue
if ($queue) {{
    Set-Printer -Name {queue_literal} -DriverName {driver_literal} -PortName {port_literal}
}} else {{
    Add-Printer -Name {queue_literal} -DriverName {driver_literal} -PortName {port_literal}
}}
'''
        self.connection_var.set("NETWORK")
        self._connection_mode_changed()
        self._run_admin_action("Налаштування мережевого принтера", script, queue_name)

    def _run_admin_action(self, title, script, queue_name=None):
        self._set_connection_status("checking", f"{title}…")

        def worker():
            try:
                result = self._run_elevated_powershell(script)
                if result.returncode != 0:
                    raise RuntimeError(
                        (result.stderr or result.stdout or "Операцію скасовано").strip()
                    )
                self.after(0, done, True, "Готово")
            except Exception as exc:
                self.after(0, done, False, str(exc))

        def done(success, message):
            if success:
                self._refresh_printers()
                if queue_name and queue_name in self.printer_infos:
                    self.printer_var.set(queue_name)
                messagebox.showinfo(title, "Операцію успішно завершено")
            else:
                messagebox.showerror(title, message)
            self._schedule_connection_check()

        threading.Thread(target=worker, daemon=True).start()

    def _layout_for_data_row(self, row):
        layout = self._layout_data()
        for element in layout["elements"]:
            if element.get("type") == "text":
                value = str(element.get("text", ""))
                for column, replacement in row.items():
                    value = value.replace(
                        "{" + str(column) + "}",
                        "" if replacement is None else str(replacement),
                    )
                element["text"] = value
            elif element.get("source_kind") in ("qr", "code128"):
                value = str(element.get("code_value", ""))
                for column, replacement in row.items():
                    value = value.replace(
                        "{" + str(column) + "}",
                        "" if replacement is None else str(replacement),
                    )
                element["code_value"] = value
                element["path"] = str(self._generate_code_file(element["source_kind"], value))
        return layout

    def _batch_print_csv(self):
        if not self.elements:
            messagebox.showwarning("Серійний друк", "Макет порожній")
            return
        printer = self.printer_var.get().strip()
        if not printer or not self.connection_ok:
            messagebox.showwarning(
                "Серійний друк",
                "Спочатку дочекайтеся зеленого індикатора підключення принтера.",
            )
            return
        path = filedialog.askopenfilename(
            title="Виберіть CSV-таблицю",
            filetypes=[("CSV-таблиця", "*.csv"), ("Текстові таблиці", "*.txt"), ("Усі файли", "*.*")],
        )
        if not path:
            return
        try:
            raw = None
            for encoding in ("utf-8-sig", "cp1251", "utf-16"):
                try:
                    raw = Path(path).read_text(encoding=encoding)
                    break
                except UnicodeError:
                    continue
            if raw is None:
                raise ValueError("Не вдалося визначити кодування CSV")
            try:
                dialect = csv.Sniffer().sniff(raw[:4096], delimiters=",;\t|")
            except csv.Error:
                dialect = csv.excel
            rows = list(csv.DictReader(raw.splitlines(), dialect=dialect))
            if not rows:
                raise ValueError("CSV не містить рядків даних")
            if not rows[0].keys():
                raise ValueError("CSV не містить заголовків колонок")
            copies = int(self.copies_var.get())
            if not 1 <= copies <= 99:
                raise ValueError("Кількість копій має бути від 1 до 99")
        except Exception as exc:
            messagebox.showerror("Серійний друк", str(exc))
            return

        layouts = []
        try:
            for row in rows:
                layouts.append(self._print_ready_layout(self._layout_for_data_row(row)))
        except Exception as exc:
            messagebox.showerror("Серійний друк", f"Не вдалося сформувати етикетки:\n{exc}")
            return

        total = len(layouts) * copies
        if not messagebox.askyesno(
            "Серійний друк",
            f"Знайдено рядків: {len(layouts)}. Буде надруковано етикеток: {total}. Продовжити?",
        ):
            return
        self.print_button.configure(state="disabled")
        self.printing_active = True
        self.status_var.set(f"Серійний друк: 0 із {total}")
        threading.Thread(
            target=self._batch_print_worker,
            args=(layouts, printer, copies, self.connection_var.get(), self.network_ip_var.get().strip()),
            daemon=True,
        ).start()

    def _print_ready_layout(self, layout):
        """Копія макета, де оброблені зображення замінено готовими PNG під роздільність принтера.

        Сам механізм друку не змінюється: він, як і раніше, малює файл у рамці елемента.
        """
        layout = copy.deepcopy(layout)
        folder = self.data_dir / "print_cache"
        for element in layout.get("elements", []):
            if element.get("type") != "image" or not element.get("visible", True):
                continue
            adjust = self._element_adjustments(element)
            if not (self._has_transform(element) or adjust or element.get("photo")
                    or image_orientation(element.get("path", "")) != 1):
                continue
            os.stat(element.get("path", ""))  # зрозуміла помилка, якщо файл зник
            key = hashlib.sha256(self._element_cache_key(
                element, float(element.get("width", 0)), float(element.get("height", 0)), "print"
            ).encode("utf-8")).hexdigest()[:32]
            target = folder / f"{key}.png"
            if not target.is_file():
                folder.mkdir(parents=True, exist_ok=True)
                # Точки (Ч/Б) — рівно 1:1 з точками принтера, решта — з запасом 2×.
                factor = 1 if self._output_mode(element, adjust)[0] else 2
                fit_box = (
                    float(element["width"]) * PRINTER_DOTS_PER_MM * factor,
                    float(element["height"]) * PRINTER_DOTS_PER_MM * factor,
                )
                self._element_bitmap(element, fit_box, adjust).save(target, "PNG")
            element["path"] = str(target)
        return layout

    def _batch_print_worker(self, layouts, printer, copies, mode, ip):
        completed_count = 0
        for layout in layouts:
            ok, message = self._print_worker(layout, printer, copies, mode, ip, notify=False)
            if not ok:
                self.after(0, self._print_done, False, message)
                return
            completed_count += copies
            self.after(0, self.status_var.set, f"Серійний друк: {completed_count} із {len(layouts) * copies}")
        self.after(0, self._print_done, True, f"Серійний друк завершено. Надруковано: {completed_count}")

    def _print_layout(self):
        if not self.elements:
            messagebox.showwarning("Друк", "Макет порожній")
            return
        printer = self.printer_var.get().strip()
        if not printer:
            messagebox.showwarning("Друк", "Виберіть принтер")
            return
        if not self.connection_ok:
            messagebox.showwarning(
                "Друк",
                "Принтер не підтвердив реальне підключення. Перевірте червоний індикатор.",
            )
            self._schedule_connection_check()
            return
        try:
            copies = int(self.copies_var.get())
            if not 1 <= copies <= 99:
                raise ValueError
        except ValueError:
            messagebox.showerror("Друк", "Кількість копій має бути від 1 до 99")
            return
        for element in self.elements:
            if not element.get("visible", True):
                continue
            if element["type"] == "image" and not os.path.isfile(element["path"]):
                messagebox.showerror("Друк", f"Не знайдено зображення:\n{element['path']}")
                return

        try:
            layout = self._print_ready_layout(self._layout_data())
        except Exception as exc:
            messagebox.showerror("Друк", f"Не вдалося підготувати зображення:\n{exc}")
            return

        self.print_button.configure(state="disabled")
        self.printing_active = True
        self.status_var.set("Надсилання на принтер…")
        mode = self.connection_var.get()
        ip = self.network_ip_var.get().strip()
        threading.Thread(
            target=self._print_worker,
            args=(layout, printer, copies, mode, ip),
            daemon=True,
        ).start()

    def _print_worker(self, layout, printer, copies, mode, ip, notify=True):
        temp_path = None
        try:
            connected, details = self._probe_connection_values(mode, printer, ip)
            if not connected:
                raise RuntimeError(details)
            layout = copy.deepcopy(layout)
            layout["elements"] = [
                element for element in layout.get("elements", []) if element.get("visible", True)
            ]
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", encoding="utf-8", delete=False
            ) as temp:
                json.dump(layout, temp, ensure_ascii=False)
                temp_path = temp.name
            env = os.environ.copy()
            env.update({
                "LABEL_DESIGNER_JSON": temp_path,
                "LABEL_DESIGNER_PRINTER": printer,
                "LABEL_DESIGNER_COPIES": str(copies),
            })
            script = r'''
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$layout = Get-Content -Raw -LiteralPath $env:LABEL_DESIGNER_JSON -Encoding UTF8 | ConvertFrom-Json
$printerName = $env:LABEL_DESIGNER_PRINTER
$doc = New-Object System.Drawing.Printing.PrintDocument
$doc.PrinterSettings.PrinterName = $printerName
if (-not $doc.PrinterSettings.IsValid) { throw "Printer '$printerName' is unavailable" }
$doc.PrintController = New-Object System.Drawing.Printing.StandardPrintController
$doc.OriginAtMargins = $false
$doc.DefaultPageSettings.Margins = New-Object System.Drawing.Printing.Margins(0, 0, 0, 0)
$doc.DefaultPageSettings.Landscape = $false
$driverWidthMm = [double]$layout.label_width_mm
# Драйвер XP-420B віднімає приблизно 2 мм з кожного боку. Запит 54 мм
# дає фактичну друковану ширину близько 50 мм без масштабування макета.
if ($printerName -like '*XP-420B*') { $driverWidthMm += 4.0 }
$paperW = [int][Math]::Round($driverWidthMm / 25.4 * 100)
$paperH = [int][Math]::Round([double]$layout.label_height_mm / 25.4 * 100)
$doc.DefaultPageSettings.PaperSize = New-Object System.Drawing.Printing.PaperSize('Custom label', $paperW, $paperH)
$brush = [System.Drawing.Brushes]::Black
$handler = {
    param($sender, $e)
    $e.Graphics.PageUnit = [System.Drawing.GraphicsUnit]::Millimeter
    $e.Graphics.Clear([System.Drawing.Color]::White)
    $e.Graphics.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality
    $e.Graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $e.Graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $e.Graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
    $e.Graphics.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
    foreach ($item in $layout.elements) {
        if ($item.type -eq 'text') {
            $style = if ([bool]$item.bold) { [System.Drawing.FontStyle]::Bold } else { [System.Drawing.FontStyle]::Regular }
            $font = New-Object System.Drawing.Font([string]$item.font, [single]$item.size, $style, [System.Drawing.GraphicsUnit]::Point)
            try {
                $e.Graphics.DrawString([string]$item.text, $font, $brush, [single]$item.x, [single]$item.y)
            } finally { $font.Dispose() }
        } elseif ($item.type -eq 'image') {
            $image = [System.Drawing.Image]::FromFile([string]$item.path)
            try {
                $x = [single]$item.x
                $y = [single]$item.y
                $width = [single]$item.width
                $height = [single]$item.height
                if ([bool]$item.preserve_aspect -and $image.Width -gt 0 -and $image.Height -gt 0) {
                    $scale = [Math]::Min($width / [single]$image.Width, $height / [single]$image.Height)
                    $drawWidth = [single]($image.Width * $scale)
                    $drawHeight = [single]($image.Height * $scale)
                    $drawX = [single]($x + ($width - $drawWidth) / 2)
                    $drawY = [single]($y + ($height - $drawHeight) / 2)
                    $e.Graphics.DrawImage($image, $drawX, $drawY, $drawWidth, $drawHeight)
                } else {
                    $e.Graphics.DrawImage($image, $x, $y, $width, $height)
                }
            } finally { $image.Dispose() }
        }
    }
    $e.HasMorePages = $false
}
$doc.add_PrintPage($handler)
try {
    $copies = [int]$env:LABEL_DESIGNER_COPIES
    for ($copy = 1; $copy -le $copies; $copy++) {
        $doc.DocumentName = "Custom label $copy of $copies"
        $doc.Print()
    }
} finally {
    $doc.remove_PrintPage($handler)
    $doc.Dispose()
}
'''
            completed = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
                env=env,
                capture_output=True,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if completed.returncode != 0:
                raise RuntimeError((completed.stderr or completed.stdout or "Невідома помилка").strip())
            message = f"Надруковано копій: {copies}"
            if notify:
                self.after(0, self._print_done, True, message)
            return True, message
        except Exception as exc:
            message = str(exc)
            if notify:
                self.after(0, self._print_done, False, message)
            return False, message
        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

    def _print_done(self, success, message):
        self.printing_active = False
        self.print_button.configure(state="normal" if self.connection_ok else "disabled")
        self.status_var.set(message)
        if success:
            messagebox.showinfo("Друк", message)
        else:
            messagebox.showerror("Помилка друку", message)
        self._schedule_connection_check()


if __name__ == "__main__":
    LabelDesigner().mainloop()
