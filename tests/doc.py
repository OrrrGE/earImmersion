"""e2e 测试用的"文档"窗口：显示一段文字，并把几个关键位置的屏幕坐标写进 JSON。

用法：python tests/doc.py <输出的 json 路径>
"""
import json
import sys

TEXT = ('plaid is an efficient engine for late interaction retrieval.\n\n这一行是中文不应该触发。\n\n'
        'Utility-Oriented state-of-the-art\n\ncache set\n')
OUT = sys.argv[1]
A = TEXT.index('late')                              # 短语的第一个字符
B = TEXT.index('retrieval') + len('retrieval') - 1  # 短语的最后一个字符
ZH = TEXT.index('中文')
C = TEXT.index('Utility-Oriented')                    # 带连字符的复合词
D = C + len('Utility-Oriented') - 1
E = TEXT.index('state-of-the-art')                    # 词典里收了整条、有现成发音的复合词
F = E + len('state-of-the-art') - 1
G = TEXT.index('cache set')                           # 这两个词，词典给的发音是 WAV 而不是 MP3
H = G + len('cache set') - 1


def save(rect, origin, title_y):
    """rect(i) -> 第 i 个字符的屏幕矩形 (x, y, w, h)。"""
    def mid(i):
        x, y, w, h = rect(i)
        return [x + w // 2, y + h // 2]

    def span(i, j):
        ax, ay, _, ah = rect(i)
        bx, by, bw, bh = rect(j)
        return [ax + 1, ay + ah // 2, bx + bw + 1, by + bh // 2]
    ox, oy = origin
    # 空白处：最后一行文字的下面、靠左。弹窗总是出现在鼠标的右下方，靠左才不会点到它身上
    _, ly, _, lh = rect(len(TEXT) - 2)
    with open(OUT, 'w') as f:
        json.dump({'word': mid(2), 'zh': mid(ZH), 'drag': span(A, B), 'compound': span(C, D), 'entry': span(E, F),
                   'wav': span(G, H),
                   'blank': [ox + 24, ly + lh + 70], 'title': [ox + 200, title_y]}, f)


if sys.platform.startswith('linux'):
    import gi
    gi.require_version('Gtk', '3.0')
    gi.require_version('Gdk', '3.0')
    from gi.repository import Gdk, GLib, Gtk

    css = Gtk.CssProvider()
    css.load_from_data(b'textview { font-size: 20pt; }')
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css, 800)
    win = Gtk.Window(title='e2e-doc')
    win.set_default_size(760, 520)
    win.move(60, 60)
    view = Gtk.TextView()
    view.set_wrap_mode(Gtk.WrapMode.WORD)
    view.set_left_margin(12)
    view.set_top_margin(12)
    buf = view.get_buffer()
    buf.set_text(TEXT)
    win.add(view)
    win.connect('destroy', Gtk.main_quit)
    win.show_all()

    def ready():
        _, ox, oy = view.get_window(Gtk.TextWindowType.TEXT).get_origin()

        def rect(i):
            r = view.get_iter_location(buf.get_iter_at_offset(i))
            x, y = view.buffer_to_window_coords(Gtk.TextWindowType.TEXT, r.x, r.y)
            return ox + x, oy + y, r.width, r.height
        save(rect, (ox, oy), oy - 14)
        return False

    GLib.timeout_add(1000, ready)
    Gtk.main()
else:
    import tkinter as tk

    if sys.platform == 'win32':
        import ctypes
        try:
            ctypes.WinDLL('shcore').SetProcessDpiAwareness(2)   # 这样拿到的坐标才是真实的屏幕像素
        except Exception:
            pass
    root = tk.Tk()
    root.title('e2e-doc')
    root.geometry('760x520+60+60')
    text = tk.Text(root, font=('Helvetica' if sys.platform == 'darwin' else 'Segoe UI', 20), wrap='word', padx=12, pady=12)
    text.pack(fill='both', expand=True)
    text.insert('1.0', TEXT)
    root.lift()
    root.attributes('-topmost', True)   # 先顶到最前面，保证不被别的窗口盖住
    root.after(700, lambda: root.attributes('-topmost', False))

    def ready():
        root.update_idletasks()
        ox, oy = text.winfo_rootx(), text.winfo_rooty()

        def rect(i):
            x, y, w, h = text.bbox('1.0 + %d chars' % i)
            return ox + x, oy + y, w, h
        save(rect, (ox, oy), root.winfo_rooty() - 14)

    root.after(1200, ready)
    root.mainloop()
