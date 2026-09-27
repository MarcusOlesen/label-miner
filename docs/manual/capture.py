"""Capturing a Tk window and drawing numbered callouts on the screenshot.

The same helpers as in the Label Printer manual, so both manuals' screenshots
look alike. Windows only (the window is captured with the Win32 PrintWindow
API, which renders it even if something else covers it).
"""

import ctypes
from ctypes import wintypes

from PIL import Image, ImageDraw, ImageFont

ORANGE = (217, 98, 43)
WHITE = (255, 255, 255)


# --------------------------------------------------------------------------
# Window capture (PrintWindow renders the window even if something covers it)
# --------------------------------------------------------------------------


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


def _setup_winapi():
    u, g = ctypes.windll.user32, ctypes.windll.gdi32
    u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    u.GetWindowDC.argtypes = [wintypes.HWND]
    u.GetWindowDC.restype = wintypes.HDC
    u.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    u.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    g.CreateCompatibleDC.argtypes = [wintypes.HDC]
    g.CreateCompatibleDC.restype = wintypes.HDC
    g.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    g.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    g.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    g.SelectObject.restype = wintypes.HGDIOBJ
    g.GetDIBits.argtypes = [
        wintypes.HDC,
        wintypes.HBITMAP,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.UINT,
    ]
    g.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    g.DeleteDC.argtypes = [wintypes.HDC]
    ctypes.windll.dwmapi.DwmGetWindowAttribute.argtypes = [
        wintypes.HWND,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    return u, g


def grab_window(root):
    """Returns (image, (left, top)) of the visible window in screen pixels."""
    root.update()
    u, g = _setup_winapi()
    hwnd = int(root.wm_frame(), 16)
    rect, ext = wintypes.RECT(), wintypes.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(rect))
    # The visible frame (GetWindowRect includes invisible resize borders).
    ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(ext), ctypes.sizeof(ext))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    hdc_win = u.GetWindowDC(hwnd)
    hdc = g.CreateCompatibleDC(hdc_win)
    bmp = g.CreateCompatibleBitmap(hdc_win, w, h)
    g.SelectObject(hdc, bmp)
    u.PrintWindow(hwnd, hdc, 2)  # PW_RENDERFULLCONTENT
    bmi = BITMAPINFOHEADER(40, w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    g.GetDIBits(hdc, bmp, 0, h, buf, ctypes.byref(bmi), 0)
    g.DeleteObject(bmp)
    g.DeleteDC(hdc)
    u.ReleaseDC(hwnd, hdc_win)
    img = Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)
    box = (ext.left - rect.left, ext.top - rect.top, ext.right - rect.left, ext.bottom - rect.top)
    return img.crop(box), (ext.left, ext.top)


# --------------------------------------------------------------------------
# Callouts
# --------------------------------------------------------------------------


def _font(size, bold=True):
    for name in ("segoeuib.ttf", "arialbd.ttf") if bold else ("segoeui.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def annotate(img, circles=(), boxes=(), arrows=(), scale=1.0):
    """circles: [(number, (x, y))]; boxes: [(x0, y0, x1, y1)];
    arrows: [((x0, y0), (x1, y1))] pointer lines from a circle to its target.
    All in image pixels."""
    img = img.convert("RGB")
    d = ImageDraw.Draw(img)
    width = max(3, round(3 * scale))
    for x0, y0, x1, y1 in boxes:
        d.rounded_rectangle((x0, y0, x1, y1), radius=round(6 * scale), outline=ORANGE, width=width)
    for (x0, y0), (x1, y1) in arrows:
        d.line((x0, y0, x1, y1), fill=ORANGE, width=width)
        dot = round(5 * scale)
        d.ellipse((x1 - dot, y1 - dot, x1 + dot, y1 + dot), fill=ORANGE)
    r = round(17 * scale)
    f = _font(round(20 * scale))
    for number, (x, y) in circles:
        d.ellipse((x - r - 2, y - r - 2, x + r + 2, y + r + 2), fill=WHITE)
        d.ellipse((x - r, y - r, x + r, y + r), fill=ORANGE)
        d.text((x, y + 1), str(number), font=f, fill=WHITE, anchor="mm")
    return img
