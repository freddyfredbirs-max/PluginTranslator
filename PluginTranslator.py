"""
Universal locale-file translator with GUI
==========================================
Supports .yml/.yaml, .json, and .properties files — the three most common
locale formats used by Minecraft plugins (and many other apps).

Requirements:
    pip install ruamel.yaml

Run:
    python PluginTranslator.py
 #Kapakal
------------------------------------------------------------------------
HOW THE FILE IS ORGANISED (read this before editing)
------------------------------------------------------------------------
1. CORE LOGIC   — translation engine, placeholder protection, file I/O.
                  You normally don't need to touch this.
2. GUI LAYOUT   — window, widgets, colors, fonts, spacing.
                  This is YOUR section to customize freely.
3. GUI LOGIC    — connects buttons to the core logic (threading, progress).
                  Safe to leave alone unless you add new controls.

Search for "CUSTOMIZE APPEARANCE HERE" to jump straight to the styling zone.
------------------------------------------------------------------------
"""

import re
import json
import time
import queue
import warnings
import threading
import urllib.request
import urllib.parse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

import tkinter as tk
from tkinter import ttk, filedialog, messagebox


def _ensure_ruamel_yaml() -> bool:
    """Try to import ruamel.yaml; if missing and we're running as a plain
    .py script (not a compiled .exe), auto pip-install it and retry.
    Returns True if ruamel.yaml is available after this call."""
    try:
        import ruamel.yaml  # noqa: F401
        return True
    except ImportError:
        pass

    if getattr(sys, "frozen", False):
        # Running as a compiled .exe — there's no Python/pip to reach for here.
        # ruamel.yaml must be bundled at build time instead (see build notes).
        return False

    try:
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "ruamel.yaml"])
        import importlib
        importlib.invalidate_caches()
        import ruamel.yaml  # noqa: F401
        return True
    except Exception:
        return False


# ============================================================================
# 1. CORE LOGIC — translation engine (format-agnostic)
# ============================================================================

import sys

# Cache always lives in ONE fixed place — next to this script (or next to the
# .exe if compiled with PyInstaller) — regardless of which folder the
# input/output locale files are in. This means translations accumulate in a
# single cache no matter which project you're working on.
#
# NOTE: when frozen into a PyInstaller .exe, __file__ points into a temporary
# extraction folder that changes every run — so we use sys.executable's
# folder instead in that case.
if getattr(sys, "frozen", False):
    _SCRIPT_DIR = Path(sys.executable).resolve().parent
else:
    _SCRIPT_DIR = Path(__file__).resolve().parent
CACHE_FILE_PATH = _SCRIPT_DIR / "translation_cache.json"

# ---------------------------------------------------------------------------
# Placeholder protection — tokens that must NOT be translated
# ---------------------------------------------------------------------------
_BUILT_IN_PATTERNS = [
    r'&%[0-9a-fk-orA-FK-OR]',           # &%0 &%a &%f — CMI hex-color legend tokens
                                         # (must come BEFORE the printf pattern below,
                                         # since %d / %f would otherwise collide with
                                         # hex digits d/f and get masked incorrectly)
    r'\[[^\[\]\n]{1,60}\]',            # [playerName]
    r'\{[^{}\n]{1,60}\}',              # {gcw} {0}
    r'%[A-Za-z0-9_.]{1,40}%',          # %player_name%
    r'%\d{1,3}\$[sdf]',                # %1$s  %2$d  (numbered printf args only)
    r'(?<![0-9a-fA-F])%s\b',           # bare %s (not preceded by a hex digit context)
    r'[&\u00A7][0-9a-fk-orA-FK-OR]',   # &a  §c
    r'</?[^<>\n]{1,60}>',              # <red> <player>
    r'\([+\-][^()\n]{1,20}\)',         # (+cb) (-s)
    r'/[a-zA-Z][a-zA-Z0-9_./:~\-]*',   # /cmi /warp
    r'![\w:]+!',                       # !actionbar!
    r'#[0-9a-fA-F]{6}',                # #FF0000
]

# Add your own protected patterns here (see the --scan tip in the README below)
PROTECTED_EXTRA: list[str] = []

PLACEHOLDER_RE = re.compile("|".join(_BUILT_IN_PATTERNS + PROTECTED_EXTRA))

HASH_SUFFIX_RE = re.compile(r'\s*[0-9a-fA-F]{32}[a-z]{2}')
BARE_HASH_RE   = re.compile(r'\b[0-9a-fA-F]{32}\b')

# ---------------------------------------------------------------------------
# Placeholder masking using Private Use Area (PUA) characters.
#
# WHY NOT "@@0@@" style ASCII tokens:
# In right-to-left languages (Arabic, Hebrew, etc.) the Unicode bidirectional
# algorithm can reorder digit runs embedded inside RTL text, splitting a
# token like "@@0@@" into pieces (e.g. "0@@@@") — the restore regex then
# fails to find it and "@@" garbage is left in the output.
#
# A single Private Use Area character (U+E000–U+F8FF) is one indivisible
# Unicode code point — bidi reordering can move it as a whole but can never
# split it apart, and translation engines never try to "translate" it since
# it isn't a real word in any language. This is the same technique used by
# professional CAT/localization tools to protect placeholders.
# ---------------------------------------------------------------------------
_PUA_START = 0xE000
_PUA_END   = 0xF8FF
_PUA_RANGE_RE = re.compile(f'[{chr(_PUA_START)}-{chr(_PUA_END)}]')


