#!/usr/bin/env python3
from __future__ import annotations

import json
import io
import os
import sys
import threading
import tkinter as tk
import ctypes
from dataclasses import dataclass
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

import board_cut_optimizer
import board_data_to_csv


def get_runtime_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def configure_frozen_tk() -> None:
    if not getattr(sys, "frozen", False):
        return
    runtime_dir = get_runtime_dir()
    os.environ.setdefault("TCL_LIBRARY", os.path.join(runtime_dir, "tcl8.6"))
    os.environ.setdefault("TK_LIBRARY", os.path.join(runtime_dir, "tk8.6"))
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(runtime_dir)


configure_frozen_tk()


APP_ENGLISH_NAME = "board_cut_optimizer"
APP_CHINESE_NAME = "板优排"
APP_VERSION = "V1.2.1"
APP_AUTHOR = "有钱任性买辣条"
APP_TITLE = f"{APP_CHINESE_NAME} {APP_ENGLISH_NAME} {APP_VERSION}"
APP_DESCRIPTION = "板材开料数据整理、厚度校验、自动排版、重量统计与排板图输出。"

APP_DIR = get_runtime_dir()
RESOURCE_ROOT = getattr(sys, "_MEIPASS", APP_DIR)
RESOURCE_DIR = os.path.join(RESOURCE_ROOT, "assets")
CONFIG_PATH = os.path.join(APP_DIR, "board_gui_settings.json")
ICON_ICO_PATH = os.path.join(RESOURCE_DIR, "board_gui_icon.ico")
ICON_PNG_PATH = os.path.join(RESOURCE_DIR, "board_gui_icon.png")

BG_APP = "#edf3f8"
BG_SURFACE = "#ffffff"
BG_SURFACE_ALT = "#f7fafc"
BG_ACCENT = "#0f766e"
BG_ACCENT_HOVER = "#115e59"
BG_STATUS_READY = "#dcfce7"
BG_STATUS_BUSY = "#fef3c7"
BG_STATUS_ERROR = "#fee2e2"
FG_STATUS_READY = "#166534"
FG_STATUS_BUSY = "#92400e"
FG_STATUS_ERROR = "#b91c1c"
BORDER = "#dbe4ee"
TEXT_PRIMARY = "#0f172a"
TEXT_SECONDARY = "#475569"
TEXT_MUTED = "#64748b"
EDITOR_BG = "#f8fbff"


@dataclass
class AppSettings:
    archive_root: str = ""
    weight_table_path: str = ""
    last_board_length: str = "1220"
    last_board_width: str = "2440"


def load_settings() -> AppSettings:
    if not os.path.exists(CONFIG_PATH):
        return AppSettings()
    with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    return AppSettings(
        archive_root=str(raw.get("archive_root", "")),
        weight_table_path=str(raw.get("weight_table_path", "")),
        last_board_length=str(raw.get("last_board_length", "1220")),
        last_board_width=str(raw.get("last_board_width", "2440")),
    )


def save_settings(settings: AppSettings) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "archive_root": settings.archive_root,
                "weight_table_path": settings.weight_table_path,
                "last_board_length": settings.last_board_length,
                "last_board_width": settings.last_board_width,
            },
            handle,
            ensure_ascii=False,
            indent=2,
        )


