"""（仅 Windows，测试用）把主屏幕的缩放比例调高几档，打印调整前后的实际比例。

用法：python tests/dpi.py <档数>     例如 2 = 从 100% 调到 150%（每档 25%）
用的是"设置"里那个缩放滑块背后的系统调用；它没有写进微软的文档，所以只在测试机上用。
"""
import ctypes
import sys
import time
from ctypes import wintypes as w

u, sh = ctypes.WinDLL('user32'), ctypes.WinDLL('shcore')
sh.SetProcessDpiAwareness(2)
u.MonitorFromPoint.restype, u.MonitorFromPoint.argtypes = ctypes.c_void_p, [w.POINT, w.DWORD]
sh.GetDpiForMonitor.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p]
u.SystemParametersInfoW.argtypes = [w.UINT, w.UINT, ctypes.c_void_p, w.UINT]


def state():
    x, y = w.UINT(), w.UINT()
    sh.GetDpiForMonitor(u.MonitorFromPoint(w.POINT(0, 0), 1), 0, ctypes.byref(x), ctypes.byref(y))
    return '%d x %d at %d%%' % (u.GetSystemMetrics(0), u.GetSystemMetrics(1), round(x.value * 100 / 96.0))


print('before:', state(), flush=True)
now = ctypes.c_int(0)
u.SystemParametersInfoW(0x009E, 0, ctypes.byref(now), 0)       # SPI_GETLOGICALDPIOVERRIDE：现在比推荐值高 / 低几档
ok = u.SystemParametersInfoW(0x009F, ctypes.c_uint(int(sys.argv[1])).value, None, 1)   # SPI_SETLOGICALDPIOVERRIDE
time.sleep(3)
print('override was %d, set to %s: %s' % (now.value, sys.argv[1], 'accepted' if ok else 'refused'), flush=True)
print('after:', state(), flush=True)