def _mask(text: str) -> tuple[str, list[str]]:
    placeholders: list[str] = []
    def repl(m: re.Match) -> str:
        idx = len(placeholders)
        placeholders.append(m.group())
        if idx > (_PUA_END - _PUA_START):
            # Absurdly many placeholders in one string — fall back to leaving
            # it untranslated rather than overflowing the PUA range.
            return m.group()
        return chr(_PUA_START + idx)
    return PLACEHOLDER_RE.sub(repl, text), placeholders


def _unmask(text: str, placeholders: list[str]) -> str:
    def repl(m: re.Match) -> str:
        idx = ord(m.group()) - _PUA_START
        return placeholders[idx] if 0 <= idx < len(placeholders) else m.group()
    return _PUA_RANGE_RE.sub(repl, text)


# Invisible characters Google Translate sometimes inserts as internal segment
# separators when it splits long strings for translation (same root cause as
# the hash-suffix issue, just a different artifact shape).
INVISIBLE_ARTIFACT_RE = re.compile('[\u200B\u200C\u200D\uFEFF]')


def _strip_hash(text: str) -> str:
    prev = None
    while prev != text:
        prev = text
        text = HASH_SUFFIX_RE.sub("", text)
    text = INVISIBLE_ARTIFACT_RE.sub("", text)
    return text.strip()


class Translator:
    """Wraps the free Google Translate endpoint with cache + retry."""

    def __init__(self, source_lang: str, target_lang: str, cache_path: Path,
                 threads: int = 6, max_retries: int = 4, polite_delay: float = 0.06):
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.cache_path = cache_path
        self.threads = threads
        self.max_retries = max_retries
        self.polite_delay = polite_delay
        self._cache: dict[str, str] = {}
        self._cache_lock = Lock()
        self._load_cache()

    def _load_cache(self) -> None:
        if self.cache_path.exists():
            try:
                with open(self.cache_path, encoding="utf-8") as f:
                    self._cache.update(json.load(f))
            except Exception:
                pass

    def save_cache(self) -> None:
        try:
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, ensure_ascii=False)
        except Exception:
            pass

    def _call_google(self, text: str) -> str:
        encoded = urllib.parse.quote(text)
        url = (
            f"https://translate.googleapis.com/translate_a/single"
            f"?client=gtx&sl={self.source_lang}&tl={self.target_lang}&dt=t&q={encoded}"
        )
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")

        data = json.loads(raw)
        result = []
        if isinstance(data, list) and data and isinstance(data[0], list):
            for segment in data[0]:
                if isinstance(segment, list) and segment and isinstance(segment[0], str):
                    seg_text = segment[0]
                    if not BARE_HASH_RE.fullmatch(seg_text.strip()):
                        result.append(seg_text)
        return "".join(result)

    def _translate_raw(self, text: str) -> str:
        last_err = None
        for attempt in range(self.max_retries + 1):
            try:
                time.sleep(0.4 * (2 ** (attempt - 1)) if attempt > 0 else self.polite_delay)
                return _strip_hash(self._call_google(text))
            except Exception as e:
                last_err = e
        raise RuntimeError(f"Translation failed after {self.max_retries+1} attempts: {last_err}")

    def translate_text(self, text: str) -> str:
        if not text or not text.strip():
            return text

        masked, placeholders = _mask(text)
        if not _PUA_RANGE_RE.sub('', masked).strip():
            return _unmask(masked, placeholders)

        cache_key = f"{self.source_lang}>{self.target_lang}>{masked}"
        with self._cache_lock:
            cached = self._cache.get(cache_key)

        if cached is None:
            try:
                cached = self._translate_raw(masked)
            except Exception:
                cached = masked  # fall back: leave masked text (better than crashing)
            with self._cache_lock:
                self._cache[cache_key] = cached

        return _unmask(cached, placeholders)

    def translate_many(self, strings: list[str], progress_cb=None, cancel_event=None) -> list[str]:
        """Translate a list of strings in parallel, preserving order.
        If cancel_event is set mid-run, stops submitting new work and returns
        whatever was completed so far (untranslated entries keep original text)."""
        results: list[str] = list(strings)  # default: keep original if cancelled early
        total = len(strings)
        done = 0
        lock = Lock()

        def worker(idx_text):
            idx, text = idx_text
            if cancel_event is not None and cancel_event.is_set():
                return
            results[idx] = self.translate_text(text)
            nonlocal done
            with lock:
                done += 1
                if progress_cb:
                    progress_cb(done, total)

        with ThreadPoolExecutor(max_workers=self.threads) as ex:
            futures = []
            for item in enumerate(strings):
                if cancel_event is not None and cancel_event.is_set():
                    break
                futures.append(ex.submit(worker, item))
            for f in as_completed(futures):
                exc = f.exception()
                if exc:
                    raise exc
        return results


