"""端到端测试：在真实桌面上启动程序，用合成的鼠标事件选词、点喇叭，再核对日志、焦点和剪贴板。

用法：python tests/e2e.py <启动程序的命令…>        （Windows / macOS / Linux-X11 通用）
截图和结果写到 ./e2e/。设 E2E_SOFT=1 时只报告，不因检查失败而返回非零。

释义和发音来自外部的词典服务。如果只有这几项没过，而测试进程自己（对照实验）也连不上词典服务，
说明是这台机器的网络问题而不是程序的问题：等一分钟整个重跑一遍，以重跑的结果为准。
"""
import ctypes
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from urllib.request import Request, urlopen

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'desktop'))
import speak as app_code    # noqa: E402  （只借用里面的常量和画图标的函数，不会启动程序）

OUT = os.path.abspath('e2e')
SOFT = os.environ.get('E2E_SOFT') == '1'
SENTINEL = 'E2E-SENTINEL'
checks = []


# ───────────────────────── 各平台的"手"和"眼" ─────────────────────────

class Win:
    lazy = True

    def __init__(self):
        from ctypes import wintypes as w
        self.w, self.u = w, ctypes.WinDLL('user32')
        self.scale = 1.0
        try:    # 声明"按真实像素工作"，并读出主屏幕的缩放比例
            sh = ctypes.WinDLL('shcore')
            sh.SetProcessDpiAwareness(2)
            self.u.MonitorFromPoint.restype, self.u.MonitorFromPoint.argtypes = ctypes.c_void_p, [w.POINT, w.DWORD]
            sh.GetDpiForMonitor.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p]
            dx, dy = w.UINT(), w.UINT()
            if not sh.GetDpiForMonitor(self.u.MonitorFromPoint(w.POINT(0, 0), 1), 0, ctypes.byref(dx), ctypes.byref(dy)):
                self.scale = dx.value / 96.0
        except Exception as e:
            print('could not read the screen scale: %r' % e, flush=True)
        u, P = self.u, ctypes.c_void_p
        self.ENUM = ctypes.WINFUNCTYPE(w.BOOL, P, w.LPARAM)
        for name, res, args in (
                ('EnumWindows', w.BOOL, [self.ENUM, w.LPARAM]), ('IsWindowVisible', w.BOOL, [P]),
                ('GetClassNameW', ctypes.c_int, [P, w.LPWSTR, ctypes.c_int]),
                ('GetWindowTextW', ctypes.c_int, [P, w.LPWSTR, ctypes.c_int]),
                ('GetForegroundWindow', P, []), ('FindWindowW', P, [w.LPCWSTR, w.LPCWSTR]),
                ('PostMessageW', w.BOOL, [P, w.UINT, w.WPARAM, w.LPARAM]),
                ('GetCursorInfo', w.BOOL, [P]), ('LoadCursorW', P, [P, P]),
                ('GetAsyncKeyState', ctypes.c_short, [ctypes.c_int])):
            f = getattr(u, name)
            f.restype, f.argtypes = res, args
        self.audio = ctypes.WinDLL('winmm').waveOutGetNumDevs() > 0   # 这台机器有没有声卡

    def data_dir(self):
        return os.path.join(os.environ['LOCALAPPDATA'], 'earImmersion')

    def move(self, x, y):
        self.u.SetCursorPos(int(x), int(y))
        self.u.mouse_event(1, 1, 0, 0, 0)    # 再补一对真实的移动事件，窗口才会更新光标形状
        self.u.mouse_event(1, -1, 0, 0, 0)

    def button(self, down, clicks=1):
        self.u.mouse_event(2 if down else 4, 0, 0, 0, 0)

    def shot(self, path):
        from PIL import ImageGrab
        ImageGrab.grab().save(path)

    def _ps(self, script):
        return subprocess.run(['powershell', '-NoProfile', '-Command', script], capture_output=True, timeout=60).stdout

    def clipboard(self):
        return self._ps('Get-Clipboard -Raw').decode('ascii', 'replace').strip()

    def set_clipboard(self, s):
        self._ps("Set-Clipboard -Value '%s'" % s)

    def windows(self):
        """所有可见的顶层窗口：(句柄, 类名, 标题)。"""
        out, u = [], self.u

        def cb(h, _):
            if u.IsWindowVisible(h):
                cls, txt = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(256)
                u.GetClassNameW(h, cls, 256)
                u.GetWindowTextW(h, txt, 256)
                out.append((h, cls.value, txt.value))
            return True
        u.EnumWindows(self.ENUM(cb), 0)
        return out

    def front(self):
        buf = ctypes.create_unicode_buffer(256)
        self.u.GetWindowTextW(self.u.GetForegroundWindow(), buf, 256)
        return buf.value

    def doc_front(self):
        return self.front() == 'e2e-doc'

    def mods(self):
        """系统认为现在还按着的修饰键（程序模拟完"复制"之后应该一个都不剩）。"""
        return [n for n, vk in (('Ctrl', 0x11), ('Shift', 0x10), ('Alt', 0x12)) if self.u.GetAsyncKeyState(vk) < 0]

    def release(self):
        pass

    def record(self, path=None):
        """录下这台机器放出来的声音（要有虚拟声卡：它把放出的声音原样送回录音端）。
        不给 path = 开始录；给了 path = 停下，存成 wav。→ 成功与否"""
        mm = ctypes.WinDLL('winmm')
        mci = mm.mciSendStringW
        if path is not None:
            errs = [(c.split()[0], e) for c, e in ((c, mci(c, None, 0, None)) for c in
                                                   ('stop e2erec', 'save e2erec "%s"' % path, 'close e2erec')) if e]
            if errs:
                print('recording could not be saved: %s' % errs, flush=True)
            return not errs
        tried = []
        for rate, ch in ((16000, 1), (48000, 2), (44100, 2), (48000, 1), (44100, 1)):    # 录音设备不一定什么格式都收
            mci('open new type waveaudio alias e2erec', None, 0, None)
            fmt = mci('set e2erec time format ms bitspersample 16 channels %d samplespersec %d bytespersec %d alignment %d'
                      % (ch, rate, rate * ch * 2, ch * 2), None, 0, None)
            err = mci('record e2erec', None, 0, None)
            if not err:
                print('recording the sound output at %d Hz, %d channel(s)' % (rate, ch), flush=True)
                return True
            tried.append((rate, ch, fmt, err))
            mci('close e2erec', None, 0, None)
        print('recording not possible (%d recording devices): %s' % (mm.waveInGetNumDevs(), tried), flush=True)
        return False

    def cursor(self):
        """当前鼠标光标的名字（程序靠它判断是不是在选字）。"""
        w = self.w

        class CURSORINFO(ctypes.Structure):
            _fields_ = [('cbSize', w.DWORD), ('flags', w.DWORD), ('hCursor', ctypes.c_void_p), ('pt', w.POINT)]
        ci = CURSORINFO()
        ci.cbSize = ctypes.sizeof(ci)
        self.u.GetCursorInfo(ctypes.byref(ci))
        names = {self.u.LoadCursorW(None, i): n for i, n in ((32512, 'ARROW'), (32513, 'IBEAM'), (32649, 'HAND'))}
        return names.get(ci.hCursor, 'other')


