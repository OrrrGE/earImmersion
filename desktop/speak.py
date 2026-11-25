#!/usr/bin/env python3
"""磨耳朵 · 划词发音（桌面版）

在任意软件里选中英文 → 鼠标旁出现小喇叭 → 点击听标准发音并显示中文释义。
单文件、无第三方依赖，支持 Windows / macOS / Linux(X11)。用法见 README.md。
"""
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
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

ACCENT = 2          # 2 = 美音，1 = 英音
MAX_LEN = 300       # 选中内容超过这个长度就不出现喇叭
IBEAM_ONLY = True   # 仅 Windows：只有鼠标是"I"形文本光标时才出现（减少拖窗口时的误触发）
CACHE = os.path.join(os.path.expanduser('~'), '.cache', 'moerduo')

YD = 'https://dict.youdao.com/'
DICTS = '{"count":99,"dicts":[["ec","fanyi","web_trans","aiDefinition"]]}'
BLUE, INK, EDGE = '#1a73e8', '#1f2328', '#c9d1d9'


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
    return os.path.join(CACHE, hashlib.md5(('%d:%s' % (ACCENT, q)).encode()).hexdigest() + '.mp3')


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
            os.makedirs(CACHE, exist_ok=True)
            with open(path + '.tmp', 'wb') as f:
                f.write(b)
            os.replace(path + '.tmp', path)
            return path
    return None


# ───────────────────────── 各平台：弹窗 / 鼠标状态 / 取选中文本 / 播放 ─────────────────────────
# 每个平台类提供同一组方法：
#   after loop | show hide say paint box | mouse ibeam grab | play stop

