#!/usr/bin/env python3
"""earImmersion（磨耳朵）· 划词发音（桌面版）

在任意软件里选中英文 → 鼠标旁出现小喇叭 → 点击听标准发音并显示中文释义。
单文件、无第三方依赖，支持 Windows / macOS / Linux(X11)。用法见 README.md。
"""
import collections
import ctypes
import hashlib
import json
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

VERSION = '1.0.1'
ACCENT = 2          # 2 = 美音，1 = 英音
MAX_LEN = 300       # 选中内容超过这个长度就不出现喇叭
ANY_CURSOR = False  # 仅 Windows：鼠标是普通箭头时（拖窗口、点图标）也出现喇叭
PORT = 47631        # 本机端口：防止重复启动；再次运行时靠它找到已经在运行的那个

WIN, MAC = sys.platform == 'win32', sys.platform == 'darwin'
# 数据目录：日志 log.txt、可选的设置 config.json、发音缓存 voice/
DATA = (os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'), 'earImmersion') if WIN
        else os.path.expanduser('~/Library/Caches/earImmersion') if MAC
        else os.path.join(os.environ.get('XDG_CACHE_HOME') or os.path.expanduser('~/.cache'), 'earImmersion'))
VOICE, LOG = os.path.join(DATA, 'voice'), os.path.join(DATA, 'log.txt')

YD = 'https://dict.youdao.com/'
DICTS = '{"count":99,"dicts":[["ec","fanyi","web_trans","aiDefinition"]]}'
BLUE, INK, EDGE = '#1a73e8', '#1f2328', '#c9d1d9'
_log_lock = threading.Lock()


def log(msg):
    """往日志里写一行。出了问题靠它排查；只记动作和长度，不记选中的文字。"""
    try:
        with _log_lock, open(LOG, 'a', encoding='utf-8') as f:
            f.write('%s %s\n' % (time.strftime('%H:%M:%S'), msg))
    except Exception:
        pass


def load_config():
    """读取 config.json（可选），例如 {"accent": 1, "max_len": 300, "any_cursor": true}。"""
    global ACCENT, MAX_LEN, ANY_CURSOR
    try:
        with open(os.path.join(DATA, 'config.json'), encoding='utf-8') as f:
            c = json.load(f)
        ACCENT = 1 if c.get('accent', ACCENT) == 1 else 2
        MAX_LEN = int(c.get('max_len', MAX_LEN))
        ANY_CURSOR = bool(c.get('any_cursor', ANY_CURSOR))
        log('config accent=%d max_len=%d any_cursor=%s' % (ACCENT, MAX_LEN, ANY_CURSOR))
    except FileNotFoundError:
        pass
    except Exception as e:
        log('config ignored: %r' % e)


# ───────────────────────── 取词清洗 / 网络 ─────────────────────────

def clean(s):
    """整理选中的文本；不是以英文为主的内容返回空串。"""
    s = re.sub(r'(?<=[a-z])-\s*\n\s*(?=[a-z])', '', s or '')  # 合并 PDF 断行连字符
    s = ' '.join(s.split())
    if not s or len(s) > MAX_LEN:
        return ''
    letters = len(re.findall('[A-Za-z]', s))
    return s if letters and letters * 2 >= len(s.replace(' ', '')) else ''


def wrap(s, cols=52):
    """按显示宽度折行（汉字算 2 列）。自己折行，是因为两套界面库的自动换行对中文都不理想。"""
    out = []
    for line in s.split('\n'):
        cur, n = '', 0
        for ch in line:
            w = 2 if ord(ch) > 0x2e7f else 1
            if n + w > cols:
                out.append(cur)
                cur, n = '', 0
            cur += ch
            n += w
        out.append(cur)
    return '\n'.join(out)


def http(url, data=None):
    req = Request(url, urlencode(data).encode() if data else None, {'User-Agent': 'Mozilla/5.0'})
    with urlopen(req, timeout=6) as r:
        return r.read()


def _norm(s):
    return re.sub('[^a-z0-9]+', '', s.lower())


def lookup_one(q):
    d = json.loads(http(YD + 'jsonapi_s?doctype=json&jsonversion=4',
                        {'q': q, 'le': 'en', 'client': 'web', 'keyfrom': 'webdict', 'dicts': DICTS}))
    if _norm(d.get('input') or '') != _norm(q):  # 查不到时接口会返回一个无关词条
        return None
    ec = d.get('ec') or {}
    w = ec.get('word') or {}
    if isinstance(w, list):
        w = w[0] if w else {}
    lines = [((t.get('pos') or '') + ' ' + t['tran']).strip() for t in w.get('trs') or [] if t.get('tran')]
    if not lines and ec.get('web_trans'):
        lines = ['；'.join(ec['web_trans'][:3])]
    if not lines and (d.get('fanyi') or {}).get('tran'):
        lines = [d['fanyi']['tran']]
    if not lines:
        words = (d.get('aiDefinition') or {}).get('words') or [{}]
        s = re.split('[。\n]', re.sub('<[^>]+>', '', words[0].get('content') or ''))[0]
        lines = [s] if s else []
    return {'phone': w.get('usphone') or w.get('ukphone') or '', 'lines': lines[:3]} if lines else None


def lookup(q):
    """返回 {'phone': 音标, 'lines': [释义…]}，查不到返回 None。"""
    try:
        r = lookup_one(q)
    except Exception:
        r = None
    if r:
        return r
    # 整体查不到：几个不相关的词一起选中时，逐词给释义
    ws = list(dict.fromkeys(w for w in (re.sub('^[^A-Za-z]+|[^A-Za-z]+$', '', x) for x in q.split()) if w))
    if not 2 <= len(ws) <= 6:
        return None
    rs = [None] * len(ws)

    def one(i):
        try:
            rs[i] = lookup_one(ws[i])
        except Exception:
            pass
    ts = [threading.Thread(target=one, args=(i,)) for i in range(len(ws))]
    [t.start() for t in ts]
    [t.join() for t in ts]
    lines = ['%s：%s' % (w, r['lines'][0]) for w, r in zip(ws, rs) if r]
    return {'phone': '', 'lines': lines} if lines else None


def voice_path(q):
    return os.path.join(VOICE, hashlib.md5(('%d:%s' % (ACCENT, q)).encode()).hexdigest() + '.mp3')


def voice(q):
    """下载发音到缓存并返回文件路径；失败返回 None。先有道真人发音（区分大小写），再百度合成。"""
    path = voice_path(q)
    if os.path.exists(path):
        return path
    urls = ['%sdictvoice?audio=%s&type=%d' % (YD, quote(t), ACCENT) for t in dict.fromkeys([q.lower(), q])]
    urls.append('https://fanyi.baidu.com/gettts?lan=%s&spd=3&source=web&text=%s'
                % ('uk' if ACCENT == 1 else 'en', quote(q)))
    for u in urls:
        try:
            b = http(u)
        except Exception:
            continue
        if len(b) > 500 and b[:1] != b'{':
            with open(path + '.tmp', 'wb') as f:
                f.write(b)
            os.replace(path + '.tmp', path)
            return path
    return None