class Mac:
    lazy = True
    scale = 1.0

    def __init__(self):
        self.cg = ctypes.CDLL('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
        self.cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        P = ctypes.c_void_p

        class CGPoint(ctypes.Structure):
            _fields_ = [('x', ctypes.c_double), ('y', ctypes.c_double)]
        self.CGPoint = CGPoint
        self.cg.CGEventCreateMouseEvent.restype = P
        self.cg.CGEventCreateMouseEvent.argtypes = [P, ctypes.c_uint32, CGPoint, ctypes.c_uint32]
        self.cg.CGEventSetIntegerValueField.argtypes = [P, ctypes.c_uint32, ctypes.c_int64]
        self.cg.CGEventPost.argtypes = [ctypes.c_uint32, P]
        self.cg.AXIsProcessTrusted.restype = ctypes.c_bool
        self.cg.CGEventCreateKeyboardEvent.restype = P
        self.cg.CGEventCreateKeyboardEvent.argtypes = [P, ctypes.c_uint16, ctypes.c_bool]
        self.cg.CGEventSetFlags.argtypes = [P, ctypes.c_uint64]
        self.cg.CGEventSourceFlagsState.restype = ctypes.c_uint64
        self.cg.CGEventSourceFlagsState.argtypes = [ctypes.c_int32]
        self.cf.CFRelease.argtypes = [P]
        self.pos, self.held = (0.0, 0.0), False
        print('test process accessibility-trusted:', self.cg.AXIsProcessTrusted(), flush=True)
        try:    # 对照：系统自带的提示音放得出来，才说明这台机器能出声
            self.audio = subprocess.run(['afplay', '/System/Library/Sounds/Tink.aiff'],
                                        capture_output=True, timeout=20).returncode == 0
        except Exception:
            self.audio = False

    def data_dir(self):
        return os.path.expanduser('~/Library/Caches/earImmersion')

    def _post(self, kind, clicks=1):
        ev = self.cg.CGEventCreateMouseEvent(None, kind, self.CGPoint(*self.pos), 0)
        self.cg.CGEventSetIntegerValueField(ev, 1, clicks)   # kCGMouseEventClickState
        self.cg.CGEventPost(0, ev)
        self.cf.CFRelease(ev)

    def move(self, x, y):
        self.pos = (float(x), float(y))
        self._post(6 if self.held else 5)     # 按住时是"拖动"，否则是"移动"

    def button(self, down, clicks=1):
        self.held = down
        self._post(1 if down else 2, clicks)

    def shot(self, path):
        subprocess.run(['screencapture', '-x', path], timeout=60)

    def clipboard(self):
        return subprocess.run(['pbpaste'], capture_output=True).stdout.decode('utf-8', 'replace').strip()

    def set_clipboard(self, s):
        subprocess.run(['pbcopy'], input=s.encode())

    def front(self):
        """最前面那个程序的进程号。"""
        out = subprocess.run('lsappinfo info -only pid "$(lsappinfo front)"', shell=True,
                             capture_output=True, text=True).stdout
        m = re.search(r'\d+', out)
        return int(m.group(0)) if m else 0

    def doc_front(self):
        return self.front() == self.doc.pid

    def cursor(self):
        return ''

    def mods(self):
        """系统认为现在还按着的修饰键。合成的鼠标事件会带上它们，而"⌘ + 点击"不会把被点的窗口切到前台。"""
        held = self.cg.CGEventSourceFlagsState(0)
        return [n for n, bit in (('shift', 0x20000), ('control', 0x40000), ('option', 0x80000), ('command', 0x100000))
                if held & bit]

    def release(self):
        """发一个"修饰键全都松开了"的事件。"""
        ev = self.cg.CGEventCreateKeyboardEvent(None, 55, False)
        self.cg.CGEventSetFlags(ev, 0)
        self.cg.CGEventPost(0, ev)
        self.cf.CFRelease(ev)
        time.sleep(0.3)


class X11:
    lazy = False
    audio = True
    scale = 1.0

    def __init__(self):
        self.x, self.xt = ctypes.CDLL('libX11.so.6'), ctypes.CDLL('libXtst.so.6')
        self.x.XOpenDisplay.restype = ctypes.c_void_p
        self.d = ctypes.c_void_p(self.x.XOpenDisplay(None))

    def data_dir(self):
        return os.path.join(os.environ.get('XDG_CACHE_HOME') or os.path.expanduser('~/.cache'), 'earImmersion')

    def move(self, x, y):
        self.xt.XTestFakeMotionEvent(self.d, -1, int(x), int(y), 0)
        self.x.XFlush(self.d)

    def button(self, down, clicks=1):
        self.xt.XTestFakeButtonEvent(self.d, 1, int(down), 0)
        self.x.XFlush(self.d)

    def shot(self, path):
        import gi
        gi.require_version('Gtk', '3.0')
        gi.require_version('Gdk', '3.0')
        from gi.repository import Gdk, Gtk  # noqa: F401  （导入 Gtk 才会连接显示）
        root = Gdk.get_default_root_window()
        Gdk.pixbuf_get_from_window(root, 0, 0, root.get_width(), root.get_height()).savev(path, 'png', [], [])

    def clipboard(self):
        return SENTINEL

    def set_clipboard(self, s):
        pass

    def front(self):
        return ''

    def doc_front(self):
        return True

    def cursor(self):
        return ''

    def mods(self):
        return []

    def release(self):
        pass


# ───────────────────────── 通用动作 ─────────────────────────

class Log:
    def __init__(self, path):
        self.path, self.pos = path, 0

    def read(self):
        try:
            with open(self.path, encoding='utf-8') as f:
                return f.read()
        except OSError:
            return ''

    def mark(self):
        self.pos = len(self.read())

    def new(self):
        return self.read()[self.pos:]

    def wait(self, pattern, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            m = re.search(pattern, self.new())
            if m:
                return m
            time.sleep(0.1)
        return None


def check(name, ok, info='', hard=True, net=False):
    """hard=False：这台机器上没法验证的项目（比如没有声卡）；net=True：结果取决于外部的词典服务。"""
    checks.append({'name': name, 'ok': bool(ok), 'hard': hard, 'net': net, 'info': str(info)})
    print('%s %s %s' % ('PASS' if ok else ('FAIL' if hard else 'WARN'), name, '' if info is None else info), flush=True)
    return bool(ok)


def reachable(patience=12):
    """对照实验：测试进程自己现在连不连得上词典服务。连得上返回 True，否则返回原因。"""
    out = []

    def go():
        try:
            req = Request('https://dict.youdao.com/dictvoice?audio=test&type=2', headers={'User-Agent': 'Mozilla/5.0'})
            with urlopen(req, timeout=8) as r:
                out.append(len(r.read()) > 500 or 'unexpected answer')
        except Exception as e:
            out.append(repr(e))
    t = threading.Thread(target=go, daemon=True)    # 域名解析卡住时 urlopen 的超时不管用，所以放到线程里限时
    t.start()
    t.join(patience)
    return out[0] if out else 'no answer within %d seconds' % patience


def glide(d, x, y, steps=8):
    x0, y0 = d.at
    for i in range(1, steps + 1):
        d.move(x0 + (x - x0) * i / steps, y0 + (y - y0) * i / steps)
        time.sleep(0.03)
    d.at = (x, y)


def click(d, x, y, n=1):
    glide(d, x, y)
    time.sleep(0.15)
    for i in range(n):
        d.button(True, i + 1)
        time.sleep(0.08)
        d.button(False, i + 1)
        time.sleep(0.08)


def tap(d, x, y, n=2):
    """触控板"轻点"：按下和松开几乎同时发生，轮询鼠标状态是看不到的。"""
    glide(d, x, y)
    time.sleep(0.15)
    for i in range(n):
        d.button(True, i + 1)
        d.button(False, i + 1)
        time.sleep(0.12)


def drag(d, x0, y0, x1, y1):
    glide(d, x0, y0)
    time.sleep(0.15)
    d.button(True)
    time.sleep(0.1)
    glide(d, x1, y1, 12)
    time.sleep(0.1)
    d.button(False)


def shot(d, name):
    try:
        d.shot(os.path.join(OUT, name + '.png'))
    except Exception as e:
        print('screenshot %s failed: %r' % (name, e), flush=True)


def icon_at(log):
    """喇叭图标的位置。程序每次显示弹窗，都会把弹窗的左上角和图标的边长记进日志。→ (中心 x, 中心 y, 左, 上, 边长)"""
    found = re.findall(r' popup at (-?\d+),(-?\d+) icon (\d+)', log.new())
    if not found:
        return None
    x, y, n = map(int, found[-1])
    return x + n // 2, y + n // 2, x, y, n


def look(d, log, name):
    """刚出现的喇叭：大小对不对，画得清不清楚（把它从截图里裁出来，和预先算好的位图逐个像素比）。"""
    at = icon_at(log)
    want = int(round((app_code.ICON - 2) * d.scale)) + 2
    check('喇叭的边长是 %d 像素（1.0.1 是 %d）' % (want, int(round(30 * d.scale)) + 2), at and at[4] == want,
          at and 'popup %d,%d size %d, screen scale %.2f' % (at[2], at[3], at[4], d.scale))
    try:
        from PIL import Image
        im = Image.open(os.path.join(OUT, name + '.png')).convert('RGB')
    except Exception as e:
        return print('icon not inspected: %r' % e, flush=True)
    if not at or isinstance(d, Mac):    # macOS 的截图分辨率和坐标不一定是一比一，只留图不比对
        return
    _, _, x, y, n = at
    box = im.crop((x - 6, y - 6, x + n + 6, y + n + 6))
    box.resize((box.width * 8, box.height * 8), Image.NEAREST).save(os.path.join(OUT, name + '-icon-x8.png'))
    got = list(im.crop((x + 1, y + 1, x + n - 1, y + n - 1)).getdata())
    shades = len(set(got))
    if not isinstance(d, Win):
        return print('icon: %d different colours inside the %d px box' % (shades, n), flush=True)
    ref = [tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))
           for c in app_code.speaker(n - 2, app_code.BLUE, '#ffffff').replace('{', ' ').replace('}', ' ').split()]
    off = sum(1 for a, b in zip(got, ref) if max(abs(p - q) for p, q in zip(a, b)) > 2)
    check('屏幕上的喇叭和抗锯齿位图逐像素一致', len(got) == len(ref) and off == 0,
          '%d of %d pixels differ; %d shades between blue and white' % (off, len(ref), shades))