class Linux:
    """GTK3（桌面版 Linux 自带的 python3-gi）。X11 有"主选区"，松开鼠标即可直接读到选中文本。"""
    lazy = False

    def __init__(self, on_click, on_enter):
        os.environ.setdefault('GDK_BACKEND', 'x11')
        import gi
        gi.require_version('Gtk', '3.0')
        gi.require_version('Gdk', '3.0')
        from gi.repository import Gdk, GLib, Gtk
        self.Gdk, self.GLib, self.Gtk = Gdk, GLib, Gtk
        self.disp = Gdk.Display.get_default()
        if not self.disp:
            sys.exit('磨耳朵：连接不到 X11 显示（纯 Wayland 会话暂不支持）')
        self.pointer = self.disp.get_default_seat().get_pointer()
        self.rootwin = Gdk.get_default_root_window()
        self.clip = Gtk.Clipboard.get(Gdk.SELECTION_PRIMARY)
        self.fresh, self.last = True, None
        self.clip.connect('owner-change', lambda *a: setattr(self, 'fresh', True))

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
        win.connect('button-release-event', lambda *a: on_click())
        win.connect('enter-notify-event', lambda *a: on_enter())
        self.at = (0, 0)
        self.bin = self.proc = self.player = None

    def after(self, ms, fn):
        self.GLib.timeout_add(ms, lambda: fn() and False)

    def loop(self):
        import signal
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        self.Gtk.main()

    def _place(self):
        self.win.resize(1, 1)  # 弹出式窗口不会自己缩小，先压到最小再由内容撑开
        w, h = self.win.get_preferred_size()[1].width, self.win.get_preferred_size()[1].height
        x, y = self.at
        g = self.disp.get_monitor_at_point(x, y).get_geometry()
        x = max(g.x, min(x, g.x + g.width - w - 8))
        if y + h > g.y + g.height - 8:
            y -= h + 28
        self.win.move(x, y)

    def show(self, x, y):
        self.at = (x + 12, y + 16)
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

    def mouse(self):
        _, x, y, mask = self.rootwin.get_device_position(self.pointer)
        return x, y, bool(mask & self.Gdk.ModifierType.BUTTON1_MASK)

    def ibeam(self):
        return None

    def grab(self):
        text = self.clip.wait_for_text() or ''
        # 选区没换过主人、内容也没变 = 只是拖了窗口之类，并没有新选中东西
        stale = not self.fresh and text == self.last
        self.fresh, self.last = False, text
        return '' if stale else text

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
                if not self.player:
                    print('磨耳朵：没有可用的播放器，请安装 gstreamer1.0-plugins-good 或 ffmpeg', file=sys.stderr)
        self.stop()
        if self.bin is not None:
            self.bin.set_property('uri', self.GLib.filename_to_uri(path, None))
            self.bin.set_state(self.Gst.State.PLAYING)
        elif self.player:
            self.proc = subprocess.Popen(self.player + [path], stdin=subprocess.DEVNULL,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def stop(self):
        if self.bin is not None:
            self.bin.set_state(self.Gst.State.NULL)
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        self.proc = None


class TkUI:
    """Windows / macOS 共用的弹窗（Tk 随官方 Python 安装包自带）。"""
    lazy = True  # 没有主选区：先出喇叭，点击时才模拟"复制"取词（不点就不会动剪贴板）
    font = ('TkDefaultFont', 10)

    def __init__(self, on_click, on_enter):
        import tkinter as tk
        self.tk = tk
        root = self.root = tk.Tk()
        root.withdraw()
        pop = self.pop = tk.Toplevel(root, bg=EDGE, padx=1, pady=1)
        pop.withdraw()
        self.setup(pop)
        pop.attributes('-topmost', True)
        box = tk.Frame(pop, bg='#ffffff')
        box.pack()
        ico = self.ico = tk.Canvas(box, width=30, height=30, bg='#ffffff', highlightthickness=0, cursor='hand2')
        ico.grid(row=0, column=0, sticky='n')
        ico.create_polygon(7, 12, 11, 12, 16, 7, 16, 23, 11, 18, 7, 18, fill=BLUE)
        for a, b in ((13, 10), (11, 6)):
            ico.create_arc(a, b, 34 - a, 30 - b, start=-55, extent=110, style='arc', outline=BLUE, width=2)
        self.info = tk.Label(box, bg='#ffffff', fg=INK, font=self.font, justify='left',
                             anchor='w', padx=6, pady=5)
        for w in (ico, self.info):
            w.bind('<ButtonRelease-1>', lambda e: on_click())
        pop.bind('<Enter>', lambda e: on_enter())
        self.at = (0, 0)

    def setup(self, win):
        win.overrideredirect(True)

    def after(self, ms, fn):
        self.root.after(ms, fn)

    def loop(self):
        self.root.mainloop()

    def _place(self):
        p = self.pop
        p.update_idletasks()
        x, y = self.at
        x = max(p.winfo_vrootx(), min(x, p.winfo_vrootx() + p.winfo_vrootwidth() - p.winfo_reqwidth() - 8))
        if y + p.winfo_reqheight() > p.winfo_vrooty() + p.winfo_vrootheight() - 8:
            y -= p.winfo_reqheight() + 28
        p.geometry('+%d+%d' % (x, y))

    def show(self, x, y):
        self.at = (x + 12, y + 16)
        self.info.grid_forget()
        self.paint(False)
        self._place()
        self.pop.deiconify()
        self.pop.lift()

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

    def ibeam(self):
        return None


class Windows(TkUI):
    font = ('Microsoft YaHei UI', 10)

    def __init__(self, *a):
        from ctypes import wintypes
        u = self.u = ctypes.windll.user32
        self.mci = ctypes.windll.winmm.mciSendStringW
        self.pt = wintypes.POINT()

        class CURSORINFO(ctypes.Structure):
            _fields_ = [('cbSize', wintypes.DWORD), ('flags', wintypes.DWORD),
                        ('hCursor', ctypes.c_void_p), ('pt', wintypes.POINT)]
        self.ci = CURSORINFO()
        self.ci.cbSize = ctypes.sizeof(CURSORINFO)
        u.LoadCursorW.restype = ctypes.c_void_p
        u.LoadCursorW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        u.GetParent.restype = ctypes.c_void_p
        u.GetParent.argtypes = [ctypes.c_void_p]
        u.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        u.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
        self.beam = u.LoadCursorW(None, 32513)                 # IDC_IBEAM
        self.btn = 0x02 if u.GetSystemMetrics(23) else 0x01    # 左右键互换时取右键
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)     # 高分屏下坐标才对得上
        except Exception:
            pass
        super().__init__(*a)

    def setup(self, win):
        win.overrideredirect(True)
        win.update_idletasks()
        hwnd = self.u.GetParent(win.winfo_id()) or win.winfo_id()
        ex = self.u.GetWindowLongW(hwnd, -20)
        # 不抢焦点 | 不进任务栏 | 置顶 —— 点喇叭时焦点留在原软件里，Ctrl+C 才能复制到选中的词
        self.u.SetWindowLongW(hwnd, -20, ex | 0x08000000 | 0x80 | 0x8)

    def mouse(self):
        self.u.GetCursorPos(ctypes.byref(self.pt))
        return self.pt.x, self.pt.y, bool(self.u.GetAsyncKeyState(self.btn) & 0x8000)

    def ibeam(self):
        if not IBEAM_ONLY:
            return None
        return bool(self.u.GetCursorInfo(ctypes.byref(self.ci))) and self.ci.hCursor == self.beam

    def grab(self):
        u, r = self.u, self.root
        try:
            old = r.clipboard_get()
        except self.tk.TclError:
            old = None
        seq = u.GetClipboardSequenceNumber()
        for key, flag in ((0x11, 0), (0x43, 0), (0x43, 2), (0x11, 2)):  # Ctrl+C
            u.keybd_event(key, 0, flag, 0)
        end = time.time() + 0.5
        while u.GetClipboardSequenceNumber() == seq and time.time() < end:
            time.sleep(0.02)
        if u.GetClipboardSequenceNumber() == seq:
            return ''
        time.sleep(0.03)
        try:
            text = r.clipboard_get()
        except self.tk.TclError:
            text = ''
        if old is not None:  # 还原原来的剪贴板文本
            r.clipboard_clear()
            r.clipboard_append(old)
        return text

    def play(self, path):
        self.stop()
        self.mci('open "%s" type mpegvideo alias moerduo' % path, None, 0, None)
        self.mci('play moerduo', None, 0, None)

    def stop(self):
        self.mci('close moerduo', None, 0, None)