# ---------------------------------------------------------------------------
# File format handlers — each knows how to read/collect/write its format
# ---------------------------------------------------------------------------

class FormatHandler:
    """Base interface for a locale file format."""
    extensions: tuple[str, ...] = ()

    def load(self, path: Path):
        raise NotImplementedError

    def collect_strings(self, data) -> list[tuple[list, str]]:
        """Return [(path, string), ...] for every translatable string."""
        raise NotImplementedError

    def write_back(self, path_entry, translated: str) -> None:
        """Write a translated string back into the data structure."""
        raise NotImplementedError

    def save(self, data, path: Path) -> None:
        raise NotImplementedError


class YamlHandler(FormatHandler):
    extensions = (".yml", ".yaml")

    def __init__(self):
        from ruamel.yaml import YAML
        self.yaml = YAML()
        self.yaml.preserve_quotes = True
        self.yaml.width = 4096
        # Some real-world plugin locale files have duplicate keys (technically
        # invalid YAML, but common in practice). Without this, ruamel.yaml
        # raises DuplicateKeyError and the whole file fails to load.
        # allow_duplicate_keys makes it take the LAST occurrence, like most
        # lenient YAML parsers do, instead of crashing.
        self.yaml.allow_duplicate_keys = True

    def load(self, path: Path):
        with open(path, encoding="utf-8") as f:
            return self.yaml.load(f)

    def collect_strings(self, data):
        items = []

        def walk(node, path):
            if isinstance(node, str):
                items.append((list(path), node))
            elif isinstance(node, dict):
                for k, v in node.items():
                    walk(v, path + [("dict", k, node)])
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    walk(v, path + [("list", i, node)])

        walk(data, [])
        return items

    def write_back(self, path_entry, translated: str) -> None:
        if not path_entry:
            return
        last = path_entry[-1]
        last[2][last[1]] = translated

    def save(self, data, path: Path) -> None:
        with open(path, "w", encoding="utf-8") as f:
            self.yaml.dump(data, f)


class JsonHandler(FormatHandler):
    extensions = (".json",)

    def load(self, path: Path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def collect_strings(self, data):
        items = []

        def walk(node, path):
            if isinstance(node, str):
                items.append((list(path), node))
            elif isinstance(node, dict):
                for k, v in node.items():
                    walk(v, path + [("dict", k, node)])
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    walk(v, path + [("list", i, node)])

        walk(data, [])
        return items

    def write_back(self, path_entry, translated: str) -> None:
        if not path_entry:
            return
        last = path_entry[-1]
        last[2][last[1]] = translated

    def save(self, data, path: Path) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


class PropertiesHandler(FormatHandler):
    """
    Simple line-based .properties handler (key=value or key: value).
    Preserves comments (# or !) and blank lines exactly.
    """
    extensions = (".properties", ".lang")

    _KV_RE = re.compile(r'^(\s*)([^=:\s][^=:]*?)(\s*[=:]\s*)(.*)$')

    def load(self, path: Path):
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
        return {"lines": [l.rstrip("\n") for l in lines]}

    def collect_strings(self, data):
        items = []
        lines = data["lines"]
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("!"):
                continue
            m = self._KV_RE.match(line)
            if m:
                value = m.group(4)
                if value.strip():
                    items.append(([("line", i, lines)], value))
        return items

    def write_back(self, path_entry, translated: str) -> None:
        if not path_entry:
            return
        _, idx, lines = path_entry[0]
        line = lines[idx]
        m = self._KV_RE.match(line)
        if m:
            lines[idx] = m.group(1) + m.group(2) + m.group(3) + translated

    def save(self, data, path: Path) -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(data["lines"]) + "\n")


HANDLERS: list[FormatHandler] = []  # populated lazily (YAML needs ruamel installed)


def get_handler_for(path: Path) -> FormatHandler:
    suffix = path.suffix.lower()
    if suffix in (".yml", ".yaml"):
        return YamlHandler()
    if suffix == ".json":
        return JsonHandler()
    if suffix in (".properties", ".lang"):
        return PropertiesHandler()
    raise ValueError(f"Unsupported file extension: {suffix}")


# ============================================================================
# 2. GUI LAYOUT  ---  CUSTOMIZE APPEARANCE HERE
# ============================================================================
# Everything visual (colors, fonts, spacing, window size, labels) lives in
# this section. Change these values freely — the translation logic above
# doesn't depend on any of it.