def speak(d, log, name, chars, clips=None, mt=False):
    """选中文字后弹出了喇叭：点它，核对结果。clips：应该由几段词典发音接起来读；mt：应该用到机翻。"""
    at = icon_at(log)
    if not check(name + '：日志里有喇叭的位置', at):
        return
    x, y = at[:2]
    if d.lazy:
        check(name + '：出现喇叭后，前台仍是文档', d.doc_front(), d.front())
    log.mark()
    glide(d, x, y)
    time.sleep(0.4)
    click(d, x, y)
    played = log.wait(r' play (ok|fail)(?: (\d+) clips (\d+) ms)?.*', 15)
    done = played and played.group(1) == 'ok' and log.wait(r' played (\d+) clips in (\d+) ms', 12)
    translated = mt and log.wait(r' mt (ok|none)(.*)', 12)
    time.sleep(1.2)
    shot(d, name)
    new = log.new()
    if d.lazy:
        check(name + '：取到选中的 %d 个字符' % chars, ' grab %d chars -> ok' % chars in new)
        check(name + '：点喇叭后，前台仍是文档', d.doc_front(), d.front())
        check(name + '：剪贴板已还原', d.clipboard() == SENTINEL, repr(d.clipboard()[:40]))
        check(name + '：模拟"复制"之后没有留下按着的修饰键', not d.mods(), d.mods())
    got = [check(name + '：取到了释义', ' mean ok' in new, net=True),
           check(name + '：下载到了发音', ' voice ok' in new or played, net=True)]
    if clips:
        m = re.search(r' voice ok (\d+) clips( unsure)?( wav)?', new)
        want = clips if isinstance(clips, tuple) else (clips,)
        if name == '3d-wav':    # 这一项要测的就是 WAV 格式的发音放不放得出来
            check(name + '：取到的发音里有 WAV 格式的', m and m.group(3), m and m.group(0), hard=False, net=True)
        got.append(check(name + '：发音由 %s 段词典发音组成（不是整段语音合成）' % ' 或 '.join(map(str, want)),
                         m and int(m.group(1)) in want and not m.group(2), m and m.group(0), net=True))
    if mt:  # 机翻用的是公共的免费接口，按来源地址限额：测试机上不一定有额度，所以只报告，不算失败
        check(name + '：机翻有结果', translated and translated.group(1) == 'ok', translated and translated.group(0),
              hard=False)
    # 没有声卡的机器上放不出声音，下面几项只能跳过
    check(name + '：播放了发音', played and played.group(1) == 'ok', played and played.group(0), hard=d.audio, net=True)
    if played and played.group(2):
        n, ms = int(played.group(2)), int(played.group(3))
        took = done and int(done.group(2))
        check(name + '：%d 段全部播完，用时和音频的长度对得上' % n,
              done and int(done.group(1)) == n and ms * 0.8 <= took <= ms + 1500 + 400 * n,
              '%s; audio is %d ms' % (done and done.group(0), ms), hard=d.audio and not isinstance(d, X11), net=True)
    if isinstance(d, Win):
        m = re.search(r' playing: mode=(\w+) position=(\d+)/(\d+)', new)
        check(name + '：播放进度在走', m and int(m.group(2)) > 0, m and m.group(0), hard=d.audio, net=True)
    if not all(got) and not d.offline:
        answer = reachable()
        print('control: can the test itself reach the dictionary service right now? %s' % answer, flush=True)
        d.offline = answer is not True