def center_child_window(child: tk.Toplevel, parent: tk.Misc) -> None:
    parent.update_idletasks()
    child.update_idletasks()
    parent_x = parent.winfo_rootx()
    parent_y = parent.winfo_rooty()
    parent_width = parent.winfo_width()
    parent_height = parent.winfo_height()
    child_width = child.winfo_width()
    child_height = child.winfo_height()
    x = parent_x + max((parent_width - child_width) // 2, 0)
    y = parent_y + max((parent_height - child_height) // 2, 0)
    child.geometry(f"+{x}+{y}")


def prepare_centered_dialog(child: tk.Toplevel, parent: tk.Misc) -> None:
    child.withdraw()
    child.update_idletasks()
    center_child_window(child, parent)
    child.deiconify()
    child.lift(parent)
    child.focus_force()


def create_card(parent: tk.Misc, padding: int | tuple[int, int, int, int] = 18) -> ttk.Frame:
    return ttk.Frame(parent, padding=padding, style="Card.TFrame")


def copy_pil_image_to_windows_clipboard(image: Image.Image) -> None:
    if os.name != "nt":
        raise RuntimeError("当前仅支持在 Windows 中复制图片到剪贴板。")

    image = image.convert("RGB")
    output = io.BytesIO()
    image.save(output, "BMP")
    bmp_data = output.getvalue()[14:]

    CF_DIB = 8
    GHND = 0x0042
    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32

    handle = kernel32.GlobalAlloc(GHND, len(bmp_data))
    if not handle:
        raise RuntimeError("分配剪贴板内存失败。")

    locked = kernel32.GlobalLock(handle)
    if not locked:
        kernel32.GlobalFree(handle)
        raise RuntimeError("锁定剪贴板内存失败。")

    ctypes.memmove(locked, bmp_data, len(bmp_data))
    kernel32.GlobalUnlock(handle)

    if not user32.OpenClipboard(None):
        kernel32.GlobalFree(handle)
        raise RuntimeError("无法打开系统剪贴板。")

    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(CF_DIB, handle):
            kernel32.GlobalFree(handle)
            raise RuntimeError("写入剪贴板失败。")
        handle = None
    finally:
        user32.CloseClipboard()


class ImagePreviewDialog(tk.Toplevel):
    def __init__(self, master: "BoardGuiApp", image_path: str) -> None:
        super().__init__(master.root)
        self.master_app = master
        self.image_path = image_path
        self.title("排板图预览")
        self.geometry("1280x1500")
        self.minsize(960, 900)
        self.transient(master.root)
        self.configure(background=BG_APP)
        master.apply_window_icon(self)

        self.source_image = Image.open(image_path)
        self.zoom = 1.0
        self.min_zoom = 0.1
        self.max_zoom = 4.0
        self.image_ref: ImageTk.PhotoImage | None = None
        self.image_item_id: int | None = None
        self._render_job: int | None = None
        self._last_render_signature: tuple[int, int, float] | None = None
        self.context_menu = tk.Menu(self, tearoff=False)
        self.context_menu.add_command(label="复制图片到剪贴板", command=self.copy_image_to_clipboard)

        wrapper = create_card(self, 14)
        wrapper.grid(row=0, column=0, sticky="nsew", padx=14, pady=14)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        wrapper.rowconfigure(0, weight=1)
        wrapper.columnconfigure(0, weight=1)

        canvas_frame = tk.Frame(wrapper, bg=BORDER, bd=0, highlightthickness=0)
        canvas_frame.grid(row=0, column=0, sticky="nsew")
        canvas_frame.rowconfigure(0, weight=1)
        canvas_frame.columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(canvas_frame, bg="#f3f7fb", highlightthickness=0, cursor="fleur")
        self.canvas.grid(row=0, column=0, sticky="nsew", padx=1, pady=1)

        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.canvas.bind("<MouseWheel>", self._on_mouse_wheel)
        self.canvas.bind("<Button-4>", self._on_mouse_wheel)
        self.canvas.bind("<Button-5>", self._on_mouse_wheel)
        self.canvas.bind("<ButtonPress-1>", self._start_pan)
        self.canvas.bind("<B1-Motion>", self._drag_pan)
        self.canvas.bind("<Button-3>", self._show_context_menu)

        prepare_centered_dialog(self, master.root)
        self.fit_to_width()

    def _on_canvas_resize(self, _event: tk.Event) -> None:
        self.schedule_render_image()

    def schedule_render_image(self) -> None:
        if self._render_job is not None:
            self.after_cancel(self._render_job)
        self._render_job = self.after(80, self.render_image)

    def _on_mouse_wheel(self, event: tk.Event) -> None:
        if getattr(event, "delta", 0) > 0 or getattr(event, "num", None) == 4:
            self.adjust_zoom(1.1)
        else:
            self.adjust_zoom(0.9)

    def _start_pan(self, event: tk.Event) -> None:
        self.canvas.scan_mark(event.x, event.y)

    def _drag_pan(self, event: tk.Event) -> None:
        self.canvas.scan_dragto(event.x, event.y, gain=1)

    def _show_context_menu(self, event: tk.Event) -> None:
        try:
            self.context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.context_menu.grab_release()

    def adjust_zoom(self, factor: float) -> None:
        self.zoom = max(self.min_zoom, min(self.max_zoom, self.zoom * factor))
        self.schedule_render_image()

    def fit_to_width(self) -> None:
        self.update_idletasks()
        canvas_width = max(self.canvas.winfo_width(), 200)
        scale_x = canvas_width / self.source_image.width
        self.zoom = max(self.min_zoom, min(self.max_zoom, scale_x))
        self.schedule_render_image()

    def render_image(self) -> None:
        self._render_job = None
        canvas_width = max(self.canvas.winfo_width(), 1)
        canvas_height = max(self.canvas.winfo_height(), 1)
        signature = (canvas_width, canvas_height, round(self.zoom, 4))
        if self._last_render_signature == signature and self.image_ref is not None:
            return
        width = max(1, int(self.source_image.width * self.zoom))
        height = max(1, int(self.source_image.height * self.zoom))
        resized = self.source_image.resize((width, height), Image.Resampling.BILINEAR)
        self.image_ref = ImageTk.PhotoImage(resized)
        self.canvas.delete("all")
        self.image_item_id = self.canvas.create_image(0, 0, anchor="nw", image=self.image_ref)
        self.canvas.configure(scrollregion=(0, 0, width, height))
        self._last_render_signature = signature

    def copy_image_to_clipboard(self) -> None:
        try:
            copy_pil_image_to_windows_clipboard(self.source_image)
        except Exception as exc:
            messagebox.showerror("复制失败", str(exc), parent=self)
            return
        messagebox.showinfo("复制完成", "排板图已复制到系统剪贴板。", parent=self)


class AboutDialog(tk.Toplevel):
    def __init__(self, master: "BoardGuiApp") -> None:
        super().__init__(master.root)
        self.title("关于")
        self.resizable(False, False)
        self.transient(master.root)
        self.grab_set()
        self.configure(background=BG_APP)
        master.apply_window_icon(self)

        frame = create_card(self, 20)
        frame.grid(padx=16, pady=16, sticky="nsew")
        frame.columnconfigure(0, weight=1)

        if master.brand_icon_small is not None:
            tk.Label(frame, image=master.brand_icon_small, bg=BG_SURFACE).grid(row=0, column=0, sticky="w")
        ttk.Label(frame, text=APP_CHINESE_NAME, style="DialogTitle.TLabel").grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Label(frame, text=APP_ENGLISH_NAME, style="Subtle.TLabel").grid(row=2, column=0, sticky="w", pady=(4, 0))
        ttk.Separator(frame).grid(row=3, column=0, sticky="ew", pady=16)
        ttk.Label(frame, text=f"版本：{APP_VERSION}", style="Body.TLabel").grid(row=4, column=0, sticky="w")
        ttk.Label(frame, text=f"作者：{APP_AUTHOR}", style="Body.TLabel").grid(row=5, column=0, sticky="w", pady=(6, 0))
        ttk.Label(frame, text=APP_DESCRIPTION, style="BodyMuted.TLabel", wraplength=420, justify="left").grid(
            row=6, column=0, sticky="w", pady=(14, 0)
        )
        ttk.Button(frame, text="关闭", command=self.destroy, style="Secondary.TButton").grid(
            row=7, column=0, sticky="e", pady=(18, 0)
        )
        prepare_centered_dialog(self, master.root)


class SettingsDialog(tk.Toplevel):
    def __init__(self, master: "BoardGuiApp", settings: AppSettings) -> None:
        super().__init__(master.root)
        self.master_app = master
        self.title("设置")
        self.resizable(False, False)
        self.transient(master.root)
        self.grab_set()
        self.configure(background=BG_APP)
        master.apply_window_icon(self)

        self.archive_root_var = tk.StringVar(value=settings.archive_root)
        self.weight_table_var = tk.StringVar(value=settings.weight_table_path)

        frame = create_card(self, 20)
        frame.grid(padx=16, pady=16, sticky="nsew")
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="留档根目录", style="FieldLabel.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 8))
        ttk.Entry(frame, textvariable=self.archive_root_var, width=56).grid(
            row=0, column=1, sticky="ew", padx=(10, 10)
        )
        ttk.Button(frame, text="选择", command=self.pick_archive_root, style="Secondary.TButton").grid(
            row=0, column=2, sticky="ew"
        )

        ttk.Label(frame, text="重量表 CSV", style="FieldLabel.TLabel").grid(row=1, column=0, sticky="w", pady=(12, 8))
        ttk.Entry(frame, textvariable=self.weight_table_var, width=56).grid(
            row=1, column=1, sticky="ew", padx=(10, 10)
        )
        ttk.Button(frame, text="选择", command=self.pick_weight_table, style="Secondary.TButton").grid(
            row=1, column=2, sticky="ew"
        )

        button_row = ttk.Frame(frame, style="Card.TFrame")
        button_row.grid(row=2, column=0, columnspan=3, sticky="e", pady=(18, 0))
        ttk.Button(button_row, text="取消", command=self.destroy, style="Secondary.TButton").grid(
            row=0, column=0, padx=(0, 8)
        )
        ttk.Button(button_row, text="保存", command=self.save, style="Primary.TButton").grid(row=0, column=1)
        prepare_centered_dialog(self, master.root)

    def pick_archive_root(self) -> None:
        selected = filedialog.askdirectory(
            title="选择留档根目录",
            initialdir=self.archive_root_var.get() or APP_DIR,
        )
        if selected:
            self.archive_root_var.set(selected)

    def pick_weight_table(self) -> None:
        selected = filedialog.askopenfilename(
            title="选择重量表 CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialdir=os.path.dirname(self.weight_table_var.get()) if self.weight_table_var.get() else APP_DIR,
        )
        if selected:
            self.weight_table_var.set(selected)

    def save(self) -> None:
        archive_root = self.archive_root_var.get().strip()
        weight_table_path = self.weight_table_var.get().strip()
        if not archive_root:
            messagebox.showerror("设置错误", "请先选择留档根目录。", parent=self)
            return
        if not os.path.isdir(archive_root):
            messagebox.showerror("设置错误", "留档根目录不存在。", parent=self)
            return
        if not weight_table_path:
            messagebox.showerror("设置错误", "请先选择重量表 CSV。", parent=self)
            return
        if not os.path.isfile(weight_table_path):
            messagebox.showerror("设置错误", "重量表 CSV 不存在。", parent=self)
            return
        self.master_app.settings.archive_root = archive_root
        self.master_app.settings.weight_table_path = weight_table_path
        save_settings(self.master_app.settings)
        self.master_app.refresh_settings_status()
        self.destroy()


class BoardGuiApp:
    def __init__(self) -> None:
        self.settings = load_settings()
        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.geometry("1560x860")
        self.root.minsize(1380, 780)
        self.root.configure(background=BG_APP)

        self.brand_icon_small: tk.PhotoImage | None = None
        self.app_icon: tk.PhotoImage | None = None
        self.generate_button: ttk.Button | None = None
        self.preview_image_path: str | None = None
        self.preview_photo: ImageTk.PhotoImage | None = None
        self.preview_source_image: Image.Image | None = None
        self.preview_label: tk.Label | None = None
        self.preview_hint_label: ttk.Label | None = None
        self.thickness_summary_tree: ttk.Treeview | None = None
        self.thickness_summary_meta_var = tk.StringVar(value="生成后显示各厚度用量与重量")
        self.thickness_summary_overview_var = tk.StringVar(value="生成后显示实际整板、面积折算与总重")
        self._preview_render_job: int | None = None
        self._preview_render_size: tuple[int, int] | None = None

        self.filename_var = tk.StringVar()
        self.board_length_var = tk.StringVar(value=self.settings.last_board_length)
        self.board_width_var = tk.StringVar(value=self.settings.last_board_width)
        self.status_var = tk.StringVar(value="请先在菜单“设置”中配置留档根目录和重量表 CSV。")
        self.settings_var = tk.StringVar()
        self.summary_var = tk.StringVar(value="等待设置")
        self.summary_sheets_var = tk.StringVar(value="-")
        self.summary_equivalent_var = tk.StringVar(value="-")
        self.summary_weight_var = tk.StringVar(value="-")
        self.summary_output_var = tk.StringVar(value="未生成文件")
        self.board_hint_var = tk.StringVar(value=f"{self.board_length_var.get()} x {self.board_width_var.get()} mm")

        self.board_length_var.trace_add("write", self._on_dimension_change)
        self.board_width_var.trace_add("write", self._on_dimension_change)

        self.apply_window_icon(self.root)
        self.configure_styles()
        self._build_menu()
        self._build_ui()
        self.refresh_settings_status()
        self.update_generate_state()

    def apply_window_icon(self, window: tk.Tk | tk.Toplevel) -> None:
        if os.path.exists(ICON_ICO_PATH):
            try:
                window.iconbitmap(ICON_ICO_PATH)
            except Exception:
                pass
        if os.path.exists(ICON_PNG_PATH):
            try:
                self.app_icon = tk.PhotoImage(file=ICON_PNG_PATH)
                self.brand_icon_small = self.app_icon.subsample(12, 12)
                window.iconphoto(True, self.app_icon)
            except Exception:
                pass

    def configure_styles(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=BG_APP, foreground=TEXT_PRIMARY, font=("Microsoft YaHei UI", 10))
        style.configure("Card.TFrame", background=BG_SURFACE)
        style.configure("CardTitle.TLabel", background=BG_SURFACE, foreground=TEXT_PRIMARY, font=("Microsoft YaHei UI", 12, "bold"))
        style.configure("DialogTitle.TLabel", background=BG_SURFACE, foreground=TEXT_PRIMARY, font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("FieldLabel.TLabel", background=BG_SURFACE, foreground=TEXT_SECONDARY, font=("Microsoft YaHei UI", 10))
        style.configure("Body.TLabel", background=BG_SURFACE, foreground=TEXT_PRIMARY, font=("Microsoft YaHei UI", 10))
        style.configure("BodyMuted.TLabel", background=BG_SURFACE, foreground=TEXT_SECONDARY, font=("Microsoft YaHei UI", 10))
        style.configure("Subtle.TLabel", background=BG_SURFACE, foreground=TEXT_MUTED, font=("Microsoft YaHei UI", 10))
        style.configure("TEntry", fieldbackground="#ffffff", foreground=TEXT_PRIMARY, bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER, padding=7)
        style.configure("Primary.TButton", background=BG_ACCENT, foreground="#ffffff", borderwidth=0, padding=(18, 10), font=("Microsoft YaHei UI", 10, "bold"))
        style.map("Primary.TButton", background=[("active", BG_ACCENT_HOVER), ("disabled", "#9fbab6")])
        style.configure("Secondary.TButton", background="#edf4f7", foreground=TEXT_PRIMARY, bordercolor=BORDER, lightcolor="#edf4f7", darkcolor="#edf4f7", padding=(14, 9))
        style.map("Secondary.TButton", background=[("active", "#dfeff2")])
        style.configure(
            "Summary.Treeview",
            background=BG_SURFACE,
            fieldbackground=BG_SURFACE,
            foreground=TEXT_PRIMARY,
            rowheight=26,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
            font=("Microsoft YaHei UI", 10),
        )
        style.configure(
            "Summary.Treeview.Heading",
            background=BG_SURFACE,
            foreground=TEXT_MUTED,
            relief="flat",
            font=("Microsoft YaHei UI", 9, "bold"),
        )

    def _build_menu(self) -> None:
        menu_bar = tk.Menu(self.root, tearoff=False, bg="#f8fbff", fg=TEXT_PRIMARY, activebackground="#dcecf0")
        action_menu = tk.Menu(menu_bar, tearoff=False)
        action_menu.add_command(label="生成 CSV 和 PNG", command=self.generate)
        action_menu.add_command(label="清空原始数据", command=self.clear_text)
        action_menu.add_separator()
        action_menu.add_command(label="退出", command=self.root.destroy)
        menu_bar.add_cascade(label="操作", menu=action_menu)
        menu_bar.add_command(label="设置", command=self.open_settings)
        other_menu = tk.Menu(menu_bar, tearoff=False)
        other_menu.add_command(label="关于", command=self.open_about)
        menu_bar.add_cascade(label="其它", menu=other_menu)
        self.root.config(menu=menu_bar)

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        summary = ttk.Frame(self.root, style="Card.TFrame", padding=16)
        summary.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 0))
        for i in range(4):
            summary.columnconfigure(i, weight=1)
        self._create_summary_box(summary, 0, "实际整张", self.summary_sheets_var)
        self._create_summary_box(summary, 1, "面积折算", self.summary_equivalent_var)
        self._create_summary_box(summary, 2, "总重量", self.summary_weight_var)
        self._create_summary_box(summary, 3, "输出状态", self.summary_var)

        body = tk.Frame(self.root, bg=BG_APP, padx=18, pady=18)
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=0)
        body.grid_columnconfigure(1, weight=1)
        body.grid_columnconfigure(2, weight=0)
        body.grid_rowconfigure(0, weight=1)

        left = create_card(body, 18)
        left.grid(row=0, column=0, sticky="nsw", padx=(0, 16))
        left.columnconfigure(0, weight=1)

        ttk.Label(left, text="订单参数", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(left, text="输入文件名与整板尺寸，然后粘贴原始开料数据。", style="Subtle.TLabel").grid(
            row=1, column=0, sticky="w", pady=(4, 14)
        )

        form = ttk.Frame(left, style="Card.TFrame")
        form.grid(row=2, column=0, sticky="ew")
        form.columnconfigure(0, weight=1)
        ttk.Label(form, text="文件名", style="FieldLabel.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.filename_var, width=28).grid(row=1, column=0, sticky="ew", pady=(6, 12))

        dims = ttk.Frame(form, style="Card.TFrame")
        dims.grid(row=2, column=0, sticky="ew")
        dims.columnconfigure(0, weight=1)
        dims.columnconfigure(1, weight=1)

        left_dim = ttk.Frame(dims, style="Card.TFrame")
        left_dim.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        left_dim.columnconfigure(0, weight=1)
        ttk.Label(left_dim, text="整板长度 (mm)", style="FieldLabel.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(left_dim, textvariable=self.board_length_var).grid(row=1, column=0, sticky="ew", pady=(6, 0))

        right_dim = ttk.Frame(dims, style="Card.TFrame")
        right_dim.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        right_dim.columnconfigure(0, weight=1)
        ttk.Label(right_dim, text="整板宽度 (mm)", style="FieldLabel.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(right_dim, textvariable=self.board_width_var).grid(row=1, column=0, sticky="ew", pady=(6, 0))

        meta = ttk.Frame(left, style="Card.TFrame")
        meta.grid(row=3, column=0, sticky="ew", pady=(16, 0))
        meta.columnconfigure(0, weight=1)
        ttk.Label(meta, text="当前配置", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(meta, textvariable=self.settings_var, style="BodyMuted.TLabel", wraplength=280, justify="left").grid(
            row=1, column=0, sticky="w", pady=(8, 0)
        )
        ttk.Label(meta, text="本次整板", style="FieldLabel.TLabel").grid(row=2, column=0, sticky="w", pady=(12, 0))
        ttk.Label(meta, textvariable=self.board_hint_var, style="Body.TLabel").grid(row=3, column=0, sticky="w", pady=(4, 0))

        action = ttk.Frame(left, style="Card.TFrame")
        action.grid(row=4, column=0, sticky="ew", pady=(18, 0))
        action.columnconfigure(0, weight=1)
        self.generate_button = ttk.Button(action, text="生成 CSV 和 PNG", command=self.generate, style="Primary.TButton")
        self.generate_button.grid(row=0, column=0, sticky="ew")
        ttk.Button(action, text="清空原始数据", command=self.clear_text, style="Secondary.TButton").grid(
            row=1, column=0, sticky="ew", pady=(10, 0)
        )

        middle = tk.Frame(body, bg=BG_APP)
        middle.grid(row=0, column=1, sticky="nsew")
        middle.grid_columnconfigure(0, weight=1)
        middle.grid_rowconfigure(0, weight=1)

        editor_card = create_card(middle, 18)
        editor_card.grid(row=0, column=0, sticky="nsew")
        editor_card.columnconfigure(0, weight=1)
        editor_card.rowconfigure(1, weight=1)

        header = ttk.Frame(editor_card, style="Card.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="原始数据", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="示例：595*1220*20mm 1张 / 18厚 600x500 3块 / 500x300 厚18 数量4",
            style="Subtle.TLabel",
        ).grid(row=0, column=1, sticky="e")

        editor_wrap = tk.Frame(editor_card, bg=BORDER, bd=0, highlightthickness=0)
        editor_wrap.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        editor_wrap.grid_rowconfigure(0, weight=1)
        editor_wrap.grid_columnconfigure(0, weight=1)

        self.text = tk.Text(
            editor_wrap,
            wrap="word",
            font=("Microsoft YaHei UI", 11),
            bg=EDITOR_BG,
            fg=TEXT_PRIMARY,
            insertbackground=TEXT_PRIMARY,
            relief="flat",
            padx=14,
            pady=14,
            undo=True,
            spacing1=2,
            spacing3=2,
        )
        self.text.grid(row=0, column=0, sticky="nsew", padx=1, pady=1)
        scrollbar = ttk.Scrollbar(editor_wrap, orient="vertical", command=self.text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.text.configure(yscrollcommand=scrollbar.set)

        footer = create_card(middle, 18)
        footer.grid(row=1, column=0, sticky="ew", pady=(16, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, text="结果摘要", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(footer, textvariable=self.status_var, style="BodyMuted.TLabel", wraplength=760, justify="left").grid(
            row=1, column=0, sticky="w", pady=(10, 0)
        )
        ttk.Label(footer, textvariable=self.summary_output_var, style="Subtle.TLabel", wraplength=760, justify="left").grid(
            row=2, column=0, sticky="w", pady=(8, 0)
        )

        preview_card = create_card(body, 18)
        preview_card.grid(row=0, column=2, sticky="nsew", padx=(16, 0))
        preview_card.columnconfigure(0, weight=1)
        preview_card.rowconfigure(1, weight=0)
        preview_card.rowconfigure(2, weight=1)
        preview_card.rowconfigure(3, weight=0)
        ttk.Label(preview_card, text="排板图预览", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")

        summary_panel = ttk.Frame(preview_card, style="Card.TFrame")
        summary_panel.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        summary_panel.columnconfigure(0, weight=1)
        ttk.Label(summary_panel, text="板材总明细", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(summary_panel, textvariable=self.thickness_summary_meta_var, style="Subtle.TLabel").grid(
            row=1, column=0, sticky="w", pady=(4, 8)
        )
        ttk.Label(summary_panel, textvariable=self.thickness_summary_overview_var, style="Body.TLabel").grid(
            row=2, column=0, sticky="w", pady=(0, 8)
        )

        summary_table_frame = tk.Frame(summary_panel, bg=BORDER, bd=0, highlightthickness=0)
        summary_table_frame.grid(row=3, column=0, sticky="ew")
        summary_table_frame.columnconfigure(0, weight=1)
        summary_table_frame.rowconfigure(0, weight=1)

        self.thickness_summary_tree = ttk.Treeview(
            summary_table_frame,
            columns=("thickness", "equivalent", "weight"),
            show="headings",
            height=6,
            style="Summary.Treeview",
        )
        self.thickness_summary_tree.heading("thickness", text="厚度")
        self.thickness_summary_tree.heading("equivalent", text="用量(张)")
        self.thickness_summary_tree.heading("weight", text="重量(kg)")
        self.thickness_summary_tree.column("thickness", width=88, anchor="w", stretch=False)
        self.thickness_summary_tree.column("equivalent", width=104, anchor="center", stretch=False)
        self.thickness_summary_tree.column("weight", width=104, anchor="center", stretch=False)
        self.thickness_summary_tree.grid(row=0, column=0, sticky="nsew", padx=1, pady=1)
        summary_scrollbar = ttk.Scrollbar(summary_table_frame, orient="vertical", command=self.thickness_summary_tree.yview)
        summary_scrollbar.grid(row=0, column=1, sticky="ns")
        self.thickness_summary_tree.configure(yscrollcommand=summary_scrollbar.set)

        preview_wrap = tk.Frame(preview_card, bg=BORDER, bd=0, highlightthickness=0, cursor="hand2")
        preview_wrap.grid(row=2, column=0, sticky="nsew", pady=(12, 0))
        preview_wrap.grid_rowconfigure(0, weight=1)
        preview_wrap.grid_columnconfigure(0, weight=1)
        preview_wrap.bind("<Button-1>", self.open_preview_dialog)

        self.preview_label = tk.Label(
            preview_wrap,
            bg="#f3f7fb",
            fg=TEXT_MUTED,
            text="暂无排板图",
            font=("Microsoft YaHei UI", 10),
            justify="center",
        )
        self.preview_label.grid(row=0, column=0, sticky="nsew", padx=1, pady=1)
        self.preview_label.bind("<Button-1>", self.open_preview_dialog)
        self.preview_label.bind("<Configure>", self._on_preview_resize)

        self.preview_hint_label = ttk.Label(
            preview_card,
            text="生成后可在此预览，点击打开放大窗口。",
            style="Subtle.TLabel",
            justify="left",
            wraplength=320,
        )
        self.preview_hint_label.grid(row=3, column=0, sticky="w", pady=(10, 0))
        self.update_thickness_summary([], "生成后显示各厚度用量与重量")

    def _create_summary_box(self, parent: ttk.Frame, column: int, title: str, variable: tk.StringVar) -> None:
        box = ttk.Frame(parent, style="Card.TFrame", padding=(14, 12))
        box.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 10, 0))
        bg = tk.Frame(box, bg=BG_SURFACE_ALT, highlightbackground=BORDER, highlightthickness=1, bd=0)
        bg.pack(fill="both", expand=True)
        tk.Label(bg, text=title, bg=BG_SURFACE_ALT, fg=TEXT_MUTED, font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=10, pady=(10, 4))
        tk.Label(bg, textvariable=variable, bg=BG_SURFACE_ALT, fg=TEXT_PRIMARY, font=("Microsoft YaHei UI", 16, "bold")).pack(
            anchor="w", padx=10, pady=(0, 10)
        )

    def update_thickness_summary(self, rows: list[tuple[str, str, str]], meta_text: str | None = None) -> None:
        self.thickness_summary_meta_var.set(meta_text or "生成后显示各厚度用量与重量")
        if self.thickness_summary_tree is None:
            return
        for item in self.thickness_summary_tree.get_children():
            self.thickness_summary_tree.delete(item)
        for thickness, equivalent, weight in rows:
            self.thickness_summary_tree.insert("", "end", values=(thickness, equivalent, weight))

    def _on_dimension_change(self, *_args: object) -> None:
        self.board_hint_var.set(f"{self.board_length_var.get().strip() or '-'} x {self.board_width_var.get().strip() or '-'} mm")

    def refresh_settings_status(self) -> None:
        archive_text = self.settings.archive_root or "未设置"
        weight_text = self.settings.weight_table_path or "未设置"
        self.settings_var.set(f"留档根目录：{archive_text}\n重量表：{weight_text}")
        self._on_dimension_change()
        self.update_generate_state()

    def update_generate_state(self) -> None:
        ready = (
            bool(self.settings.archive_root)
            and os.path.isdir(self.settings.archive_root)
            and bool(self.settings.weight_table_path)
            and os.path.isfile(self.settings.weight_table_path)
        )
        if self.generate_button is not None:
            self.generate_button.state(["!disabled"] if ready else ["disabled"])
        if ready:
            self.summary_var.set("配置已就绪")
        else:
            self.summary_var.set("等待设置")
            self.status_var.set("请先在菜单“设置”中选择有效的留档根目录和重量表 CSV。")

    def open_settings(self) -> None:
        SettingsDialog(self, self.settings)

    def open_about(self) -> None:
        AboutDialog(self)

    def clear_text(self) -> None:
        self.text.delete("1.0", tk.END)

    def validate_inputs(self) -> tuple[str, float, float, str]:
        filename = self.filename_var.get().strip().strip('"')
        if not filename:
            raise ValueError("请先输入文件名。")
        board_length = float(self.board_length_var.get().strip())
        board_width = float(self.board_width_var.get().strip())
        if board_length <= 20 or board_width <= 20:
            raise ValueError("整板长度和宽度必须大于 20 mm。")
        raw_text = self.text.get("1.0", tk.END).strip()
        if not raw_text:
            raise ValueError("请先粘贴原始数据。")
        return filename, board_length, board_width, raw_text

    def set_busy(self, busy: bool) -> None:
        self.root.config(cursor="watch" if busy else "")
        if self.generate_button is not None:
            self.generate_button.state(["disabled"] if busy else ["!disabled"])

    def _on_preview_resize(self, _event: tk.Event) -> None:
        self.schedule_preview_render()

    def schedule_preview_render(self) -> None:
        if self.preview_source_image is None or self.preview_label is None:
            return
        if self._preview_render_job is not None:
            self.root.after_cancel(self._preview_render_job)
        self._preview_render_job = self.root.after(80, self.render_preview_thumbnail)

    def clear_preview(self) -> None:
        self.preview_image_path = None
        self.preview_source_image = None
        self.preview_photo = None
        if self.preview_label is not None:
            self.preview_label.configure(image="", text="暂无排板图")
        if self.preview_hint_label is not None:
            self.preview_hint_label.configure(text="生成后可在此预览，点击打开放大窗口。")

    def set_preview_image(self, image_path: str) -> None:
        self.preview_image_path = image_path
        self.preview_source_image = Image.open(image_path)
        self._preview_render_size = None
        self.render_preview_thumbnail()
        if self.preview_hint_label is not None:
            self.preview_hint_label.configure(text=f"点击打开预览窗口\n{image_path}")

    def render_preview_thumbnail(self) -> None:
        self._preview_render_job = None
        if self.preview_label is None or self.preview_source_image is None:
            return
        width = max(self.preview_label.winfo_width(), 320)
        height = max(self.preview_label.winfo_height(), 420)
        width = min(width, 560)
        height = min(height, 420)
        size = (width, height)
        if self._preview_render_size == size and self.preview_photo is not None:
            return
        image = self.preview_source_image.copy()
        image.thumbnail((width - 16, height - 16), Image.Resampling.BILINEAR)
        self.preview_photo = ImageTk.PhotoImage(image)
        self.preview_label.configure(image=self.preview_photo, text="")
        self._preview_render_size = size

    def open_preview_dialog(self, _event: tk.Event | None = None) -> None:
        if not self.preview_image_path or not os.path.exists(self.preview_image_path):
            messagebox.showinfo("预览", "当前还没有可预览的排板图。", parent=self.root)
            return
        ImagePreviewDialog(self, self.preview_image_path)

    def generate(self) -> None:
        ready = (
            bool(self.settings.archive_root)
            and os.path.isdir(self.settings.archive_root)
            and bool(self.settings.weight_table_path)
            and os.path.isfile(self.settings.weight_table_path)
        )
        if not ready:
            messagebox.showerror("配置错误", "请先在菜单“设置”中选择有效的留档根目录和重量表 CSV。", parent=self.root)
            return
        try:
            filename, board_length, board_width, raw_text = self.validate_inputs()
        except Exception as exc:
            messagebox.showerror("输入错误", str(exc), parent=self.root)
            return

        self.settings.last_board_length = self.board_length_var.get().strip()
        self.settings.last_board_width = self.board_width_var.get().strip()
        save_settings(self.settings)
        self.refresh_settings_status()

        self.set_busy(True)
        self.status_var.set("正在生成，请稍候...")
        self.summary_var.set("处理中")
        self.summary_output_var.set("正在写入 CSV 与 PNG")

        worker = threading.Thread(
            target=self._generate_worker,
            args=(filename, board_length, board_width, raw_text),
            daemon=True,
        )
        worker.start()

    def _generate_worker(self, filename: str, board_length: float, board_width: float, raw_text: str) -> None:
        try:
            csv_path, _rows, _ = board_data_to_csv.convert_board_data_to_csv(
                raw_text,
                filename,
                self.settings.archive_root,
                self.settings.weight_table_path,
            )
            results, _ = board_cut_optimizer.generate_layout_outputs(
                csv_path,
                board_length,
                board_width,
                weight_table_path=self.settings.weight_table_path,
            )
            summary_rows = [
                (
                    board_cut_optimizer.fmt_number(result.thickness),
                    f"{result.sheet_equivalent:.1f}",
                    f"{result.total_weight_kg:.1f}" if result.total_weight_kg > 0 else "-",
                )
                for result in results
            ]
            png_path = board_cut_optimizer.default_report_png_path(csv_path)
            total_equivalent = board_cut_optimizer.ceil_to_tenth(sum(result.sheet_equivalent for result in results))
            total_weight = sum(result.total_weight_kg for result in results)
            total_integer = sum(result.integer_sheets for result in results)
            summary = (
                f"已生成：{csv_path} | {png_path} | 实际整张 {total_integer} 张 | "
                f"面积折算 {total_equivalent:.1f} 张 | 总重量 {total_weight:.1f} kg"
            )
            self.root.after(
                0,
                self._on_generate_success,
                summary,
                csv_path,
                png_path,
                total_integer,
                total_equivalent,
                total_weight,
                summary_rows,
            )
        except Exception as exc:
            self.root.after(0, self._on_generate_error, str(exc))

    def _on_generate_success(
        self,
        summary: str,
        csv_path: str,
        png_path: str,
        total_integer: int,
        total_equivalent: float,
        total_weight: float,
        summary_rows: list[tuple[str, str, str]],
    ) -> None:
        self.set_busy(False)
        self.update_generate_state()
        self.status_var.set(summary)
        self.summary_var.set("已完成")
        self.summary_sheets_var.set(f"{total_integer} 张")
        self.summary_equivalent_var.set(f"{total_equivalent:.1f} 张")
        self.summary_weight_var.set(f"{total_weight:.1f} kg")
        self.summary_output_var.set(f"CSV：{csv_path}\nPNG：{png_path}")
        self.thickness_summary_overview_var.set(
            f"实际整板 {total_integer} 张 · 面积折算 {total_equivalent:.1f} 张 · 总重 {total_weight:.1f} kg"
        )
        self.update_thickness_summary(
            summary_rows,
            f"厚度 / 用量(张) / 重量(kg)",
        )
        self.set_preview_image(png_path)
        messagebox.showinfo("生成完成", summary, parent=self.root)

    def _on_generate_error(self, message: str) -> None:
        self.set_busy(False)
        self.status_var.set(f"生成失败：{message}")
        self.summary_var.set("失败")
        self.summary_output_var.set(message)
        messagebox.showerror("生成失败", message, parent=self.root)

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    BoardGuiApp().run()


if __name__ == "__main__":
    main()
