#!/usr/bin/env python3
"""
千恋万花 立绘合成工具
Sprite Composer for Senren Banka visual novel character sprites.
"""

import json
import re
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from configparser import ConfigParser
from pathlib import Path

from PIL import Image, ImageDraw, ImageTk

# ============================================================
# UTF-16-LE file handling
# ============================================================

def read_utf16le(filepath):
    """Read a UTF-16-LE encoded file, return text content."""
    with open(filepath, 'rb') as f:
        data = f.read()
    if data[:2] == b'\xff\xfe':
        data = data[2:]
    return data.decode('utf-16-le')


# ============================================================
# Data parsing
# ============================================================

def parse_info_file(filepath):
    """Parse _info.txt.
    Returns (dresses, faces).
    dresses: dict[dress_name][diff_num] = {resource_name, ...}
    faces: dict[face_code] = [resource_path, ...]
    """
    text = read_utf16le(filepath)
    dresses = {}  # dress_name -> {diff_num: {resource_name, ...}}
    faces = {}    # face_code -> [resource_path, ...]

    for line in text.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        parts = line.split('\t')
        if len(parts) < 4:
            continue

        entry_type = parts[0]

        if entry_type == 'dress' and len(parts) >= 5:
            dress_name = parts[1]
            diff_num = int(parts[3])
            resource = parts[4]
            dresses.setdefault(dress_name, {}).setdefault(diff_num, set()).add(resource)

        elif entry_type == 'face' and len(parts) >= 4:
            face_code = parts[1]
            resource = parts[3]
            faces.setdefault(face_code, []).append(resource)

    return dresses, faces


def parse_layout_file(filepath):
    """Parse a layout .txt file. Returns (layers, canvas_width, canvas_height)."""
    text = read_utf16le(filepath)
    layers = []
    canvas_width = 3600
    canvas_height = 5100

    for line in text.strip().split('\n'):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = line.split('\t')

        # Canvas dimension line: empty first fields, then width/height
        if parts[0] == '' and len(parts) >= 5:
            try:
                if parts[3]:
                    canvas_width = int(parts[3])
                if parts[4]:
                    canvas_height = int(parts[4])
            except ValueError:
                pass
            continue

        if len(parts) < 9:
            continue

        try:
            layer = {
                'layer_type': int(parts[0]) if parts[0] else 0,
                'name': parts[1],
                'left': int(parts[2]) if parts[2] else 0,
                'top': int(parts[3]) if parts[3] else 0,
                'width': int(parts[4]) if parts[4] else 0,
                'height': int(parts[5]) if parts[5] else 0,
                'image_type': int(parts[6]) if parts[6] else 0,
                'opacity': int(parts[7]) if parts[7] else 255,
                'visible': int(parts[8]) if parts[8] else 0,
                'layer_id': int(parts[9]) if len(parts) > 9 and parts[9] else 0,
                'group_layer_id': int(parts[10]) if len(parts) > 10 and parts[10] else 0,
            }
            layers.append(layer)
        except (ValueError, IndexError):
            continue

    return layers, canvas_width, canvas_height


def parse_stand_file(filepath):
    """Parse .stand file (custom format with %[ objects). Returns list of variants."""
    text = read_utf16le(filepath)
    # Filter out // comment lines (e.g. 茉子 stand file)
    text = '\n'.join(line for line in text.split('\n')
                     if not line.strip().startswith('//'))
    text = text.replace('%[', '{')
    text = text.replace("'", '"')
    text = re.sub(r'(\w+)\s*:', r'"\1":', text)

    result = []
    stack = []
    for ch in text:
        if ch == '[':
            stack.append('[')
            result.append(ch)
        elif ch == '{':
            stack.append('{')
            result.append(ch)
        elif ch == ']':
            if stack and stack[-1] == '{':
                result.append('}')
                stack.pop()
            elif stack and stack[-1] == '[':
                result.append(']')
                stack.pop()
            else:
                result.append(ch)
        else:
            result.append(ch)

    fixed = ''.join(result)
    fixed = re.sub(r',(\s*)([}\]])', r'\1\2', fixed)
    return json.loads(fixed)


def sorted_face_codes(faces):
    """Sort face codes: by number, then base < h < n."""
    def sort_key(code):
        num = re.match(r'(\d+)', code).group(1)
        suffix = code[len(num):]
        order = {'': 0, 'h': 1, 'n': 2}
        return (int(num), order.get(suffix, 99))
    return sorted(faces.keys(), key=sort_key)