def run(d, log, pos, cmd, t0):
    wx, wy = pos['word']
    show = r' select \w+ .*-> show'

    check('程序启动', log.wait(r' start \S+', 90), '启动用了 %.1f 秒' % (time.time() - t0))
    hello = log.wait(r' hello', 10)
    time.sleep(0.8)
    shot(d, '0-start')
    check('启动后显示了提示', hello)
    click(d, *pos['blank'])
    time.sleep(0.8)
    if d.lazy:
        check('点了文档之后，前台是文档', d.doc_front(), d.front())

    log.mark()
    click(d, wx, wy, 2)
    m = log.wait(show, 6)
    time.sleep(0.5)
    shot(d, '1-speaker')
    check('双击选词后出现喇叭', m, d.cursor())
    if not d.lazy:
        check('识别出选中了 5 个字符', m and ' 5 chars' in m.group(0))
    look(d, log, '1-speaker')
    speak(d, log, '2-word', 5, clips=1)

    click(d, *pos['blank'])
    time.sleep(0.8)
    log.mark()
    drag(d, *pos['drag'])
    m = log.wait(show, 6)
    time.sleep(0.5)
    check('拖选短语后出现喇叭', m)
    # 第二次取词：上一次还原剪贴板之后还能不能再取到。三个词各有词典发音，应该接起来读
    speak(d, log, '3-phrase', 26, clips=3, mt=True)

    click(d, *pos['blank'])
    time.sleep(0.8)
    log.mark()
    drag(d, *pos['compound'])
    m = log.wait(show, 6)
    time.sleep(0.5)
    check('拖选带连字符的复合词后出现喇叭', m)
    speak(d, log, '3b-compound', 16, clips=2, mt=True)     # Utility-Oriented：两个词分别用词典发音

    click(d, *pos['blank'])
    time.sleep(0.8)
    log.mark()
    drag(d, *pos['entry'])
    m = log.wait(show, 6)
    time.sleep(0.5)
    check('拖选词典里收了整条的复合词后出现喇叭', m)
    speak(d, log, '3c-entry', 16, clips=(1, 5))            # state-of-the-art：词典里有整条的发音，一段读完（它偶尔取不到，那就逐词读）

    click(d, *pos['blank'])
    time.sleep(0.8)
    log.mark()
    drag(d, *pos['wav'])
    m = log.wait(show, 6)
    time.sleep(0.5)
    check('拖选 cache set 后出现喇叭', m)
    speak(d, log, '3d-wav', 9, clips=2, mt=True)           # cache、set：词典给的这两段发音是 WAV，不是 MP3

    click(d, *pos['blank'])
    time.sleep(0.8)
    log.mark()
    tap(d, wx, wy)
    m = log.wait(show, 6)
    time.sleep(0.5)
    shot(d, '4-tap')
    check('触控板式的快速轻点两下也能出喇叭', m)

    if d.lazy:  # 这两个系统事先不知道选了什么：选中文也会出喇叭，点了之后应该提示"不是英文"，并且同样把剪贴板还原
        click(d, *pos['blank'])
        time.sleep(0.8)
        log.mark()
        click(d, *pos['zh'], 2)
        m = log.wait(show, 6)
        time.sleep(0.5)
        at = icon_at(log)
        log.mark()
        if at:
            glide(d, *at[:2])
            time.sleep(0.4)
            click(d, *at[:2])
        got = log.wait(r' grab \d+ chars -> ([a-z ]+)', 6)
        time.sleep(0.8)
        shot(d, '4-chinese')
        check('选中中文后点喇叭：提示不是英文', m and got and got.group(1) == 'not english', got and got.group(0))
        check('选中中文后点喇叭：剪贴板已还原', d.clipboard() == SENTINEL, repr(d.clipboard()[:40]))
        check('选中中文后点喇叭：没有留下按着的修饰键', not d.mods(), d.mods())
        check('选中中文后点喇叭：前台仍是文档', d.doc_front(), d.front())
    else:
        click(d, *pos['blank'])
        time.sleep(0.8)
        log.mark()
        click(d, *pos['zh'], 2)
        skipped = log.wait(r' select \w+ \d+ chars -> skip', 5)
        time.sleep(0.6)
        check('选中中文不出喇叭', skipped and '-> show' not in log.new(), skipped and skipped.group(0))

    if isinstance(d, Win):
        click(d, *pos['blank'])
        time.sleep(0.8)
        log.mark()
        tx, ty = pos['title']
        glide(d, tx, ty)
        time.sleep(0.3)
        over = d.cursor()
        drag(d, tx, ty, tx + 60, ty + 30)
        m = log.wait(r' select \w+ .*-> (show|skip)', 4)
        shot(d, '5-titlebar')
        check('拖动窗口标题栏不出喇叭', m and m.group(1) == 'skip', '%s; cursor %s' % (m and m.group(0), over))
        check('托盘图标已创建', ' tray True' in log.read())

        # 托盘菜单：假装在托盘图标上松开了右键，菜单会出现在鼠标所在的位置
        bx, by = pos['blank'][0] + 60, pos['blank'][1] + 30
        glide(d, bx, by)
        log.mark()
        d.u.PostMessageW(d.u.FindWindowW('earImmersionHidden', None), 0x8001, 0, 0x0205)
        time.sleep(1.2)
        shot(d, '6-tray-menu')
        click(d, bx + int(40 * d.scale), by + int(14 * d.scale))    # 第一项"使用说明"
        check('托盘菜单能用（选"使用说明"后再次显示提示）', log.wait(r' hello', 6))
        time.sleep(0.5)

        # 再运行一次：应弹出"正在运行，要退出吗"，选"是"后原来的实例退出
        second = subprocess.Popen(cmd)
        dlg, end = None, time.time() + 60
        while not dlg and time.time() < end:
            time.sleep(0.5)
            dlg = [x for x in d.windows() if x[1] == '#32770' and x[2] == 'earImmersion']
        shot(d, '7-second-launch')
        check('再次运行时弹出对话框', dlg)
        if dlg:
            log.mark()
            d.u.PostMessageW(dlg[0][0], 0x0111, 6, 0)   # WM_COMMAND, IDYES
            check('选"是"后程序退出', log.wait(r' quit', 10))
        try:
            second.wait(10)
        except subprocess.TimeoutExpired:
            second.kill()
    else:
        bundle = cmd[0].split('.app/')[0] + '.app' if '.app/' in cmd[0] else None
        if bundle:  # macOS 的打包版：点程序坞图标 = 再"打开"一次，应该再显示一遍提示；程序坞菜单里的"退出"能退出
            log.mark()
            subprocess.run(['open', '-a', os.path.abspath(bundle)], capture_output=True, timeout=20)
            check('再打开一次时重新显示提示', log.wait(r' hello', 8), hard=False)
            log.mark()
            try:
                subprocess.run(['osascript', '-e', 'tell application id "io.github.orrrge.earimmersion" to quit'],
                               capture_output=True, timeout=15)
            except subprocess.TimeoutExpired:
                pass
            if check('收到系统的"退出"后程序退出', log.wait(r' quit', 6), hard=False):
                return
        # 其他系统上的对话框不好自动点，直接走"再运行一次 → 退出"背后的那条本机连接
        log.mark()
        try:
            with socket.create_connection(('127.0.0.1', app_code.PORT), timeout=3) as s:
                s.sendall(b'quit')
                answer = s.recv(32)
        except OSError as e:
            answer = repr(e).encode()
        check('收到退出请求后程序退出', answer.startswith(b'earImmersion') and log.wait(r' quit', 10), answer)


