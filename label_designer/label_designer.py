#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Простий редактор та друк наліпок 50x30 мм для Windows/Xprinter."""

import base64
import copy
import csv
import ctypes
import hashlib
import io
import ipaddress
import json
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

if os.name == "nt":
    import winreg

from PIL import Image, ImageTk
import barcode
import qrcode
from barcode.writer import ImageWriter


APP_TITLE = "Редактор наліпок 50×30 — Xprinter"
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
LOCAL_DRIVER_SOURCE = (
    r"C:\Windows\System32\DriverStore\FileRepository"
    r"\xprinter.inf_amd64_a2184ce9ef55d7a6"
)


def mm_to_px(value):
    return round(float(value) * PX_PER_MM)


def px_to_mm(value):
    return round(float(value) / PX_PER_MM, 2)


class LabelDesigner(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1200x820")
        self.minsize(1080, 740)

        self.elements = []
        self.selected_id = None
        self.canvas_items = {}
        self.photo_refs = {}
        self.drag_start = None
        self.drag_origin = None
        self.drag_started = False
        self.resize_state = None
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

        self._build_ui()
        self._refresh_printers()
        self._new_layout(confirm=False)
        self.autosave_suspended = False
        self.after(250, self._offer_autosave_recovery)
        self.connection_after_id = self.after(500, self._schedule_connection_check)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        self.show_grid_var = tk.BooleanVar(value=True)
        self.snap_var = tk.BooleanVar(value=True)
        self.safe_margin_var = tk.BooleanVar(value=True)
        self.zoom_var = tk.StringVar(value="100%")

        toolbar = ttk.Frame(self, padding=8)
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="Новий", command=self._new_layout).pack(side="left", padx=2)
        ttk.Button(toolbar, text="Відкрити", command=self._load_layout).pack(side="left", padx=2)
        ttk.Button(toolbar, text="Зберегти", command=self._save_layout).pack(side="left", padx=2)
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(toolbar, text="+ Текст", command=self._add_text).pack(side="left", padx=2)
        ttk.Button(toolbar, text="+ Зображення", command=self._add_image).pack(side="left", padx=2)
        ttk.Button(toolbar, text="Видалити", command=self._delete_selected).pack(side="left", padx=2)
        ttk.Button(toolbar, text="На передній план", command=self._bring_front).pack(side="left", padx=2)
        ttk.Button(toolbar, text="🔒 Елемент", command=self._toggle_selected_lock).pack(
            side="left", padx=2
        )
        ttk.Button(toolbar, text="🔒 Макет", command=self._toggle_layout_lock).pack(
            side="left", padx=2
        )
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(
            toolbar, text="Центр ↔", command=lambda: self._center_selected(horizontal=True)
        ).pack(side="left", padx=2)
        ttk.Button(
            toolbar, text="Центр ↕", command=lambda: self._center_selected(vertical=True)
        ).pack(side="left", padx=2)

        advanced = ttk.Frame(self, padding=(8, 0, 8, 7))
        advanced.pack(fill="x")
        ttk.Button(advanced, text="↶ Скасувати", command=self._undo).pack(side="left", padx=2)
        ttk.Button(advanced, text="↷ Повторити", command=self._redo).pack(side="left", padx=2)
        ttk.Button(advanced, text="Дублювати", command=self._duplicate_selected).pack(side="left", padx=2)
        ttk.Button(advanced, text="+ QR", command=self._add_qr).pack(side="left", padx=2)
        ttk.Button(advanced, text="+ Штрихкод", command=self._add_barcode).pack(side="left", padx=2)
        ttk.Separator(advanced, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Checkbutton(
            advanced, text="Сітка", variable=self.show_grid_var, command=self._render_all
        ).pack(side="left", padx=3)
        ttk.Checkbutton(advanced, text="Прив’язка", variable=self.snap_var).pack(side="left", padx=3)
        ttk.Checkbutton(
            advanced, text="Безпечні поля", variable=self.safe_margin_var, command=self._render_all
        ).pack(side="left", padx=3)
        ttk.Label(advanced, text="Масштаб:").pack(side="left", padx=(12, 3))
        zoom_combo = ttk.Combobox(
            advanced,
            textvariable=self.zoom_var,
            values=("75%", "100%", "125%", "150%", "200%"),
            state="readonly",
            width=6,
        )
        zoom_combo.pack(side="left")
        zoom_combo.bind("<<ComboboxSelected>>", self._set_zoom)

        body = ttk.Frame(self, padding=(8, 0, 8, 8))
        body.pack(fill="both", expand=True)

        workspace = ttk.Frame(body)
        workspace.pack(side="left", fill="both", expand=True)
        ttk.Label(
            workspace,
            text=("Наліпка 50×30 мм — перетягуйте елементи мишкою; "
                  "тягніть сині маркери для зміни розміру, Shift зберігає пропорції"),
        ).pack(anchor="w", pady=(0, 6))

        canvas_holder = tk.Frame(workspace, bg="#d6d6d6", padx=24, pady=24)
        canvas_holder.pack(fill="both", expand=True)
        canvas_holder.rowconfigure(0, weight=1)
        canvas_holder.columnconfigure(0, weight=1)
        self.canvas_view = tk.Canvas(canvas_holder, bg="#d6d6d6", highlightthickness=0)
        canvas_x_scroll = ttk.Scrollbar(canvas_holder, orient="horizontal", command=self.canvas_view.xview)
        canvas_y_scroll = ttk.Scrollbar(canvas_holder, orient="vertical", command=self.canvas_view.yview)
        self.canvas_view.configure(xscrollcommand=canvas_x_scroll.set, yscrollcommand=canvas_y_scroll.set)
        self.canvas_view.grid(row=0, column=0, sticky="nsew")
        canvas_y_scroll.grid(row=0, column=1, sticky="ns")
        canvas_x_scroll.grid(row=1, column=0, sticky="ew")
        self.canvas = tk.Canvas(
            self.canvas_view,
            width=CANVAS_WIDTH,
            height=CANVAS_HEIGHT,
            bg="white",
            highlightthickness=1,
            highlightbackground="#555555",
        )
        self.canvas_window = self.canvas_view.create_window(24, 24, window=self.canvas, anchor="nw")
        self.canvas_view.configure(scrollregion=(0, 0, CANVAS_WIDTH + 48, CANVAS_HEIGHT + 48))
        self.canvas.bind("<Button-1>", self._canvas_click)
        self.canvas.bind("<Double-Button-1>", self._canvas_double_click)
        self.canvas.bind("<B1-Motion>", self._canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self._canvas_release)

        side = ttk.Frame(body, width=370, padding=(14, 0, 0, 0))
        side.pack(side="right", fill="y")
        side.pack_propagate(False)

        notebook = ttk.Notebook(side)
        notebook.pack(fill="both", expand=True)
        edit_tab = ttk.Frame(notebook, padding=8)
        print_tab = ttk.Frame(notebook, padding=8)
        notebook.add(edit_tab, text="Редагування")
        notebook.add(print_tab, text="Друк")

        props = ttk.LabelFrame(edit_tab, text="Властивості елемента", padding=10)
        props.pack(fill="x")

        self.type_var = tk.StringVar(value="Нічого не вибрано")
        ttk.Label(props, textvariable=self.type_var, font=("Segoe UI", 10, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )

        ttk.Label(props, text="X, мм").grid(row=1, column=0, sticky="w", pady=3)
        self.x_var = tk.StringVar()
        ttk.Entry(props, textvariable=self.x_var, width=16).grid(row=1, column=1, sticky="ew")
        ttk.Label(props, text="Y, мм").grid(row=2, column=0, sticky="w", pady=3)
        self.y_var = tk.StringVar()
        ttk.Entry(props, textvariable=self.y_var, width=16).grid(row=2, column=1, sticky="ew")

        self.text_frame = ttk.Frame(props)
        self.text_frame.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
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
        ttk.Label(self.text_frame, text="Кегль, pt").grid(row=2, column=0, sticky="w", pady=3)
        self.size_var = tk.StringVar(value="12")
        ttk.Entry(self.text_frame, textvariable=self.size_var).grid(row=2, column=1, sticky="ew")
        self.bold_var = tk.BooleanVar()
        ttk.Checkbutton(self.text_frame, text="Жирний", variable=self.bold_var).grid(
            row=3, column=1, sticky="w", pady=3
        )
        self.text_frame.columnconfigure(1, weight=1)

        self.image_frame = ttk.Frame(props)
        self.image_frame.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Label(self.image_frame, text="Ширина, мм").grid(row=0, column=0, sticky="w", pady=3)
        self.width_var = tk.StringVar()
        ttk.Entry(self.image_frame, textvariable=self.width_var).grid(row=0, column=1, sticky="ew")
        ttk.Label(self.image_frame, text="Висота, мм").grid(row=1, column=0, sticky="w", pady=3)
        self.height_var = tk.StringVar()
        ttk.Entry(self.image_frame, textvariable=self.height_var).grid(row=1, column=1, sticky="ew")
        self.image_path_var = tk.StringVar()
        ttk.Label(self.image_frame, textvariable=self.image_path_var, wraplength=270).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(5, 0)
        )
        ttk.Button(
            self.image_frame,
            text="Замінити зображення…",
            command=self._replace_image,
        ).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.image_frame.columnconfigure(1, weight=1)

        ttk.Label(
            props,
            text="Зміни застосовуються автоматично",
            foreground="#4b6b4b",
        ).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(10, 0)
        )
        props.columnconfigure(1, weight=1)

        layers = ttk.LabelFrame(edit_tab, text="Шари", padding=8)
        layers.pack(fill="both", expand=True, pady=(10, 0))
        self.layers_list = tk.Listbox(layers, height=8, exportselection=False)
        self.layers_list.pack(fill="both", expand=True)
        self.layers_list.bind("<<ListboxSelect>>", self._layer_selected)
        layer_buttons = ttk.Frame(layers)
        layer_buttons.pack(fill="x", pady=(6, 0))
        ttk.Button(layer_buttons, text="Вище", command=lambda: self._move_layer(1)).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(layer_buttons, text="Нижче", command=lambda: self._move_layer(-1)).pack(
            side="left", fill="x", expand=True, padx=4
        )
        ttk.Button(layer_buttons, text="Сховати/показати", command=self._toggle_visibility).pack(
            side="left", fill="x", expand=True
        )

        printing = ttk.LabelFrame(print_tab, text="Друк", padding=10)
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
        setup_buttons.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(6, 4))
        ttk.Button(
            setup_buttons, text="Встановити драйвер", command=self._install_driver
        ).pack(side="left", fill="x", expand=True)
        ttk.Button(
            setup_buttons, text="Налаштувати мережу", command=self._configure_network_printer
        ).pack(side="left", fill="x", expand=True, padx=(5, 0))

        refresh_buttons = ttk.Frame(printing)
        refresh_buttons.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        ttk.Button(
            refresh_buttons, text="Оновити черги", command=self._refresh_printers
        ).pack(side="left", fill="x", expand=True)
        ttk.Button(
            refresh_buttons, text="Перевірити зараз", command=self._schedule_connection_check
        ).pack(side="left", fill="x", expand=True, padx=(5, 0))

        self.connection_status_label = tk.Label(
            printing,
            text="● Перевірка підключення…",
            fg="#9a6700",
            bg=self.cget("background"),
            anchor="w",
            justify="left",
            wraplength=310,
        )
        self.connection_status_label.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(3, 9))

        ttk.Separator(printing).grid(row=7, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        ttk.Label(printing, text="Кількість копій").grid(row=8, column=0, sticky="w")
        self.copies_var = tk.IntVar(value=1)
        ttk.Spinbox(printing, from_=1, to=99, textvariable=self.copies_var, width=8).grid(
            row=8, column=1, sticky="e"
        )
        self.print_button = ttk.Button(
            printing,
            text="ДРУКУВАТИ",
            command=self._print_layout,
            state="disabled",
        )
        self.print_button.grid(row=9, column=0, columnspan=2, sticky="ew", pady=(12, 0), ipady=5)
        ttk.Button(
            printing,
            text="СЕРІЙНИЙ ДРУК CSV",
            command=self._batch_print_csv,
        ).grid(row=10, column=0, columnspan=2, sticky="ew", pady=(7, 0), ipady=3)
        ttk.Label(
            printing,
            text="У тексті, QR або штрихкоді використовуйте поля {serial}, {name} тощо",
            wraplength=310,
            foreground="#555555",
        ).grid(row=11, column=0, columnspan=2, sticky="w", pady=(8, 0))
        printing.columnconfigure(0, weight=1)
        printing.columnconfigure(1, weight=1)

        for variable in (
            self.x_var,
            self.y_var,
            self.text_var,
            self.font_var,
            self.size_var,
            self.bold_var,
            self.width_var,
            self.height_var,
        ):
            variable.trace_add("write", self._schedule_live_apply)
        self.network_ip_entry.configure(state="disabled")

        self.status_var = tk.StringVar(value="Готово")
        ttk.Label(side, textvariable=self.status_var, wraplength=300).pack(fill="x", pady=(12, 0))
        self._show_property_frame(None)

        self.bind("<Control-z>", self._undo)
        self.bind("<Control-y>", self._redo)
        self.bind("<Control-c>", self._copy_selected)
        self.bind("<Control-v>", self._paste_element)
        self.bind("<Control-d>", self._duplicate_selected)
        self.bind("<Delete>", self._delete_shortcut)
        for key in ("<Left>", "<Right>", "<Up>", "<Down>"):
            self.bind(key, self._nudge_selected)

    def _snapshot(self):
        return {
            "elements": copy.deepcopy(self.elements),
            "layout_locked": bool(self.layout_locked),
            "selected_id": self.selected_id,
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
            self.status_var.set("Елемент скопійовано")
        return "break" if event else None

    def _paste_element(self, event=None):
        if self._event_in_text_input(event):
            return
        if not self.clipboard_element:
            return "break" if event else None
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
        return "break" if event else None

    def _duplicate_selected(self, event=None):
        if self._event_in_text_input(event):
            return
        element = self._element()
        if not element:
            return "break" if event else None
        self.clipboard_element = copy.deepcopy(element)
        result = self._paste_element()
        self.status_var.set("Елемент продубльовано")
        return "break" if event else result

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
        self.canvas.configure(
            width=round(LABEL_WIDTH_MM * PX_PER_MM),
            height=round(LABEL_HEIGHT_MM * PX_PER_MM),
        )
        self.canvas_view.configure(
            scrollregion=(
                0,
                0,
                round(LABEL_WIDTH_MM * PX_PER_MM) + 48,
                round(LABEL_HEIGHT_MM * PX_PER_MM) + 48,
            )
        )
        self._render_all()
        self.status_var.set(f"Масштаб перегляду: {round(factor * 100)}%")

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
        return f"{prefix} {name[:34]}{lock}"

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
        self.status_var.set("Створено новий макет 50×30 мм")

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
            filetypes=[("Зображення", "*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff"), ("Усі файли", "*.*")],
        )
        if not path:
            return
        try:
            with Image.open(path) as image:
                ratio = image.width / image.height
        except Exception as exc:
            messagebox.showerror("Зображення", f"Не вдалося відкрити файл:\n{exc}")
            return
        height = 12.0
        width = min(20.0, height * ratio)
        if width == 20.0:
            height = width / ratio
        element = {
            "id": uuid.uuid4().hex,
            "type": "image",
            "x": 2.0,
            "y": 2.0,
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

    def _replace_image(self):
        """Замінити файл вибраного зображення, не змінюючи його рамку та позицію."""
        element = self._element()
        if not element or element.get("type") != "image":
            messagebox.showinfo("Заміна зображення", "Спочатку виберіть зображення")
            return
        path = filedialog.askopenfilename(
            title="Виберіть нове зображення",
            filetypes=[
                ("Зображення", "*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff"),
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
                    image = Image.open(element["path"]).convert("RGBA")
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

    def _canvas_click(self, event):
        if self.inline_editor:
            # Натискання поза вбудованим полем завершує редагування так само,
            # як Enter або втрата фокуса. Після цього звичайно обробляємо клік:
            # порожнє місце зніме виділення, а інший елемент буде вибрано.
            self._finish_inline_edit(commit=True)
        hits = self.canvas.find_overlapping(event.x, event.y, event.x, event.y)
        for item in reversed(hits):
            tags = self.canvas.gettags(item)
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
        if element_type == "text":
            self.text_frame.grid()
            self.image_frame.grid_remove()
        elif element_type == "image":
            self.text_frame.grid_remove()
            self.image_frame.grid()
        else:
            self.text_frame.grid_remove()
            self.image_frame.grid_remove()

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
                self.image_path_var.set(element["path"])
            self._show_property_frame(element["type"])
        finally:
            self.loading_properties = False

    def _apply_properties(self, show_error=True):
        self.live_apply_job = None
        element = self._element()
        if not element:
            return False
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
        except ValueError as exc:
            if show_error:
                messagebox.showerror("Властивості", str(exc))
            return False
        changed = any(element.get(key) != value for key, value in values.items())
        if not changed:
            return True
        self._record_history()
        element.update(values)
        self._render_all()
        self.status_var.set("Зміни застосовано автоматично")
        return True

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
                            ("Зображення", "*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff"),
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
        self.elements = elements
        self.layout_locked = bool(data.get("layout_locked", False))
        self.selected_id = None
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
                layouts.append(self._layout_for_data_row(row))
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

        self.print_button.configure(state="disabled")
        self.printing_active = True
        self.status_var.set("Надсилання на принтер…")
        layout = self._layout_data()
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