def load_variant_data(base_dir, filename):
    """Load all data files for a single stand variant. Returns a dict."""
    data = {'prefix': filename}

    # High-res layout
    hd_path = base_dir / f"{filename}.txt"
    if hd_path.exists():
        data['layers_hd'], data['canvas_w_hd'], data['canvas_h_hd'] = \
            parse_layout_file(str(hd_path))
    else:
        data['layers_hd'], data['canvas_w_hd'], data['canvas_h_hd'] = [], 3600, 5100

    # Low-res layout
    ld_path = base_dir / f"{filename}_0.txt"
    if ld_path.exists():
        data['layers_ld'], data['canvas_w_ld'], data['canvas_h_ld'] = \
            parse_layout_file(str(ld_path))
    else:
        # Fallback: halve high-res values
        data['layers_ld'] = [
            {**l,
             'left': l['left'] // 2, 'top': l['top'] // 2,
             'width': l['width'] // 2, 'height': l['height'] // 2}
            for l in data['layers_hd']
        ]
        data['canvas_w_ld'] = data['canvas_w_hd'] // 2
        data['canvas_h_ld'] = data['canvas_h_hd'] // 2

    # Info file
    info_path = base_dir / f"{filename}_info.txt"
    if info_path.exists():
        data['dresses'], data['faces'] = parse_info_file(str(info_path))
    else:
        data['dresses'], data['faces'] = {}, {}

    return data


# ============================================================
# Main Application
# ============================================================

class SpriteComposer(tk.Tk):
    """Main application window — character list."""

    def __init__(self):
        super().__init__()
        self.title("千恋万花 立绘合成工具")
        self.geometry("400x500")
        self.resizable(True, True)
        self.minsize(300, 300)

        self.characters = {}
        self._load_config()
        self._build_ui()

    def _load_config(self):
        config_path = Path(__file__).parent / "config.ini"
        if not config_path.exists():
            messagebox.showerror("错误", f"找不到配置文件: {config_path}")
            self.destroy()
            return

        cp = ConfigParser()
        cp.read(config_path, encoding='utf-8')

        for section in cp.sections():
            stand = cp.get(section, 'stand', fallback='')
            if not stand:
                continue
            decorations_raw = cp.get(section, 'decorations', fallback='')
            deco_specs = self._parse_decorations(decorations_raw)
            self.characters[section] = {
                'name': section,
                'stand': stand,
                'decorations': deco_specs,
            }

        if not self.characters:
            messagebox.showwarning("警告", "config.ini 中没有找到角色配置。")

    @staticmethod
    def _parse_decorations(raw):
        """Parse decorations config string.
        Format: "显示名: 匹配关键词 [, bind: 服装关键词]; ..."
        Returns list of (display_name, match_pattern, bind_keyword_or_None).
        """
        if not raw or not raw.strip():
            return []
        specs = []
        for item in raw.split(';'):
            item = item.strip()
            if not item:
                continue
            bind_keyword = None
            if ', bind:' in item:
                item, bind_part = item.split(', bind:', 1)
                bind_keyword = bind_part.strip()
            if ':' not in item:
                continue
            name, pattern = item.split(':', 1)
            specs.append((name.strip(), pattern.strip(), bind_keyword))
        return specs

    def _build_ui(self):
        ttk.Label(self, text="选择角色", font=("", 16, "bold")).pack(pady=15)

        list_frame = ttk.Frame(self)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 10))

        scrollbar = ttk.Scrollbar(list_frame, orient="vertical")
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self._char_listbox = tk.Listbox(
            list_frame, yscrollcommand=scrollbar.set,
            font=("", 12), exportselection=False,
        )
        self._char_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self._char_listbox.yview)

        for name in sorted(self.characters.keys()):
            self._char_listbox.insert(tk.END, name)

        self._char_listbox.bind('<Double-Button-1>', self._on_char_select)
        ttk.Button(self, text="打开选中角色", command=self._open_character).pack(
            pady=(0, 15))

        if not self.characters:
            ttk.Label(list_frame, text="（无可用角色）", foreground="gray").pack()

    def _on_char_select(self, event=None):
        self._open_character()

    def _open_character(self):
        sel = self._char_listbox.curselection()
        if not sel:
            messagebox.showinfo("提示", "请先选择一个角色。")
            return
        name = self._char_listbox.get(sel[0])
        if name in self.characters:
            CharacterEditor(self, self.characters[name])


# ============================================================
# Character Editor Window
# ============================================================