def heard(path):
    """录音里有多少秒是有声音的（按 0.1 秒一格，峰值超过满刻度的 1% 算有声音）。"""
    import array
    import wave
    with wave.open(path) as f:
        rate, a = f.getframerate() * f.getnchannels(), array.array('h', f.readframes(f.getnframes()))
    step = rate // 10
    return sum(1 for i in range(0, len(a) - step, step) if max(a[i:i + step], key=abs) not in range(-327, 328)) / 10.0


def attempt(d, cmd):
    """完整地跑一遍：开文档窗口、启动程序、逐项检查、收尾。"""
    del checks[:]
    d.at, d.offline = (5, 5), False
    tape = os.path.join(OUT, 'loopback.wav')
    taping = isinstance(d, Win) and d.audio and d.record()
    log = Log(os.path.join(d.data_dir(), 'log.txt'))
    try:
        os.remove(log.path)
    except OSError:
        pass
    d.set_clipboard(SENTINEL)
    if d.mods():
        print('modifier keys reported down before the test: %s; releasing them' % d.mods(), flush=True)
        d.release()
        print('after releasing: %s' % d.mods(), flush=True)
    spot = os.path.join(OUT, 'doc.json')
    if os.path.exists(spot):
        os.remove(spot)
    d.doc = doc = subprocess.Popen([sys.executable, os.path.join(HERE, 'doc.py'), spot])
    end = time.time() + 30
    while not os.path.exists(spot) and time.time() < end:
        time.sleep(0.2)
    time.sleep(0.5)
    app = None
    try:
        with open(spot) as f:
            pos = json.load(f)
        t0 = time.time()
        app = subprocess.Popen(cmd)
        run(d, log, pos, cmd, t0)
    except Exception:
        import traceback
        check('测试脚本自身出错', False, traceback.format_exc())
    finally:
        if taping:
            try:
                secs = d.record(tape) and heard(tape)
                spoken = sum(int(ms) for ms in re.findall(r' play ok \d+ clips (\d+) ms', log.read())) / 1000.0
                check('从虚拟声卡上录到了程序放出的声音', spoken and secs and secs >= spoken * 0.6,
                      'sound in %.1f s of the recording; the clips played add up to %.1f s' % (secs or 0, spoken),
                      net=True)
            except Exception as e:
                check('从虚拟声卡上录到了程序放出的声音', False, repr(e), hard=False)
        print('\n──── app log ────\n' + log.read(), flush=True)
        time.sleep(1)
        if app and app.poll() is not None:
            print('app exit code:', app.returncode, flush=True)
        for p in (app, doc):
            if p and p.poll() is None:
                if sys.platform == 'win32':
                    subprocess.run(['taskkill', '/F', '/T', '/PID', str(p.pid)], capture_output=True)
                else:
                    p.terminate()
        if sys.platform == 'darwin':    # 万一"再打开一次"时多启动了一个
            subprocess.run(['pkill', '-f', 'earImmersion.app/Contents/MacOS/earImmersion'], capture_output=True)
        with open(os.path.join(OUT, 'result.json'), 'w', encoding='utf-8') as f:
            json.dump(checks, f, ensure_ascii=False, indent=1)
    failed = [c for c in checks if not c['ok'] and c['hard']]
    skipped = [c['name'] for c in checks if not c['ok'] and not c['hard']]
    print('\n%d checks, %d failed%s' % (len(checks), len(failed),
                                        ': ' + '; '.join(c['name'] for c in failed) if failed else ''), flush=True)
    if skipped:
        print('could not be verified on this machine: ' + '; '.join(skipped), flush=True)
    return failed


def main():
    cmd = sys.argv[1:]
    os.makedirs(OUT, exist_ok=True)
    d = Win() if sys.platform == 'win32' else Mac() if sys.platform == 'darwin' else X11()
    print('this machine can play sound: %s; screen scale: %.2f' % (d.audio, d.scale), flush=True)
    failed = attempt(d, cmd)
    if failed and d.offline and all(c['net'] for c in failed):
        print('\nOnly the lookups failed, and the test itself could not reach the dictionary service either:\n'
              'that is this machine\'s network, not the app. Running everything once more in a minute.\n', flush=True)
        time.sleep(60)
        failed = attempt(d, cmd)
    sys.exit(1 if failed and not SOFT else 0)


if __name__ == '__main__':
    main()