# ───────────────────────── 各平台：弹窗 / 鼠标 / 取选中文本 / 播放 ─────────────────────────
# 每个平台类提供同一组东西：
#   lazy     True = 没法直接知道选中了什么，点喇叭时才取词（Windows / macOS）
#   sourced  True = events() 能报告每一次鼠标按下 / 松开，不会漏掉很快的轻点
#   manual   True = 界面库收不到对弹窗的点击，由主逻辑根据全局的鼠标状态判断"点了喇叭"（macOS）
#   after loop quit close | show hide say paint box | poll events grab | play stop

class Linux:
    """GTK3（桌面版 Linux 自带的 python3-gi）。X11 有"主选区"：一选中文字就能知道选了什么。"""
    lazy = sourced = manual = False
    tip = '退出：再运行一次本程序'

    def __init__(self, app):
        os.environ.setdefault('GDK_BACKEND', 'x11')
        import gi
        gi.require_version('Gtk', '3.0')
        gi.require_version('Gdk', '3.0')
        from gi.repository import Gdk, GLib, Gtk
        self.app, self.Gdk, self.GLib, self.Gtk = app, Gdk, GLib, Gtk
        self.disp = Gdk.Display.get_default()
        if not self.disp:
            raise SystemExit('earImmersion：连接不到 X11 显示（纯 Wayland 会话暂不支持）')
        self.pointer = self.disp.get_default_seat().get_pointer()
        self.rootwin = Gdk.get_default_root_window()
        self.clip = Gtk.Clipboard.get(Gdk.SELECTION_PRIMARY)
        self.changed = False
        self.clip.connect('owner-change', lambda *a: setattr(self, 'changed', True))

        css = Gtk.CssProvider()
        css.load_from_data(('''
            .moerduo { background: #fff; border: 1px solid %s; }
            .moerduo image { color: %s; padding: 7px; }
            .moerduo.on image { color: #fff; background: %s; }
            .moerduo label { color: %s; padding: 5px 9px 5px 7px; font-size: 10pt; }
        ''' % (EDGE, BLUE, BLUE, INK)).encode())
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css, 800)
        win = self.win = Gtk.Window(type=Gtk.WindowType.POPUP)
        win.get_style_context().add_class('moerduo')
        win.set_keep_above(True)
        box = Gtk.Box()
        ico = Gtk.Image.new_from_icon_name('audio-volume-high-symbolic', Gtk.IconSize.MENU)
        ico.set_valign(Gtk.Align.START)
        self.info = Gtk.Label(xalign=0)
        self.info.set_no_show_all(True)
        box.add(ico)
        box.add(self.info)
        win.add(box)
        win.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
                       | Gdk.EventMask.ENTER_NOTIFY_MASK)
        win.connect('button-release-event', lambda *a: app.click())
        win.connect('enter-notify-event', lambda *a: app.prefetch())
        self.at, self.pin = (0, 0), False
        self.bin = self.proc = self.player = None

    def after(self, ms, fn):
        self.GLib.timeout_add(ms, lambda: fn() and False)

    def loop(self):
        import signal
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        self.Gtk.main()

    def quit(self):
        self.Gtk.main_quit()

    def close(self):
        pass

    def _place(self):
        self.win.resize(1, 1)  # 弹出式窗口不会自己缩小，先压到最小再由内容撑开
        nat = self.win.get_preferred_size()[1]
        w, h = nat.width, nat.height
        if self.pin:           # 启动提示：放在主屏右上角
            g = (self.disp.get_primary_monitor() or self.disp.get_monitor(0)).get_workarea()
            x, y = g.x + g.width - w - 16, g.y + 16
        else:
            x, y = self.at
            g = self.disp.get_monitor_at_point(x, y).get_geometry()
            x = max(g.x, min(x, g.x + g.width - w - 8))
            if y + h > g.y + g.height - 8:
                y -= h + 28
        self.win.move(x, y)

    def show(self, x, y, pin=False):
        self.at, self.pin = (x + 12, y + 16), pin
        self.info.hide()
        self.paint(False)
        self._place()
        self.win.show_all()

    def hide(self):
        self.win.hide()

    def say(self, msg):
        self.info.set_text(msg)
        self.info.show()
        self._place()

    def paint(self, on):
        ctx = self.win.get_style_context()
        ctx.add_class('on') if on else ctx.remove_class('on')

    def box(self):
        return self.win.get_position() + self.win.get_size()

    def poll(self):
        _, x, y, mask = self.rootwin.get_device_position(self.pointer)
        down = bool(mask & self.Gdk.ModifierType.BUTTON1_MASK)
        if self.changed and not down:   # 主选区换了内容、鼠标也松开了：把选中的文字读出来
            self.changed = False
            self.clip.request_text(lambda clip, text: self.app.selected(text or '', x, y))
        return x, y, down, None

    def events(self):
        return ()

    def play(self, path):
        if self.bin is None and self.player is None:  # 首次播放时才加载：优先进程内 GStreamer，没有再找外部播放器
            try:
                import gi
                gi.require_version('Gst', '1.0')
                from gi.repository import Gst
                Gst.init(None)
                self.Gst, self.bin = Gst, Gst.ElementFactory.make('playbin')
            except Exception:
                self.bin = None
            if self.bin is None:
                self.player = next((p for p in (['ffplay', '-nodisp', '-autoexit', '-loglevel', 'quiet'],
                                                ['mpv', '--no-video', '--really-quiet'],
                                                ['mpg123', '-q']) if shutil.which(p[0])), [])
        self.stop()
        if self.bin is not None:
            self.bin.set_property('uri', self.GLib.filename_to_uri(path, None))
            self.bin.set_state(self.Gst.State.PLAYING)
        elif self.player:
            self.proc = subprocess.Popen(self.player + [path], stdin=subprocess.DEVNULL,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            return '没有可用的播放器，请安装 gstreamer1.0-plugins-good 或 ffmpeg'

    def stop(self):
        if self.bin is not None:
            self.bin.set_state(self.Gst.State.NULL)
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        self.proc = None


class TkUI:
    """Windows / macOS 共用的弹窗（Tk 随官方 Python 安装包自带）。"""
    lazy = True     # 这两个系统没有"主选区"：先出喇叭，点击时才模拟一次"复制"取词（不点就不会动剪贴板）
    sourced = True
    manual = False
    font = ('TkDefaultFont', 10)

    def __init__(self, app):
        import tkinter as tk
        self.app, self.tk = app, tk
        root = self.root = tk.Tk()
        root.withdraw()
        root.report_callback_exception = lambda *e: log('tk error: ' + ''.join(traceback.format_exception(*e)))
        k = max(1.0, root.winfo_fpixels('1i') / 96)     # 高分屏上把图标按比例放大
        pop = self.pop = tk.Toplevel(root, bg=EDGE, padx=1, pady=1)
        pop.withdraw()
        self.frameless(pop)
        pop.attributes('-topmost', True)
        box = tk.Frame(pop, bg='#ffffff')
        box.pack()
        ico = self.ico = tk.Canvas(box, width=30 * k, height=30 * k, bg='#ffffff', highlightthickness=0, cursor='hand2')
        ico.grid(row=0, column=0, sticky='n')
        ico.create_polygon([v * k for v in (7, 12, 11, 12, 16, 7, 16, 23, 11, 18, 7, 18)], fill=BLUE)
        for a, b in ((13, 10), (11, 6)):
            ico.create_arc(a * k, b * k, (34 - a) * k, (30 - b) * k, start=-55, extent=110,
                           style='arc', outline=BLUE, width=2 * k)
        self.info = tk.Label(box, bg='#ffffff', fg=INK, font=self.font, justify='left', anchor='w', padx=6, pady=5)
        if not self.manual:
            for widget in (ico, self.info):
                widget.bind('<ButtonRelease-1>', lambda e: app.click())
        pop.bind('<Enter>', lambda e: app.prefetch())
        self.at, self.pin = (0, 0), False

    def frameless(self, win):
        win.overrideredirect(True)

    def after(self, ms, fn):
        self.root.after(ms, fn)

    def loop(self):
        self.root.mainloop()

    def quit(self):
        self.root.quit()

    def close(self):
        pass

    def corner(self, w, h):
        """启动提示放在屏幕的哪个角。"""
        return self.pop.winfo_screenwidth() - w - 16, 36

    def _place(self):
        p = self.pop
        p.update_idletasks()
        w, h = p.winfo_reqwidth(), p.winfo_reqheight()
        if self.pin:
            x, y = self.corner(w, h)
        else:
            x, y = self.at
            x = max(p.winfo_vrootx(), min(x, p.winfo_vrootx() + p.winfo_vrootwidth() - w - 8))
            if y + h > p.winfo_vrooty() + p.winfo_vrootheight() - 8:
                y -= h + 28
        p.geometry('+%d+%d' % (x, y))

    def show(self, x, y, pin=False):
        self.at, self.pin = (x + 12, y + 16), pin
        self.info.grid_forget()
        self.paint(False)
        self._place()
        self.reveal()

    def reveal(self):
        self.pop.deiconify()
        self.pop.lift()
        self.pop.update_idletasks()

    def hide(self):
        self.pop.withdraw()

    def say(self, msg):
        self.info.configure(text=msg)
        self.info.grid(row=0, column=1, sticky='w')
        self._place()

    def paint(self, on):
        bg, fg = (BLUE, '#ffffff') if on else ('#ffffff', BLUE)
        self.ico.configure(bg=bg)
        for item in self.ico.find_all():
            self.ico.itemconfigure(item, **{'fill' if self.ico.type(item) == 'polygon' else 'outline': fg})

    def box(self):
        p = self.pop
        return p.winfo_rootx(), p.winfo_rooty(), p.winfo_width(), p.winfo_height()

    def nothing(self):
        """点了喇叭却没复制到任何文字时的提示。"""
        return '没有取到文字：请先选中英文，再点喇叭'


class Windows(TkUI):
    font = ('Microsoft YaHei UI', 10)
    tip = '退出：右键任务栏右下角的喇叭图标（可能收在 ^ 里）'

    def __init__(self, app):
        from ctypes import wintypes as w
        P, L, I = ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_int
        u = self.u = ctypes.WinDLL('user32')
        k = self.k = ctypes.WinDLL('kernel32')
        self.w, self.shell, self.mm = w, ctypes.WinDLL('shell32'), ctypes.WinDLL('winmm')
        self.mci = self.mm.mciSendStringW
        self.mci.restype, self.mci.argtypes = w.DWORD, [w.LPCWSTR, P, w.UINT, P]
        self.mm.mciGetErrorStringW.argtypes = [w.DWORD, P, w.UINT]
        for name, res, args in (('GlobalAlloc', P, [w.UINT, ctypes.c_size_t]), ('GlobalLock', P, [P]),
                                ('GlobalUnlock', w.BOOL, [P]), ('GlobalFree', P, [P]),
                                ('GetModuleHandleW', P, [w.LPCWSTR])):
            f = getattr(k, name)
            f.restype, f.argtypes = res, args
        self.shell.Shell_NotifyIconW.argtypes = [w.DWORD, P]
        self.shell.ExtractIconExW.argtypes = [w.LPCWSTR, I, P, P, w.UINT]
        # 64 位系统上句柄和指针是 8 字节：不声明参数类型的话会被截成 4 字节
        for name, res, args in (
                ('GetForegroundWindow', P, []), ('SetForegroundWindow', w.BOOL, [P]),
                ('GetWindowThreadProcessId', w.DWORD, [P, P]),
                ('LoadCursorW', P, [P, P]), ('LoadIconW', P, [P, P]),
                ('GetCursorInfo', w.BOOL, [P]), ('GetCursorPos', w.BOOL, [P]),
                ('GetAsyncKeyState', ctypes.c_short, [I]), ('GetSystemMetrics', I, [I]),
                ('keybd_event', None, [ctypes.c_ubyte, ctypes.c_ubyte, w.DWORD, P]),
                ('GetClipboardSequenceNumber', w.DWORD, []),
                ('OpenClipboard', w.BOOL, [P]), ('CloseClipboard', w.BOOL, []), ('EmptyClipboard', w.BOOL, []),
                ('GetClipboardData', P, [w.UINT]), ('SetClipboardData', P, [w.UINT, P]),
                ('SystemParametersInfoW', w.BOOL, [w.UINT, w.UINT, P, w.UINT]),
                ('DefWindowProcW', L, [P, w.UINT, w.WPARAM, w.LPARAM]),
                ('RegisterClassW', w.ATOM, [P]),
                ('CreateWindowExW', P, [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, I, I, I, I, P, P, P, P]),
                ('RegisterRawInputDevices', w.BOOL, [P, w.UINT, w.UINT]),
                ('GetRawInputData', w.UINT, [P, w.UINT, P, P, w.UINT]),
                ('GetMessageW', I, [P, P, w.UINT, w.UINT]), ('TranslateMessage', w.BOOL, [P]),
                ('DispatchMessageW', L, [P]), ('PostMessageW', w.BOOL, [P, w.UINT, w.WPARAM, w.LPARAM]),
                ('RegisterWindowMessageW', w.UINT, [w.LPCWSTR]),
                ('CreatePopupMenu', P, []), ('AppendMenuW', w.BOOL, [P, w.UINT, P, w.LPCWSTR]),
                ('TrackPopupMenu', I, [P, w.UINT, I, I, I, P, P]), ('DestroyMenu', w.BOOL, [P])):
            f = getattr(u, name)
            f.restype, f.argtypes = res, args
        self.getlong = getattr(u, 'GetWindowLongPtrW', None) or u.GetWindowLongW   # 32 位系统上只有不带 Ptr 的
        self.setlong = getattr(u, 'SetWindowLongPtrW', None) or u.SetWindowLongW
        self.getlong.restype, self.getlong.argtypes = L, [P, I]
        self.setlong.restype, self.setlong.argtypes = L, [P, I, L]

        class CURSORINFO(ctypes.Structure):
            _fields_ = [('cbSize', w.DWORD), ('flags', w.DWORD), ('hCursor', P), ('pt', w.POINT)]

        class RAWINPUT(ctypes.Structure):    # RAWINPUTHEADER + RAWMOUSE（只用到鼠标）
            _fields_ = [('dwType', w.DWORD), ('dwSize', w.DWORD), ('hDevice', P), ('wParam', w.WPARAM),
                        ('usFlags', w.USHORT), ('_pad', w.USHORT), ('usButtonFlags', w.USHORT),
                        ('usButtonData', w.USHORT), ('ulRawButtons', w.ULONG), ('lLastX', w.LONG),
                        ('lLastY', w.LONG), ('ulExtraInformation', w.ULONG)]

        class RAWINPUTDEVICE(ctypes.Structure):
            _fields_ = [('usUsagePage', w.USHORT), ('usUsage', w.USHORT), ('dwFlags', w.DWORD), ('hwndTarget', P)]

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [('style', w.UINT), ('lpfnWndProc', P), ('cbClsExtra', I), ('cbWndExtra', I),
                        ('hInstance', P), ('hIcon', P), ('hCursor', P), ('hbrBackground', P),
                        ('lpszMenuName', w.LPCWSTR), ('lpszClassName', w.LPCWSTR)]

        class NOTIFYICONDATAW(ctypes.Structure):
            _fields_ = [('cbSize', w.DWORD), ('hWnd', P), ('uID', w.UINT), ('uFlags', w.UINT),
                        ('uCallbackMessage', w.UINT), ('hIcon', P), ('szTip', w.WCHAR * 128),
                        ('dwState', w.DWORD), ('dwStateMask', w.DWORD), ('szInfo', w.WCHAR * 256),
                        ('uVersion', w.UINT), ('szInfoTitle', w.WCHAR * 64), ('dwInfoFlags', w.DWORD),
                        ('guidItem', ctypes.c_byte * 16), ('hBalloonIcon', P)]

        self.CURSORINFO, self.RAWINPUT, self.RAWINPUTDEVICE = CURSORINFO, RAWINPUT, RAWINPUTDEVICE
        self.WNDCLASSW, self.NOTIFYICONDATAW = WNDCLASSW, NOTIFYICONDATAW
        self.WNDPROC = ctypes.WINFUNCTYPE(L, P, w.UINT, w.WPARAM, w.LPARAM)

        # 系统自带的"不是在选字"的光标：箭头、等待、上箭头、各种调整大小、禁止、手形、后台忙、帮助
        self.plain = {u.LoadCursorW(None, i) for i in
                      (32512, 32514, 32516, 32642, 32643, 32644, 32645, 32646, 32648, 32649, 32650, 32651)}
        swapped = u.GetSystemMetrics(23)                    # 左右键互换（左手鼠标）
        self.vk, self.bits = (2, (4, 8)) if swapped else (1, (1, 2))
        self.evs = collections.deque()                      # 后台线程收到的按键事件，等主线程来取
        self.ri, self.hwnd, self.nid, self.src, self.WM_TASKBAR = RAWINPUT(), None, None, None, 0

        try:
            ctypes.WinDLL('shcore').SetProcessDpiAwareness(1)   # 高分屏下坐标才对得上
        except Exception:
            pass
        front = u.GetForegroundWindow()
        super().__init__(app)
        self.root.update()
        if front and self._mine(u.GetForegroundWindow()):   # Tk 初始化时会把（看不见的）主窗口设成活动窗口：把焦点还回去
            u.SetForegroundWindow(front)
        self.ready = threading.Event()
        threading.Thread(target=self._pump, daemon=True).start()
        self.ready.wait(5)

    # —— 后台线程：一个隐藏窗口，接收全局的鼠标按键（Raw Input）和托盘图标的点击 ——
    def _pump(self):
        try:
            self._window()
        except Exception:
            log('window thread error: ' + traceback.format_exc())
        finally:
            self.ready.set()
        msg = self.w.MSG()
        while self.u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            self.u.TranslateMessage(ctypes.byref(msg))
            self.u.DispatchMessageW(ctypes.byref(msg))

    def _window(self):
        u, P = self.u, ctypes.c_void_p
        self._proc = self.WNDPROC(self._wndproc)            # 必须留着这个引用，否则回调会被回收
        self.WM_TASKBAR = u.RegisterWindowMessageW('TaskbarCreated')
        wc = self.WNDCLASSW()
        wc.lpfnWndProc, wc.lpszClassName = ctypes.cast(self._proc, P), 'earImmersionHidden'
        wc.hInstance = self.k.GetModuleHandleW(None)
        u.RegisterClassW(ctypes.byref(wc))
        self.hwnd = u.CreateWindowExW(0, wc.lpszClassName, 'earImmersion', 0, 0, 0, 0, 0, None, None, wc.hInstance, None)
        rid = self.RAWINPUTDEVICE(1, 2, 0x100, self.hwnd)   # 通用桌面 / 鼠标；RIDEV_INPUTSINK：不在前台也收得到
        raw = bool(u.RegisterRawInputDevices(ctypes.byref(rid), 1, ctypes.sizeof(rid)))
        icon = P()
        self.shell.ExtractIconExW(sys.executable, 0, None, ctypes.byref(icon), 1)   # 用 exe 自己的图标
        nid = self.nid = self.NOTIFYICONDATAW()
        nid.cbSize, nid.hWnd, nid.uID, nid.uFlags, nid.uCallbackMessage = ctypes.sizeof(nid), self.hwnd, 1, 1 | 2 | 4, 0x8001
        nid.hIcon, nid.szTip = icon.value or u.LoadIconW(None, 32512), 'earImmersion 划词发音'
        log('window %s raw %s tray %s' % (bool(self.hwnd), raw, self._tray()))

    def _tray(self, add=True):
        return bool(self.nid and self.shell.Shell_NotifyIconW(0 if add else 2, ctypes.byref(self.nid)))

    def _wndproc(self, hwnd, msg, wp, lp):
        try:
            if msg == 0x00FF:                                   # WM_INPUT：某个鼠标有动静
                ri, size = self.ri, self.w.UINT(ctypes.sizeof(self.ri))
                n = self.u.GetRawInputData(lp, 0x10000003, ctypes.byref(ri), ctypes.byref(size),
                                           self.RAWINPUT.usFlags.offset)
                if n and n != 0xFFFFFFFF and ri.dwType == 0:    # 0 = 鼠标
                    for bit, down in zip(self.bits, (True, False)):
                        if ri.usButtonFlags & bit:
                            self.evs.append((down,) + self._where())
            elif msg == 0x8001 and lp in (0x0202, 0x0205):      # 在托盘图标上松开了左键 / 右键
                self._menu()
            elif msg == self.WM_TASKBAR and msg:                # 资源管理器重启了：重新放一次托盘图标
                self._tray()
        except Exception:
            log('wndproc error: ' + traceback.format_exc())
        return self.u.DefWindowProcW(hwnd, msg, wp, lp)

    def _menu(self):
        u, pt = self.u, self.w.POINT()
        menu = u.CreatePopupMenu()
        u.AppendMenuW(menu, 0, 1, '使用说明')
        u.AppendMenuW(menu, 0, 3, '打开日志所在的文件夹')
        u.AppendMenuW(menu, 0, 2, '退出 earImmersion')
        u.GetCursorPos(ctypes.byref(pt))
        u.SetForegroundWindow(self.hwnd)                    # 不这样做的话，点菜单外面菜单不会消失
        cmd = u.TrackPopupMenu(menu, 0x0100, pt.x, pt.y, 0, self.hwnd, None)   # TPM_RETURNCMD
        u.PostMessageW(self.hwnd, 0, 0, 0)
        u.DestroyMenu(menu)
        if cmd == 3:
            os.startfile(DATA)
        elif cmd:
            self.app.todo.append('hello' if cmd == 1 else 'quit')

    # —— 鼠标 ——
    def _where(self):
        pt = self.w.POINT()
        self.u.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y, self._texty()

    def _texty(self):
        """光标像不像在选字：系统的箭头 / 手形 / 调整大小等，或光标被隐藏 → False；工字形和软件自绘的光标 → True。"""
        if ANY_CURSOR:
            return None
        ci = self.CURSORINFO()
        ci.cbSize = ctypes.sizeof(ci)
        if not self.u.GetCursorInfo(ctypes.byref(ci)):
            return None
        return ci.flags == 1 and ci.hCursor not in self.plain

    def poll(self):
        x, y, texty = self._where()
        return x, y, self.u.GetAsyncKeyState(self.vk) < 0, texty

    def events(self):
        return [self.evs.popleft() for _ in range(len(self.evs))]

    def _mine(self, hwnd):
        pid = self.w.DWORD()
        self.u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value == os.getpid()

    # —— 弹窗 ——
    def corner(self, w, h):
        rc = self.w.RECT()
        if not self.u.SystemParametersInfoW(0x30, 0, ctypes.byref(rc), 0) or not rc.right:   # 屏幕去掉任务栏后的区域
            return self.pop.winfo_screenwidth() - w - 16, self.pop.winfo_screenheight() - h - 64
        return rc.right - w - 16, rc.bottom - h - 16

    def show(self, x, y, pin=False):
        u = self.u
        front = u.GetForegroundWindow()
        if front and not self._mine(front):
            self.src = front                                # 用户正在用的窗口：稍后的 Ctrl+C 要发给它
        super().show(x, y, pin)
        hwnd = int(self.pop.wm_frame(), 16)                 # Tk 到第一次显示时才创建外框窗口，所以只能在这里设
        ex = self.getlong(hwnd, -20)
        if not ex & 0x08000000:                             # WS_EX_NOACTIVATE：点喇叭时不抢焦点，Ctrl+C 才能发给原软件
            self.setlong(hwnd, -20, ex | 0x08000000)
            log('popup %#x exstyle %#x +noactivate' % (hwnd, ex))
        if self.src and self._mine(u.GetForegroundWindow()):
            log('popup took focus on show, giving it back')
            u.SetForegroundWindow(self.src)

    # —— 取选中的文字 ——
    def _clip(self, put=None):
        """读出（put=None）或写入剪贴板里的文本。

        不用 Tk 自带的剪贴板功能：它写入时只是"登记"，等别人来取才给内容；
        于是下一个程序复制时会来通知我们，而我们正等着它复制完——互相等到超时，第二次取词必定失败。
        """
        u, k = self.u, self.k
        for _ in range(25):                 # 剪贴板可能正被别的程序占着，稍等再试
            if u.OpenClipboard(None):
                break
            time.sleep(0.02)
        else:
            return log('clipboard busy')
        try:
            if put is None:
                h = u.GetClipboardData(13)  # 13 = CF_UNICODETEXT
                p = k.GlobalLock(h) if h else None
                if not p:
                    return None
                try:
                    return ctypes.wstring_at(p)
                finally:
                    k.GlobalUnlock(h)
            raw = (put + '\0').encode('utf-16-le')
            h = k.GlobalAlloc(2, len(raw))  # 2 = GMEM_MOVEABLE
            if h:
                ctypes.memmove(k.GlobalLock(h), raw, len(raw))
                k.GlobalUnlock(h)
                u.EmptyClipboard()
                if not u.SetClipboardData(13, h):
                    k.GlobalFree(h)
        finally:
            u.CloseClipboard()

    def grab(self):
        u = self.u
        if self.src and self._mine(u.GetForegroundWindow()):   # 万一点喇叭时把焦点抢过来了，先还给原来的软件
            log('popup took focus on click, giving it back')
            u.SetForegroundWindow(self.src)
            time.sleep(0.08)
        old = self._clip()
        seq = u.GetClipboardSequenceNumber()
        for key, flag in ((0x11, 0), (0x43, 0), (0x43, 2), (0x11, 2)):   # 按下 Ctrl、C，再依次松开
            u.keybd_event(key, 0, flag, None)
        end = time.time() + 0.8
        while u.GetClipboardSequenceNumber() == seq and time.time() < end:
            time.sleep(0.02)
        if u.GetClipboardSequenceNumber() == seq:
            return ''
        time.sleep(0.05)        # 等原软件把剪贴板写完
        text = self._clip() or ''
        if old is not None:     # 把剪贴板里原来的文本放回去
            self._clip(old)
        return text

    # —— 播放 ——
    def play(self, path):
        self.stop()
        for cmd in ('open "%s" type mpegvideo alias earimm' % path, 'set earimm time format milliseconds', 'play earimm'):
            err = self.mci(cmd, None, 0, None)
            if err and not cmd.startswith('set'):
                buf = ctypes.create_unicode_buffer(256)
                self.mm.mciGetErrorStringW(err, buf, 256)
                return '%s %d %s' % (cmd.split()[0], err, buf.value)
        self.root.after(400, self._probe)

    def _probe(self):
        """开始播放后看一眼进度并记进日志：排查"点了没声音"时用得上。"""
        buf = ctypes.create_unicode_buffer(64)
        got = [buf.value if not self.mci('status earimm ' + what, buf, 64, None) else '?'
               for what in ('mode', 'position', 'length')]
        log('playing: mode=%s position=%s/%s' % tuple(got))

    def stop(self):
        self.mci('close earimm', None, 0, None)

    def close(self):
        self._tray(False)


class Mac(TkUI):
    # 本程序不在前台时，Tk 会把鼠标点击丢掉；而弹窗又必须不抢前台（否则 ⌘C 就发给了自己）
    manual = True
    font = ('PingFang SC', 13)
    tip = '退出：在程序坞（Dock）的 earImmersion 图标上右键 → 退出'

    def __init__(self, app):
        P = ctypes.c_void_p
        cg = self.cg = ctypes.CDLL('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
        for name, res, args in (
                ('CGEventSourceButtonState', ctypes.c_bool, [ctypes.c_int32, ctypes.c_uint32]),
                ('CGEventSourceCounterForEventType', ctypes.c_uint32, [ctypes.c_int32, ctypes.c_uint32]),
                ('CGEventCreateKeyboardEvent', P, [P, ctypes.c_uint16, ctypes.c_bool]),
                ('CGEventSetFlags', None, [P, ctypes.c_uint64]), ('CGEventPost', None, [ctypes.c_uint32, P]),
                ('CGEventSourceFlagsState', ctypes.c_uint64, [ctypes.c_int32]),
                ('CFRelease', None, [P]), ('AXIsProcessTrusted', ctypes.c_bool, []),
                ('AXIsProcessTrustedWithOptions', ctypes.c_bool, [P])):
            f = getattr(cg, name)
            f.restype, f.argtypes = res, args
        self.proc, self.mapped = None, False
        self.env = dict(os.environ, LANG='en_US.UTF-8')
        self.count = self._count()
        super().__init__(app)
        # 点程序坞里的图标 = "再打开一次"：把启动提示再显示一遍；程序坞菜单里的"退出"走正常的退出流程
        self.root.createcommand('::tk::mac::ReopenApplication', lambda: app.todo.append('hello'))
        self.root.createcommand('::tk::mac::Quit', lambda: app.todo.append('quit'))
        ok = self.trusted(ask=True)
        log('accessibility trusted: %s' % ok)
        if not ok:
            self.tip += '\n⚠ 还差一步授权：系统设置 → 隐私与安全性 → 辅助功能 → 打开 earImmersion'

    def frameless(self, win):
        # help = 无边框的浮动小窗；nonActivating = 点它不会把本程序切到前台（⌘C 才能发给原来的软件）；
        # canJoinAllSpaces = 在所有桌面和全屏的软件上都能显示
        for attrs in ('noActivates nonActivating canJoinAllSpaces', 'noActivates nonActivating', 'noActivates'):
            try:
                win.tk.call('::tk::unsupported::MacWindowStyle', 'style', win._w, 'help', attrs)
                return log('popup style: ' + attrs)
            except self.tk.TclError as e:
                log('popup style %r refused: %s' % (attrs, e))
        win.overrideredirect(True)

    def reveal(self):
        # 窗口只在第一次真正"显示"：之后每重新显示一次，Tk 都会把本程序切到前台
        if not self.mapped:
            self.pop.deiconify()
            self.mapped = True
        self.pop.update_idletasks()

    def hide(self):
        self.pop.geometry('+-20000+-20000')     # 不收起窗口，只是挪到屏幕外面

    def trusted(self, ask=False):
        """有没有"辅助功能"权限（模拟 ⌘C 要用）。ask=True：没有的话让系统弹出授权提示。"""
        try:
            if not ask:
                return self.cg.AXIsProcessTrusted()
            P = ctypes.c_void_p
            cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
            cf.CFDictionaryCreate.restype, cf.CFDictionaryCreate.argtypes = P, [P, P, P, ctypes.c_long, P, P]
            keys = (P * 1)(P.in_dll(self.cg, 'kAXTrustedCheckOptionPrompt').value)
            vals = (P * 1)(P.in_dll(cf, 'kCFBooleanTrue').value)
            opts = cf.CFDictionaryCreate(None, keys, vals, 1,
                                         ctypes.addressof(P.in_dll(cf, 'kCFTypeDictionaryKeyCallBacks')),
                                         ctypes.addressof(P.in_dll(cf, 'kCFTypeDictionaryValueCallBacks')))
            ok = self.cg.AXIsProcessTrustedWithOptions(opts)
            self.cg.CFRelease(opts)
            return ok
        except Exception:
            log('accessibility check error: ' + traceback.format_exc())
            return True

    def nothing(self):
        if not self.trusted():
            return '还没有"辅助功能"权限，取不到选中的文字：\n系统设置 → 隐私与安全性 → 辅助功能 → 打开 earImmersion'
        return super().nothing()

    def _count(self):
        f = self.cg.CGEventSourceCounterForEventType    # 系统累计的"左键按下 / 松开"事件次数
        return f(0, 1) + f(0, 2)

    def poll(self):
        x, y = self.root.winfo_pointerxy()
        return x, y, self.cg.CGEventSourceButtonState(0, 0), None

    def events(self):
        now = self._count()
        n, self.count = now - self.count, now
        if not 0 < n < 20:
            return ()
        x, y = self.root.winfo_pointerxy()
        return [(None, x, y, None)] * n                 # None = "按键状态翻转了一次"

    def _paste(self):
        return subprocess.run(['pbpaste'], capture_output=True, env=self.env).stdout.decode('utf-8', 'replace')

    def _copy(self, s):
        subprocess.run(['pbcopy'], input=s.encode(), env=self.env)

    def _in_front(self):
        """本程序是不是在最前面（是的话 ⌘C 会发给自己，取不到词）。"""
        try:
            out = subprocess.run('lsappinfo info -only pid "$(lsappinfo front)"', shell=True,
                                 capture_output=True, text=True, timeout=2).stdout
            return str(os.getpid()) in re.findall(r'\d+', out)
        except Exception:
            return None

    def _key(self, code, down, flags):
        ev = self.cg.CGEventCreateKeyboardEvent(None, code, down)
        self.cg.CGEventSetFlags(ev, flags)
        self.cg.CGEventPost(0, ev)
        self.cg.CFRelease(ev)

    def grab(self):
        log('in front: %s' % self._in_front())
        old = self._paste()
        self._copy('')  # 先清空，才能判断 ⌘C 是否真的复制到了东西
        # 像真键盘那样完整地按一遍：⌘ 按下 → C 按下 → C 松开 → ⌘ 松开（55 = ⌘，8 = C）。
        # 只发"带 ⌘ 标记的 C"的话，系统会一直以为 ⌘ 还按着：之后程序合成的点击都成了"⌘ + 点击"
        try:
            for code, down in ((55, True), (8, True), (8, False)):
                self._key(code, down, 0x100000)
        finally:
            self._key(55, False, 0)
        text, end = '', time.time() + 0.8
        while not text and time.time() < end:
            time.sleep(0.03)
            text = self._paste()
        self._copy(old)
        held = self.cg.CGEventSourceFlagsState(0) & 0x1e0000        # ⇧ ⌃ ⌥ ⌘
        if held:
            log('modifier keys down after copy: %#x' % held)
        return text

    def play(self, path):
        self.stop()
        self.proc = subprocess.Popen(['afplay', path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            code = self.proc.wait(0.15)     # 放不出来的话它会立刻退出
        except subprocess.TimeoutExpired:
            return None
        return 'afplay 退出码 %d' % code if code else None

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        self.proc = None


# ───────────────────────── 主逻辑（与平台无关） ─────────────────────────

class App:
    TICK = 40  # 毫秒：主循环的节拍

    def __init__(self, platform, todo):
        self.q = queue.Queue()                  # 后台线程取回来的发音 / 释义
        self.todo = todo                        # 别的线程要主线程做的事：'quit' / 'hello'
        self.means, self.voices, self.busy = {}, {}, set()
        self.text = None        # 当前选中的文本；Windows / macOS 在点喇叭之前是 None
        self.want = None        # 等发音下载完要播放的文本
        self.bad = None         # (文本, 原因)：这段文本的发音没取到，或者没播出来
        self.visible = self.clicked = self.note = self.down = self.own = self.quitting = False
        self.seen, self.keep = 0.0, 4           # 鼠标最后一次在弹窗上的时刻；离开多少秒后自动消失
        self.lag = self.errors = 0
        self.press, self.last_up = (0, 0, None), (0, 0, 0.0)
        self.os = platform(self)
        self.os.after(400, self.hello)
        self.os.after(self.TICK, self.tick)

    def hello(self):
        """启动提示：让人一眼看到"它开起来了"，同时把弹窗这条路先走一遍。"""
        self.text, self.clicked, self.note, self.want = None, False, True, None
        self.os.show(0, 0, pin=True)
        self.os.say(wrap('earImmersion 已启动\n选中英文 → 点旁边出现的小喇叭 → 听发音、看释义\n' + self.os.tip))
        self.visible, self.seen, self.keep = True, time.monotonic(), 8
        log('hello')

    def quit(self):
        log('quit')
        self.quitting = True
        self.os.stop()
        self.os.close()
        self.os.quit()

    # —— 主循环 ——
    def inside(self, x, y):
        if not self.visible:
            return False
        bx, by, bw, bh = self.os.box()
        return 0 <= x - bx < bw and 0 <= y - by < bh

    def tick(self):
        try:
            now = time.monotonic()
            x, y, down, texty = self.os.poll()
            for d, ex, ey, et in self.os.events():
                self.button(not self.down if d is None else d, ex, ey, et, now, 'event')
            if down == self.down:
                self.lag = 0
            elif self.os.sourced and not self.lag:
                self.lag = 1        # 事件还没到？等一拍；下一拍仍然对不上，才以轮询到的状态为准（兜底）
            else:
                self.lag = 0
                self.button(down, x, y, texty, now, 'poll')

            while self.todo:
                self.quit() if self.todo.popleft() == 'quit' else self.hello()
            while True:
                try:
                    kind, text, res = self.q.get_nowait()
                except queue.Empty:
                    break
                self.busy.discard((kind, text))
                self.done(kind, text, res)

            if self.visible:
                if self.inside(x, y):
                    self.seen = now
                elif now - self.seen > self.keep:
                    self.hide()
        except Exception:
            self.errors += 1
            if self.errors <= 20:
                log('tick error: ' + traceback.format_exc())
        finally:
            if not self.quitting:
                self.os.after(self.TICK, self.tick)

    def button(self, down, x, y, texty, now, via):
        """鼠标左键按下 / 松开了。Windows / macOS 靠"拖动"或"双击"判断用户刚选了一段文字。"""
        if down == self.down:
            return
        self.down = down
        if down:
            self.own = self.inside(x, y)
            self.press = (x, y, texty)
            if self.visible and not self.own and not self.note:    # 启动提示不因为点了别处就消失
                self.hide()
            return
        if self.own:
            if self.os.manual and self.inside(x, y):
                self.click()
            return
        if not self.os.lazy:
            return
        px, py, ptexty = self.press
        ux, uy, ut = self.last_up
        self.last_up = (x, y, now)
        kind = ('drag' if abs(x - px) + abs(y - py) > 6 else
                'double' if now - ut < 0.5 and abs(x - ux) + abs(y - uy) < 6 else None)
        if not kind:
            return
        if ptexty is False and texty is False:  # 按下和松开时都是普通箭头：多半是在拖窗口、点图标
            return log('select %s via %s -> skip (plain cursor)' % (kind, via))
        self.os.after(80, lambda: self.show(None, x, y, '%s via %s' % (kind, via)))  # 稍等，让原软件先把选区更新好

    def selected(self, raw, x, y):
        """（Linux）主选区换了内容。"""
        try:
            text = clean(raw)
            if not raw or (self.visible and not self.note and text == self.text):
                return                  # 空的，或者只是同一段文字被重新声明了一遍
            if not text:
                return log('select primary %d chars -> skip' % len(raw))
            self.show(text, x, y, 'primary')
        except Exception:
            log('selected error: ' + traceback.format_exc())

    def show(self, text, x, y, why):
        self.text, self.clicked, self.note, self.want, self.bad = text, False, False, None, None
        self.os.show(x, y)
        self.visible, self.seen, self.keep = True, time.monotonic(), 4
        log('select %s%s -> show' % (why, '' if text is None else ' %d chars' % len(text)))

    def hide(self):
        self.os.hide()
        self.visible, self.want = False, None

    def render(self):
        """按当前已拿到的数据刷新释义卡片。"""
        t = self.text
        if t in self.means:
            m = self.means[t]
            msg = ('/%s/\n' % m['phone'] if m['phone'] else '') + '\n'.join(m['lines'])
        else:
            msg = '…' if ('mean', t) in self.busy else '未找到释义'
        self.os.say(wrap(msg + ('\n（%s）' % self.bad[1] if self.bad and self.bad[0] == t else '')))

    # —— 取数据（后台线程）与播放 ——
    def prefetch(self):
        """鼠标移到喇叭上就开始取数据，点击时更快。"""
        t = self.text
        if not t:
            return
        if t not in self.voices and os.path.exists(voice_path(t)):
            self.voices[t] = voice_path(t)
        for kind, fn, have in (('voice', voice, self.voices), ('mean', lookup, self.means)):
            if t not in have and (kind, t) not in self.busy:
                self.busy.add((kind, t))
                threading.Thread(target=self.work, args=(kind, fn, t), daemon=True).start()

    def work(self, kind, fn, text):
        try:
            res = fn(text)
        except Exception:
            res = None
        self.q.put((kind, text, res))

    def click(self):
        if self.note:
            return self.hide()
        if self.text is None:   # Windows / macOS：现在才去"复制"选中的内容
            raw = self.os.grab()
            self.text = clean(raw) or None
            long = len(' '.join(raw.split())) > MAX_LEN
            log('grab %d chars -> %s' % (len(self.text or raw), 'ok' if self.text else 'too long' if long
                                         else 'not english' if raw else 'nothing copied'))
            if self.text is None:
                self.keep = 4
                return self.os.say(wrap('选中的内容太长（超过 %d 个字符）' % MAX_LEN if long
                                        else '选中的不是英文' if raw else self.os.nothing()))
        t = self.text
        self.clicked, self.bad, self.keep = True, None, 20
        self.prefetch()
        self.render()
        if t in self.voices:
            self.speak(t)
        else:
            self.want = t
            self.os.paint(True)

    def speak(self, t):
        err = self.os.play(self.voices[t])
        log('play ' + ('fail: ' + err if err else 'ok'))
        if err:
            self.bad = (t, '播放失败：' + err)
            self.os.paint(False)
            return self.render()
        self.os.paint(True)
        self.os.after(900, lambda: self.want is None and self.os.paint(False))

    def done(self, kind, text, res):
        log('%s %s' % (kind, 'ok' if res else 'none'))
        if kind == 'mean':
            if res:
                self.means[text] = res
        else:
            if res:
                self.voices[text] = res
            if text == self.want:
                self.want = None
                if res:
                    self.speak(text)
                else:
                    self.bad = (text, '发音获取失败，请检查网络')
                    self.os.paint(False)
        if text == self.text and self.clicked and self.visible:
            self.render()


# ───────────────────────── 启动 ─────────────────────────

def ask(text):
    """弹一个"是 / 否"对话框。"""
    if WIN:   # MB_YESNO | MB_ICONINFORMATION | MB_SETFOREGROUND | MB_TOPMOST；返回 6 = "是"
        return ctypes.windll.user32.MessageBoxW(None, text, 'earImmersion', 0x4 | 0x40 | 0x10000 | 0x40000) == 6
    if MAC:
        import tkinter
        import tkinter.messagebox
        tkinter.Tk().withdraw()
        return tkinter.messagebox.askyesno('earImmersion', text)
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk
    box = Gtk.MessageDialog(message_type=Gtk.MessageType.QUESTION, buttons=Gtk.ButtonsType.YES_NO, text=text)
    box.set_title('earImmersion')
    yes = box.run() == Gtk.ResponseType.YES
    box.destroy()
    return yes


def hail(word):
    """跟已经在运行的那个实例说句话；返回它的应答（不是它，或没有应答，返回 b''）。"""
    try:
        with socket.create_connection(('127.0.0.1', PORT), timeout=3) as s:
            s.sendall(word)
            reply = s.recv(32)
            return reply if reply.startswith(b'earImmersion') else b''
    except OSError:
        return b''


def serve(srv, todo, born):
    """后台线程：应答"又运行了一次"的那个进程。"""
    while True:
        try:
            conn, _ = srv.accept()
            with conn:
                conn.settimeout(2)
                word = conn.recv(16)
                conn.sendall(b'earImmersion' + (b' new' if time.monotonic() - born < 8 else b''))
                if word == b'quit':
                    todo.append('quit')
                conn.recv(1)    # 等对方先断开，这样本机端口不会被占住一分钟（否则退出后没法马上再启动）
        except OSError:
            time.sleep(0.2)


def prune(keep=1000):
    """发音缓存只保留最近的 keep 个文件。"""
    try:
        fs = sorted((os.path.join(VOICE, f) for f in os.listdir(VOICE)), key=os.path.getmtime)
        for f in fs[:-keep]:
            os.remove(f)
    except OSError:
        pass


def main():
    if WIN:
        platform = Windows
    elif MAC:
        platform = Mac
    else:
        platform = Linux
        try:
            import gi  # noqa: F401
        except ImportError:  # conda / venv 的 Python 没有 GTK 绑定，自动换系统 Python 重新启动
            sys_py = '/usr/bin/python3'
            if os.path.exists(sys_py) and os.path.realpath(sys.executable) != os.path.realpath(sys_py):
                os.execv(sys_py, [sys_py, os.path.abspath(__file__)] + sys.argv[1:])
            sys.exit('earImmersion：缺少 GTK 绑定，请先安装：sudo apt install python3-gi gir1.2-gtk-3.0')
    os.makedirs(VOICE, exist_ok=True)

    srv = socket.socket()
    if not WIN:     # 在 Windows 上这个选项的含义相反（会允许两个程序同时占用端口），所以不能设
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind(('127.0.0.1', PORT))
        srv.listen(2)
    except OSError:
        reply = hail(b'ping')
        if reply:           # 已经有一个在运行了
            # 它才刚启动（多半是等不及又双击了一次）：什么都不做，它马上会显示启动提示
            if reply != b'earImmersion new' and ask(
                    'earImmersion 正在运行。\n\n用法：选中英文 → 点旁边出现的小喇叭。\n\n要退出它吗？'):
                hail(b'quit')
            return
        srv = None          # 端口被别的程序占着：照常运行，只是没法防止重复启动
    todo = collections.deque()
    if srv:                 # 尽早开始应答，免得启动慢的时候被当成"没在运行"
        threading.Thread(target=serve, args=(srv, todo, time.monotonic()), daemon=True).start()

    try:
        if os.path.getsize(LOG) > 200000:
            os.remove(LOG)
    except OSError:
        pass
    log('start %s %s python %s frozen=%s%s' % (VERSION, sys.platform, sys.version.split()[0],
                                              getattr(sys, 'frozen', False), '' if srv else ' (port busy)'))
    load_config()
    prune()
    App(platform, todo).os.loop()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        err = traceback.format_exc()
        log('fatal: ' + err)
        if not WIN:
            raise
        # Windows 版没有控制台窗口：出错必须弹出来，否则就是"双击没反应"
        ctypes.windll.user32.MessageBoxW(None, 'earImmersion 启动失败：\n\n%s\n日志：%s' % (err[-700:], LOG),
                                         'earImmersion', 0x10)
        sys.exit(1)