class CharacterEditor(tk.Toplevel):
    """Editor window for a single character."""

    def __init__(self, master, cfg):
        super().__init__(master)
        self.cfg = cfg
        self.title(f"立绘编辑 — {cfg['name']}")

        # Per-variant data: variant_key -> {prefix, layers_hd, layers_ld, ...}
        self.variant_data = {}
        self.variant_keys = []  # ordered list of variant filenames
        self._current = None    # currently selected variant data dict

        # Current selection state
        self.stand_var = tk.StringVar()
        self.resolution = tk.StringVar(value="high")
        self.selected_dress = tk.StringVar()
        self.selected_face = tk.StringVar()
        self.deco_hoho = tk.BooleanVar(value=False)

        # Dress display mapping: display_name -> set of resource names
        self._dress_display_map = {}

        # Dynamic decorations (from config.ini): list of (display_name, match_pattern, bind_keyword)
        self._deco_specs = cfg.get('decorations', [])
        # Runtime decoration state
        self._deco_vars = {}       # display_name -> tk.BooleanVar
        self._deco_widgets = {}    # display_name -> ttk.Checkbutton
        self._deco_available = {}  # display_name -> bool (per current variant)

        # Character-level feature flags (set in _load_data)
        self._has_hoho = {}  # variant_key -> bool

        # Missing file tracking
        self._missing_paths = set()
        self._last_warned_missing = set()

        # Preview state
        self._composite_image = None
        self._preview_scale = 1.0
        self._render_pending = False
        self._png_cache = {}  # path -> PIL.Image (cleared on resolution/stand change)
        self._zoom_after_id = None
        self._zoom_var = tk.StringVar(value="100%")
        self._zoom_editing = False
        self._zoom_before_edit = "100%"

        self._load_data()
        self._build_ui()
        self._update_preview()

        self.geometry("1100x750")
        self.minsize(800, 500)

    # ============================================================
    # Data loading
    # ============================================================

    def _load_data(self):
        """Load .stand file, discover and load all variant data."""
        stand_rel = self.cfg['stand']
        stand_path = Path(__file__).parent / stand_rel
        if not stand_path.exists():
            messagebox.showerror("错误", f"找不到 stand 文件: {stand_path}")
            return

        base_dir = stand_path.parent

        # Parse .stand to get variant filenames
        variants = parse_stand_file(str(stand_path))
        for v in variants:
            fname = v.get('filename', '')
            if fname:
                self.variant_keys.append(fname)
                self.variant_data[fname] = load_variant_data(base_dir, fname)

        if not self.variant_data:
            messagebox.showerror("错误", "stand 文件中没有找到变体。")
            return

        # Detect feature flags per variant
        for key, data in self.variant_data.items():
            for res_key in ['layers_hd', 'layers_ld']:
                for layer in data.get(res_key, []):
                    if layer['name'] == '頬':
                        self._has_hoho[key] = True

    # ============================================================
    # Current variant helpers
    # ============================================================

    @property
    def _prefix(self):
        """Current variant's PNG prefix."""
        return self._current['prefix'] if self._current else ''

    def _get_layers(self):
        """Return (layers, canvas_w, canvas_h) for current variant + resolution."""
        if not self._current:
            return [], 3600, 5100
        if self.resolution.get() == 'low':
            return (self._current['layers_ld'],
                    self._current['canvas_w_ld'],
                    self._current['canvas_h_ld'])
        return (self._current['layers_hd'],
                self._current['canvas_w_hd'],
                self._current['canvas_h_hd'])

    def _get_png_path(self, layer_id):
        """Get the path to a PNG file for the given layer_id."""
        if not self._current:
            return None
        base_dir = (Path(__file__).parent / self.cfg['stand']).parent
        if self.resolution.get() == 'low':
            filename = f"{self._prefix}_0_{layer_id}.png"
        else:
            filename = f"{self._prefix}_{layer_id}.png"
        return base_dir / filename

    def _load_png(self, png_path_str):
        """Load a PNG image, using cache when available."""
        if png_path_str in self._png_cache:
            return self._png_cache[png_path_str]
        img = Image.open(png_path_str)
        if img.mode != 'RGBA':
            img = img.convert('RGBA')
        img.load()
        self._png_cache[png_path_str] = img
        return img

    # ============================================================
    # UI construction
    # ============================================================

    def _build_ui(self):
        self.columnconfigure(0, weight=0)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        self.rowconfigure(1, weight=0)

        # ---- Left panel ----
        left_panel = ttk.Frame(self, padding=8)
        left_panel.grid(row=0, column=0, sticky="ns")
        left_panel.rowconfigure(0, weight=0)
        left_panel.rowconfigure(1, weight=0)
        left_panel.rowconfigure(2, weight=1)
        left_panel.rowconfigure(3, weight=0)

        # Stand variant — Listbox (allows empty selection)
        stand_frame = ttk.LabelFrame(left_panel, text="站位变体", padding=5)
        stand_frame.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        list_height = min(4, max(1, len(self.variant_keys)))
        self._stand_list = tk.Listbox(stand_frame, height=list_height,
                                      exportselection=False)
        self._stand_list.pack(fill=tk.BOTH, expand=True)
        for key in self.variant_keys:
            self._stand_list.insert(tk.END, key)
        self._stand_list.bind('<ButtonRelease-1>', self._on_stand_click)
        # Default selection
        if self.variant_keys:
            self._stand_list.selection_set(0)
            self.stand_var.set(self.variant_keys[0])
            self._current = self.variant_data[self.variant_keys[0]]

        # Resolution — Listbox (no empty selection)
        res_frame = ttk.LabelFrame(left_panel, text="分辨率", padding=5)
        res_frame.grid(row=0, column=1, sticky="ew", padx=(5, 0), pady=(0, 5))
        self._res_list = tk.Listbox(res_frame, height=2, exportselection=False)
        self._res_list.pack(fill=tk.BOTH, expand=True)
        self._res_list.insert(tk.END, "高清")
        self._res_list.insert(tk.END, "低清")
        self._res_list.selection_set(0)
        self._res_list.bind('<ButtonRelease-1>', self._on_res_click)

        # Dress list
        dress_frame = ttk.LabelFrame(left_panel, text="服装", padding=5)
        dress_frame.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 5))
        self._dress_list = tk.Listbox(dress_frame, height=8, width=26,
                                     exportselection=False)
        self._dress_list.pack(fill=tk.BOTH, expand=True)
        self._dress_list.bind('<ButtonRelease-1>', self._on_dress_click)

        # Face list
        face_frame = ttk.LabelFrame(left_panel, text="表情", padding=5)
        face_frame.grid(row=2, column=0, columnspan=2, sticky="ns", pady=(0, 5))
        self._face_list = tk.Listbox(face_frame, height=20, width=26,
                                     exportselection=False)
        self._face_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        face_scroll = ttk.Scrollbar(face_frame, orient="vertical",
                                    command=self._face_list.yview)
        face_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._face_list.config(yscrollcommand=face_scroll.set)
        self._face_list.bind('<ButtonRelease-1>', self._on_face_click)

        # Decorations — data-driven from config.ini
        deco_frame = ttk.LabelFrame(left_panel, text="装饰物（独立勾选）", padding=5)
        deco_frame.grid(row=3, column=0, columnspan=2, sticky="ew")

        # Dynamic decoration checkboxes (from config.ini)
        self._deco_vars = {}
        self._deco_widgets = {}
        self._deco_available = {}
        for display_name, match_pattern, bind_keyword in self._deco_specs:
            var = tk.BooleanVar(value=False)
            self._deco_vars[display_name] = var
            cb = ttk.Checkbutton(deco_frame, text=display_name, variable=var,
                                command=self._on_change)
            self._deco_widgets[display_name] = cb

        # 頬 — special case with exact name match (per design doc 11.2)
        self._hoho_cb = ttk.Checkbutton(deco_frame, text="頬（脸颊红晕）",
                                        variable=self.deco_hoho,
                                        command=self._on_change)
        self._update_deco_visibility()

        # ---- Right panel: preview ----
        preview_frame = ttk.Frame(self, padding=5)
        preview_frame.grid(row=0, column=1, sticky="nsew")
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(0, weight=1)
        preview_frame.rowconfigure(1, weight=0)

        canvas_container = ttk.Frame(preview_frame)
        canvas_container.grid(row=0, column=0, sticky="nsew")
        canvas_container.columnconfigure(0, weight=1)
        canvas_container.rowconfigure(0, weight=1)

        self._canvas = tk.Canvas(canvas_container, bg="#808080", highlightthickness=0)
        self._canvas.grid(row=0, column=0, sticky="nsew")

        h_scroll = ttk.Scrollbar(canvas_container, orient="horizontal",
                                 command=self._canvas.xview)
        h_scroll.grid(row=1, column=0, sticky="ew")
        v_scroll = ttk.Scrollbar(canvas_container, orient="vertical",
                                 command=self._canvas.yview)
        v_scroll.grid(row=0, column=1, sticky="ns")
        self._canvas.config(xscrollcommand=h_scroll.set, yscrollcommand=v_scroll.set)

        self._canvas.bind("<ButtonPress-1>", self._on_pan_start)
        self._canvas.bind("<B1-Motion>", self._on_pan_move)
        self._canvas.bind("<MouseWheel>", self._on_zoom)
        self._canvas.bind("<Configure>", self._on_canvas_resize)

        # Zoom controls
        zoom_bar = ttk.Frame(preview_frame)
        zoom_bar.grid(row=1, column=0, sticky="ew", pady=(5, 0))
        ttk.Button(zoom_bar, text="−", width=3, command=self._zoom_out).pack(side=tk.LEFT)
        ttk.Button(zoom_bar, text="+", width=3, command=self._zoom_in).pack(side=tk.LEFT)
        ttk.Button(zoom_bar, text="适应窗口", command=self._zoom_fit).pack(
            side=tk.LEFT, padx=(5, 0))
        self._zoom_entry = tk.Entry(zoom_bar, textvariable=self._zoom_var,
                                    width=7, justify='center', relief='flat',
                                    readonlybackground='SystemButtonFace')
        self._zoom_entry.pack(side=tk.LEFT, padx=10)
        self._zoom_entry.configure(state='readonly')
        self._zoom_entry.bind('<Button-1>', self._on_zoom_entry_click)
        self._zoom_entry.bind('<Return>', self._on_zoom_entry_confirm)
        self._zoom_entry.bind('<Escape>', self._on_zoom_entry_cancel)
        # FocusOut: confirm edit via after_idle to avoid race with other handlers
        self._zoom_entry.bind('<FocusOut>', lambda e: self.after_idle(self._on_zoom_entry_confirm))

        # ---- Bottom bar ----
        bottom_bar = ttk.Frame(self, padding=8)
        bottom_bar.grid(row=1, column=0, columnspan=2, sticky="ew")
        ttk.Button(bottom_bar, text="导出 PNG ...", command=self._export_png).pack(
            side=tk.RIGHT)
        self._status_label = ttk.Label(bottom_bar, text="")
        self._status_label.pack(side=tk.LEFT)

        # Populate dress/face lists now that all widgets exist
        self._repopulate_lists()

    def _update_deco_visibility(self):
        """Show/hide decoration checkboxes based on current variant's available layers."""
        layers, _, _ = self._get_layers()

        # Dynamic decorations: show if any layer name contains the match pattern
        for display_name, match_pattern, _bind_keyword in self._deco_specs:
            cb = self._deco_widgets.get(display_name)
            if cb is None:
                continue
            available = any(match_pattern in l['name'] for l in layers
                           if l['layer_type'] == 0)
            self._deco_available[display_name] = available
            if available:
                cb.pack(anchor="w")
            else:
                cb.pack_forget()
                var = self._deco_vars.get(display_name)
                if var is not None:
                    var.set(False)

        # Special: 頬 — exact name match
        key = self.stand_var.get()
        if key and self._has_hoho.get(key, False):
            self._hoho_cb.pack(anchor="w")
        else:
            self._hoho_cb.pack_forget()
            self.deco_hoho.set(False)

    # ============================================================
    # Event handlers
    # ============================================================

    def _on_stand_click(self, event):
        prev = self.stand_var.get()
        self.after_idle(lambda p=prev: self._toggle_stand(p))

    def _toggle_stand(self, prev_val):
        sel = self._stand_list.curselection()
        if not sel:
            return
        new_val = self._stand_list.get(sel[0])
        if new_val == prev_val:
            # Toggle off — allow empty stand selection
            self._stand_list.selection_clear(0, tk.END)
            self.stand_var.set('')
            self._current = None
            self._png_cache.clear()
            self._repopulate_lists()
            self._update_deco_visibility()
        else:
            self.stand_var.set(new_val)
            if new_val in self.variant_data:
                self._current = self.variant_data[new_val]
                self._png_cache.clear()
                self._repopulate_lists()
                self._update_deco_visibility()
        self._update_preview()

    def _on_res_click(self, event):
        prev = self.resolution.get()
        self.after_idle(lambda p=prev: self._toggle_res(p))

    def _toggle_res(self, prev_val):
        sel = self._res_list.curselection()
        if not sel:
            # Resolution does NOT allow empty selection — restore previous
            for i in range(self._res_list.size()):
                val = self._res_list.get(i)
                if (val == '高清' and prev_val == 'high') or \
                   (val == '低清' and prev_val == 'low'):
                    self._res_list.selection_set(i)
                    break
            return
        new_val_raw = self._res_list.get(sel[0])
        new_val = 'high' if new_val_raw == '高清' else 'low'
        if new_val != prev_val:
            self.resolution.set(new_val)
            self._png_cache.clear()
        self._update_preview()

    @staticmethod
    def _face_display_name(code, faces):
        """Build a human-readable face display name.

        Extracts the primary face name from info.txt resource paths and
        appends markers for blush (h) and tears (n) variants.
        """
        num_match = re.match(r'(\d+)([hn]?)$', code)
        if not num_match:
            return code
        num = num_match.group(1)
        suffix = num_match.group(2)

        base_name = None

        # For h/n variants, prefer the base (unsuffixed) face name
        if suffix:
            base_code = num
            if base_code in faces:
                for res in faces[base_code]:
                    if '/' in res:
                        part = res.split('/', 1)[1]
                        if '涙' not in part and '頬' not in part:
                            base_name = part
                            break

        # Fallback: extract from current code's own resources
        if base_name is None and code in faces:
            for res in faces[code]:
                if '/' in res:
                    part = res.split('/', 1)[1]
                    if '涙' not in part and '頬' not in part:
                        base_name = part
                        break

        if base_name is None:
            base_name = code

        if suffix == 'h':
            return f"{code} {base_name} +頬"
        elif suffix == 'n':
            return f"{code} {base_name} +涙"
        else:
            return f"{code} {base_name}"

    def _repopulate_lists(self):
        """Rebuild dress and face Listboxes from current variant's info."""
        if not self._current:
            self._dress_list.delete(0, tk.END)
            self._face_list.delete(0, tk.END)
            self._dress_display_map.clear()
            self.selected_dress.set('')
            self.selected_face.set('')
            return

        self._dress_list.delete(0, tk.END)
        self._dress_display_map.clear()

        dresses = self._current['dresses']

        for dress_name in sorted(dresses.keys()):
            diffs = dresses[dress_name]

            # Check if all diffs have the same resource set (e.g. レナ 女神)
            unique_sets = set(frozenset(r) for r in diffs.values())

            if len(unique_sets) == 1:
                # All diffs identical — show as single entry without diff suffix
                resources = diffs[list(diffs.keys())[0]]
                self._dress_display_map[dress_name] = resources
                self._dress_list.insert(tk.END, dress_name)
            else:
                # Multiple distinct diffs — expand to base_name-action_name
                for diff_num in sorted(diffs.keys()):
                    resources = diffs[diff_num]
                    # Action name = first resource excluding hair covers and ears
                    action_resources = [r for r in resources
                                        if 'かぶせ' not in r and '前髪' not in r
                                        and 'ケモミミ' not in r]
                    if action_resources:
                        action_name = action_resources[0]
                    else:
                        action_name = str(diff_num)
                    display = f"{dress_name}-{action_name}"
                    self._dress_display_map[display] = resources
                    self._dress_list.insert(tk.END, display)

        self._face_list.delete(0, tk.END)
        self._face_display_to_code = {}
        faces = self._current['faces']
        for code in sorted_face_codes(faces):
            display = self._face_display_name(code, faces)
            self._face_display_to_code[display] = code
            self._face_list.insert(tk.END, display)

        self.selected_dress.set('')
        self.selected_face.set('')

    def _on_change(self, *_):
        self._update_preview()

    def _on_dress_click(self, event):
        prev = self.selected_dress.get()
        self.after_idle(lambda p=prev: self._toggle_dress(p))

    def _toggle_dress(self, prev_val):
        sel = self._dress_list.curselection()
        if not sel:
            return
        new_val = self._dress_list.get(sel[0])
        if new_val == prev_val:
            self._dress_list.selection_clear(0, tk.END)
            self.selected_dress.set('')
        else:
            self.selected_dress.set(new_val)
        self._update_preview()

    def _on_face_click(self, event):
        prev = self.selected_face.get()
        self.after_idle(lambda p=prev: self._toggle_face(p))

    def _toggle_face(self, prev_val):
        sel = self._face_list.curselection()
        if not sel:
            return
        display = self._face_list.get(sel[0])
        new_code = self._face_display_to_code.get(display, display)
        if new_code == prev_val:
            self._face_list.selection_clear(0, tk.END)
            self.selected_face.set('')
        else:
            self.selected_face.set(new_code)
        self._update_preview()

    def _on_pan_start(self, event):
        self._canvas.scan_mark(event.x, event.y)

    def _on_pan_move(self, event):
        self._canvas.scan_dragto(event.x, event.y, gain=1)

    def _on_zoom(self, event):
        delta = event.delta / 120
        if delta > 0:
            self._preview_scale = min(4.0, self._preview_scale * 1.25)
        else:
            self._preview_scale = max(0.1, self._preview_scale / 1.25)

        # Cancel any pending full-res render
        if self._zoom_after_id:
            self.after_cancel(self._zoom_after_id)

        # Draft render: half-resolution for responsiveness during active zoom
        self._render_to_canvas(draft=True)
        # Schedule full-res render after 150ms idle
        self._zoom_after_id = self.after(150, self._do_zoom_render)

    def _do_zoom_render(self):
        self._zoom_after_id = None
        self._render_to_canvas(draft=False)

    def _on_canvas_resize(self, *_):
        if not self._render_pending:
            self._render_pending = True
            self.after(50, self._delayed_render)

    def _delayed_render(self):
        self._render_pending = False
        self._render_to_canvas()

    def _zoom_in(self):
        self._preview_scale = min(4.0, self._preview_scale * 1.25)
        self._render_to_canvas()

    def _zoom_out(self):
        self._preview_scale = max(0.1, self._preview_scale / 1.25)
        self._render_to_canvas()

    def _zoom_fit(self):
        self._preview_scale = 0
        self._render_to_canvas()

    # ---- Zoom percentage entry handlers ----

    def _on_zoom_entry_click(self, event):
        """Enable editing of zoom percentage on click."""
        if not self._zoom_editing:
            self._zoom_editing = True
            self._zoom_before_edit = self._zoom_var.get()
            self._zoom_entry.configure(state='normal')
            self._zoom_entry.select_range(0, tk.END)
            self._zoom_entry.icursor(tk.END)

    def _on_zoom_entry_confirm(self, event=None):
        """Validate and apply the manually entered zoom percentage."""
        if not self._zoom_editing:
            return
        raw = self._zoom_var.get().strip().rstrip('%')
        try:
            pct = int(raw)
            if 1 <= pct <= 400:
                self._preview_scale = pct / 100.0
                self._zoom_var.set(f"{pct}%")
                self._zoom_entry.configure(state='readonly')
                self._zoom_editing = False
                self._render_to_canvas()
                return
        except ValueError:
            pass
        # Invalid input — revert
        self._zoom_var.set(self._zoom_before_edit)
        self._zoom_entry.configure(state='readonly')
        self._zoom_editing = False

    def _on_zoom_entry_cancel(self, event):
        """Abort editing and restore previous zoom value."""
        if self._zoom_editing:
            self._zoom_var.set(self._zoom_before_edit)
            self._zoom_entry.configure(state='readonly')
            self._zoom_editing = False

    # ============================================================
    # Compositing logic
    # ============================================================

    def _get_visible_layer_ids(self):
        """Determine which layers should be visible based on current selection."""
        if not self._current:
            return set()

        layers, canvas_w, canvas_h = self._get_layers()
        dresses = self._current['dresses']
        faces = self._current['faces']
        dress = self.selected_dress.get()
        face_code = self.selected_face.get()

        resource_names = set()

        # Dress resources (from expanded display map)
        if dress and dress in self._dress_display_map:
            resource_names.update(self._dress_display_map[dress])

        # Common resources (intersection of ALL dress+diff resource sets)
        # Only added when a dress is selected (14.3: allow face/deco without dress)
        if dress:
            all_sets = []
            for dress_name, diffs in dresses.items():
                for diff_num, resources in diffs.items():
                    all_sets.append(resources)
            if all_sets:
                common = all_sets[0].copy()
                for s in all_sets[1:]:
                    common &= s
                resource_names.update(common)

        # Face resources
        if face_code and face_code in faces:
            for resource_path in faces[face_code]:
                if '/' in resource_path:
                    layer_name = resource_path.split('/', 1)[1]
                else:
                    layer_name = resource_path
                resource_names.add(layer_name)

        # Dynamic decorations (from config.ini)
        for display_name, match_pattern, bind_keyword in self._deco_specs:
            if not self._deco_available.get(display_name, False):
                continue
            var = self._deco_vars.get(display_name)
            if var is None:
                continue

            if var.get():
                # If bound to current dress, dress already provides specific variant
                is_bound = bind_keyword is not None and dress and bind_keyword in dress
                if not is_bound:
                    for layer in layers:
                        if match_pattern in layer['name']:
                            resource_names.add(layer['name'])
            else:
                # Preserve dress-provided resources matching this pattern (BUG-4 fix)
                dress_deco = set()
                if dress and dress in self._dress_display_map:
                    dress_deco = {n for n in self._dress_display_map[dress]
                                  if match_pattern in n}
                resource_names = {n for n in resource_names
                                  if match_pattern not in n or n in dress_deco}

        # Special: 頬 — exact name match (per design doc 11.2)
        if self.deco_hoho.get():
            for layer in layers:
                if layer['name'] == '頬':
                    resource_names.add(layer['name'])

        # Match resource names to layer_ids
        visible_ids = set()
        for layer in layers:
            if layer['layer_type'] == 0 and layer['name'] in resource_names:
                visible_ids.add(layer['layer_id'])

        return visible_ids

    def _composite(self):
        """Build the full-resolution composite image. Returns PIL Image.

        Layers are composited in reverse file order: body/dress layers appear
        at the end of the layout file and must be drawn FIRST (bottom of Z-order);
        ears/effects/tears appear near the beginning and must be drawn LAST (top).
        """
        layers, canvas_w, canvas_h = self._get_layers()
        visible_ids = self._get_visible_layer_ids()

        visible_layers = [l for l in layers
                          if l['layer_type'] == 0 and l['layer_id'] in visible_ids]

        self._missing_paths = set()

        if not visible_layers:
            return Image.new("RGBA", (1, 1), (0, 0, 0, 0))

        layer_images = []
        min_x = float('inf')
        min_y = float('inf')
        max_x = float('-inf')
        max_y = float('-inf')

        for layer in visible_layers:
            png_path = self._get_png_path(layer['layer_id'])
            if png_path is None or not png_path.exists():
                self._missing_paths.add(
                    str(png_path) if png_path else f"layer_id={layer['layer_id']}"
                )
                continue
            try:
                img = self._load_png(str(png_path))
            except Exception:
                self._missing_paths.add(str(png_path))
                continue

            left = layer['left']
            top = layer['top']
            right = left + img.width
            bottom = top + img.height

            min_x = min(min_x, left)
            min_y = min(min_y, top)
            max_x = max(max_x, right)
            max_y = max(max_y, bottom)

            layer_images.append((img, left, top))

        if not layer_images:
            return Image.new("RGBA", (1, 1), (0, 0, 0, 0))

        # Use bounding box of all visible layers (WYSIWYG — no nominal canvas padding)
        final_w = max_x - min_x
        final_h = max_y - min_y

        composite = Image.new("RGBA", (final_w, final_h), (0, 0, 0, 0))
        # Reverse file order so that body (end of file) is drawn first = bottom
        for img, left, top in reversed(layer_images):
            composite.alpha_composite(img, (left - min_x, top - min_y))

        return composite

    def _show_missing_warning(self, missing):
        """Show a warning dialog listing missing files."""
        lines = "\n".join(sorted(missing)[:20])
        if len(missing) > 20:
            lines += f"\n... 及其他 {len(missing) - 20} 个文件"
        messagebox.showwarning("缺失文件", f"以下文件未找到，合成结果可能不完整:\n\n{lines}")

    def _update_preview(self):
        """Rebuild composite and render to canvas."""
        self._composite_image = self._composite()
        self._zoom_fit()
        self._render_to_canvas()

        visible_ids = self._get_visible_layer_ids()
        layers, _, _ = self._get_layers()
        visible_count = sum(1 for l in layers
                            if l['layer_type'] == 0 and l['layer_id'] in visible_ids)
        self._status_label.config(text=f"可见图层: {visible_count}")

        # Update dynamic decoration bind states: auto-enable + disable when
        # selected dress matches a decoration's bind keyword
        dress = self.selected_dress.get()
        for display_name, _match_pattern, bind_keyword in self._deco_specs:
            cb = self._deco_widgets.get(display_name)
            var = self._deco_vars.get(display_name)
            if cb is None or var is None:
                continue
            if bind_keyword and dress and bind_keyword in dress:
                cb.state(['disabled'])
                var.set(True)
            else:
                cb.state(['!disabled'])

        # Show missing file warning if new files are missing
        if self._missing_paths and self._missing_paths != self._last_warned_missing:
            self._last_warned_missing = self._missing_paths.copy()
            self.after_idle(
                lambda m=self._missing_paths.copy(): self._show_missing_warning(m)
            )
        elif not self._missing_paths:
            self._last_warned_missing = set()

    # ============================================================
    # Rendering
    # ============================================================

    def _make_checkered_bg(self, width, height, grid_size=16):
        """Create a checkered background image using a tiled 2x2 pattern."""
        tile_size = grid_size * 2
        light = (220, 220, 220, 255)
        dark = (255, 255, 255, 255)

        tile = Image.new("RGBA", (tile_size, tile_size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(tile)
        draw.rectangle([0, 0, grid_size - 1, grid_size - 1], fill=light)
        draw.rectangle([grid_size, 0, tile_size - 1, grid_size - 1], fill=dark)
        draw.rectangle([0, grid_size, grid_size - 1, tile_size - 1], fill=dark)
        draw.rectangle([grid_size, grid_size, tile_size - 1, tile_size - 1], fill=light)

        bg = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        for y in range(0, height, tile_size):
            for x in range(0, width, tile_size):
                bg.paste(tile, (x, y))
        return bg

    def _render_to_canvas(self, draft=False):
        """Render the composite image onto the tkinter Canvas.

        Args:
            draft: If True, use faster resize filter for responsiveness (during zoom).
                   Target size is the same as full-res — no size jump.
        """
        if self._composite_image is None:
            return

        canvas_w = self._canvas.winfo_width()
        canvas_h = self._canvas.winfo_height()
        if canvas_w < 10 or canvas_h < 10:
            return

        img = self._composite_image

        if self._preview_scale <= 0:
            scale_w = (canvas_w - 20) / img.width
            scale_h = (canvas_h - 20) / img.height
            scale = min(scale_w, scale_h, 1.0)
        else:
            scale = self._preview_scale

        new_w = int(img.width * scale)
        new_h = int(img.height * scale)

        if new_w < 1 or new_h < 1:
            return

        bg = self._make_checkered_bg(new_w, new_h, max(8, int(16 * scale)))
        # Draft uses faster filter; full-res uses high-quality LANCZOS
        resample = Image.BILINEAR if draft else Image.LANCZOS
        resized = img.resize((new_w, new_h), resample)
        bg.alpha_composite(resized, (0, 0))

        self._tk_image = ImageTk.PhotoImage(bg)
        self._canvas.delete("all")
        self._canvas.create_image(
            new_w // 2, new_h // 2,
            image=self._tk_image, anchor="center"
        )
        self._canvas.config(scrollregion=(0, 0, new_w, new_h))

        # Only update zoom display for full-res renders
        if not draft:
            pct = int(scale * 100)
            self._zoom_var.set(f"{pct}%")

    # ============================================================
    # Export
    # ============================================================

    def _export_png(self):
        """Export the current composite to a PNG file."""
        if self._composite_image is None:
            messagebox.showwarning("警告", "没有可导出的图像。")
            return

        export_img = self._composite()
        if export_img is None:
            messagebox.showwarning("警告", "无法合成图像。")
            return

        dress = self.selected_dress.get() or "nodress"
        face = self.selected_face.get() or "noface"
        res_label = "HD" if self.resolution.get() == 'high' else "LD"
        default_name = f"{self.cfg['name']}_{dress}_{face}_{res_label}.png"

        filepath = filedialog.asksaveasfilename(
            parent=self,
            defaultextension=".png",
            filetypes=[("PNG 图片", "*.png"), ("所有文件", "*.*")],
            initialfile=default_name,
        )
        if not filepath:
            return

        try:
            export_img.save(filepath, "PNG", optimize=False)
            messagebox.showinfo("导出成功", f"已保存到:\n{filepath}")
        except Exception as e:
            messagebox.showerror("导出失败", str(e))


# ============================================================
# Entry point
# ============================================================

def main():
    app = SpriteComposer()
    app.mainloop()


if __name__ == '__main__':
    main()
