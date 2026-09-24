#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Редактор та друк наліпок (типово 50x30 мм, є власні пресети) для Windows/Xprinter."""

import base64
import copy
import csv
import ctypes
import hashlib
import io
import ipaddress
import json
import math
import os
import socket
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import uuid
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter import font as tkfont

if os.name == "nt":
    import winreg

from PIL import Image, ImageTk
import barcode
import qrcode
from barcode.writer import ImageWriter


APP_TITLE = "Редактор наліпок {size} — Xprinter"
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
COLORS = {
    "panel": "#ffffff",
    "soft": "#f4f6f9",
    "border": "#d8dee6",
    "text": "#1f2933",
    "muted": "#6b7785",
    "accent": "#1f6feb",
    "accent_hover": "#1a5fd0",
    "accent_pressed": "#154fae",
    "accent_soft": "#e8f0fe",
    "workspace": "#d3d8df",
    "shadow": "#aeb6c1",
    "status": "#eef1f5",
    "ok": "#087f23",
    "error": "#c00000",
    "checking": "#9a6700",
}
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
            bg="#1f2933",
            fg="#ffffff",
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


class LabelDesigner(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE.format(size=size_text(LABEL_WIDTH_MM, LABEL_HEIGHT_MM)))
        self.geometry("1280x840")
        self.minsize(1120, 740)

        self.elements = []
        self.selected_id = None
        self.canvas_items = {}
        self.photo_refs = {}
        self.drag_start = None
        self.drag_origin = None
        self.drag_started = False
        self.resize_state = None
        self.rotate_state = None
        self.image_size_cache = {}
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
        self.clipboard_element = None
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
        last_size = self._load_size_presets()

        self._build_ui()
        try:
            self._set_label_size(*self._validate_label_size(*last_size))
        except (TypeError, ValueError):
            self._set_label_size(LABEL_WIDTH_MM, LABEL_HEIGHT_MM)
        self._refresh_printers()
        self._new_layout(confirm=False)
        self.autosave_suspended = False
        self.after(250, self._offer_autosave_recovery)
        self.connection_after_id = self.after(500, self._schedule_connection_check)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _setup_style(self):
        self.configure(bg=COLORS["panel"])
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            try:
                tkfont.nametofont(name).configure(family=UI_FONT, size=10)
            except tk.TclError:
                pass
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        panel = COLORS["panel"]
        border = COLORS["border"]
        accent = COLORS["accent"]
        style.configure(
            ".",
            background=panel,
            foreground=COLORS["text"],
            font=(UI_FONT, 10),
            bordercolor=border,
            lightcolor=panel,
            darkcolor=panel,
            focuscolor=accent,
            troughcolor=COLORS["soft"],
        )
        style.configure("TFrame", background=panel)
        style.configure("TLabel", background=panel)
        style.configure("Title.TLabel", font=(UI_FONT, 11, "bold"))
        style.configure("Caption.TLabel", foreground=COLORS["muted"], font=(UI_FONT, 8))
        style.configure("Hint.TLabel", foreground=COLORS["muted"], font=(UI_FONT, 9))
        style.configure("Ok.TLabel", foreground="#4b6b4b", font=(UI_FONT, 9))
        style.configure("Status.TFrame", background=COLORS["status"])
        style.configure("Status.TLabel", background=COLORS["status"], foreground=COLORS["text"], font=(UI_FONT, 9))
        style.configure(
            "TLabelframe", background=panel, bordercolor=border, relief="solid", borderwidth=1
        )
        style.configure(
            "TLabelframe.Label", background=panel, foreground=COLORS["muted"], font=(UI_FONT, 9, "bold")
        )
        # width=0: кнопки за шириною тексту, а не з мінімальною шириною теми clam.
        for name, pad in (("TButton", (10, 5)), ("Tool.TButton", (7, 4))):
            style.configure(
                name,
                padding=pad,
                width=0,
                background=COLORS["soft"],
                bordercolor=border,
                lightcolor=COLORS["soft"],
                darkcolor=COLORS["soft"],
                focusthickness=0,
            )
            style.map(
                name,
                background=[("pressed", "#d4e3fc"), ("active", COLORS["accent_soft"])],
                lightcolor=[("pressed", "#d4e3fc"), ("active", COLORS["accent_soft"])],
                darkcolor=[("pressed", "#d4e3fc"), ("active", COLORS["accent_soft"])],
                bordercolor=[("active", "#9ec0f5")],
            )
        style.configure(
            "Accent.TButton",
            padding=(12, 9),
            background=accent,
            foreground="#ffffff",
            bordercolor=accent,
            lightcolor=accent,
            darkcolor=accent,
            font=(UI_FONT, 11, "bold"),
        )
        style.map(
            "Accent.TButton",
            background=[("disabled", "#a9c3ea"), ("pressed", COLORS["accent_pressed"]),
                        ("active", COLORS["accent_hover"])],
            lightcolor=[("disabled", "#a9c3ea"), ("pressed", COLORS["accent_pressed"]),
                        ("active", COLORS["accent_hover"])],
            darkcolor=[("disabled", "#a9c3ea"), ("pressed", COLORS["accent_pressed"]),
                       ("active", COLORS["accent_hover"])],
            bordercolor=[("disabled", "#a9c3ea")],
            foreground=[("disabled", "#f3f7fd")],
        )
        for name in ("TEntry", "TCombobox", "TSpinbox"):
            style.configure(
                name, fieldbackground="#ffffff", bordercolor=border, lightcolor=border,
                darkcolor=border, padding=4, arrowsize=13,
            )
            style.map(name, bordercolor=[("focus", accent)], lightcolor=[("focus", accent)])
        style.map("TCombobox", fieldbackground=[("readonly", "#ffffff")])
        style.configure("TNotebook", background=panel, borderwidth=0, tabmargins=(0, 0, 0, 0))
        style.configure(
            "TNotebook.Tab", padding=(16, 7), background=COLORS["soft"], bordercolor=border,
            lightcolor=COLORS["soft"], font=(UI_FONT, 10),
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", panel)],
            lightcolor=[("selected", panel)],
            foreground=[("selected", accent)],
        )
        style.configure("TCheckbutton", background=panel)
        style.configure("TRadiobutton", background=panel)
        style.configure("Horizontal.TScale", background=panel, troughcolor=COLORS["soft"])

    def _toolbar_group(self, parent, caption, separator=True, side="left"):
        outer = ttk.Frame(parent)
        outer.pack(side=side, fill="y")
        buttons = ttk.Frame(outer)
        buttons.pack(side="top")
        ttk.Label(outer, text=caption, style="Caption.TLabel").pack(side="top", pady=(3, 0))
        if separator:
            ttk.Separator(parent, orient="vertical").pack(side=side, fill="y", padx=8)
        return buttons

    @staticmethod
    def _tool_button(parent, text, command, tip=None, style="Tool.TButton", side="left"):
        button = ttk.Button(parent, text=text, command=command, style=style)
        button.pack(side=side, padx=1)
        if tip:
            ToolTip(button, tip)
        return button

    def _build_menu(self):
        menubar = tk.Menu(self)
        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="Новий макет", accelerator="Ctrl+N", command=self._new_layout)
        file_menu.add_command(label="Відкрити…", accelerator="Ctrl+O", command=self._load_layout)
        file_menu.add_command(label="Зберегти", accelerator="Ctrl+S", command=self._save_layout)
        file_menu.add_command(label="Зберегти як…", accelerator="Ctrl+Shift+S", command=self._save_layout_as)
        file_menu.add_separator()
        file_menu.add_command(label="Вихід", command=self._on_close)
        menubar.add_cascade(label="Файл", menu=file_menu)

        edit_menu = tk.Menu(menubar, tearoff=False)
        edit_menu.add_command(label="Скасувати", accelerator="Ctrl+Z", command=self._undo)
        edit_menu.add_command(label="Повторити", accelerator="Ctrl+Y", command=self._redo)
        edit_menu.add_separator()
        edit_menu.add_command(label="Копіювати елемент", accelerator="Ctrl+C", command=self._copy_selected)
        edit_menu.add_command(
            label="Вставити (текст, картинку або елемент)", accelerator="Ctrl+V",
            command=self._paste_element,
        )
        edit_menu.add_command(label="Дублювати", accelerator="Ctrl+D", command=self._duplicate_selected)
        edit_menu.add_command(label="Видалити", accelerator="Del", command=self._delete_selected)
        edit_menu.add_separator()
        edit_menu.add_command(label="Заблокувати / розблокувати елемент", command=self._toggle_selected_lock)
        edit_menu.add_command(label="Заблокувати / розблокувати макет", command=self._toggle_layout_lock)
        menubar.add_cascade(label="Правка", menu=edit_menu)

        insert_menu = tk.Menu(menubar, tearoff=False)
        insert_menu.add_command(label="Текст", command=self._add_text)
        insert_menu.add_command(label="Зображення з файлу…", command=self._add_image)
        insert_menu.add_command(label="QR-код…", command=self._add_qr)
        insert_menu.add_command(label="Штрихкод Code 128…", command=self._add_barcode)
        insert_menu.add_separator()
        insert_menu.add_command(
            label="З буфера обміну", accelerator="Ctrl+V", command=self._paste_element
        )
        menubar.add_cascade(label="Вставка", menu=insert_menu)

        image_menu = tk.Menu(menubar, tearoff=False)
        image_menu.add_command(
            label="Повернути за годинниковою ⟳ 90°", accelerator="Ctrl+R",
            command=lambda: self._rotate_selected(90),
        )
        image_menu.add_command(
            label="Повернути проти годинникової ⟲ 90°", accelerator="Ctrl+Shift+R",
            command=lambda: self._rotate_selected(-90),
        )
        image_menu.add_command(label="Повернути на 180°", command=lambda: self._rotate_selected(180))
        image_menu.add_separator()
        image_menu.add_command(label="Віддзеркалити по ширині ⇆", command=lambda: self._flip_selected("h"))
        image_menu.add_command(label="Віддзеркалити по висоті ⇅", command=lambda: self._flip_selected("v"))
        image_menu.add_separator()
        image_menu.add_command(label="Скинути поворот і віддзеркалення", command=self._reset_image_transform)
        menubar.add_cascade(label="Зображення", menu=image_menu)

        view_menu = tk.Menu(menubar, tearoff=False)
        view_menu.add_checkbutton(label="Сітка", variable=self.show_grid_var, command=self._render_all)
        view_menu.add_checkbutton(label="Прив’язка", variable=self.snap_var)
        view_menu.add_checkbutton(
            label="Безпечні поля", variable=self.safe_margin_var, command=self._render_all
        )
        view_menu.add_separator()
        for level in ZOOM_LEVELS:
            view_menu.add_radiobutton(
                label=f"Масштаб {level}", value=level, variable=self.zoom_var, command=self._set_zoom
            )
        menubar.add_cascade(label="Вигляд", menu=view_menu)

        label_menu = tk.Menu(menubar, tearoff=False)
        label_menu.add_command(label="Пресети розміру…", command=self._open_size_presets_dialog)
        menubar.add_cascade(label="Наліпка", menu=label_menu)
        self.config(menu=menubar)

    def _build_ui(self):
        self._setup_style()
        self.show_grid_var = tk.BooleanVar(value=True)
        self.snap_var = tk.BooleanVar(value=True)
        self.safe_margin_var = tk.BooleanVar(value=True)
        self.zoom_var = tk.StringVar(value="100%")
        self.size_preset_var = tk.StringVar()
        self.size_status_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Готово")
        self._build_menu()

        # Рядок стану пакуємо першим, щоб він завжди лишався видимим унизу вікна.
        statusbar = ttk.Frame(self, style="Status.TFrame", padding=(10, 4))
        statusbar.pack(side="bottom", fill="x")
        self.status_conn_label = tk.Label(
            statusbar, text="● Перевірка принтера…", fg=COLORS["checking"],
            bg=COLORS["status"], font=(UI_FONT, 9),
        )
        self.status_conn_label.pack(side="right", padx=(14, 0))
        ttk.Label(statusbar, textvariable=self.size_status_var, style="Status.TLabel").pack(
            side="right", padx=(14, 0)
        )
        ttk.Label(statusbar, textvariable=self.status_var, style="Status.TLabel").pack(
            side="left", fill="x", expand=True
        )

        # ---- Верхня панель інструментів -------------------------------------------------
        toolbar = ttk.Frame(self, padding=(10, 8, 10, 6))
        toolbar.pack(fill="x")
        size_group = self._toolbar_group(toolbar, "Розмір наліпки", separator=False, side="right")
        self.size_combo = ttk.Combobox(
            size_group, textvariable=self.size_preset_var, state="readonly", width=17
        )
        self.size_combo.pack(side="left", padx=(0, 4), ipady=1)
        self.size_combo.bind("<<ComboboxSelected>>", self._size_preset_selected)
        self._tool_button(
            size_group, "⚙", self._open_size_presets_dialog,
            "Додати, змінити або видалити власні розміри наліпок",
        )

        group = self._toolbar_group(toolbar, "Файл")
        self._tool_button(group, "Новий", self._new_layout, "Новий макет (Ctrl+N)")
        self._tool_button(group, "Відкрити", self._load_layout, "Відкрити макет (Ctrl+O)")
        self._tool_button(group, "Зберегти", self._save_layout, "Зберегти макет (Ctrl+S)")

        group = self._toolbar_group(toolbar, "Додати")
        self._tool_button(group, "+ Текст", self._add_text, "Додати текстовий блок")
        self._tool_button(group, "+ Зображення", self._add_image, "Додати картинку з файлу")
        self._tool_button(group, "+ QR", self._add_qr, "Додати QR-код")
        self._tool_button(group, "+ Штрихкод", self._add_barcode, "Додати штрихкод Code 128")
        self._tool_button(
            group, "Вставити", self._paste_element,
            "Ctrl+V — вставити текст або картинку з буфера обміну\n"
            "(скриншот, фото, текст із Word/браузера, файл зображення)",
        )

        group = self._toolbar_group(toolbar, "Правка")
        self._tool_button(group, "↶", self._undo, "Скасувати (Ctrl+Z)")
        self._tool_button(group, "↷", self._redo, "Повторити (Ctrl+Y)")
        self._tool_button(group, "Дублювати", self._duplicate_selected, "Дублювати елемент (Ctrl+D)")
        self._tool_button(group, "Видалити", self._delete_selected, "Видалити елемент (Delete)")

        tk.Frame(self, bg=COLORS["border"], height=1).pack(fill="x")

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)

        # ---- Права панель ----------------------------------------------------------------
        side = ttk.Frame(body, width=392, padding=(12, 10, 12, 10))
        side.pack(side="right", fill="y")
        side.pack_propagate(False)
        tk.Frame(body, bg=COLORS["border"], width=1).pack(side="right", fill="y")

        # ---- Робоча область --------------------------------------------------------------
        workspace = ttk.Frame(body)
        workspace.pack(side="left", fill="both", expand=True)

        viewbar = ttk.Frame(workspace, padding=(10, 6))
        viewbar.pack(fill="x")
        ttk.Label(viewbar, text="Масштаб").pack(side="left", padx=(0, 4))
        zoom_combo = ttk.Combobox(
            viewbar, textvariable=self.zoom_var, values=ZOOM_LEVELS, state="readonly", width=6
        )
        zoom_combo.pack(side="left")
        zoom_combo.bind("<<ComboboxSelected>>", self._set_zoom)
        ToolTip(zoom_combo, "Масштаб перегляду (Ctrl + коліщатко миші)")
        ttk.Separator(viewbar, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Checkbutton(
            viewbar, text="Сітка", variable=self.show_grid_var, command=self._render_all
        ).pack(side="left", padx=4)
        ttk.Checkbutton(viewbar, text="Прив’язка", variable=self.snap_var).pack(side="left", padx=4)
        ttk.Checkbutton(
            viewbar, text="Безпечні поля", variable=self.safe_margin_var, command=self._render_all
        ).pack(side="left", padx=4)
        self._tool_button(
            viewbar, "🔒 Макет", self._toggle_layout_lock,
            "Заблокувати / розблокувати всі елементи макета", side="right",
        )
        tk.Frame(workspace, bg=COLORS["border"], height=1).pack(fill="x")

        ttk.Label(
            workspace,
            text=("Тягніть мишкою  •  сині маркери — розмір (Shift — пропорції)  •  "
                  "↻ — поворот  •  Ctrl+V — вставити текст/картинку"),
            style="Hint.TLabel",
            padding=(10, 5),
        ).pack(side="bottom", fill="x")
        tk.Frame(workspace, bg=COLORS["border"], height=1).pack(side="bottom", fill="x")

        canvas_holder = tk.Frame(workspace, bg=COLORS["workspace"])
        canvas_holder.pack(fill="both", expand=True)
        canvas_holder.rowconfigure(0, weight=1)
        canvas_holder.columnconfigure(0, weight=1)
        self.canvas_view = tk.Canvas(canvas_holder, bg=COLORS["workspace"], highlightthickness=0)
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
            highlightbackground="#8a94a3",
        )
        self.canvas_window = self.canvas_view.create_window(32, 32, window=self.canvas, anchor="nw")
        self.canvas_view.bind("<Configure>", self._center_canvas)
        self.canvas.bind("<Button-1>", self._canvas_click)
        self.canvas.bind("<Double-Button-1>", self._canvas_double_click)
        self.canvas.bind("<B1-Motion>", self._canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self._canvas_release)
        self.canvas.bind("<Motion>", self._canvas_motion)
        for widget in (self.canvas_view, self.canvas):
            widget.bind("<MouseWheel>", self._mouse_wheel)
            widget.bind("<Control-MouseWheel>", self._mouse_wheel_zoom)
            widget.bind("<Shift-MouseWheel>", self._mouse_wheel_horizontal)
            widget.bind("<Button-4>", self._mouse_wheel)
            widget.bind("<Button-5>", self._mouse_wheel)

        notebook = ttk.Notebook(side)
        notebook.pack(fill="both", expand=True)
        props_tab = ttk.Frame(notebook, padding=(4, 10, 4, 4))
        layers_tab = ttk.Frame(notebook, padding=(4, 10, 4, 4))
        print_tab = ttk.Frame(notebook, padding=(4, 10, 4, 4))
        notebook.add(props_tab, text="Властивості")
        notebook.add(layers_tab, text="Шари")
        notebook.add(print_tab, text="Друк")

        # ---- Вкладка «Властивості» ---------------------------------------------------------
        props_tab.columnconfigure(0, weight=1)
        self.type_var = tk.StringVar(value="Нічого не вибрано")
        ttk.Label(props_tab, textvariable=self.type_var, style="Title.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 8)
        )
        self.empty_hint = ttk.Label(
            props_tab,
            text=("Виберіть елемент на полотні\nабо додайте новий кнопками «Додати».\n\n"
                  "Порада: скопіюйте текст чи картинку в будь-якій програмі\n"
                  "і натисніть тут Ctrl+V."),
            style="Hint.TLabel",
            justify="left",
        )
        self.empty_hint.grid(row=1, column=0, sticky="nw", pady=(6, 0))
        self.element_panel = ttk.Frame(props_tab)
        self.element_panel.grid(row=2, column=0, sticky="nsew")
        self.element_panel.columnconfigure(0, weight=1)

        position = ttk.LabelFrame(self.element_panel, text="Положення, мм", padding=8)
        position.grid(row=0, column=0, sticky="ew")
        self.x_var = tk.StringVar()
        self.y_var = tk.StringVar()
        ttk.Label(position, text="X").grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(position, textvariable=self.x_var, width=9).grid(row=0, column=1, sticky="ew")
        ttk.Label(position, text="Y").grid(row=0, column=2, sticky="w", padx=(14, 6))
        ttk.Entry(position, textvariable=self.y_var, width=9).grid(row=0, column=3, sticky="ew")
        position.columnconfigure(1, weight=1)
        position.columnconfigure(3, weight=1)

        self.text_frame = ttk.LabelFrame(self.element_panel, text="Текст", padding=8)
        self.text_frame.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(self.text_frame, text="Текст").grid(row=0, column=0, sticky="w", pady=3)
        self.text_var = tk.StringVar()
        ttk.Entry(self.text_frame, textvariable=self.text_var).grid(row=0, column=1, sticky="ew")
        ttk.Label(self.text_frame, text="Шрифт").grid(row=1, column=0, sticky="w", pady=3)
        self.font_var = tk.StringVar(value="Tahoma")
        ttk.Combobox(
            self.text_frame,
            textvariable=self.font_var,
            values=("Tahoma", "Arial", "Segoe UI", "Calibri", "Times New Roman"),
            state="normal",
        ).grid(row=1, column=1, sticky="ew")
        ttk.Label(self.text_frame, text="Кегль, pt").grid(row=2, column=0, sticky="w", pady=3, padx=(0, 8))
        self.size_var = tk.StringVar(value="12")
        ttk.Spinbox(
            self.text_frame, from_=4, to=200, increment=1, textvariable=self.size_var
        ).grid(row=2, column=1, sticky="ew")
        self.bold_var = tk.BooleanVar()
        ttk.Checkbutton(self.text_frame, text="Жирний", variable=self.bold_var).grid(
            row=3, column=1, sticky="w", pady=3
        )
        self.text_frame.columnconfigure(1, weight=1)

        self.image_frame = ttk.Frame(self.element_panel)
        self.image_frame.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        self.image_frame.columnconfigure(0, weight=1)
        dims = ttk.LabelFrame(self.image_frame, text="Розмір рамки, мм", padding=8)
        dims.grid(row=0, column=0, sticky="ew")
        self.width_var = tk.StringVar()
        self.height_var = tk.StringVar()
        ttk.Label(dims, text="Ш").grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(dims, textvariable=self.width_var, width=9).grid(row=0, column=1, sticky="ew")
        ttk.Label(dims, text="В").grid(row=0, column=2, sticky="w", padx=(14, 6))
        ttk.Entry(dims, textvariable=self.height_var, width=9).grid(row=0, column=3, sticky="ew")
        dims.columnconfigure(1, weight=1)
        dims.columnconfigure(3, weight=1)
        self.image_path_var = tk.StringVar()
        ttk.Label(dims, textvariable=self.image_path_var, wraplength=320, style="Hint.TLabel").grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(6, 0)
        )
        ttk.Button(
            dims,
            text="Замінити зображення…",
            command=self._replace_image,
            style="Tool.TButton",
        ).grid(row=2, column=0, columnspan=4, sticky="ew", pady=(6, 0))

        rotate = ttk.LabelFrame(self.image_frame, text="Поворот і віддзеркалення", padding=8)
        rotate.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        rotate.columnconfigure(1, weight=1)
        turn_row = ttk.Frame(rotate)
        turn_row.grid(row=0, column=0, columnspan=3, sticky="ew")
        for column, (text, command, tip) in enumerate((
            ("⟲ 90°", lambda: self._rotate_selected(-90), "Проти годинникової стрілки (Ctrl+Shift+R)"),
            ("⟳ 90°", lambda: self._rotate_selected(90), "За годинниковою стрілкою (Ctrl+R)"),
            ("↻ 180°", lambda: self._rotate_selected(180), "Перевернути догори дном"),
        )):
            button = ttk.Button(turn_row, text=text, command=command, style="Tool.TButton")
            button.grid(row=0, column=column, sticky="ew", padx=1)
            ToolTip(button, tip)
            turn_row.columnconfigure(column, weight=1)
        flip_row = ttk.Frame(rotate)
        flip_row.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(4, 0))
        for column, (text, axis, tip) in enumerate((
            ("⇆ По ширині", "h", "Віддзеркалити зліва направо"),
            ("⇅ По висоті", "v", "Віддзеркалити згори донизу"),
        )):
            button = ttk.Button(
                flip_row, text=text, command=lambda a=axis: self._flip_selected(a), style="Tool.TButton"
            )
            button.grid(row=0, column=column, sticky="ew", padx=1)
            ToolTip(button, tip)
            flip_row.columnconfigure(column, weight=1)
        ttk.Label(rotate, text="Кут, °").grid(row=2, column=0, sticky="w", pady=(8, 0), padx=(0, 8))
        self.rotation_var = tk.StringVar(value="0")
        ttk.Spinbox(
            rotate, from_=0, to=359, increment=1, wrap=True, textvariable=self.rotation_var, width=7
        ).grid(row=2, column=1, sticky="ew", pady=(8, 0))
        ttk.Button(
            rotate, text="Скинути", command=self._reset_image_transform, style="Tool.TButton"
        ).grid(row=2, column=2, sticky="e", pady=(8, 0), padx=(6, 0))
        self.rotation_scale_var = tk.DoubleVar(value=0.0)
        self.rotation_slider_active = False
        self.rotation_scale = ttk.Scale(
            rotate, from_=0, to=359, variable=self.rotation_scale_var, command=self._rotation_scale_moved
        )
        self.rotation_scale.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        self.rotation_scale.bind("<ButtonPress-1>", self._rotation_scale_pressed, add="+")
        self.rotation_scale.bind("<ButtonRelease-1>", self._rotation_scale_released, add="+")
        self.flip_state_var = tk.StringVar()
        ttk.Label(rotate, textvariable=self.flip_state_var, style="Hint.TLabel").grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(4, 0)
        )
        ttk.Label(
            rotate,
            text="Або тягніть круглий маркер ↻ над картинкою. Shift — крок 15°.",
            style="Hint.TLabel",
            wraplength=320,
        ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(2, 0))

        actions = ttk.LabelFrame(self.element_panel, text="Дії з елементом", padding=8)
        actions.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        for index, (text, command, tip) in enumerate((
            ("Центр ↔", lambda: self._center_selected(horizontal=True), "Центрувати по горизонталі"),
            ("Центр ↕", lambda: self._center_selected(vertical=True), "Центрувати по вертикалі"),
            ("На передній план", self._bring_front, "Перемістити над іншими елементами"),
            ("🔒 Блокувати", self._toggle_selected_lock, "Заблокувати / розблокувати від зсуву"),
        )):
            button = ttk.Button(actions, text=text, command=command, style="Tool.TButton")
            button.grid(row=index // 2, column=index % 2, sticky="ew", padx=1, pady=1)
            ToolTip(button, tip)
        actions.columnconfigure(0, weight=1)
        actions.columnconfigure(1, weight=1)

        ttk.Label(
            self.element_panel, text="✓ Зміни застосовуються автоматично", style="Ok.TLabel"
        ).grid(row=4, column=0, sticky="w", pady=(8, 0))

        # ---- Вкладка «Шари» --------------------------------------------------------------
        ttk.Label(layers_tab, text="Верхній рядок — верхній шар", style="Hint.TLabel").pack(
            anchor="w", pady=(0, 6)
        )
        self.layers_list = tk.Listbox(
            layers_tab,
            height=8,
            exportselection=False,
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            highlightcolor=COLORS["accent"],
            selectbackground=COLORS["accent"],
            selectforeground="#ffffff",
            activestyle="none",
            font=(UI_FONT, 10),
        )
        self.layers_list.pack(fill="both", expand=True)
        self.layers_list.bind("<<ListboxSelect>>", self._layer_selected)
        layer_buttons = ttk.Frame(layers_tab)
        layer_buttons.pack(fill="x", pady=(6, 0))
        ttk.Button(
            layer_buttons, text="▲ Вище", command=lambda: self._move_layer(1), style="Tool.TButton"
        ).pack(side="left", fill="x", expand=True)
        ttk.Button(
            layer_buttons, text="▼ Нижче", command=lambda: self._move_layer(-1), style="Tool.TButton"
        ).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(
            layer_buttons, text="👁 Сховати/показати", command=self._toggle_visibility,
            style="Tool.TButton",
        ).pack(side="left", fill="x", expand=True)

        # ---- Вкладка «Друк» --------------------------------------------------------------
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
            fg=COLORS["checking"],
            bg=COLORS["panel"],
            anchor="w",
            justify="left",
            wraplength=320,
            font=(UI_FONT, 10),
        )
        self.connection_status_label.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        printing.columnconfigure(0, weight=1)
        printing.columnconfigure(1, weight=1)

        job = ttk.LabelFrame(print_tab, text="Друк", padding=10)
        job.pack(fill="x", pady=(10, 0))
        ttk.Label(job, textvariable=self.size_status_var, style="Hint.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 6)
        )
        ttk.Label(job, text="Кількість копій").grid(row=1, column=0, sticky="w")
        self.copies_var = tk.IntVar(value=1)
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
        ttk.Button(
            job,
            text="СЕРІЙНИЙ ДРУК CSV",
            command=self._batch_print_csv,
        ).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(7, 0), ipady=2)
        ttk.Label(
            job,
            text="У тексті, QR або штрихкоді використовуйте поля {serial}, {name} тощо",
            wraplength=320,
            style="Hint.TLabel",
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))
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

        self.bind("<Control-z>", self._undo)
        self.bind("<Control-y>", self._redo)
        self.bind("<Control-c>", self._copy_selected)
        self.bind("<Control-v>", self._paste_element)
        self.bind("<Control-d>", self._duplicate_selected)
        self.bind("<Delete>", self._delete_shortcut)
        self.bind("<Control-n>", lambda _event: self._new_layout() or "break")
        self.bind("<Control-o>", lambda _event: self._load_layout() or "break")
        self.bind("<Control-s>", lambda _event: self._save_layout() or "break")
        self.bind("<Control-S>", lambda _event: self._save_layout_as() or "break")
        self.bind("<Control-r>", lambda event: self._rotate_selected(90, event))
        self.bind("<Control-R>", lambda event: self._rotate_selected(-90, event))
        for key in ("<Left>", "<Right>", "<Up>", "<Down>"):
            self.bind(key, self._nudge_selected)

    def _snapshot(self):
        return {
            "elements": copy.deepcopy(self.elements),
            "layout_locked": bool(self.layout_locked),
            "selected_id": self.selected_id,
            "label_width_mm": LABEL_WIDTH_MM,
            "label_height_mm": LABEL_HEIGHT_MM,
        }

    @staticmethod
    def _snapshot_key(snapshot):
        return json.dumps(snapshot, ensure_ascii=False, sort_keys=True)

    def _record_history(self):
        if self.history_suspended:
            return
        snapshot = self._snapshot()
        if self.undo_stack and self._snapshot_key(self.undo_stack[-1]) == self._snapshot_key(snapshot):
            return
        self.undo_stack.append(snapshot)
        self.undo_stack = self.undo_stack[-MAX_HISTORY:]
        self.redo_stack.clear()

    def _restore_snapshot(self, snapshot):
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
            self.selected_id = candidate if any(e.get("id") == candidate for e in self.elements) else None
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
        self.status_var.set("Зміну повторено")
        return "break"

    def _read_autosave_payload(self):
        try:
            if self.autosave_path.is_file():
                with self.autosave_path.open("r", encoding="utf-8") as stream:
                    payload = json.load(stream)
                if payload.get("elements"):
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
        self.autosave_job = None
        try:
            with self.autosave_path.open("w", encoding="utf-8") as stream:
                json.dump(self._layout_data(embed_images=True), stream, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _offer_autosave_recovery(self):
        payload = self.recovery_payload
        self.recovery_payload = None
        if not payload:
            return
        if messagebox.askyesno(
            "Відновлення макета",
            "Знайдено автозбережений макет після попереднього незавершеного сеансу. Відновити його?",
        ):
            self._load_layout_payload(payload)
            self.status_var.set("Автозбережений макет відновлено")

    def _on_close(self):
        try:
            if self.autosave_job:
                self.after_cancel(self.autosave_job)
            self.autosave_path.unlink(missing_ok=True)
        except OSError:
            pass
        self.destroy()

    @staticmethod
    def _event_in_text_input(event):
        if event is None:
            return False
        widget = getattr(event, "widget", None)
        return isinstance(widget, (tk.Entry, ttk.Entry, ttk.Combobox, ttk.Spinbox, tk.Listbox))

    def _copy_selected(self, event=None):
        if self._event_in_text_input(event):
            return
        element = self._element()
        if element:
            self.clipboard_element = copy.deepcopy(element)
            # Кладемо «підпис» у системний буфер: так Ctrl+V знає, що вставляти
            # саме скопійований елемент, а не текст, скопійований деінде пізніше.
            if element.get("type") == "text":
                signature = str(element.get("text", ""))
            else:
                signature = f"Елемент наліпки: {self._layer_name(element)[2:].strip()}"
            try:
                self.clipboard_clear()
                self.clipboard_append(signature)
                self.clipboard_signature = signature
            except tk.TclError:
                self.clipboard_signature = None
            self.status_var.set("Елемент скопійовано")
        return "break" if event else None

    def _paste_element(self, event=None):
        """Ctrl+V: вставити текст або картинку з буфера обміну чи скопійований елемент."""
        if self._event_in_text_input(event) or self.inline_editor:
            return
        kind, value = self._read_system_clipboard()
        if kind == "files":
            self._paste_image_files(value)
        elif kind == "text" and self.clipboard_element and value == self.clipboard_signature:
            self._paste_internal_element()
        elif kind == "text":
            self._paste_text(value)
        elif kind == "image":
            self._paste_clipboard_image(value)
        elif self.clipboard_element:
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
        if not self.clipboard_element:
            return
        self._record_history()
        element = copy.deepcopy(self.clipboard_element)
        element["id"] = uuid.uuid4().hex
        element["x"] = round(float(element.get("x", 0)) + 1.0, 2)
        element["y"] = round(float(element.get("y", 0)) + 1.0, 2)
        element["locked"] = False
        self.elements.append(element)
        self.selected_id = element["id"]
        self._render_all()
        self._load_properties()
        self.status_var.set("Елемент вставлено")

    def _duplicate_selected(self, event=None):
        if self._event_in_text_input(event):
            return
        element = self._element()
        if not element:
            return "break" if event else None
        self.clipboard_element = copy.deepcopy(element)
        self._paste_internal_element()
        self.status_var.set("Елемент продубльовано")
        return "break" if event else None

    def _delete_shortcut(self, event=None):
        if self._event_in_text_input(event) or self.inline_editor:
            return
        self._delete_selected()
        return "break"

    def _nudge_selected(self, event):
        if self._event_in_text_input(event) or self.inline_editor:
            return
        element = self._element()
        if not element or element.get("locked"):
            return
        step = 0.5 if event.state & 0x0001 else 0.1
        dx = {"Left": -step, "Right": step}.get(event.keysym, 0.0)
        dy = {"Up": -step, "Down": step}.get(event.keysym, 0.0)
        self._record_history()
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
        self.canvas_view.create_rectangle(
            x + 3, y + 4, x + label_width + 4, y + label_height + 5,
            fill=COLORS["shadow"], outline="", tags="decor",
        )
        self.canvas_view.create_text(
            x + label_width / 2, y + label_height + 18,
            text=size_text(LABEL_WIDTH_MM, LABEL_HEIGHT_MM),
            fill="#4a5563", font=(UI_FONT, 9), tags="decor",
        )
        self.canvas_view.configure(
            scrollregion=(
                0,
                0,
                max(view_width, x + label_width + pad),
                max(view_height, y + label_height + pad + 24),
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
        self.canvas_view.yview_scroll(step, "units")
        return "break"

    def _mouse_wheel_horizontal(self, event):
        self.canvas_view.xview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"

    def _mouse_wheel_zoom(self, event):
        return self._zoom_step(1 if event.delta > 0 else -1)

    def _zoom_step(self, direction):
        current = self.zoom_var.get()
        index = ZOOM_LEVELS.index(current) if current in ZOOM_LEVELS else ZOOM_LEVELS.index("100%")
        target = max(0, min(len(ZOOM_LEVELS) - 1, index + direction))
        if target != index:
            self.zoom_var.set(ZOOM_LEVELS[target])
            self._set_zoom()
        return "break"

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
        return f"{prefix} {name[:34]}{transform}{lock}"

    def _refresh_layers(self):
        if not hasattr(self, "layers_list"):
            return
        self.layers_list.delete(0, tk.END)
        self.layer_ids = []
        for element in reversed(self.elements):
            self.layer_ids.append(element["id"])
            self.layers_list.insert(tk.END, self._layer_name(element))
        if self.selected_id in self.layer_ids:
            index = self.layer_ids.index(self.selected_id)
            self.layers_list.selection_set(index)
            self.layers_list.see(index)

    def _layer_selected(self, _event=None):
        selection = self.layers_list.curselection()
        if not selection:
            return
        index = selection[0]
        if index < len(self.layer_ids):
            self.selected_id = self.layer_ids[index]
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
        element = self._element()
        if not element:
            return
        self._record_history()
        element["visible"] = not element.get("visible", True)
        self._render_all()
        self._load_properties()
        self.status_var.set("Видимість шару змінено")

    def _element(self, element_id=None):
        element_id = element_id or self.selected_id
        return next((e for e in self.elements if e["id"] == element_id), None)

    def _new_layout(self, confirm=True):
        if confirm and self.elements and not messagebox.askyesno("Новий макет", "Очистити поточний макет?"):
            return
        if self.elements:
            self._record_history()
        self._finish_inline_edit(commit=False)
        self.elements.clear()
        self.selected_id = None
        self.layout_locked = False
        self.current_file = None
        self._render_all()
        self.status_var.set(f"Створено новий макет {size_text(LABEL_WIDTH_MM, LABEL_HEIGHT_MM)}")

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
            with Image.open(path) as image:
                ratio = image.width / image.height
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
        element.pop("image_data_b64", None)
        element.pop("image_name", None)
        element.pop("image_ext", None)
        self._render_all()
        self._load_properties()
        self.status_var.set("Зображення замінено; позицію та рамку збережено")

    def _delete_selected(self):
        if not self.selected_id:
            return
        self._record_history()
        self.elements = [e for e in self.elements if e["id"] != self.selected_id]
        self.selected_id = None
        self._render_all()
        self._load_properties()

    def _bring_front(self):
        element = self._element()
        if not element:
            return
        self._record_history()
        self.elements.remove(element)
        self.elements.append(element)
        self._render_all()

    def _toggle_selected_lock(self):
        element = self._element()
        if not element:
            messagebox.showinfo("Блокування", "Спочатку виберіть елемент")
            return
        self._record_history()
        element["locked"] = not bool(element.get("locked"))
        self._render_all()
        self._load_properties()
        self.status_var.set(
            "Елемент заблоковано від зсуву та зміни розміру"
            if element["locked"] else "Елемент розблоковано"
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
        """Центрувати вибраний елемент відносно наліпки 50x30 мм."""
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
                    image = self._transformed_image(element)
                    box_size = (
                        max(1, mm_to_px(element["width"])),
                        max(1, mm_to_px(element["height"])),
                    )
                    if element.get("preserve_aspect"):
                        image.thumbnail(box_size, Image.Resampling.LANCZOS)
                        target = Image.new("RGBA", box_size, (255, 255, 255, 0))
                        offset = (
                            (box_size[0] - image.width) // 2,
                            (box_size[1] - image.height) // 2,
                        )
                        target.alpha_composite(image, offset)
                    else:
                        target = image.resize(box_size, Image.Resampling.LANCZOS)
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
        item = self.canvas_items.get(self.selected_id)
        if not item:
            return
        bbox = self.canvas.bbox(item)
        if bbox:
            element = self._element()
            locked = bool(element and element.get("locked"))
            outline = "#d97706" if locked else "#1976d2"
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
                    fill="#ffffff", outline="#1976d2", width=2,
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
                    middle_x, anchor_y, middle_x, handle_y, fill="#1976d2", width=1,
                    tags="selection",
                )
                rotate_radius = 8
                self.canvas.create_oval(
                    middle_x - rotate_radius, handle_y - rotate_radius,
                    middle_x + rotate_radius, handle_y + rotate_radius,
                    fill="#1976d2", outline="#ffffff", width=2,
                    tags=("selection", "rotate_handle"),
                )
                self.canvas.create_text(
                    middle_x, handle_y, text="↻", fill="#ffffff", font=(UI_FONT, 9, "bold"),
                    tags=("selection", "rotate_handle"),
                )

    def _canvas_click(self, event):
        if self.inline_editor:
            # Натискання поза вбудованим полем завершує редагування так само,
            # як Enter або втрата фокуса. Після цього звичайно обробляємо клік:
            # порожнє місце зніме виділення, а інший елемент буде вибрано.
            self._finish_inline_edit(commit=True)
        hits = self.canvas.find_overlapping(event.x, event.y, event.x, event.y)
        for item in reversed(hits):
            tags = self.canvas.gettags(item)
            if "rotate_handle" in tags and self.selected_id:
                self._start_rotate_drag(event)
                return
            if "resize_handle" in tags and self.selected_id:
                direction = next(
                    tag[7:] for tag in tags
                    if tag.startswith("resize_") and tag != "resize_handle"
                )
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
        self.selected_id = found
        self.drag_start = (event.x, event.y) if found else None
        element = self._element(found) if found else None
        self.drag_origin = (
            (float(element["x"]), float(element["y"])) if element else None
        )
        self.drag_started = False
        self.drag_history_recorded = False
        self.resize_state = None
        self.rotate_state = None
        if element and element.get("type") == "text":
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
        if not self.snap_var.get():
            return x, y
        # Базова сітка 0,5 мм.
        x = round(x * 2) / 2
        y = round(y * 2) / 2
        width, height = self._element_size_mm(element)
        threshold = 0.65
        x_targets = [
            0.0,
            SAFE_MARGIN_MM,
            (LABEL_WIDTH_MM - width) / 2,
            LABEL_WIDTH_MM - SAFE_MARGIN_MM - width,
            LABEL_WIDTH_MM - width,
        ]
        y_targets = [
            0.0,
            SAFE_MARGIN_MM,
            (LABEL_HEIGHT_MM - height) / 2,
            LABEL_HEIGHT_MM - SAFE_MARGIN_MM - height,
            LABEL_HEIGHT_MM - height,
        ]
        for other in self.elements:
            if other is element or not other.get("visible", True):
                continue
            other_w, other_h = self._element_size_mm(other)
            ox, oy = float(other.get("x", 0)), float(other.get("y", 0))
            x_targets.extend(
                (ox, ox + other_w - width, ox + other_w / 2 - width / 2, ox + other_w, ox - width)
            )
            y_targets.extend(
                (oy, oy + other_h - height, oy + other_h / 2 - height / 2, oy + other_h, oy - height)
            )
        closest_x = min(x_targets, key=lambda target: abs(target - x))
        closest_y = min(y_targets, key=lambda target: abs(target - y))
        if abs(closest_x - x) <= threshold:
            x = closest_x
        if abs(closest_y - y) <= threshold:
            y = closest_y
        return x, y

    def _canvas_drag(self, event):
        if self.inline_editor:
            return
        if self.rotate_state:
            self._rotate_drag(event)
            return
        if self.resize_state:
            self._resize_selected(event)
            return
        if not self.selected_id or not self.drag_start or not self.drag_origin:
            return
        element = self._element()
        if not element or element.get("locked"):
            return
        dx = event.x - self.drag_start[0]
        dy = event.y - self.drag_start[1]
        if not self.drag_started:
            if dx * dx + dy * dy < DRAG_THRESHOLD_PX * DRAG_THRESHOLD_PX:
                return
            self.drag_started = True
            if not self.drag_history_recorded:
                self._record_history()
                self.drag_history_recorded = True
        new_x = self.drag_origin[0] + dx / PX_PER_MM
        new_y = self.drag_origin[1] + dy / PX_PER_MM
        new_x, new_y = self._snap_position(element, new_x, new_y)
        element["x"] = round(new_x, 2)
        element["y"] = round(new_y, 2)
        item = self.canvas_items[self.selected_id]
        self.canvas.coords(item, mm_to_px(element["x"]), mm_to_px(element["y"]))
        self._draw_selection()
        self._load_properties()

    def _canvas_release(self, _event):
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
        if self.drag_started:
            self.status_var.set("Елемент переміщено")
            self._schedule_autosave()
        self.drag_started = False
        self.drag_history_recorded = False
        if self.resize_state:
            self.resize_state = None
            self._load_properties()
            self.status_var.set("Розмір елемента змінено")

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
            highlightcolor="#1976d2",
            highlightbackground="#1976d2",
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
            self.empty_hint.grid()
            return
        self.empty_hint.grid_remove()
        self.element_panel.grid()
        if element_type == "text":
            self.text_frame.grid()
            self.image_frame.grid_remove()
        else:
            self.text_frame.grid_remove()
            self.image_frame.grid()

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
        self.loading_properties = True
        try:
            element = self._element()
            if not element:
                self.type_var.set("Нічого не вибрано")
                self.x_var.set("")
                self.y_var.set("")
                self._show_property_frame(None)
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
                self.type_var.set("Зображення" + lock_suffix)
                self.width_var.set(str(element["width"]))
                self.height_var.set(str(element["height"]))
                if element.get("source_kind") in ("qr", "code128"):
                    kind = "QR-код" if element["source_kind"] == "qr" else "Штрихкод Code 128"
                    self.image_path_var.set(f"{kind}: {element.get('code_value', '')}")
                else:
                    self.image_path_var.set(f"Файл: {Path(element['path']).name}")
                angle = self._normalize_angle(element.get("rotation", 0))
                self.rotation_var.set(f"{angle:g}")
                if not self.rotation_slider_active:
                    self.rotation_scale_var.set(angle)
                flips = [
                    label for key, label in (("flip_h", "по ширині"), ("flip_v", "по висоті"))
                    if element.get(key)
                ]
                self.flip_state_var.set(
                    "Віддзеркалено " + " і ".join(flips) if flips else "Без віддзеркалення"
                )
            self._show_property_frame(element["type"])
        finally:
            self.loading_properties = False

    def _apply_properties(self, show_error=True):
        self.live_apply_job = None
        element = self._element()
        if not element:
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

    def _image_pixel_size(self, path):
        try:
            key = (str(path), os.path.getmtime(path))
        except OSError:
            key = (str(path), None)
        size = self.image_size_cache.get(key)
        if size is None:
            with Image.open(path) as image:
                size = image.size
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
                image_width, image_height = self._image_pixel_size(element["path"])
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

    def _selected_image_for_transform(self):
        element = self._element()
        if not element or element.get("type") != "image":
            self.status_var.set("Спочатку виберіть зображення, QR-код або штрихкод")
            return None
        if element.get("locked"):
            self.status_var.set("Елемент заблоковано — спочатку розблокуйте його")
            return None
        return element

    def _rotate_selected(self, delta, event=None):
        if self._event_in_text_input(event) or self.inline_editor:
            return None
        element = self._selected_image_for_transform()
        if element:
            self._record_history()
            angle = self._normalize_angle(element.get("rotation", 0)) + delta
            self._set_image_rotation(element, angle)
            self._render_all()
            self._load_properties()
            self.status_var.set(f"Поворот: {self._normalize_angle(angle):g}°")
        return "break" if event else None

    def _flip_selected(self, axis):
        element = self._selected_image_for_transform()
        if not element:
            return
        self._record_history()
        key = "flip_h" if axis == "h" else "flip_v"
        # Віддзеркалення відносно екрана: для вже поверненої картинки кут змінює знак.
        element[key] = not element.get(key)
        element["rotation"] = self._normalize_angle(-self._normalize_angle(element.get("rotation", 0)))
        self._render_all()
        self._load_properties()
        self.status_var.set(
            "Віддзеркалено по ширині" if axis == "h" else "Віддзеркалено по висоті"
        )

    def _reset_image_transform(self):
        element = self._selected_image_for_transform()
        if not element or not self._has_transform(element):
            return
        self._record_history()
        self._set_image_rotation(element, 0)
        element["flip_h"] = False
        element["flip_v"] = False
        self._render_all()
        self._load_properties()
        self.status_var.set("Поворот і віддзеркалення скинуто")

    def _rotation_scale_pressed(self, _event=None):
        if self._selected_image_for_transform():
            self._record_history()
            self.rotation_slider_active = True

    def _rotation_scale_moved(self, value):
        if self.loading_properties:
            return
        element = self._element()
        if not element or element.get("type") != "image" or element.get("locked"):
            return
        angle = round(float(value))
        if angle == self._normalize_angle(element.get("rotation", 0)):
            return
        if not self.rotation_slider_active:
            self._record_history()
        self._set_image_rotation(element, angle)
        self._render_all()
        self._load_properties()

    def _rotation_scale_released(self, _event=None):
        if self.rotation_slider_active:
            self.rotation_slider_active = False
            self._load_properties()
            element = self._element()
            if element:
                self.status_var.set(
                    f"Поворот: {self._normalize_angle(element.get('rotation', 0)):g}°"
                )

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

    def _layout_data(self, embed_images=False):
        elements = copy.deepcopy(self.elements)
        for element in elements:
            if element.get("type") != "image":
                continue
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
            "version": 4,
            "label_width_mm": LABEL_WIDTH_MM,
            "label_height_mm": LABEL_HEIGHT_MM,
            "layout_locked": bool(self.layout_locked),
            "portable_images": True,
            "elements": elements,
        }

    def _save_layout(self):
        path = self.current_file or filedialog.asksaveasfilename(
            title="Зберегти макет",
            defaultextension=".json",
            filetypes=[("Макет наліпки", "*.json")],
        )
        if not path:
            return
        try:
            payload = self._layout_data(embed_images=True)
            Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            messagebox.showerror("Збереження макета", str(exc))
            return
        self.current_file = path
        self.status_var.set(f"Переносний макет збережено: {path}")

    def _save_layout_as(self):
        previous = self.current_file
        self.current_file = None
        self._save_layout()
        if self.current_file is None:
            self.current_file = previous

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
        self.title(APP_TITLE.format(size=size_text(LABEL_WIDTH_MM, LABEL_HEIGHT_MM)))
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

    def _open_size_presets_dialog(self):
        if self.presets_dialog and self.presets_dialog.winfo_exists():
            self.presets_dialog.lift()
            self.presets_dialog.focus_set()
            return
        dialog = tk.Toplevel(self)
        self.presets_dialog = dialog
        dialog.title("Пресети розміру наліпок")
        dialog.configure(bg=COLORS["panel"])
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
            highlightbackground=COLORS["border"],
            highlightcolor=COLORS["accent"],
            selectbackground=COLORS["accent"],
            selectforeground="#ffffff",
            activestyle="none",
            font=(UI_FONT, 10),
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
        path = filedialog.askopenfilename(
            title="Відкрити макет", filetypes=[("Макет наліпки", "*.json"), ("Усі файли", "*.*")]
        )
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            self._record_history()
            self._load_layout_payload(data)
            self.current_file = path
            self.status_var.set(f"Відкрито: {path}")
        except Exception as exc:
            messagebox.showerror("Відкриття макета", str(exc))

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
        colors = {
            "ok": ("#087f23", "● "),
            "error": ("#c00000", "● "),
            "checking": ("#9a6700", "● "),
        }
        color, prefix = colors[state]
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
        """Копія макета, де повернуті/віддзеркалені зображення замінено готовими PNG.

        Сам механізм друку не змінюється: він, як і раніше, малює файл у рамці елемента.
        """
        layout = copy.deepcopy(layout)
        folder = self.data_dir / "print_cache"
        for element in layout.get("elements", []):
            if (element.get("type") != "image" or not element.get("visible", True)
                    or not self._has_transform(element)):
                continue
            source = os.path.abspath(element.get("path", ""))
            stat = os.stat(source)
            key = hashlib.sha256(json.dumps([
                source,
                stat.st_mtime_ns,
                stat.st_size,
                self._normalize_angle(element.get("rotation", 0)),
                bool(element.get("flip_h")),
                bool(element.get("flip_v")),
            ]).encode("utf-8")).hexdigest()[:32]
            target = folder / f"{key}.png"
            if not target.is_file():
                folder.mkdir(parents=True, exist_ok=True)
                self._transformed_image(element).save(target, "PNG")
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