APP_TITLE      = "Locale Translator"
WINDOW_SIZE    = "720x560"
MIN_WINDOW_SIZE = (600, 450)  # (width, height) — window can't be shrunk past this
BG_COLOR       = "#1e1e2e"
FG_COLOR       = "#cdd6f4"
ACCENT_COLOR   = "#89b4fa"
BUTTON_COLOR   = "#313244"
FONT_FAMILY    = "Segoe UI"
FONT_SIZE      = 10
LOG_BG         = "#11111b"
LOG_FG         = "#a6e3a1"

LANGUAGES = {
    "English":              "en",
    "Русский":              "ru",
    "Українська":           "uk",
    "Deutsch":              "de",
    "Français":             "fr",
    "Español":              "es",
    "Italiano":             "it",
    "Português":            "pt",
    "Nederlands":           "nl",
    "Polski":               "pl",
    "Čeština":              "cs",
    "Magyar":               "hu",
    "Română":               "ro",
    "Български":            "bg",
    "Ελληνικά":             "el",
    "Türkçe":               "tr",
    "العربية":              "ar",
    "עברית":                "iw",
    "हिन्दी":                "hi",
    "ไทย":                  "th",
    "Tiếng Việt":           "vi",
    "Bahasa Indonesia":     "id",
    "中文(简体)":            "zh-CN",
    "日本語":                "ja",
    "한국어":                "ko",
    "Svenska":              "sv",
    "Dansk":                "da",
    "Suomi":                "fi",
    "Norsk":                "no",
}
# Shown only in the "From" dropdown — tells Google Translate to detect the
# source language automatically (sl=auto).
SOURCE_ONLY_LANGUAGES = {
    "Auto-detect": "auto",
}


# ---------------------------------------------------------------------------
# Simple tooltip helper (tkinter has no built-in tooltip widget)
# ---------------------------------------------------------------------------
class Tooltip:
    """Attach a hover tooltip to any widget: Tooltip(widget, 'text')"""

    def __init__(self, widget: tk.Widget, text: str):
        self.widget = widget
        self.text = text
        self.tip_window: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def set_text(self, text: str):
        self.text = text

    def _show(self, _event=None):
        if self.tip_window or not self.text:
            return
        x = self.widget.winfo_rootx() + 10
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        label = tk.Label(tw, text=self.text, justify="left", bg="#f9e2af",
                          fg="#1e1e2e", relief="solid", borderwidth=1,
                          font=(FONT_FAMILY, 9), padx=6, pady=3, wraplength=280)
        label.pack()

    def _hide(self, _event=None):
        if self.tip_window:
            self.tip_window.destroy()
            self.tip_window = None


# ---------------------------------------------------------------------------
# Interface (UI) localization — separate from translation target languages.
# This only changes the labels/buttons of the app itself, not what gets
# translated in the locale file.
# ---------------------------------------------------------------------------
UI_STRINGS = {
    "en": {
        "ui_lang_label":    "UI:",
        "input_file":       "Input file:",
        "not_selected":     "(not selected)",
        "browse":           "Browse...",
        "from":             "From:",
        "to":               "To:",
        "output_file":      "Output file:",
        "auto":             "(auto)",
        "change":           "Change...",
        "scan":             "Scan for unknown tokens",
        "clear_cache":      "Clear cache",
        "clear_cache_tip":  "Deletes translation_cache.json.\nUse this if you changed a "
                             "placeholder pattern and need\nGoogle to re-translate strings "
                             "that were cached before the fix.",
        "cancel":           "Cancel",
        "translate":        "Translate",
        "ready":            "Ready.",
        "progress":         "Progress: {done}/{total}",
        "cache_cleared":    "Cache cleared.",
        "cache_clear_confirm_title": "Clear cache?",
        "cache_clear_confirm_msg":   "This will delete translation_cache.json.\n"
                                      "All strings will be re-translated from scratch "
                                      "next time.\n\nContinue?",
        "no_cache_file":    "No cache file found — nothing to clear.",
    },
    "ru": {
        "ui_lang_label":    "Язык:",
        "input_file":       "Исходный файл:",
        "not_selected":     "(не выбран)",
        "browse":           "Обзор...",
        "from":             "Откуда:",
        "to":               "Куда:",
        "output_file":      "Файл результата:",
        "auto":             "(авто)",
        "change":           "Изменить...",
        "scan":             "Проверить неизвестные токены",
        "clear_cache":      "Очистить кэш",
        "clear_cache_tip":  "Удаляет translation_cache.json.\nИспользуй если поменял "
                             "паттерн плейсхолдера и нужно,\nчтобы Google заново перевёл "
                             "строки, закэшированные до правки.",
        "cancel":           "Отмена",
        "translate":        "Перевести",
        "ready":            "Готово к работе.",
        "progress":         "Прогресс: {done}/{total}",
        "cache_cleared":    "Кэш очищен.",
        "cache_clear_confirm_title": "Очистить кэш?",
        "cache_clear_confirm_msg":   "Файл translation_cache.json будет удалён.\n"
                                      "В следующий раз все строки переведутся заново "
                                      "с нуля.\n\nПродолжить?",
        "no_cache_file":    "Файл кэша не найден — нечего очищать.",
    },
    "uk": {
        "ui_lang_label":    "Мова:",
        "input_file":       "Вихідний файл:",
        "not_selected":     "(не вибрано)",
        "browse":           "Огляд...",
        "from":             "Звідки:",
        "to":               "Куди:",
        "output_file":      "Файл результату:",
        "auto":             "(авто)",
        "change":           "Змінити...",
        "scan":             "Перевірити невідомі токени",
        "clear_cache":      "Очистити кеш",
        "clear_cache_tip":  "Видаляє translation_cache.json.\nВикористовуй якщо змінив "
                             "патерн плейсхолдера і потрібно,\nщоб Google заново переклав "
                             "рядки, закешовані до правки.",
        "cancel":           "Скасувати",
        "translate":        "Перекласти",
        "ready":            "Готово до роботи.",
        "progress":         "Прогрес: {done}/{total}",
        "cache_cleared":    "Кеш очищено.",
        "cache_clear_confirm_title": "Очистити кеш?",
        "cache_clear_confirm_msg":   "Файл translation_cache.json буде видалено.\n"
                                      "Наступного разу всі рядки перекладуться заново "
                                      "з нуля.\n\nПродовжити?",
        "no_cache_file":    "Файл кешу не знайдено — нічого очищати.",
    },
}