class Mac(TkUI):
    font = ('PingFang SC', 13)

    def __init__(self, *a):
        cg = self.cg = ctypes.CDLL('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
        P = ctypes.c_void_p
        cg.CGEventSourceButtonState.restype = ctypes.c_bool
        cg.CGEventSourceButtonState.argtypes = [ctypes.c_int32, ctypes.c_uint32]
        cg.CGEventCreateKeyboardEvent.restype = P
        cg.CGEventCreateKeyboardEvent.argtypes = [P, ctypes.c_uint16, ctypes.c_bool]
        cg.CGEventSetFlags.argtypes = [P, ctypes.c_uint64]
        cg.CGEventPost.argtypes = [ctypes.c_uint32, P]
        cg.CFRelease.argtypes = [P]
        self.proc = None
        self.env = dict(os.environ, LANG='en_US.UTF-8')
        super().__init__(*a)

    def setup(self, win):
        try:  # 不激活本程序的浮动窗口，点喇叭时焦点留在原软件里，⌘C 才能复制到选中的词
            win.tk.call('::tk::unsupported::MacWindowStyle', 'style', win._w, 'help', 'noActivates')
        except self.tk.TclError:
            win.overrideredirect(True)

    def mouse(self):
        x, y = self.root.winfo_pointerxy()
        return x, y, bool(self.cg.CGEventSourceButtonState(0, 0))

    def _paste(self):
        return subprocess.run(['pbpaste'], capture_output=True, env=self.env).stdout.decode('utf-8', 'replace')

    def _copy(self, s):
        subprocess.run(['pbcopy'], input=s.encode(), env=self.env)

    def grab(self):
        old = self._paste()
        self._copy('')  # 先清空，才能判断 ⌘C 是否真的复制到了东西
        for down in (True, False):
            ev = self.cg.CGEventCreateKeyboardEvent(None, 8, down)  # 8 = C 键
            self.cg.CGEventSetFlags(ev, 0x100000)                   # ⌘
            self.cg.CGEventPost(0, ev)
            self.cg.CFRelease(ev)
        text, end = '', time.time() + 0.6
        while not text and time.time() < end:
            time.sleep(0.03)
            text = self._paste()
        self._copy(old)
        return text

    def play(self, path):
        self.stop()
        self.proc = subprocess.Popen(['afplay', path])

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        self.proc = None


# ───────────────────────── 主逻辑（与平台无关） ─────────────────────────

class App:
    TICK = 40  # 毫秒：轮询鼠标状态的间隔

    def __init__(self, platform):
        self.os = platform(self.click, self.prefetch)
        self.q = queue.Queue()
        self.means, self.voices, self.busy = {}, {}, set()
        self.text = None        # 当前选中的文本；懒取词平台在点击前为 None
        self.want = None        # 等待发音下载完成后要播放的文本
        self.bad = None         # 发音获取失败的文本
        self.visible = self.clicked = self.down = self.own = False
        self.shown = 0.0
        self.press = (0, 0, None)
        self.last_up = (0, 0, 0.0)
        self.os.after(self.TICK, self.tick)

    # —— 鼠标轮询：识别"拖选"和"双击" ——
    def inside(self, x, y):
        if not self.visible:
            return False
        bx, by, bw, bh = self.os.box()
        return 0 <= x - bx < bw and 0 <= y - by < bh

    def tick(self):
        try:
            x, y, down = self.os.mouse()
            now = time.monotonic()
            if down and not self.down:
                self.own = self.inside(x, y)
                self.press = (x, y, self.os.ibeam())
                if self.visible and not self.own:
                    self.hide()
            elif self.down and not down and not self.own:
                px, py, beam = self.press
                ux, uy, ut = self.last_up
                drag = abs(x - px) + abs(y - py) > 6
                double = now - ut < 0.5 and abs(x - ux) + abs(y - uy) < 6
                self.last_up = (x, y, now)
                if drag or double:  # 稍等一下，让原软件先把选区更新好
                    self.os.after(80, lambda: self.selected(x, y, beam))
            self.down = down

            while True:
                try:
                    kind, text, res = self.q.get_nowait()
                except queue.Empty:
                    break
                self.busy.discard((kind, text))
                self.done(kind, text, res)

            if self.visible:
                if self.inside(x, y):
                    self.shown = now
                elif now - self.shown > (20 if self.clicked else 4):
                    self.hide()
        finally:
            self.os.after(self.TICK, self.tick)

    def selected(self, x, y, beam):
        if self.os.lazy:
            if beam is False and self.os.ibeam() is False:
                return
            self.show(None, x, y)
        else:
            text = clean(self.os.grab())
            if text:
                self.show(text, x, y)

    def show(self, text, x, y):
        self.text, self.clicked, self.want, self.bad = text, False, None, None
        self.os.show(x, y)
        self.visible, self.shown = True, time.monotonic()

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
        self.os.say(wrap(msg) + ('\n（发音获取失败）' if self.bad == t else ''))

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
        if self.text is None:  # 懒取词平台：现在才去复制选中的内容
            self.text = clean(self.os.grab()) or None
            if self.text is None:
                self.shown = time.monotonic() - 2.5
                return self.os.say('未选中英文')
        t = self.text
        self.clicked, self.bad = True, None
        self.prefetch()
        self.render()
        if t in self.voices:
            self.speak(t)
        else:
            self.want = t
            self.os.paint(True)

    def speak(self, t):
        self.os.paint(True)
        self.os.play(self.voices[t])
        self.os.after(900, lambda: self.want is None and self.os.paint(False))

    def done(self, kind, text, res):
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
                    self.bad = text
                    self.os.paint(False)
        if text == self.text and self.clicked and self.visible:
            self.render()


def prune(keep=1000):
    """发音缓存只保留最近的 keep 个文件。"""
    try:
        fs = sorted((os.path.join(CACHE, f) for f in os.listdir(CACHE)), key=os.path.getmtime)
        for f in fs[:-keep]:
            os.remove(f)
    except OSError:
        pass


def main():
    if sys.platform == 'win32':
        platform = Windows
    elif sys.platform == 'darwin':
        platform = Mac
    else:
        platform = Linux
        try:
            import gi  # noqa: F401
        except ImportError:  # conda / venv 的 Python 没有 GTK 绑定，自动换系统 Python 重新启动
            sys_py = '/usr/bin/python3'
            if os.path.exists(sys_py) and os.path.realpath(sys.executable) != os.path.realpath(sys_py):
                os.execv(sys_py, [sys_py, os.path.abspath(__file__)] + sys.argv[1:])
            sys.exit('磨耳朵：缺少 GTK 绑定，请先安装：sudo apt install python3-gi gir1.2-gtk-3.0')
    lock = socket.socket()  # 占一个本地端口，防止重复启动出现两个喇叭
    try:
        lock.bind(('127.0.0.1', 47631))
    except OSError:
        sys.exit('磨耳朵已经在运行了')
    prune()
    App(platform).os.loop()


if __name__ == '__main__':
    main()
