from __future__ import annotations

import json
import math
import os
import re
import sys
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

import matplotlib
matplotlib.use("TkAgg")
from matplotlib import rcParams
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from PIL import Image, ImageDraw, ImageEnhance, ImageOps, ImageTk

import osu_data
from pp_calc import PPCalculator
from osu_data import (
    BeatmapInfo,
    Score,
    attach_beatmaps,
    load_cache,
    mode_name,
    mode_short,
    parse_scores_db,
    save_cache,
    scan_beatmaps,
)

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
CACHE_PATH = BASE_DIR / "beatmap_cache.json"

APP_TITLE = "osu! 本地成绩墙"
FONT = "Microsoft YaHei UI"
BG = "#101018"
SURFACE = "#181824"
SURFACE_2 = "#202030"
SURFACE_3 = "#29293c"
TEXT = "#f4f4f8"
MUTED = "#9a9aab"
BORDER = "#303046"
PINK = "#ff66aa"
PINK_DARK = "#e24f92"
GREEN = "#5ee39a"
BLUE = "#66ccff"
CYAN = "#4cc9f0"
GOLD = "#ffd166"
RED = "#ff6b6b"
PURPLE = "#b388ff"

GRADE_COLORS = {
    "SS": GOLD,
    "S": GOLD,
    "A": GREEN,
    "B": BLUE,
    "C": PURPLE,
    "D": RED,
}

rcParams["font.sans-serif"] = [
    "Microsoft YaHei", "Microsoft YaHei UI", "SimHei", "Arial Unicode MS"
]
rcParams["axes.unicode_minus"] = False

MODE_COLORS = {
    0: PINK,
    1: "#ffb86b",
    2: GREEN,
    3: PURPLE,
}

MODE_FILTERS = {
    "osu!standard": 0,
    "osu!taiko": 1,
    "osu!catch": 2,
    "osu!mania": 3,
}
MODE_CHOICES = list(MODE_FILTERS.keys())

SORT_OPTIONS = [
    "时间：最新优先",
    "分数：从高到低",
    "准确率：从高到低",
    "歌曲：A-Z",
    "难度：A-Z",
]


def enable_high_dpi() -> None:
    if sys.platform != "win32":
        return
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            from ctypes import windll
            windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        value = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def save_config(data: dict) -> None:
    CONFIG_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def valid_osu_dir(path: Path) -> bool:
    return (path / "scores.db").exists() and (path / "Songs").exists()


class LoadingDialog:
    def __init__(self, root: tk.Tk, title: str = "正在扫描谱面库") -> None:
        self.root = root
        self.top = tk.Toplevel(root)
        self.top.title(title)
        self.top.configure(bg=SURFACE)
        self.top.resizable(False, False)
        self.top.transient(root)
        self.top.geometry("500x160")
        self.top.update_idletasks()
        x = (self.top.winfo_screenwidth() - 500) // 2
        y = (self.top.winfo_screenheight() - 160) // 2
        self.top.geometry(f"500x160+{x}+{y}")

        tk.Label(
            self.top, text=title, bg=SURFACE, fg=TEXT,
            font=(FONT, 14, "bold"), anchor="w",
        ).pack(fill="x", padx=24, pady=(20, 6))
        self.status = tk.StringVar(value="正在读取谱面文件...")
        tk.Label(
            self.top, textvariable=self.status, bg=SURFACE, fg=MUTED,
            font=(FONT, 9), anchor="w",
        ).pack(fill="x", padx=24, pady=(0, 10))
        self.progress = ttk.Progressbar(
            self.top, mode="determinate", maximum=100
        )
        self.progress.pack(fill="x", padx=24)
        self.top.grab_set()
        self.top.update()

    def update_progress(self, done: int, total: int, name: str) -> None:
        value = (done / total * 100.0) if total else 0
        self.progress["value"] = value
        self.status.set(f"{done} / {total}  {name}")
        self.top.update_idletasks()

    def close(self) -> None:
        try:
            self.top.grab_release()
        except tk.TclError:
            pass
        self.top.destroy()