# Which UI languages appear in the corner selector, and in what order.
UI_LANGUAGE_ORDER = ["en", "ru", "uk"]
UI_LANGUAGE_LABELS = {"en": "English", "ru": "Русский", "uk": "Українська"}


class TranslatorGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry(WINDOW_SIZE)
        self.root.minsize(*MIN_WINDOW_SIZE)  # prevents the layout from breaking
                                              # when shrunk; maximize/half-screen
                                              # snap (Windows Aero Snap) still works
        self.root.configure(bg=BG_COLOR)

        self.input_path: Path | None = None
        self.output_path: Path | None = None
        self._output_manually_set = False
        self._log_queue: queue.Queue = queue.Queue()
        self._worker_thread: threading.Thread | None = None
        self._cancel_event = threading.Event()
        self.ui_lang = "en"  # current interface language, see UI_STRINGS

        self._build_widgets()
        self._apply_ui_language()
        self.root.after(100, self._drain_log_queue)

    def t(self, key: str, **kwargs) -> str:
        """Fetch a UI string in the current interface language."""
        text = UI_STRINGS.get(self.ui_lang, UI_STRINGS["en"]).get(key, key)
        return text.format(**kwargs) if kwargs else text

    # ------------------------------------------------------------------
    # Widget construction (feel free to restyle everything below)
    # ------------------------------------------------------------------
    def _build_widgets(self):
        pad = {"padx": 10, "pady": 6}
        font_normal = (FONT_FAMILY, FONT_SIZE)
        font_bold   = (FONT_FAMILY, FONT_SIZE, "bold")
        self._font_normal = font_normal
        self._font_bold = font_bold

        # --- Corner: interface language selector ---
        frame_top = tk.Frame(self.root, bg=BG_COLOR)
        frame_top.pack(fill="x", padx=10, pady=(8, 0))

        self.lbl_ui_lang = tk.Label(frame_top, text="UI:", bg=BG_COLOR, fg=FG_COLOR,
                                     font=font_normal)
        self.lbl_ui_lang.pack(side="right", padx=(4, 4))
        self.combo_ui_lang = ttk.Combobox(
            frame_top,
            values=[UI_LANGUAGE_LABELS[c] for c in UI_LANGUAGE_ORDER],
            state="readonly", width=12
        )
        self.combo_ui_lang.set(UI_LANGUAGE_LABELS[self.ui_lang])
        self.combo_ui_lang.pack(side="right")
        self.combo_ui_lang.bind("<<ComboboxSelected>>", self._on_ui_lang_change)

        # --- File selection row ---
        frame_file = tk.Frame(self.root, bg=BG_COLOR)
        frame_file.pack(fill="x", **pad)

        self.lbl_input_title = tk.Label(frame_file, text="Input file:", bg=BG_COLOR,
                                         fg=FG_COLOR, font=font_bold)
        self.lbl_input_title.pack(side="left")
        self.lbl_input = tk.Label(frame_file, text="(not selected)", bg=BG_COLOR,
                                   fg=FG_COLOR, font=font_normal, anchor="w")
        self.lbl_input.pack(side="left", fill="x", expand=True, padx=8)
        self.btn_browse = tk.Button(frame_file, text="Browse...", command=self._pick_input,
                                     bg=BUTTON_COLOR, fg=FG_COLOR, font=font_normal,
                                     relief="flat", padx=10)
        self.btn_browse.pack(side="right")

        # --- Language selection row (translation source/target) ---
        frame_lang = tk.Frame(self.root, bg=BG_COLOR)
        frame_lang.pack(fill="x", **pad)

        self.lbl_from = tk.Label(frame_lang, text="From:", bg=BG_COLOR, fg=FG_COLOR,
                                  font=font_bold)
        self.lbl_from.pack(side="left")
        source_values = list(SOURCE_ONLY_LANGUAGES.keys()) + list(LANGUAGES.keys())
        self.combo_source = ttk.Combobox(frame_lang, values=source_values,
                                          state="readonly", width=14)
        self.combo_source.set("Auto-detect")
        self.combo_source.pack(side="left", padx=8)

        self.lbl_to = tk.Label(frame_lang, text="To:", bg=BG_COLOR, fg=FG_COLOR,
                                font=font_bold)
        self.lbl_to.pack(side="left", padx=(20, 0))
        self.combo_target = ttk.Combobox(frame_lang, values=list(LANGUAGES.keys()),
                                          state="readonly", width=14)
        self.combo_target.set("Русский")
        self.combo_target.pack(side="left", padx=8)
        self.combo_target.bind("<<ComboboxSelected>>", lambda e: self._update_suggested_output())

        # --- Output file row ---
        frame_out = tk.Frame(self.root, bg=BG_COLOR)
        frame_out.pack(fill="x", **pad)

        self.lbl_output_title = tk.Label(frame_out, text="Output file:", bg=BG_COLOR,
                                          fg=FG_COLOR, font=font_bold)
        self.lbl_output_title.pack(side="left")
        self.lbl_output = tk.Label(frame_out, text="(auto)", bg=BG_COLOR,
                                    fg=FG_COLOR, font=font_normal, anchor="w")
        self.lbl_output.pack(side="left", fill="x", expand=True, padx=8)
        self.btn_change_output = tk.Button(frame_out, text="Change...", command=self._pick_output,
                                            bg=BUTTON_COLOR, fg=FG_COLOR, font=font_normal,
                                            relief="flat", padx=10)
        self.btn_change_output.pack(side="right")

        # --- Action buttons ---
        frame_actions = tk.Frame(self.root, bg=BG_COLOR)
        frame_actions.pack(fill="x", **pad)

        self.btn_scan = tk.Button(frame_actions, text="Scan for unknown tokens",
                                   command=self._on_scan, bg=BUTTON_COLOR, fg=FG_COLOR,
                                   font=font_normal, relief="flat", padx=10, pady=6)
        self.btn_scan.pack(side="left")

        self.btn_clear_cache = tk.Button(frame_actions, text="Clear cache",
                                          command=self._on_clear_cache, bg=BUTTON_COLOR,
                                          fg=FG_COLOR, font=font_normal, relief="flat",
                                          padx=10, pady=6)
        self.btn_clear_cache.pack(side="left", padx=(10, 0))
        self.tooltip_clear_cache = Tooltip(self.btn_clear_cache, "")

        self.btn_cancel = tk.Button(frame_actions, text="Cancel",
                                     command=self._on_cancel, bg="#f38ba8", fg="#1e1e2e",
                                     font=font_bold, relief="flat", padx=14, pady=6,
                                     state="disabled")
        self.btn_cancel.pack(side="left", padx=(10, 0))

        self.btn_translate = tk.Button(frame_actions, text="Translate",
                                        command=self._on_translate, bg=ACCENT_COLOR,
                                        fg="#1e1e2e", font=font_bold, relief="flat",
                                        padx=20, pady=6)
        self.btn_translate.pack(side="right")

        # --- Progress bar ---
        self.progress = ttk.Progressbar(self.root, mode="determinate")
        self.progress.pack(fill="x", padx=10, pady=(0, 6))

        self.lbl_progress = tk.Label(self.root, text="Ready.", bg=BG_COLOR,
                                      fg=FG_COLOR, font=font_normal)
        self.lbl_progress.pack(fill="x", padx=10)

        # --- Log output ---
        frame_log = tk.Frame(self.root, bg=BG_COLOR)
        frame_log.pack(fill="both", expand=True, padx=10, pady=10)

        self.text_log = tk.Text(frame_log, bg=LOG_BG, fg=LOG_FG,
                                 font=("Consolas", 9), wrap="word", height=15)
        self.text_log.pack(side="left", fill="both", expand=True)

        scrollbar = tk.Scrollbar(frame_log, command=self.text_log.yview)
        scrollbar.pack(side="right", fill="y")
        self.text_log.config(yscrollcommand=scrollbar.set)
        self.text_log.config(state="disabled")

    # ------------------------------------------------------------------
    # Interface language switching
    # ------------------------------------------------------------------
    def _on_ui_lang_change(self, _event=None):
        label = self.combo_ui_lang.get()
        for code, lbl in UI_LANGUAGE_LABELS.items():
            if lbl == label:
                self.ui_lang = code
                break
        self._apply_ui_language()

    def _apply_ui_language(self):
        self.lbl_ui_lang.config(text=self.t("ui_lang_label"))
        self.lbl_input_title.config(text=self.t("input_file"))
        if not self.input_path:
            self.lbl_input.config(text=self.t("not_selected"))
        self.btn_browse.config(text=self.t("browse"))
        self.lbl_from.config(text=self.t("from"))
        self.lbl_to.config(text=self.t("to"))
        self.lbl_output_title.config(text=self.t("output_file"))
        if not self.output_path:
            self.lbl_output.config(text=self.t("auto"))
        self.btn_change_output.config(text=self.t("change"))
        self.btn_scan.config(text=self.t("scan"))
        self.btn_clear_cache.config(text=self.t("clear_cache"))
        self.tooltip_clear_cache.set_text(self.t("clear_cache_tip"))
        self.btn_cancel.config(text=self.t("cancel"))
        self.btn_translate.config(text=self.t("translate"))
        self.lbl_progress.config(text=self.t("ready"))

    # ------------------------------------------------------------------
    # File pickers
    # ------------------------------------------------------------------
    def _pick_input(self):
        path = filedialog.askopenfilename(
            title="Select locale file",
            filetypes=[
                ("Locale files", "*.yml *.yaml *.json *.properties *.lang"),
                ("YAML files", "*.yml *.yaml"),
                ("JSON files", "*.json"),
                ("Properties files", "*.properties *.lang"),
                ("All files", "*.*"),
            ]
        )
        if path:
            self.input_path = Path(path)
            self.lbl_input.config(text=str(self.input_path))
            self._output_manually_set = False  # new file → resume auto-suggesting
            self._update_suggested_output()

    def _update_suggested_output(self):
        """Recompute the suggested output filename from the current input file
        and selected target language — unless the user manually picked a
        custom output path via 'Change...' (respected until a new input file
        is chosen)."""
        if not self.input_path or self._output_manually_set:
            return
        target_label = self.combo_target.get()
        target_code = LANGUAGES.get(target_label)
        if not target_code:
            return
        suggested = self.input_path.with_stem(
            self.input_path.stem + f"_{target_code.upper()}"
        )
        self.output_path = suggested
        self.lbl_output.config(text=str(suggested))

    def _pick_output(self):
        path = filedialog.asksaveasfilename(
            title="Save translated file as",
            defaultextension=self.input_path.suffix if self.input_path else ".yml",
        )
        if path:
            self.output_path = Path(path)
            self.lbl_output.config(text=str(self.output_path))
            self._output_manually_set = True  # stop auto-suggesting until next input file

    # ------------------------------------------------------------------
    # Logging helpers (thread-safe: workers push to queue, GUI drains it)
    # ------------------------------------------------------------------
    def _log(self, msg: str):
        self._log_queue.put(msg)

    def _drain_log_queue(self):
        try:
            while True:
                msg = self._log_queue.get_nowait()
                self.text_log.config(state="normal")
                self.text_log.insert("end", msg + "\n")
                self.text_log.see("end")
                self.text_log.config(state="disabled")
        except queue.Empty:
            pass
        self.root.after(100, self._drain_log_queue)

    def _set_progress(self, done: int, total: int):
        self.progress["maximum"] = total
        self.progress["value"] = done
        self.lbl_progress.config(text=self.t("progress", done=done, total=total))

    def _on_clear_cache(self):
        cache_path = CACHE_FILE_PATH
        if not cache_path.exists():
            self._log(self.t("no_cache_file"))
            return
        confirmed = messagebox.askyesno(
            self.t("cache_clear_confirm_title"),
            self.t("cache_clear_confirm_msg"),
        )
        if not confirmed:
            return
        try:
            cache_path.unlink()
            self._log(self.t("cache_cleared"))
        except Exception as e:
            self._log(f"ERROR: {e}")

    def _set_buttons_enabled(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        self.btn_translate.config(state=state)
        self.btn_scan.config(state=state)
        self.btn_clear_cache.config(state=state)
        self.btn_cancel.config(state="disabled" if enabled else "normal")

    def _on_cancel(self):
        self._cancel_event.set()
        self._log("Cancel requested — stopping after current in-flight requests finish...")
        self.btn_cancel.config(state="disabled")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _on_scan(self):
        if not self._validate_input():
            return
        self._run_in_background(self._task_scan)

    def _on_translate(self):
        if not self._validate_input():
            return
        self._run_in_background(self._task_translate)

    def _validate_input(self) -> bool:
        if not self.input_path or not self.input_path.exists():
            messagebox.showerror("Error", "Please select a valid input file first.")
            return False
        return True

    def _run_in_background(self, task):
        if self._worker_thread and self._worker_thread.is_alive():
            messagebox.showwarning("Busy", "A task is already running.")
            return
        self._cancel_event.clear()
        self._set_buttons_enabled(False)
        self._worker_thread = threading.Thread(target=self._wrap_task, args=(task,), daemon=True)
        self._worker_thread.start()

    def _wrap_task(self, task):
        try:
            task()
        except Exception as e:
            self._log(f"ERROR: {e}")
            messagebox.showerror("Error", str(e))
        finally:
            self.root.after(0, lambda: self._set_buttons_enabled(True))

    # ------------------------------------------------------------------
    # Background tasks (run on a worker thread — never touch widgets directly!)
    # ------------------------------------------------------------------
    def _load_with_warnings(self, handler, path: Path):
        """Load a file via the handler, forwarding any parser warnings
        (e.g. duplicate YAML keys) into the log instead of losing them."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            data = handler.load(path)
        for w in caught:
            self._log(f"WARNING: {w.message}")
        return data

    def _task_scan(self):
        self._log(f"Loading {self.input_path.name}...")
        handler = get_handler_for(self.input_path)
        data = self._load_with_warnings(handler, self.input_path)
        items = handler.collect_strings(data)
        self._log(f"Found {len(items)} translatable strings.")

        unknown_found: dict[str, int] = {}
        candidate_patterns = [
            re.compile(r'<[A-Za-z][A-Za-z0-9_:.\-]{1,40}>'),
            re.compile(r'\{[A-Z][A-Z0-9_:.\-]{1,40}\}'),
            re.compile(r'\[\[[^\]]+\]\]'),
            re.compile(r'\$\{?[A-Za-z_][A-Za-z0-9_.]*\}?'),
            re.compile(r'<<[^>]+>>'),
        ]
        for _, text in items:
            cleaned = PLACEHOLDER_RE.sub("", text)
            for pat in candidate_patterns:
                for m in pat.finditer(cleaned):
                    token = m.group()
                    if not PLACEHOLDER_RE.search(token):
                        unknown_found[token] = unknown_found.get(token, 0) + 1

        if not unknown_found:
            self._log("No unknown placeholder-like tokens detected. You're good to go.")
        else:
            self._log(f"Found {len(unknown_found)} unknown token pattern(s):")
            for token, count in sorted(unknown_found.items(), key=lambda x: -x[1]):
                self._log(f"  {token!r}  (x{count})")
            self._log("Add these to PROTECTED_EXTRA at the top of this file if they "
                       "should NOT be translated.")

    def _task_translate(self):
        source_label = self.combo_source.get()
        source_code = SOURCE_ONLY_LANGUAGES.get(source_label) or LANGUAGES.get(source_label)
        target_code = LANGUAGES[self.combo_target.get()]

        if not source_code:
            self._log(f"ERROR: unknown source language '{source_label}'")
            return

        self._log(f"Loading {self.input_path.name}...")
        handler = get_handler_for(self.input_path)
        data = self._load_with_warnings(handler, self.input_path)
        items = handler.collect_strings(data)
        total = len(items)
        self._log(f"Found {total} strings. Translating {source_code} -> {target_code}...")

        cache_path = CACHE_FILE_PATH
        translator = Translator(source_code, target_code, cache_path)

        strings = [text for _, text in items]

        def progress_cb(done, tot):
            self.root.after(0, self._set_progress, done, tot)

        translated = translator.translate_many(
            strings, progress_cb=progress_cb, cancel_event=self._cancel_event
        )

        if self._cancel_event.is_set():
            self._log("Translation cancelled. Saving partial progress to cache (no output file written).")
            translator.save_cache()
            self.root.after(0, lambda: messagebox.showinfo(
                "Cancelled", "Translation was cancelled.\nNo output file was written, "
                              "but completed translations were saved to the cache for next time."))
            return

        for (path_entry, _), new_text in zip(items, translated):
            handler.write_back(path_entry, new_text)

        out_path = self.output_path or self.input_path.with_stem(
            self.input_path.stem + f"_{target_code.upper()}"
        )
        self._log(f"Writing {out_path.name}...")
        handler.save(data, out_path)
        translator.save_cache()

        self._log(f"Done! Saved to: {out_path}")
        self.root.after(0, lambda: messagebox.showinfo("Done", f"Translation complete!\nSaved to:\n{out_path}"))


# ============================================================================
# 3. ENTRY POINT
# ============================================================================
def resource_path(relative: str) -> Path:
    """Locate a bundled resource (like the window icon) whether running as a
    plain .py script or a PyInstaller --onefile exe (which extracts bundled
    data files into a temp folder at sys._MEIPASS)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative


def main():
    ok = _ensure_ruamel_yaml()
    if not ok and getattr(sys, "frozen", False):
        # Can't self-heal inside a compiled exe — tell the user plainly
        # instead of letting it fail later with a cryptic import error.
        root_warn = tk.Tk()
        root_warn.withdraw()
        messagebox.showwarning(
            "YAML support missing",
            "This build was compiled without ruamel.yaml, so .yml/.yaml files "
            "won't load (JSON and .properties will still work).\n\n"
            "Rebuild the .exe with:\n"
            "  pip install ruamel.yaml\n"
            "  pyinstaller --onefile --noconsole --collect-all ruamel.yaml translator_gui.py"
        )
        root_warn.destroy()

    root = tk.Tk()
    try:
        icon_path = resource_path("PluginTranslator_icon.ico")
        if icon_path.exists():
            root.iconbitmap(default=str(icon_path))
    except tk.TclError:
        pass  # icon file missing or platform doesn't support .ico — not fatal

    style = ttk.Style()
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    app = TranslatorGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