class ScoreWallApp:
    CARD_W = 270
    CARD_H = 170
    GAP = 16
    MARGIN = 24
    IMAGE_CACHE_SIZE = 64

    def __init__(
        self,
        root: tk.Tk,
        osu_dir: Path,
        scores: list[Score],
        beatmaps: dict[str, BeatmapInfo],
    ) -> None:
        self.root = root
        self.osu_dir = osu_dir
        self.scores = scores
        self.beatmaps = beatmaps
        self.filtered: list[Score] = list(scores)
        self.star_filter: tuple[float, float | None] | None = None
        saved_mode = str(load_config().get("default_mode", "osu!standard"))
        self.default_mode = (
            saved_mode if saved_mode in MODE_FILTERS else "osu!standard"
        )

        self._image_cache: OrderedDict[tuple, ImageTk.PhotoImage] = OrderedDict()
        self._visible_images: list[ImageTk.PhotoImage] = []
        self._redraw_job: str | None = None
        self._filter_job: str | None = None
        self._last_visible_key: tuple | None = None
        self._last_canvas_size: tuple[int, int] = (0, 0)
        self._cols = 1
        self._x0 = self.MARGIN
        self._scroll_height = 1
        self._current_detail: tk.Toplevel | None = None
        self._current_history: tk.Toplevel | None = None
        self._current_pp_window: tk.Toplevel | None = None
        self.pp_calculator = PPCalculator()

        self._configure_root()
        self._configure_style()
        self._build_header()
        self._build_toolbar()
        self._build_canvas()
        self._build_footer()

        self.font_title = tkfont.Font(family=FONT, size=10, weight="bold")
        self.font_meta = tkfont.Font(family=FONT, size=8)
        self.font_small = tkfont.Font(family=FONT, size=7)
        self.font_score = tkfont.Font(family=FONT, size=11, weight="bold")
        self.font_grade = tkfont.Font(family=FONT, size=10, weight="bold")

        self._refresh_stats()
        self._apply_filters(initial=True)
        self.root.after(80, self._relayout)
        self.root.after(300, self._relayout)

    def _configure_root(self) -> None:
        self.root.title(APP_TITLE)
        self.root.configure(bg=BG)
        self.root.minsize(1080, 700)
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        width = min(1480, max(1000, screen_w - 80))
        height = min(920, max(680, screen_h - 100))
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.bind("<Configure>", self._on_window_configure, add="+")

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", font=(FONT, 10))
        style.configure(
            "Dark.TButton", background=SURFACE_2, foreground=TEXT,
            borderwidth=0, padding=(12, 8), font=(FONT, 9),
        )
        style.map(
            "Dark.TButton",
            background=[("active", SURFACE_3), ("pressed", "#34344a")],
        )
        style.configure(
            "Accent.TButton", background=PINK, foreground="#191019",
            borderwidth=0, padding=(13, 8), font=(FONT, 9, "bold"),
        )
        style.map(
            "Accent.TButton",
            background=[("active", "#ff85bb"), ("pressed", PINK_DARK)],
        )
        style.configure(
            "Dark.TCombobox",
            fieldbackground=SURFACE_2, background=SURFACE_2,
            foreground=TEXT, arrowcolor=TEXT, borderwidth=0,
            padding=6,
        )
        style.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", SURFACE_2)],
            foreground=[("readonly", TEXT)],
            selectbackground=[("readonly", SURFACE_2)],
            selectforeground=[("readonly", TEXT)],
        )
        style.configure(
            "Vertical.TScrollbar", background=SURFACE_2,
            troughcolor=BG, borderwidth=0, arrowcolor=MUTED,
        )
        style.configure(
            "History.Treeview",
            background=SURFACE, fieldbackground=SURFACE,
            foreground=TEXT, rowheight=30, borderwidth=0,
            font=(FONT, 9),
        )
        style.configure(
            "History.Treeview.Heading",
            background=SURFACE_2, foreground=MUTED,
            borderwidth=0, relief="flat", padding=(8, 8),
            font=(FONT, 9, "bold"),
        )
        style.map(
            "History.Treeview",
            background=[("selected", "#3b3f4a")],
            foreground=[("selected", TEXT)],
        )
        style.map(
            "History.Treeview.Heading",
            background=[("active", SURFACE_3)],
        )

        self.root.option_add("*TCombobox*Listbox.background", SURFACE_2)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", PINK)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#161616")

    def _make_button_icon(self, kind: str, size: int = 18) -> ImageTk.PhotoImage:
        color = "#e8e8ef"
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        if kind == "refresh":
            draw.arc((3, 3, size - 3, size - 3), 35, 320, fill=color, width=2)
            draw.polygon(
                [(size - 7, 1), (size - 1, 5), (size - 7, 8)], fill=color
            )
        elif kind == "scan":
            draw.ellipse((2, 2, 12, 12), outline=color, width=2)
            draw.line((11, 11, size - 2, size - 2), fill=color, width=2)
        elif kind == "folder":
            draw.rounded_rectangle(
                (1, 5, size - 1, size - 2), radius=2,
                outline=color, width=2,
            )
            draw.line((3, 5, 7, 5), fill=color, width=2)
            draw.line((7, 5, 9, 3), fill=color, width=2)
            draw.line((9, 3, size - 3, 3), fill=color, width=2)
        elif kind == "pp":
            draw.polygon(
                [(10, 2), (4, 10), (8, 10), (6, 16), (14, 7), (10, 7)],
                fill=color,
            )
        elif kind == "settings":
            draw.ellipse((5, 5, size - 5, size - 5), outline=color, width=2)
            draw.ellipse((8, 8, size - 8, size - 8), outline=color, width=2)
            cx = cy = size / 2
            for angle in range(0, 360, 45):
                rad = math.radians(angle)
                x1 = cx + math.cos(rad) * 5
                y1 = cy + math.sin(rad) * 5
                x2 = cx + math.cos(rad) * 8
                y2 = cy + math.sin(rad) * 8
                draw.line((x1, y1, x2, y2), fill=color, width=2)
        return ImageTk.PhotoImage(image)

    def _build_header(self) -> None:
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=24, pady=(20, 10))
        header.columnconfigure(0, weight=1)

        title_row = tk.Frame(header, bg=BG)
        title_row.grid(row=0, column=0, sticky="w")
        tk.Label(
            title_row, text="osu!", bg=BG, fg=PINK,
            font=(FONT, 28, "bold"),
        ).pack(side="left")
        tk.Label(
            title_row, text=" 本地成绩墙", bg=BG, fg=TEXT,
            font=(FONT, 25, "bold"),
        ).pack(side="left")

        self.subtitle_var = tk.StringVar()
        tk.Label(
            header, textvariable=self.subtitle_var, bg=BG, fg=MUTED,
            font=(FONT, 9), anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))

        actions = tk.Frame(header, bg=BG)
        actions.grid(row=0, column=1, rowspan=2, sticky="e")
        self._button_icons = {
            "refresh": self._make_button_icon("refresh"),
            "pp": self._make_button_icon("pp"),
            "scan": self._make_button_icon("scan"),
            "folder": self._make_button_icon("folder"),
            "settings": self._make_button_icon("settings"),
        }
        ttk.Button(
            actions, text="刷新数据", image=self._button_icons["refresh"],
            compound="left", style="Dark.TButton",
            command=self.refresh_scores,
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            actions, text="可获取PP", image=self._button_icons["pp"],
            compound="left", style="Dark.TButton",
            command=self.open_pp_opportunities,
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            actions, text="重新扫描谱面", image=self._button_icons["scan"],
            compound="left", style="Dark.TButton",
            command=self.rebuild_beatmap_cache,
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            actions, text="osu! 路径", image=self._button_icons["folder"],
            compound="left", style="Dark.TButton",
            command=self.change_osu_folder,
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            actions, text="设置", image=self._button_icons["settings"],
            compound="left", style="Dark.TButton",
            command=self.open_settings,
        ).pack(side="left")

        self.stats_bar = tk.Frame(self.root, bg=BG)
        self.stats_bar.pack(fill="x", padx=24, pady=(2, 14))
        self.stat_vars = {
            "scores": tk.StringVar(),
            "maps": tk.StringVar(),
            "best": tk.StringVar(),
            "acc": tk.StringVar(),
        }
        labels = [
            ("成绩", "scores", PINK),
            ("谱面", "maps", BLUE),
            ("最高分", "best", GOLD),
            ("平均准确率", "acc", GREEN),
        ]
        for index, (label, key, color) in enumerate(labels):
            box = tk.Frame(
                self.stats_bar, bg=SURFACE,
                highlightbackground=BORDER, highlightthickness=1,
            )
            box.grid(
                row=0, column=index, sticky="ew",
                padx=(0 if index == 0 else 10, 0),
            )
            self.stats_bar.columnconfigure(index, weight=1)
            tk.Label(
                box, text=label, bg=SURFACE, fg=MUTED,
                font=(FONT, 8), anchor="w",
            ).pack(fill="x", padx=13, pady=(8, 1))
            tk.Label(
                box, textvariable=self.stat_vars[key], bg=SURFACE,
                fg=color, font=(FONT, 14, "bold"), anchor="w",
            ).pack(fill="x", padx=13, pady=(0, 8))

    def _build_toolbar(self) -> None:
        toolbar = tk.Frame(self.root, bg=BG)
        toolbar.pack(fill="x", padx=24, pady=(0, 12))

        # 第一行：搜索框和当前显示数量
        search_row = tk.Frame(toolbar, bg=BG)
        search_row.pack(fill="x")
        tk.Label(
            search_row, text="搜索", bg=BG, fg=MUTED, font=(FONT, 9)
        ).pack(side="left", padx=(0, 6))
        self.search_var = tk.StringVar()
        self.search_entry = tk.Entry(
            search_row, textvariable=self.search_var, bg=SURFACE_2,
            fg=TEXT, insertbackground=TEXT, relief="flat",
            highlightthickness=1, highlightbackground=BORDER,
            highlightcolor=PINK, font=(FONT, 10), width=30,
        )
        self.search_entry.pack(
            side="left", fill="x", expand=True, ipady=6
        )
        self.search_var.trace_add("write", self._schedule_filter)
        self.count_var = tk.StringVar()
        tk.Label(
            search_row, textvariable=self.count_var, bg=BG, fg=MUTED,
            font=(FONT, 9),
        ).pack(side="right", padx=(12, 0))

        # 第二行：模式、星级、mania 键数、排序和筛选说明
        filter_row = tk.Frame(toolbar, bg=BG)
        filter_row.pack(fill="x", pady=(8, 0))

        mode_frame = tk.Frame(filter_row, bg=BG)
        mode_frame.pack(side="left", padx=(0, 16))
        tk.Label(
            mode_frame, text="模式", bg=BG, fg=MUTED, font=(FONT, 9)
        ).pack(side="left", padx=(0, 6))
        self.mode_var = tk.StringVar(value=self.default_mode)
        self.mode_combo = ttk.Combobox(
            mode_frame, textvariable=self.mode_var,
            values=MODE_CHOICES, state="readonly",
            style="Dark.TCombobox", width=13,
        )
        self.mode_combo.pack(side="left")
        self.mode_combo.bind("<<ComboboxSelected>>", self._on_mode_change)

        star_frame = tk.Frame(filter_row, bg=BG)
        star_frame.pack(side="left", padx=(0, 16))
        tk.Label(
            star_frame, text="等级区间", bg=BG, fg=MUTED, font=(FONT, 9)
        ).pack(side="left", padx=(0, 6))
        self.star_start_var = tk.StringVar()
        self.star_end_var = tk.StringVar()
        tk.Label(
            star_frame, text="起始", bg=BG, fg=MUTED, font=(FONT, 8)
        ).pack(side="left", padx=(0, 4))
        self.star_start_entry = tk.Entry(
            star_frame, textvariable=self.star_start_var,
            bg=SURFACE_2, fg=TEXT, insertbackground=TEXT,
            relief="flat", highlightthickness=1,
            highlightbackground=BORDER, highlightcolor=PINK,
            font=(FONT, 10), width=6,
        )
        self.star_start_entry.pack(side="left", ipady=6)
        tk.Label(
            star_frame, text="—", bg=BG, fg=MUTED, font=(FONT, 10)
        ).pack(side="left", padx=6)
        tk.Label(
            star_frame, text="结束", bg=BG, fg=MUTED, font=(FONT, 8)
        ).pack(side="left", padx=(0, 4))
        self.star_end_entry = tk.Entry(
            star_frame, textvariable=self.star_end_var,
            bg=SURFACE_2, fg=TEXT, insertbackground=TEXT,
            relief="flat", highlightthickness=1,
            highlightbackground=BORDER, highlightcolor=PINK,
            font=(FONT, 10), width=6,
        )
        self.star_end_entry.pack(side="left", ipady=6)
        self.star_start_entry.bind(
            "<Return>", lambda _event: self._apply_star_input()
        )
        self.star_end_entry.bind(
            "<Return>", lambda _event: self._apply_star_input()
        )
        ttk.Button(
            star_frame, text="应用", style="Dark.TButton",
            command=self._apply_star_input,
        ).pack(side="left", padx=(6, 0))

        self.mania_keys_frame = tk.Frame(filter_row, bg=BG)
        tk.Label(
            self.mania_keys_frame, text="键数", bg=BG,
            fg=MUTED, font=(FONT, 9),
        ).pack(side="left", padx=(0, 6))
        key_counts = sorted({
            score.beatmap.key_count
            for score in self.scores
            if score.mode == 3
            and score.beatmap is not None
            and score.beatmap.key_count > 0
        })
        self.mania_key_choices = ["全部键数"] + [
            f"{count}K" for count in key_counts
        ]
        self.mania_keys_var = tk.StringVar(value="全部键数")
        self.mania_keys_combo = ttk.Combobox(
            self.mania_keys_frame, textvariable=self.mania_keys_var,
            values=self.mania_key_choices, state="readonly",
            style="Dark.TCombobox", width=9,
        )
        self.mania_keys_combo.pack(side="left")
        self.mania_keys_combo.bind(
            "<<ComboboxSelected>>", self._on_filter_change
        )

        self.sort_frame = tk.Frame(filter_row, bg=BG)
        self.sort_frame.pack(side="left", padx=(0, 16))
        tk.Label(
            self.sort_frame, text="排序", bg=BG, fg=MUTED, font=(FONT, 9)
        ).pack(side="left", padx=(0, 6))
        self.sort_var = tk.StringVar(value=SORT_OPTIONS[0])
        self.sort_combo = ttk.Combobox(
            self.sort_frame, textvariable=self.sort_var,
            values=SORT_OPTIONS, state="readonly",
            style="Dark.TCombobox", width=15,
        )
        self.sort_combo.pack(side="left")
        self.sort_combo.bind("<<ComboboxSelected>>", self._on_filter_change)

        self.clear_filter_button = ttk.Button(
            filter_row, text="清空筛选", style="Dark.TButton",
            command=self.clear_filters,
        )
        self.clear_filter_button.pack(side="left")

        self._update_mania_controls()

    def _on_mode_change(self, _event=None) -> None:
        if self.mode_var.get() != "osu!mania":
            self.mania_keys_var.set("全部键数")
        self._apply_filters()

    def _set_star_inputs(self, start: str, end: str) -> None:
        self.star_start_var.set(start)
        self.star_end_var.set(end)

    @staticmethod
    def _parse_star_filter(start_text: str, end_text: str) -> tuple[float, float] | None:
        start_text = start_text.strip()
        end_text = end_text.strip()
        if not start_text and not end_text:
            return None
        if not start_text or not end_text:
            raise ValueError("请同时填写起始等级和结束等级")
        try:
            low = float(start_text)
            high = float(end_text)
        except ValueError as exc:
            raise ValueError("等级必须是数字，例如 4.0 和 5.0") from exc
        if low < 0:
            raise ValueError("起始等级不能小于 0")
        if high <= low:
            raise ValueError("结束等级必须大于起始等级")
        return low, high

    def _apply_star_input(self) -> None:
        try:
            parsed = self._parse_star_filter(
                self.star_start_var.get(),
                self.star_end_var.get(),
            )
        except ValueError as exc:
            messagebox.showwarning(
                "等级区间格式",
                f"{exc}\n\n示例：起始等级 4.0，结束等级 5.0",
                parent=self.root,
            )
            return
        self.star_filter = parsed
        self._apply_filters()

    def _update_mania_controls(self) -> None:
        if self.mode_var.get() == "osu!mania":
            if not self.mania_keys_frame.winfo_ismapped():
                self.mania_keys_frame.pack(side="left", padx=(0, 16))
        else:
            self.mania_keys_var.set("全部键数")
            self.mania_keys_frame.pack_forget()

    def _sync_filter_controls(self) -> None:
        if self.star_filter is None:
            self._set_star_inputs("", "")
        else:
            low, high = self.star_filter
            self._set_star_inputs(f"{low:g}", f"{high:g}")
        self._update_mania_controls()
        if self.mode_var.get() != "osu!mania":
            self.mania_keys_var.set("全部键数")

    def _build_canvas(self) -> None:
        content = tk.Frame(self.root, bg=BG)
        content.pack(fill="both", expand=True, padx=24)
        content.columnconfigure(0, weight=1)
        content.columnconfigure(1, weight=0, minsize=360)
        content.rowconfigure(0, weight=1)

        shell = tk.Frame(content, bg=BG)
        shell.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(0, weight=1)

        self.canvas = tk.Canvas(
            shell, bg=BG, highlightthickness=0, bd=0,
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")

        self.vbar = ttk.Scrollbar(
            shell, orient="vertical", command=self.canvas.yview,
            style="Vertical.TScrollbar",
        )
        self.vbar.grid(row=0, column=1, sticky="ns", padx=(8, 0))
        self.canvas.configure(yscrollcommand=self._on_yscroll)

        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Button-1>", self._on_canvas_click)
        self.canvas.bind("<Motion>", self._on_canvas_motion)
        self.canvas.bind("<Leave>", lambda _e: self.canvas.configure(cursor=""))

        self._build_pie_panel(content)
        self.root.after(120, self._poll_canvas_size)

    def _build_pie_panel(self, parent: tk.Frame) -> None:
        panel = tk.Frame(
            parent, bg=SURFACE,
            highlightbackground=BORDER, highlightthickness=1, bd=0,
        )
        panel.grid(row=0, column=1, sticky="new")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(2, weight=0)

        self.pie_total_var = tk.StringVar(value="")
        header = tk.Frame(panel, bg=SURFACE)
        header.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 4))
        header.columnconfigure(0, weight=1)
        tk.Label(
            header, text="难度区间权重", bg=SURFACE, fg=TEXT,
            font=(FONT, 12, "bold"), anchor="w",
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            header, textvariable=self.pie_total_var, bg=SURFACE,
            fg=MUTED, font=(FONT, 8), anchor="e",
        ).grid(row=0, column=1, sticky="e")

        self.pie_info_var = tk.StringVar(
            value="鼠标悬停查看区间；点击扇形筛选对应成绩"
        )
        tk.Label(
            panel, textvariable=self.pie_info_var, bg=SURFACE,
            fg=MUTED, font=(FONT, 8), anchor="w", height=2,
            justify="left", wraplength=430,
        ).grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 4))

        figure = Figure(figsize=(3.3, 2.8), dpi=100, facecolor=SURFACE)
        axes = figure.add_subplot(111)
        chart_holder = tk.Frame(panel, bg=SURFACE, width=340, height=280)
        chart_holder.grid(row=2, column=0, sticky="n", padx=4, pady=4)
        chart_holder.grid_propagate(False)
        chart_holder.columnconfigure(0, weight=1)
        chart_holder.rowconfigure(0, weight=1)
        canvas = FigureCanvasTkAgg(figure, master=chart_holder)
        try:
            canvas._tkcanvas.unbind("<Map>")
            canvas._set_device_pixel_ratio(1.0)
            figure.set_dpi(100)
        except Exception:
            pass
        canvas.get_tk_widget().configure(
            bg=SURFACE, highlightthickness=0, bd=0
        )
        canvas.get_tk_widget().grid(
            row=0, column=0, sticky="nsew"
        )
        self.pie_figure = figure
        self.pie_axes = axes
        self.pie_canvas = canvas
        self.pie_wedges = []
        self.pie_hover_index = -1
        self.pie_data = {
            "keys": [],
            "counts": [],
            "labels": [],
            "colors": [],
            "total": 0,
        }

        tk.Label(
            panel, text="每 1.0 星一个区间 · 按谱面去重",
            bg=SURFACE, fg="#666a74", font=(FONT, 7), anchor="w",
        ).grid(row=3, column=0, sticky="w", padx=12, pady=(0, 8))

        canvas.mpl_connect("motion_notify_event", self._on_pie_hover)
        canvas.mpl_connect("button_press_event", self._on_pie_click)
        canvas.mpl_connect("figure_leave_event", self._on_pie_leave)

    def _build_footer(self) -> None:
        footer = tk.Frame(self.root, bg=BG)
        footer.pack(fill="x", padx=24, pady=(10, 14))
        footer.columnconfigure(0, weight=1)
        self.status_var = tk.StringVar(
            value="单击卡片查看详细成绩 · 滚轮浏览 · 数据来自本机 scores.db"
        )
        tk.Label(
            footer, textvariable=self.status_var,
            bg=BG, fg=MUTED, font=(FONT, 8), anchor="w",
        ).grid(row=0, column=0, sticky="w")
        self.footer_path_var = tk.StringVar()
        tk.Label(
            footer, textvariable=self.footer_path_var, bg=BG,
            fg="#666677", font=(FONT, 8), anchor="e",
        ).grid(row=0, column=1, sticky="e")

    def _refresh_stats(self) -> None:
        total = len(self.scores)
        maps = len({score.beatmap_md5 for score in self.scores})
        best = max((score.score for score in self.scores), default=0)
        avg_acc = (
            sum(score.accuracy for score in self.scores) / total
            if total else 0.0
        )
        self.stat_vars["scores"].set(f"{total:,}")
        self.stat_vars["maps"].set(f"{maps:,}")
        self.stat_vars["best"].set(f"{best:,}")
        self.stat_vars["acc"].set(f"{avg_acc:.2f}%")
        self.subtitle_var.set(
            f"{len(self.scores):,} 条本地成绩 · "
            f"{maps:,} 张谱面 · 玩家 {self.scores[0].player if self.scores else '-'}"
        )
        self.footer_path_var.set(str(self.osu_dir))

    def _on_filter_change(self, _event=None) -> None:
        self._apply_filters()

    def _schedule_filter(self, *_args) -> None:
        if self._filter_job is not None:
            self.root.after_cancel(self._filter_job)
        self._filter_job = self.root.after(220, self._apply_filters)

    def _apply_filters(self, initial: bool = False) -> None:
        self._filter_job = None
        query = self.search_var.get().strip().lower()
        mode = MODE_FILTERS.get(self.mode_var.get(), None)

        result: list[Score] = []
        for score in self.scores:
            if mode is not None and score.mode != mode:
                continue
            if self.star_filter is not None:
                score_map = score.beatmap
                if score_map is None or score_map.star_rating <= 0:
                    continue
                low, high = self.star_filter
                if score_map.star_rating < low:
                    continue
                if high is not None and score_map.star_rating >= high:
                    continue
            if (
                self.mode_var.get() == "osu!mania"
                and self.mania_keys_var.get() != "全部键数"
            ):
                try:
                    wanted_keys = int(self.mania_keys_var.get()[:-1])
                except ValueError:
                    wanted_keys = 0
                score_map = score.beatmap
                if score_map is None or score_map.key_count != wanted_keys:
                    continue
            if query:
                beatmap = score.beatmap
                fields = [
                    score.player,
                    score.beatmap_md5,
                    score.mods_label,
                    score.rank,
                    mode_short(score.mode),
                ]
                if beatmap is not None:
                    fields.extend([
                        beatmap.artist,
                        beatmap.title,
                        beatmap.creator,
                        beatmap.version,
                    ])
                haystack = " ".join(fields).lower()
                if query not in haystack:
                    continue
            result.append(score)

        sort_value = self.sort_var.get()
        if sort_value == "分数：从高到低":
            result.sort(key=lambda score: (score.score, score.timestamp_ticks), reverse=True)
        elif sort_value == "准确率：从高到低":
            result.sort(key=lambda score: (score.accuracy, score.score), reverse=True)
        elif sort_value == "歌曲：A-Z":
            result.sort(
                key=lambda score: (
                    score.beatmap.display_title.lower() if score.beatmap else "",
                    score.beatmap.version.lower() if score.beatmap else "",
                )
            )
        elif sort_value == "难度：A-Z":
            result.sort(
                key=lambda score: (
                    score.beatmap.version.lower() if score.beatmap else "",
                    score.beatmap.display_title.lower() if score.beatmap else "",
                )
            )
        else:
            result.sort(
                key=lambda score: (score.timestamp_ticks, score.score),
                reverse=True,
            )

        self.filtered = result
        self.count_var.set(f"显示 {len(result):,} / {len(self.scores):,}")
        self._sync_filter_controls()

        active: list[str] = []
        if self.star_filter is not None:
            active.append(
                f"星级 {self._star_bucket_label(int(self.star_filter[0]))}"
            )
        if (
            self.mode_var.get() == "osu!mania"
            and self.mania_keys_var.get() != "全部键数"
        ):
            active.append(self.mania_keys_var.get())
        query_text = self.search_var.get().strip()
        if query_text:
            active.append(f"搜索「{query_text}」")
        if active:
            self.status_var.set("筛选：" + " · ".join(active))
        else:
            self.status_var.set(
                "单击卡片查看详细成绩 · 滚轮浏览 · 数据来自本机 scores.db"
            )
        self._refresh_pie_chart()

        if not initial:
            self.canvas.yview_moveto(0)
        self._last_visible_key = None
        self._relayout()

    def clear_filters(self) -> None:
        self.search_var.set("")
        self.mode_var.set(self.default_mode)
        self.sort_var.set(SORT_OPTIONS[0])
        self.star_filter = None
        self.mania_keys_var.set("全部键数")
        self._set_star_inputs("", "")
        self._apply_filters()

    def clear_star_filter(self) -> None:
        self.star_filter = None
        self._set_star_inputs("", "")
        self._apply_filters()

    def _poll_canvas_size(self) -> None:
        try:
            size = (
                self.canvas.winfo_width(),
                self.canvas.winfo_height(),
            )
        except tk.TclError:
            return
        if size[0] > 200 and size != self._last_canvas_size:
            self._last_canvas_size = size
            self._relayout()
        self.root.after(250, self._poll_canvas_size)

    def _on_window_configure(self, event=None) -> None:
        if event is not None and event.widget is not self.root:
            return
        self._last_visible_key = None
        if self._redraw_job is not None:
            try:
                self.root.after_cancel(self._redraw_job)
            except tk.TclError:
                pass
        self._redraw_job = self.root.after(60, self._relayout)

    def _on_canvas_configure(self, _event=None) -> None:
        self._last_visible_key = None
        if self._redraw_job is not None:
            self.root.after_cancel(self._redraw_job)
        self._redraw_job = self.root.after(30, self._relayout)

    def _on_yscroll(self, first: str, last: str) -> None:
        self.vbar.set(first, last)
        if self._redraw_job is not None:
            self.root.after_cancel(self._redraw_job)
        self._redraw_job = self.root.after(25, self._redraw_visible)

    def _on_mousewheel(self, event: tk.Event) -> str:
        delta = int(-event.delta / 120) if event.delta else 0
        if delta == 0:
            delta = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(delta, "units")
        return "break"

    def _relayout(self) -> None:
        self._redraw_job = None
        width = max(self.canvas.winfo_width(), 1)
        height = max(self.canvas.winfo_height(), 1)
        if width <= 200 or height <= 100:
            self.root.after(100, self._relayout)
            return

        self._last_canvas_size = (width, height)
        self._cols = max(
            1,
            (width - 2 * self.MARGIN + self.GAP) // (self.CARD_W + self.GAP),
        )
        grid_width = (
            self._cols * self.CARD_W
            + (self._cols - 1) * self.GAP
        )
        self._x0 = max(self.MARGIN, (width - grid_width) // 2)

        rows = math.ceil(len(self.filtered) / self._cols) if self.filtered else 0
        content_height = (
            2 * self.MARGIN + rows * (self.CARD_H + self.GAP) - self.GAP
            if rows else height
        )
        self._scroll_height = max(height, content_height)
        self.canvas.configure(scrollregion=(0, 0, width, self._scroll_height))
        self._redraw_visible(force=True)

    def _visible_range(self) -> tuple[int, int, int]:
        if not self.filtered:
            return 0, 0, 0
        width = max(self.canvas.winfo_width(), 1)
        height = max(self.canvas.winfo_height(), 1)
        row_height = self.CARD_H + self.GAP
        top = self.canvas.canvasy(0)
        bottom = self.canvas.canvasy(height)
        rows = math.ceil(len(self.filtered) / self._cols)
        first_row = max(0, int(top // row_height) - 1)
        last_row = min(rows - 1, int(bottom // row_height) + 1)
        start = first_row * self._cols
        end = min(len(self.filtered), (last_row + 1) * self._cols)
        return start, end, rows

    def _redraw_visible(self, force: bool = False) -> None:
        self._redraw_job = None
        if not hasattr(self, "canvas"):
            return
        width = max(self.canvas.winfo_width(), 1)
        height = max(self.canvas.winfo_height(), 1)
        if width <= 1 or height <= 1:
            return

        start, end, rows = self._visible_range()
        key = (
            start, end, self._cols, self._x0, width,
            len(self.filtered), self.CARD_W, self.CARD_H,
        )
        if not force and key == self._last_visible_key:
            return
        self._last_visible_key = key

        self.canvas.delete("card")
        self._visible_images.clear()

        if not self.filtered:
            self.canvas.create_text(
                width // 2, height // 2,
                text="没有找到符合条件的成绩",
                fill=MUTED, font=(FONT, 15, "bold"), tags=("card",),
            )
            self.canvas.create_text(
                width // 2, height // 2 + 30,
                text="试试清空搜索条件或切换模式",
                fill="#666677", font=(FONT, 10), tags=("card",),
            )
            return

        for index in range(start, end):
            row, column = divmod(index, self._cols)
            x = self._x0 + column * (self.CARD_W + self.GAP)
            y = self.MARGIN + row * (self.CARD_H + self.GAP)
            self._draw_card(index, x, y)

    def _ellipsize(self, text: str, font: tkfont.Font, max_width: int) -> str:
        text = " ".join(str(text).split())
        if font.measure(text) <= max_width:
            return text
        ellipsis = "..."
        result = text
        while result and font.measure(result + ellipsis) > max_width:
            result = result[:-1]
        return (result.rstrip() + ellipsis) if result else ellipsis

    def _draw_card(self, index: int, x: int, y: int) -> None:
        score = self.filtered[index]
        photo = self._get_card_photo(score)
        self._visible_images.append(photo)
        tag = f"card_{index}"

        self.canvas.create_image(
            x, y, anchor="nw", image=photo, tags=("card", tag)
        )

        beatmap = score.beatmap
        mode = mode_short(score.mode)
        mode_color = MODE_COLORS.get(score.mode, PINK)
        self.canvas.create_text(
            x + 13, y + 12, anchor="nw", text=mode,
            fill=mode_color, font=(FONT, 8, "bold"), tags=("card", tag),
        )
        mods = score.mods_label
        if mods != "NM":
            self.canvas.create_text(
                x + 13 + self.font_meta.measure(mode) + 10, y + 12,
                anchor="nw", text=mods, fill=MUTED,
                font=(FONT, 7, "bold"), tags=("card", tag),
            )

        rank = score.rank
        rank_color = GRADE_COLORS.get(rank, MUTED)
        cx, cy, radius = x + self.CARD_W - 25, y + 25, 17
        self.canvas.create_oval(
            cx - radius, cy - radius, cx + radius, cy + radius,
            fill=rank_color, outline="", tags=("card", tag),
        )
        self.canvas.create_text(
            cx, cy, text=rank, fill="#17131a",
            font=self.font_grade, tags=("card", tag),
        )

        title = (
            beatmap.display_title if beatmap else "未知谱面"
        )
        artist = beatmap.display_artist if beatmap else "未找到谱面信息"
        difficulty = beatmap.version if beatmap else score.beatmap_md5[:12]
        creator = beatmap.creator if beatmap else ""

        self.canvas.create_text(
            x + 13, y + 51, anchor="nw",
            text=self._ellipsize(title, self.font_title, self.CARD_W - 26),
            fill=TEXT, font=self.font_title, tags=("card", tag),
        )
        self.canvas.create_text(
            x + 13, y + 73, anchor="nw",
            text=self._ellipsize(artist, self.font_meta, self.CARD_W - 26),
            fill="#c6c6d2", font=self.font_meta, tags=("card", tag),
        )
        diff_text = f"[{difficulty}]"
        if creator:
            diff_text += f"  by {creator}"
        self.canvas.create_text(
            x + 13, y + 91, anchor="nw",
            text=self._ellipsize(diff_text, self.font_small, self.CARD_W - 26),
            fill=PINK, font=self.font_small, tags=("card", tag),
        )

        self.canvas.create_line(
            x + 13, y + self.CARD_H - 48,
            x + self.CARD_W - 13, y + self.CARD_H - 48,
            fill="#3b3b50", tags=("card", tag),
        )
        self.canvas.create_text(
            x + 13, y + self.CARD_H - 38, anchor="nw",
            text=f"{score.score:,}", fill=TEXT,
            font=self.font_score, tags=("card", tag),
        )
        acc_color = GREEN if score.accuracy >= 95 else (GOLD if score.accuracy >= 90 else TEXT)
        self.canvas.create_text(
            x + self.CARD_W - 13, y + self.CARD_H - 37, anchor="ne",
            text=f"{score.accuracy:.2f}%", fill=acc_color,
            font=(FONT, 9, "bold"), tags=("card", tag),
        )
        self.canvas.create_text(
            x + 13, y + self.CARD_H - 17, anchor="nw",
            text=score.when.strftime("%Y-%m-%d  %H:%M"),
            fill=MUTED, font=self.font_small, tags=("card", tag),
        )
        self.canvas.create_text(
            x + self.CARD_W - 13, y + self.CARD_H - 17, anchor="ne",
            text=f"COMBO {score.max_combo}x", fill=MUTED,
            font=self.font_small, tags=("card", tag),
        )

    def _get_card_photo(self, score: Score) -> ImageTk.PhotoImage:
        beatmap = score.beatmap
        bg_path = beatmap.background if beatmap else ""
        md5 = score.beatmap_md5
        key = (bg_path, md5, self.CARD_W, self.CARD_H, score.mode)
        cached = self._image_cache.get(key)
        if cached is not None:
            self._image_cache.move_to_end(key)
            return cached

        art = self._render_art(
            bg_path, md5, self.CARD_W, self.CARD_H,
            score.mode, radius=14,
        )
        photo = ImageTk.PhotoImage(art)
        self._image_cache[key] = photo
        while len(self._image_cache) > self.IMAGE_CACHE_SIZE:
            self._image_cache.popitem(last=False)
        return photo

    def _render_art(
        self, bg_path: str, md5: str, width: int, height: int,
        mode: int, radius: int = 14,
    ) -> Image.Image:
        accent = MODE_COLORS.get(mode, PINK)
        img: Image.Image | None = None

        if bg_path:
            try:
                with Image.open(bg_path) as source:
                    source = source.convert("RGBA")
                    flattened = Image.new("RGB", source.size, (18, 18, 26))
                    flattened.paste(source, (0, 0), source)
                    source = flattened
                    try:
                        source.draft("RGB", (width * 2, height * 2))
                    except Exception:
                        pass
                    img = ImageOps.fit(
                        source, (width, height),
                        method=Image.Resampling.LANCZOS,
                        centering=(0.5, 0.38),
                    )
            except (OSError, ValueError):
                img = None

        if img is None:
            try:
                seed = int(md5[:8], 16) if md5 else 0
            except ValueError:
                seed = sum(ord(char) for char in md5)
            color_a = (
                25 + ((seed >> 16) & 45),
                20 + ((seed >> 8) & 45),
                40 + (seed & 60),
            )
            color_b = (12, 12, 20)
            img = Image.new("RGB", (width, height), color_b)
            draw = ImageDraw.Draw(img)
            for yy in range(height):
                ratio = yy / max(1, height - 1)
                color = tuple(
                    int(color_a[index] * (1 - ratio) + color_b[index] * ratio)
                    for index in range(3)
                )
                draw.line([(0, yy), (width, yy)], fill=color)
            for index in range(5):
                x1 = (seed >> (index * 3)) % width
                y1 = (seed >> (index * 5)) % height
                r = 20 + ((seed >> (index * 2)) % 70)
                shade = 35 + ((seed >> index) % 35)
                draw.ellipse(
                    (x1 - r, y1 - r, x1 + r, y1 + r),
                    fill=(shade, shade, shade + 12),
                )

        img = ImageEnhance.Brightness(img).enhance(0.56)
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        for yy in range(height):
            ratio = yy / max(1, height - 1)
            alpha = int(28 + 190 * (ratio ** 1.35))
            overlay_draw.line(
                [(0, yy), (width, yy)], fill=(5, 5, 10, alpha)
            )
        img = Image.alpha_composite(
            img.convert("RGBA"), overlay
        ).convert("RGB")

        draw = ImageDraw.Draw(img)
        draw.rectangle((0, 0, 4, height), fill=accent)
        draw.rectangle((5, 0, width, 2), fill=accent)

        mask = Image.new("L", (width, height), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, width - 1, height - 1),
            radius=radius, fill=255,
        )
        result = Image.new("RGB", (width, height), BG)
        result.paste(img, (0, 0), mask)
        return result

    def _score_at(self, canvas_x: int, canvas_y: int) -> int | None:
        if not self.filtered:
            return None
        rel_x = canvas_x - self._x0
        rel_y = canvas_y - self.MARGIN
        if rel_x < 0 or rel_y < 0:
            return None
        cell_w = self.CARD_W + self.GAP
        cell_h = self.CARD_H + self.GAP
        column = int(rel_x // cell_w)
        row = int(rel_y // cell_h)
        if rel_x - column * cell_w > self.CARD_W:
            return None
        if rel_y - row * cell_h > self.CARD_H:
            return None
        index = row * self._cols + column
        if 0 <= index < len(self.filtered):
            return index
        return None

    def _on_canvas_click(self, event: tk.Event) -> None:
        index = self._score_at(
            int(self.canvas.canvasx(event.x)),
            int(self.canvas.canvasy(event.y)),
        )
        if index is not None:
            self.show_detail(index)

    def _on_canvas_motion(self, event: tk.Event) -> None:
        index = self._score_at(
            int(self.canvas.canvasx(event.x)),
            int(self.canvas.canvasy(event.y)),
        )
        self.canvas.configure(cursor="hand2" if index is not None else "")

    def _judgment_items(self, score: Score) -> list[tuple[str, int, str]]:
        if score.mode == 3:
            return [
                ("MAX", score.geki, GOLD),
                ("300", score.n300, GOLD),
                ("200", score.katu, GREEN),
                ("100", score.n100, CYAN),
                ("50", score.n50, BLUE),
                ("MISS", score.miss, RED),
            ]
        if score.mode == 1:
            return [
                ("300", score.n300, GOLD),
                ("100", score.n100, GREEN),
                ("MISS", score.miss, RED),
            ]
        if score.mode == 2:
            return [
                ("水果", score.n300, GREEN),
                ("滴", score.n100, CYAN),
                ("小滴", score.n50, BLUE),
                ("MISS", score.miss, RED),
            ]
        return [
            ("300", score.n300, GOLD),
            ("100", score.n100, GREEN),
            ("50", score.n50, CYAN),
            ("MISS", score.miss, RED),
        ]

    def show_detail(self, index: int) -> None:
        if not (0 <= index < len(self.filtered)):
            return
        self.show_score_detail(self.filtered[index])

    def show_score_detail(self, score: Score) -> None:
        beatmap = score.beatmap

        if self._current_detail is not None:
            try:
                if self._current_detail.winfo_exists():
                    self._current_detail.destroy()
            except tk.TclError:
                pass

        win = tk.Toplevel(self.root)
        self._current_detail = win
        win.title("成绩详情 - " + (
            beatmap.display_title if beatmap else score.beatmap_md5[:12]
        ))
        win.configure(bg=BG)
        win.geometry("860x760")
        win.minsize(740, 680)
        win.transient(self.root)
        win.bind("<Escape>", lambda _event: win.destroy())

        art = self._render_art(
            beatmap.background if beatmap else "",
            score.beatmap_md5, 840, 310, score.mode, radius=18,
        )
        photo = ImageTk.PhotoImage(art)
        hero = tk.Canvas(
            win, width=840, height=310, bg=BG,
            highlightthickness=0, bd=0,
        )
        hero.pack(fill="x")
        hero.create_image(0, 0, anchor="nw", image=photo)
        hero.image = photo  # type: ignore[attr-defined]

        hero.create_text(
            28, 24, anchor="nw",
            text=f"{mode_short(score.mode)}  ·  {score.mods_label}",
            fill=MODE_COLORS.get(score.mode, PINK),
            font=(FONT, 10, "bold"),
        )
        hero.create_text(
            28, 58, anchor="nw",
            text=beatmap.display_title if beatmap else "未知谱面",
            fill=TEXT, font=(FONT, 19, "bold"), width=690,
        )
        hero.create_text(
            28, 128, anchor="nw",
            text=beatmap.display_artist if beatmap else score.beatmap_md5,
            fill="#dedee8", font=(FONT, 12), width=690,
        )
        detail_line = ""
        if beatmap:
            detail_line = f"[{beatmap.version}]"
            if beatmap.star_rating > 0:
                detail_line += f"  ·  ★ {beatmap.star_rating:.2f}"
            if beatmap.creator:
                detail_line += f"  ·  mapped by {beatmap.creator}"
        hero.create_text(
            28, 156, anchor="nw", text=detail_line,
            fill="#ff9bc7", font=(FONT, 10), width=700,
        )
        hero.create_text(
            28, 268, anchor="nw",
            text=f"玩家  {score.player}", fill=MUTED,
            font=(FONT, 10),
        )

        rank = score.rank
        rank_color = GRADE_COLORS.get(rank, MUTED)
        hero.create_oval(
            740, 26, 806, 92, fill=rank_color, outline="",
        )
        hero.create_text(
            773, 59, text=rank, fill="#15151b",
            font=(FONT, 20, "bold"),
        )

        panel = tk.Frame(win, bg=BG)
        panel.pack(fill="both", expand=True, padx=24, pady=(16, 8))
        for column in range(3):
            panel.columnconfigure(column, weight=1)

        stats = [
            ("分数", f"{score.score:,}", PINK),
            ("准确率", f"{score.accuracy:.2f}%", GREEN),
            ("最大连击", f"{score.max_combo}x", BLUE),
            ("模组", score.mods_label, GOLD),
            ("时间", score.when.strftime("%Y-%m-%d %H:%M"), TEXT),
            ("成绩等级", rank, rank_color),
        ]
        for stat_index, (label, value, color) in enumerate(stats):
            row, column = divmod(stat_index, 3)
            card = tk.Frame(
                panel, bg=SURFACE,
                highlightbackground=BORDER, highlightthickness=1,
            )
            card.grid(
                row=row, column=column, sticky="ew",
                padx=(0 if column == 0 else 8, 0),
                pady=(0, 8),
            )
            tk.Label(
                card, text=label, bg=SURFACE, fg=MUTED,
                font=(FONT, 8), anchor="w",
            ).pack(fill="x", padx=14, pady=(9, 1))
            tk.Label(
                card, text=value, bg=SURFACE, fg=color,
                font=(FONT, 13, "bold"), anchor="w",
            ).pack(fill="x", padx=14, pady=(0, 9))

        judgement_box = tk.Frame(win, bg=BG)
        judgement_box.pack(fill="x", padx=24, pady=(2, 4))
        judgement_items = self._judgment_items(score)
        judgement_total = sum(item[1] for item in judgement_items)

        heading = tk.Frame(judgement_box, bg=BG)
        heading.pack(fill="x", pady=(0, 6))
        heading.columnconfigure(0, weight=1)
        tk.Label(
            heading,
            text=f"判定统计 · {mode_name(score.mode)}",
            bg=BG, fg=TEXT, font=(FONT, 10, "bold"), anchor="w",
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            heading,
            text=f"共 {judgement_total:,} 个判定",
            bg=BG, fg=MUTED, font=(FONT, 8), anchor="e",
        ).grid(row=0, column=1, sticky="e")

        judgement_grid = tk.Frame(judgement_box, bg=BG)
        judgement_grid.pack(fill="x")
        for index, (label, count, color) in enumerate(judgement_items):
            judgement_grid.columnconfigure(index, weight=1)
            cell = tk.Frame(
                judgement_grid, bg=SURFACE,
                highlightbackground=BORDER, highlightthickness=1,
            )
            cell.grid(
                row=0, column=index, sticky="ew",
                padx=(0 if index == 0 else 6, 0),
            )
            tk.Label(
                cell, text=label, bg=SURFACE, fg=color,
                font=(FONT, 9, "bold"), anchor="w",
            ).pack(fill="x", padx=12, pady=(7, 1))
            tk.Label(
                cell, text=f"{count:,}", bg=SURFACE, fg=TEXT,
                font=(FONT, 14, "bold"), anchor="w",
            ).pack(fill="x", padx=12, pady=(0, 7))

        buttons = tk.Frame(win, bg=BG)
        buttons.pack(fill="x", padx=24, pady=(4, 18))
        if beatmap:
            ttk.Button(
                buttons, text="打开谱面文件夹", style="Dark.TButton",
                command=lambda: self._open_folder(beatmap.folder),
            ).pack(side="left")
        ttk.Button(
            buttons, text="查看该谱面历史成绩", style="Dark.TButton",
            command=lambda: self.show_beatmap_history(score),
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            buttons, text="关闭", style="Accent.TButton",
            command=win.destroy,
        ).pack(side="right")
        win.lift()
        win.focus_set()

    def show_beatmap_history(self, score: Score) -> None:
        history = [
            item for item in self.scores
            if item.beatmap_md5 == score.beatmap_md5
        ]
        history.sort(
            key=lambda item: (item.timestamp_ticks, item.score),
            reverse=True,
        )
        if not history:
            messagebox.showinfo("提示", "没有找到该谱面的历史成绩。", parent=self.root)
            return

        if self._current_history is not None:
            try:
                if self._current_history.winfo_exists():
                    self._current_history.destroy()
            except tk.TclError:
                pass

        beatmap = score.beatmap
        win = tk.Toplevel(self.root)
        self._current_history = win
        win.title("谱面历史成绩 - " + (
            beatmap.display_title if beatmap else score.beatmap_md5[:12]
        ))
        win.configure(bg=BG)
        win.geometry("900x620")
        win.minsize(760, 520)
        win.transient(self.root)
        win.bind("<Escape>", lambda _event: win.destroy())

        header = tk.Frame(win, bg=BG)
        header.pack(fill="x", padx=24, pady=(18, 10))
        tk.Label(
            header,
            text=beatmap.display_title if beatmap else "未知谱面",
            bg=BG, fg=TEXT, font=(FONT, 18, "bold"), anchor="w",
        ).pack(fill="x")
        subtitle = ""
        if beatmap:
            subtitle = f"{beatmap.display_artist}  ·  [{beatmap.version}]"
            if beatmap.star_rating > 0:
                subtitle += f"  ·  ★ {beatmap.star_rating:.2f}"
        tk.Label(
            header, text=subtitle, bg=BG, fg=MUTED,
            font=(FONT, 9), anchor="w",
        ).pack(fill="x", pady=(2, 0))

        best_score = max(item.score for item in history)
        average_accuracy = sum(item.accuracy for item in history) / len(history)
        best_combo = max(item.max_combo for item in history)

        summary = tk.Frame(win, bg=BG)
        summary.pack(fill="x", padx=24, pady=(0, 10))
        for column in range(4):
            summary.columnconfigure(column, weight=1)
        summary_data = [
            ("游玩次数", f"{len(history):,}", PINK),
            ("最高分", f"{best_score:,}", GOLD),
            ("平均准确率", f"{average_accuracy:.2f}%", GREEN),
            ("最高连击", f"{best_combo}x", BLUE),
        ]
        for index, (label, value, color) in enumerate(summary_data):
            card = tk.Frame(
                summary, bg=SURFACE,
                highlightbackground=BORDER, highlightthickness=1, bd=0,
            )
            card.grid(
                row=0, column=index, sticky="ew",
                padx=(0 if index == 0 else 8, 0),
            )
            tk.Label(
                card, text=label, bg=SURFACE, fg=MUTED,
                font=(FONT, 8), anchor="w",
            ).pack(fill="x", padx=12, pady=(8, 1))
            tk.Label(
                card, text=value, bg=SURFACE, fg=color,
                font=(FONT, 13, "bold"), anchor="w",
            ).pack(fill="x", padx=12, pady=(0, 8))

        table_card = tk.Frame(
            win, bg=SURFACE,
            highlightbackground=BORDER, highlightthickness=1, bd=0,
        )
        table_card.pack(fill="both", expand=True, padx=24)
        table_card.columnconfigure(0, weight=1)
        table_card.rowconfigure(0, weight=1)

        columns = ("time", "score", "accuracy", "rank", "combo", "mods")
        tree = ttk.Treeview(
            table_card, columns=columns, show="headings",
            selectmode="browse", style="History.Treeview",
        )
        headings = {
            "time": "时间", "score": "分数", "accuracy": "准确率",
            "rank": "评级", "combo": "最大连击", "mods": "模组",
        }
        widths = {
            "time": 160, "score": 110, "accuracy": 90,
            "rank": 60, "combo": 100, "mods": 110,
        }
        for key in columns:
            tree.heading(key, text=headings[key])
            tree.column(
                key, width=widths[key], minwidth=50,
                anchor="center" if key != "mods" else "w",
            )
        scrollbar = ttk.Scrollbar(
            table_card, orient="vertical", command=tree.yview,
            style="Vertical.TScrollbar",
        )
        tree.configure(yscrollcommand=scrollbar.set)
        tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        for index, item in enumerate(history):
            tags = []
            if item.rank in GRADE_COLORS:
                tags.append(f"rank_{item.rank.lower()}")
            if item is score:
                tags.append("current")
            tree.insert(
                "", "end", iid=str(index),
                values=(
                    item.when.strftime("%Y-%m-%d %H:%M"),
                    f"{item.score:,}",
                    f"{item.accuracy:.2f}%",
                    item.rank,
                    f"{item.max_combo}x",
                    item.mods_label,
                ),
                tags=tuple(tags),
            )
        for rank, color in GRADE_COLORS.items():
            tree.tag_configure(f"rank_{rank.lower()}", foreground=color)
        tree.tag_configure("current", background="#2b3a52")

        def open_selected_detail() -> None:
            selection = tree.selection()
            if not selection:
                messagebox.showinfo(
                    "提示", "请先选择一条历史成绩。", parent=win
                )
                return
            self.show_score_detail(history[int(selection[0])])

        def on_history_click(event) -> None:
            row_id = tree.identify_row(event.y)
            if not row_id:
                return
            tree.selection_set(row_id)
            tree.focus(row_id)
            self.show_score_detail(history[int(row_id)])

        if history:
            first_iid = "0"
            tree.selection_set(first_iid)
            tree.focus(first_iid)
        tree.bind("<ButtonRelease-1>", on_history_click)
        tree.bind("<Return>", lambda _event: open_selected_detail())

        buttons = tk.Frame(win, bg=BG)
        buttons.pack(fill="x", padx=24, pady=14)
        if beatmap:
            ttk.Button(
                buttons, text="打开谱面文件夹", style="Dark.TButton",
                command=lambda: self._open_folder(beatmap.folder),
            ).pack(side="left")
        ttk.Button(
            buttons, text="打开选中成绩详情", style="Dark.TButton",
            command=open_selected_detail,
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            buttons, text="关闭", style="Accent.TButton",
            command=win.destroy,
        ).pack(side="right")
        win.focus_set()

    def open_pp_opportunities(self) -> None:
        if not self.pp_calculator.available:
            messagebox.showerror(
                "缺少依赖",
                "PP 估算需要安装 rosu-pp-py：\n"
                "python -m pip install rosu-pp-py",
                parent=self.root,
            )
            return

        if self._current_pp_window is not None:
            try:
                if self._current_pp_window.winfo_exists():
                    self._current_pp_window.destroy()
            except tk.TclError:
                pass

        win = tk.Toplevel(self.root)
        self._current_pp_window = win
        win.title("可获取 PP 的本地 Ranked 成绩")
        win.configure(bg=BG)
        win.geometry("1080x640")
        win.minsize(860, 520)
        win.transient(self.root)
        win.bind("<Escape>", lambda _event: win.destroy())

        header = tk.Frame(win, bg=BG)
        header.pack(fill="x", padx=24, pady=(18, 8))
        header.columnconfigure(0, weight=1)
        tk.Label(
            header, text="可获取 PP 的本地 Ranked 成绩", bg=BG, fg=TEXT,
            font=(FONT, 18, "bold"), anchor="w",
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            header,
            text="仅统计本地未提交成绩；PP 为单曲原始估算，实际总 PP 取决于线上个人最佳成绩",
            bg=BG, fg=MUTED, font=(FONT, 8), anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))

        mode_var = tk.StringVar(value=self.mode_var.get())
        mode_combo = ttk.Combobox(
            header, textvariable=mode_var, values=MODE_CHOICES,
            state="readonly", style="Dark.TCombobox", width=14,
        )
        mode_combo.grid(row=1, column=1, sticky="e")

        summary_var = tk.StringVar()
        tk.Label(
            header, textvariable=summary_var, bg=BG, fg=GOLD,
            font=(FONT, 9, "bold"), anchor="e",
        ).grid(row=0, column=1, sticky="e", pady=(0, 24))

        table_card = tk.Frame(
            win, bg=SURFACE,
            highlightbackground=BORDER, highlightthickness=1, bd=0,
        )
        table_card.pack(fill="both", expand=True, padx=24)
        table_card.columnconfigure(0, weight=1)
        table_card.rowconfigure(0, weight=1)

        columns = (
            "title", "difficulty", "stars", "score",
            "accuracy", "rank", "mods", "pp", "time",
        )
        tree = ttk.Treeview(
            table_card, columns=columns, show="headings",
            selectmode="browse", style="History.Treeview",
        )
        headings = {
            "title": "谱面", "difficulty": "难度", "stars": "星级",
            "score": "分数", "accuracy": "准确率", "rank": "评级",
            "mods": "模组", "pp": "预计PP", "time": "时间",
        }
        widths = {
            "title": 230, "difficulty": 130, "stars": 65,
            "score": 90, "accuracy": 80, "rank": 55,
            "mods": 80, "pp": 85, "time": 140,
        }
        for key in columns:
            tree.heading(key, text=headings[key])
            tree.column(
                key, width=widths[key], minwidth=50,
                anchor="e" if key in ("score", "pp", "stars") else "center"
                if key != "title" else "w",
            )
        scrollbar = ttk.Scrollbar(
            table_card, orient="vertical", command=tree.yview,
            style="Vertical.TScrollbar",
        )
        tree.configure(yscrollcommand=scrollbar.set)
        tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        tree.tag_configure("top_pp", foreground=GOLD)

        rows: list[tuple[float, Score]] = []

        def refresh(_event=None) -> None:
            selected_mode = MODE_FILTERS.get(mode_var.get(), 0)
            best_by_map: dict[str, tuple[float, Score]] = {}
            for score in self.scores:
                if score.mode != selected_mode:
                    continue
                beatmap = score.beatmap
                if beatmap is None:
                    continue
                if beatmap.ranked_status not in (3, 4, 5):
                    continue
                if score.online_id != 0:
                    continue
                pp_value = self.pp_calculator.calculate(score)
                if pp_value is None or pp_value <= 0:
                    continue
                current = best_by_map.get(beatmap.md5)
                if current is None or pp_value > current[0]:
                    best_by_map[beatmap.md5] = (pp_value, score)

            nonlocal rows
            rows = sorted(
                best_by_map.values(), key=lambda item: item[0], reverse=True
            )
            tree.delete(*tree.get_children())
            for index, (pp_value, score) in enumerate(rows):
                beatmap = score.beatmap
                tree.insert(
                    "", "end", iid=str(index),
                    values=(
                        beatmap.display_title if beatmap else "未知谱面",
                        beatmap.version if beatmap else "-",
                        f"{beatmap.star_rating:.2f}" if beatmap and beatmap.star_rating else "-",
                        f"{score.score:,}",
                        f"{score.accuracy:.2f}%",
                        score.rank,
                        score.mods_label,
                        f"{pp_value:.2f}",
                        score.when.strftime("%Y-%m-%d %H:%M"),
                    ),
                    tags=("top_pp",) if index == 0 else (),
                )

            if rows:
                top_pp = rows[0][0]
                average_pp = sum(item[0] for item in rows) / len(rows)
                summary_var.set(
                    f"{len(rows)} 张候选谱面 · 最高 {top_pp:.2f}pp · 平均 {average_pp:.2f}pp"
                )
                tree.selection_set("0")
            else:
                summary_var.set("没有找到未提交的 Ranked 成绩")

        def open_selected() -> None:
            selection = tree.selection()
            if not selection:
                messagebox.showinfo(
                    "提示", "请先选择一条成绩。", parent=win
                )
                return
            self.show_score_detail(rows[int(selection[0])][1])

        tree.bind("<Double-1>", lambda _event: open_selected())
        tree.bind("<Return>", lambda _event: open_selected())
        mode_combo.bind("<<ComboboxSelected>>", refresh)
        refresh()

        buttons = tk.Frame(win, bg=BG)
        buttons.pack(fill="x", padx=24, pady=14)
        ttk.Button(
            buttons, text="打开选中成绩详情", style="Dark.TButton",
            command=open_selected,
        ).pack(side="left")
        ttk.Button(
            buttons, text="关闭", style="Accent.TButton",
            command=win.destroy,
        ).pack(side="right")
        win.focus_set()

    @staticmethod
    def _star_bucket_label(bucket: int) -> str:
        return f"{bucket}.0 ≤ ★ < {bucket + 1}.0"

    def _filter_by_star_bucket(self, bucket: int, mode_label: str) -> None:
        self.star_filter = (float(bucket), float(bucket + 1))
        self.search_var.set("")
        self._set_star_inputs(f"{bucket:g}", f"{bucket + 1:g}")
        if mode_label in MODE_FILTERS:
            self.mode_var.set(mode_label)
        self._apply_filters()
        self.status_var.set(
            f"已筛选星级区间：{self._star_bucket_label(bucket)}"
        )
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _pie_bucket_data(self) -> tuple[list[int], list[int], list[str], list[str]]:
        mode = MODE_FILTERS.get(self.mode_var.get())
        wanted_keys: int | None = None
        if (
            self.mode_var.get() == "osu!mania"
            and self.mania_keys_var.get() != "全部键数"
        ):
            try:
                wanted_keys = int(self.mania_keys_var.get()[:-1])
            except ValueError:
                wanted_keys = None

        seen: set[str] = set()
        buckets: dict[int, int] = {}
        for score in self.scores:
            if mode is not None and score.mode != mode:
                continue
            beatmap = score.beatmap
            if beatmap is None or beatmap.star_rating <= 0:
                continue
            if wanted_keys is not None and beatmap.key_count != wanted_keys:
                continue
            if beatmap.md5 in seen:
                continue
            seen.add(beatmap.md5)
            bucket = max(0, int(math.floor(beatmap.star_rating)))
            buckets[bucket] = buckets.get(bucket, 0) + 1

        keys = sorted(buckets)
        counts = [buckets[key] for key in keys]
        labels = [self._star_bucket_label(key) for key in keys]
        palette = [
            "#ffd166", "#ff9f43", "#ff6b6b", "#ff5da2",
            "#c084fc", "#7c83ff", "#4cc9f0", "#5ee39a",
            "#b8e986", "#e0e0e0", "#9aa0aa", "#666a74",
        ]
        colors = [palette[index % len(palette)] for index in range(len(keys))]
        return keys, counts, labels, colors

    def _selected_pie_index(self) -> int:
        if self.star_filter is None:
            return -1
        low, high = self.star_filter
        if abs(high - low - 1.0) > 1e-6:
            return -1
        selected_bucket = int(low)
        try:
            return self.pie_data["keys"].index(selected_bucket)
        except ValueError:
            return -1

    def _draw_pie_chart(self, hover_index: int = -1) -> None:
        self.pie_axes.clear()
        self.pie_axes.set_facecolor(SURFACE)
        keys = self.pie_data["keys"]
        counts = self.pie_data["counts"]
        labels = self.pie_data["labels"]
        colors = self.pie_data["colors"]
        total = self.pie_data["total"]

        if not keys:
            self.pie_axes.text(
                0.5, 0.5,
                "没有可用的星级数据\n请点击「重新扫描谱面」",
                ha="center", va="center",
                transform=self.pie_axes.transAxes,
                color=MUTED, fontsize=10,
            )
            self.pie_axes.set_xticks([])
            self.pie_axes.set_yticks([])
            self.pie_wedges = []
            self.pie_hover_index = -1
            self.pie_info_var.set("没有可用的星级数据")
            self.pie_canvas.draw_idle()
            return

        selected_index = self._selected_pie_index()
        active_index = hover_index if hover_index >= 0 else selected_index
        explode = [
            0.10 if index == active_index else 0.0
            for index in range(len(keys))
        ]
        wedges, _, autotexts = self.pie_axes.pie(
            counts, explode=explode, colors=colors,
            startangle=90, counterclock=False,
            wedgeprops=dict(edgecolor=SURFACE, linewidth=2),
            autopct=lambda pct: f"{pct:.1f}%" if pct >= 4.0 else "",
            textprops=dict(color=TEXT, fontsize=7),
        )
        for text in autotexts:
            text.set_color("#131318")
            text.set_fontsize(7)
            text.set_fontweight("bold")
        self.pie_axes.text(
            0, 0, f"{total}\n张谱面", ha="center", va="center",
            color=TEXT, fontsize=12, fontweight="bold",
        )
        self.pie_axes.set_aspect("equal")
        self.pie_wedges = list(wedges)
        self.pie_hover_index = hover_index

        if hover_index >= 0:
            key = keys[hover_index]
            count = counts[hover_index]
            pct = count / total * 100.0 if total else 0.0
            self.pie_info_var.set(
                f"{labels[hover_index]} · {count} 张 · {pct:.1f}%"
                "\n点击扇形筛选该区间"
            )
        elif selected_index >= 0:
            key = keys[selected_index]
            count = counts[selected_index]
            pct = count / total * 100.0 if total else 0.0
            self.pie_info_var.set(
                f"当前筛选：{labels[selected_index]} · {count} 张 · {pct:.1f}%"
            )
        else:
            self.pie_info_var.set(
                "鼠标悬停查看区间；点击扇形筛选对应成绩"
            )
        self.pie_canvas.draw_idle()

    def _refresh_pie_chart(self) -> None:
        keys, counts, labels, colors = self._pie_bucket_data()
        total = sum(counts)
        self.pie_data = {
            "keys": keys,
            "counts": counts,
            "labels": labels,
            "colors": colors,
            "total": total,
        }
        self.pie_total_var.set(
            f"{total} 张谱面" if total else "无星级数据"
        )
        self._draw_pie_chart(-1)

    def _pie_index_at(self, event) -> int:
        if event.inaxes is not self.pie_axes:
            return -1
        for index, wedge in enumerate(self.pie_wedges):
            try:
                if wedge.contains(event)[0]:
                    return index
            except Exception:
                continue
        return -1

    def _on_pie_hover(self, event) -> None:
        index = self._pie_index_at(event)
        if index == self.pie_hover_index:
            return
        self.pie_canvas.get_tk_widget().configure(
            cursor="hand2" if index >= 0 else ""
        )
        self._draw_pie_chart(index)

    def _on_pie_leave(self, _event) -> None:
        if self.pie_hover_index != -1:
            self.pie_canvas.get_tk_widget().configure(cursor="")
            self._draw_pie_chart(-1)

    def _on_pie_click(self, event) -> None:
        index = self._pie_index_at(event)
        if index < 0:
            return
        bucket = self.pie_data["keys"][index]
        self._filter_by_star_bucket(bucket, self.mode_var.get())

    def _open_folder(self, path: str) -> None:
        try:
            if path and Path(path).exists():
                os.startfile(path)  # type: ignore[attr-defined]
        except OSError as exc:
            messagebox.showerror("打开失败", str(exc), parent=self.root)

    def _ensure_beatmaps(self, scores: list[Score], show_dialog: bool = True) -> None:
        needed = {score.beatmap_md5 for score in scores}
        missing = needed - set(self.beatmaps)
        if not missing:
            attach_beatmaps(scores, self.beatmaps)
            return

        dialog = None
        try:
            if show_dialog:
                dialog = LoadingDialog(self.root, "正在补充扫描谱面库")
                scanned = scan_beatmaps(
                    self.osu_dir, dialog.update_progress
                )
            else:
                scanned = scan_beatmaps(self.osu_dir)
            self.beatmaps.update(scanned)
            save_cache(CACHE_PATH, self.osu_dir, self.beatmaps)
        finally:
            if dialog is not None:
                dialog.close()
        attach_beatmaps(scores, self.beatmaps)

    def refresh_scores(self) -> None:
        try:
            scores = parse_scores_db(self.osu_dir / "scores.db")
            self._ensure_beatmaps(scores)
        except Exception as exc:
            messagebox.showerror(
                "刷新失败", f"无法读取本地成绩：\n{exc}",
                parent=self.root,
            )
            return
        self.scores = scores
        self._refresh_stats()
        self._apply_filters()
        self.root.after(10, self._relayout)

    def rebuild_beatmap_cache(self) -> None:
        dialog = LoadingDialog(self.root, "正在扫描谱面库")
        try:
            beatmaps = scan_beatmaps(
                self.osu_dir, dialog.update_progress
            )
            save_cache(CACHE_PATH, self.osu_dir, beatmaps)
        except Exception as exc:
            dialog.close()
            messagebox.showerror(
                "扫描失败", f"无法扫描谱面库：\n{exc}",
                parent=self.root,
            )
            return
        dialog.close()
        self.beatmaps = beatmaps
        attach_beatmaps(self.scores, self.beatmaps)
        self._refresh_stats()
        self._apply_filters()
        messagebox.showinfo(
            "扫描完成",
            f"已扫描 {len(beatmaps):,} 张谱面。",
            parent=self.root,
        )

    def change_osu_folder(self) -> None:
        selected = filedialog.askdirectory(
            parent=self.root,
            title="选择 osu! 文件夹（包含 scores.db 和 Songs）",
            initialdir=str(self.osu_dir),
        )
        if not selected:
            return
        new_dir = Path(selected)
        if not valid_osu_dir(new_dir):
            messagebox.showerror(
                "路径无效",
                "该文件夹中需要同时存在 scores.db 和 Songs 文件夹。",
                parent=self.root,
            )
            return

        try:
            scores = parse_scores_db(new_dir / "scores.db")
            beatmaps = load_cache(CACHE_PATH, new_dir) or {}
            self.osu_dir = new_dir
            self.beatmaps = beatmaps
            self._ensure_beatmaps(scores)
        except Exception as exc:
            messagebox.showerror(
                "切换失败", f"无法读取该 osu! 文件夹：\n{exc}",
                parent=self.root,
            )
            return

        save_config({**load_config(), "osu_dir": str(new_dir)})
        self.scores = scores
        self._refresh_stats()
        self._apply_filters()
        self._last_visible_key = None
        self._relayout()

    def open_settings(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("设置")
        win.configure(bg=BG)
        win.geometry("480x270")
        win.minsize(440, 250)
        win.transient(self.root)
        win.grab_set()

        tk.Label(
            win, text="默认模式", bg=BG, fg=TEXT,
            font=(FONT, 16, "bold"), anchor="w",
        ).pack(fill="x", padx=24, pady=(22, 4))
        tk.Label(
            win,
            text="启动软件时默认显示的模式。保存后会立即切换，并用于下次启动。",
            bg=BG, fg=MUTED, font=(FONT, 9), anchor="w",
        ).pack(fill="x", padx=24, pady=(0, 12))

        mode_var = tk.StringVar(value=self.default_mode)
        combo = ttk.Combobox(
            win, textvariable=mode_var, values=MODE_CHOICES,
            state="readonly", style="Dark.TCombobox", width=24,
        )
        combo.pack(fill="x", padx=24, ipady=4)
        combo.focus_set()

        buttons = tk.Frame(win, bg=BG)
        buttons.pack(fill="x", padx=24, pady=22)

        def save() -> None:
            selected = mode_var.get()
            if selected not in MODE_FILTERS:
                return
            save_config({**load_config(), "default_mode": selected})
            self.default_mode = selected
            self.mode_var.set(selected)
            self.star_filter = None
            self.mania_keys_var.set("全部键数")
            self.search_var.set("")
            self._apply_filters()
            self.status_var.set(f"默认模式已保存：{selected}")
            win.destroy()

        ttk.Button(
            buttons, text="保存", style="Accent.TButton",
            command=save,
        ).pack(side="right")
        ttk.Button(
            buttons, text="取消", style="Dark.TButton",
            command=win.destroy,
        ).pack(side="right", padx=(0, 8))
        win.bind("<Escape>", lambda _event: win.destroy())
        win.focus_set()

    def on_close(self) -> None:
        self.root.destroy()


def main() -> None:
    enable_high_dpi()
    root = tk.Tk()
    root.withdraw()

    config = load_config()
    osu_dir = Path(config.get("osu_dir", str(osu_data.DEFAULT_OSU_DIR)))

    if not valid_osu_dir(osu_dir):
        messagebox.showinfo(
            "选择 osu! 文件夹",
            "没有找到有效的 osu! 安装目录。\n"
            "请选择同时包含 scores.db 和 Songs 的文件夹。",
            parent=root,
        )
        selected = filedialog.askdirectory(
            parent=root, title="选择 osu! 文件夹"
        )
        if not selected:
            root.destroy()
            return
        osu_dir = Path(selected)
        if not valid_osu_dir(osu_dir):
            messagebox.showerror(
                "路径无效",
                "该文件夹中需要同时存在 scores.db 和 Songs 文件夹。",
                parent=root,
            )
            root.destroy()
            return
        save_config({**config, "osu_dir": str(osu_dir)})

    try:
        scores = parse_scores_db(osu_dir / "scores.db")
        beatmaps = load_cache(CACHE_PATH, osu_dir)
        needed = {score.beatmap_md5 for score in scores}

        if beatmaps is None or not needed.issubset(beatmaps):
            dialog = LoadingDialog(root, "首次运行：正在扫描谱面库")
            try:
                beatmaps = scan_beatmaps(osu_dir, dialog.update_progress)
                save_cache(CACHE_PATH, osu_dir, beatmaps)
            finally:
                dialog.close()

        attach_beatmaps(scores, beatmaps)
    except Exception as exc:
        messagebox.showerror(
            "启动失败",
            f"读取 osu! 本地数据失败：\n{exc}",
            parent=root,
        )
        root.destroy()
        return

    root.deiconify()
    app = ScoreWallApp(root, osu_dir, scores, beatmaps)
    root.mainloop()


if __name__ == "__main__":
    main()





