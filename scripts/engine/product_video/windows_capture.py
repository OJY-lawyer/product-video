"""Read-only, explicitly targeted Windows window capture.

PrintWindow runs in a bounded child process. It never falls back to the desktop,
activates a window, restores a minimized window, or injects keyboard/mouse input.
"""
import ctypes
from ctypes import wintypes
from contextlib import contextmanager
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time

from .common import VideoError


def _positive_integer(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise VideoError(f'{label} 必须为正整数。')
    return value


def _validated_target(target):
    if not isinstance(target, dict):
        raise VideoError('Windows 截图目标必须为对象。')
    allowed = {'provider', 'process_id', 'window_handle', 'window_title'}
    if set(target) - allowed or target.get('provider', 'windows') != 'windows':
        raise VideoError('Windows 截图目标仅支持 process_id、window_handle 和精确 window_title。')
    if not any(k in target for k in ('process_id', 'window_handle')):
        raise VideoError('Windows 截图必须明确指定 process_id 或 window_handle；不能仅凭标题选取。')
    result = dict(target)
    for key in ('process_id', 'window_handle'):
        if key in result:
            _positive_integer(result[key], key)
    if 'window_title' in result and (not isinstance(result['window_title'], str)
                                     or not result['window_title'] or len(result['window_title']) > 4096):
        raise VideoError('window_title 必须为非空精确窗口标题，最多 4096 字符。')
    return result


def _api():
    if platform.system() != 'Windows':
        raise VideoError('windows 窗口采集仅支持 Windows。')
    user = ctypes.WinDLL('user32', use_last_error=True)
    gdi = ctypes.WinDLL('gdi32', use_last_error=True)
    signatures = [
        (user, 'IsWindow', [wintypes.HWND], wintypes.BOOL),
        (user, 'IsWindowVisible', [wintypes.HWND], wintypes.BOOL),
        (user, 'IsIconic', [wintypes.HWND], wintypes.BOOL),
        (user, 'GetWindowRect', [wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
        (user, 'GetWindowThreadProcessId', [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
        (user, 'GetWindowTextLengthW', [wintypes.HWND], ctypes.c_int),
        (user, 'GetWindowTextW', [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
        (user, 'GetAncestor', [wintypes.HWND, wintypes.UINT], wintypes.HWND),
        (user, 'GetDesktopWindow', [], wintypes.HWND),
        (user, 'GetShellWindow', [], wintypes.HWND),
        (user, 'GetDC', [wintypes.HWND], wintypes.HDC),
        (user, 'ReleaseDC', [wintypes.HWND, wintypes.HDC], ctypes.c_int),
        (user, 'PrintWindow', [wintypes.HWND, wintypes.HDC, wintypes.UINT], wintypes.BOOL),
        (gdi, 'CreateCompatibleDC', [wintypes.HDC], wintypes.HDC),
        (gdi, 'SelectObject', [wintypes.HDC, wintypes.HANDLE], wintypes.HANDLE),
        (gdi, 'DeleteObject', [wintypes.HANDLE], wintypes.BOOL),
        (gdi, 'DeleteDC', [wintypes.HDC], wintypes.BOOL),
        (gdi, 'CreateDIBSection', [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                  ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD], wintypes.HANDLE),
    ]
    for dll, name, args, result in signatures:
        fn = getattr(dll, name)
        fn.argtypes, fn.restype = args, result
    return user, gdi


@contextmanager
def _physical_coordinates(user):
    setter = getattr(user, 'SetThreadDpiAwarenessContext', None)
    previous = None
    if setter:
        setter.argtypes = [ctypes.c_void_p]
        setter.restype = ctypes.c_void_p
        previous = setter(ctypes.c_void_p(-4))
    try:
        yield
    finally:
        if previous:
            setter(previous)


def _window_details(user, handle):
    if (not user.IsWindow(handle) or user.GetAncestor(handle, 2) != handle
            or handle in (user.GetDesktopWindow(), user.GetShellWindow())):
        raise VideoError('目标不是可采集的独立顶层应用窗口。')
    pid = wintypes.DWORD()
    user.GetWindowThreadProcessId(handle, ctypes.byref(pid))
    count = user.GetWindowTextLengthW(handle)
    title = ctypes.create_unicode_buffer(min(max(count, 0), 4096) + 1)
    user.GetWindowTextW(handle, title, len(title))
    rect = wintypes.RECT()
    with _physical_coordinates(user):
        if not user.GetWindowRect(handle, ctypes.byref(rect)):
            raise VideoError('无法读取目标窗口边界；未采集任何其他窗口。')
    return {
        'window_handle': int(handle), 'process_id': int(pid.value), 'window_title': title.value,
        'visible': bool(user.IsWindowVisible(handle)), 'minimized': bool(user.IsIconic(handle)),
        'bounds': {'left': rect.left, 'top': rect.top, 'right': rect.right, 'bottom': rect.bottom},
        'width': rect.right - rect.left, 'height': rect.bottom - rect.top,
    }


def list_windows(process_id=None):
    """Return metadata for visible top-level windows, optionally for one PID."""
    if process_id is not None:
        _positive_integer(process_id, 'process_id')
    user, _ = _api()
    windows = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def visit(handle, unused):
        try:
            item = _window_details(user, handle)
            if item['visible'] and (process_id is None or item['process_id'] == process_id):
                windows.append(item)
        except VideoError:
            pass  # A window may disappear while EnumWindows is running.
        return True

    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user.EnumWindows.restype = wintypes.BOOL
    if not user.EnumWindows(visit, 0):
        raise VideoError('无法枚举 Windows 应用窗口。')
    return windows


def _choose_window(target, candidates):
    matches = [item for item in candidates if all(
        item[key] == target[key] for key in ('process_id', 'window_handle', 'window_title') if key in target
    )]
    if not matches:
        raise VideoError('没有找到指定 PID/HWND 与精确标题对应的可见窗口。')
    if len(matches) != 1:
        raise VideoError('指定进程包含多个窗口；请增加精确 window_handle 或 window_title。')
    item = matches[0]
    if item['minimized'] or not item['visible']:
        raise VideoError('目标窗口已最小化或隐藏；请先由用户恢复。不会抢占前台或改截桌面。')
    if not 1 <= item['width'] <= 16384 or not 1 <= item['height'] <= 16384 or item['width'] * item['height'] > 16_777_216:
        raise VideoError('目标窗口尺寸无效或超过本次窗口截图上限。')
    return item


class WindowsCapture:
    def __init__(self, target, root, launch=True):
        # launch is accepted for provider compatibility; this backend never launches apps.
        self.target = _validated_target(target)
        self.root = Path(root)
        self.user, _ = _api()
        selected = _choose_window(self.target, list_windows(self.target.get('process_id')))
        self.bound = {'process_id': selected['process_id'], 'window_handle': selected['window_handle']}
        if 'window_title' in self.target:
            self.bound['window_title'] = self.target['window_title']

    def inspect(self):
        item = _window_details(self.user, self.bound['window_handle'])
        selected = _choose_window(self.bound, [item])
        return {'provider': 'windows', 'window': selected,
                'capabilities': {'window_screenshot': True, 'controls': False, 'input': False,
                                 'desktop_fallback': False, 'launch': False},
                'ready': {'role': 'window', 'name': selected['window_title']}}

    def _wait_window(self, locator):
        if (not isinstance(locator, dict) or set(locator) != {'role', 'name'}
                or locator['role'] != 'window' or not isinstance(locator['name'], str) or not locator['name']):
            raise VideoError('Windows ready/wait 仅支持 role=window 与精确 name；不支持控件定位。')
        deadline = time.monotonic() + 10
        while True:
            current = self.inspect()['window']
            if current['window_title'] == locator['name']:
                return current
            if time.monotonic() >= deadline:
                raise VideoError('目标窗口未达到指定的精确标题；没有截图或发送输入。')
            time.sleep(0.1)

    def capture(self, shot, destination):
        actions = shot.get('actions', [])
        if not isinstance(actions, list) or len(actions) > 30:
            raise VideoError('Windows 截图 actions 需为最多 30 项的列表。')
        if shot.get('mask') or shot.get('points'):
            raise VideoError('Windows 窗口截图不支持 mask/points；请先准备不含私人内容的目标窗口。')
        for action in actions:
            if not isinstance(action, dict) or set(action) != {'action', 'target'} or action.get('action') != 'wait':
                raise VideoError('Windows 截图只支持只读 wait；不支持 press、鼠标或键盘操作。')
            self._wait_window(action['target'])
        before = self._wait_window(shot.get('ready'))
        destination = Path(destination)
        if destination.suffix.lower() != '.png':
            raise VideoError('Windows 目标截图必须保存为 PNG。')
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.window-capture-', suffix='.png', dir=destination.parent)
        os.close(fd)
        temporary = Path(temporary)
        env = os.environ.copy()
        package_root = str(Path(__file__).resolve().parent.parent)
        env['PYTHONPATH'] = package_root + (os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
        child = ('from product_video.windows_capture import _worker; import sys; '
                 '_worker(int(sys.argv[1]),int(sys.argv[2]),sys.argv[3])')
        try:
            try:
                result = subprocess.run(
                    [sys.executable, '-X', 'utf8', '-B', '-c', child, str(before['window_handle']),
                     str(before['process_id']), str(temporary)],
                    env=env, capture_output=True, timeout=10,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                )
            except subprocess.TimeoutExpired:
                raise VideoError('目标窗口截图超过 10 秒，已终止截图子进程；未操作或终止目标应用。') from None
            except OSError:
                raise VideoError('窗口截图子进程无法启动。') from None
            if result.returncode:
                raise VideoError('PrintWindow 无法取得有效目标画面（可能受保护、硬件加速或窗口状态变化）；未改截桌面。')
            after = self.inspect()['window']
            if any(before[k] != after[k] for k in ('process_id', 'window_handle', 'window_title', 'width', 'height')):
                raise VideoError('截图过程中目标窗口发生变化，结果已丢弃；请在稳定画面重试。')
            from PIL import Image
            with Image.open(temporary) as captured:
                if captured.size != (before['width'], before['height']):
                    raise VideoError('窗口截图尺寸不匹配，结果已丢弃。')
                captured.verify()
            os.replace(temporary, destination)
            return None
        finally:
            temporary.unlink(missing_ok=True)


def _worker(handle, pid, destination):
    """Private child-process entry; only the chosen HWND may supply pixels."""
    user, gdi = _api()
    item = _choose_window({'window_handle': handle, 'process_id': pid}, [_window_details(user, handle)])
    width, height = item['width'], item['height']

    class BitmapInfoHeader(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('width', wintypes.LONG), ('height', wintypes.LONG),
                    ('planes', wintypes.WORD), ('bits', wintypes.WORD), ('compression', wintypes.DWORD),
                    ('image_size', wintypes.DWORD), ('x_pixels', wintypes.LONG), ('y_pixels', wintypes.LONG),
                    ('colors_used', wintypes.DWORD), ('colors_important', wintypes.DWORD)]

    header = BitmapInfoHeader()
    header.size, header.width, header.height, header.planes, header.bits = ctypes.sizeof(header), width, -height, 1, 32
    header.image_size = width * height * 4
    screen_dc = memory_dc = bitmap = old_object = None
    try:
        with _physical_coordinates(user):
            screen_dc = user.GetDC(handle)  # Not GetDC(NULL): desktop capture is deliberately absent.
            if not screen_dc:
                raise VideoError('目标窗口绘图上下文不可用。')
            memory_dc = gdi.CreateCompatibleDC(screen_dc)
            pixels = ctypes.c_void_p()
            bitmap = gdi.CreateDIBSection(screen_dc, ctypes.byref(header), 0, ctypes.byref(pixels), None, 0)
            if not memory_dc or not bitmap or not pixels.value:
                raise VideoError('无法分配窗口截图缓冲区。')
            old_object = gdi.SelectObject(memory_dc, bitmap)
            if not old_object or old_object == ctypes.c_void_p(-1).value:
                raise VideoError('无法初始化窗口截图缓冲区。')
            ctypes.memset(pixels, 0, header.image_size)
            if not user.PrintWindow(handle, memory_dc, 2):
                raise VideoError('PrintWindow 拒绝目标窗口截图。')
            from PIL import Image
            image = Image.frombytes('RGB', (width, height), ctypes.string_at(pixels, header.image_size), 'raw', 'BGRX')
            if all(low == high for low, high in image.getextrema()):
                raise VideoError('目标窗口返回空白/纯色画面，拒绝作为成功截图。')
            after = _window_details(user, handle)
            if any(item[k] != after[k] for k in ('process_id', 'window_handle', 'window_title', 'width', 'height')) or after['minimized'] or not after['visible']:
                raise VideoError('目标窗口截图期间状态发生变化。')
            image.save(destination, format='PNG')
    finally:
        if old_object and old_object != ctypes.c_void_p(-1).value and memory_dc:
            gdi.SelectObject(memory_dc, old_object)
        if bitmap:
            gdi.DeleteObject(bitmap)
        if memory_dc:
            gdi.DeleteDC(memory_dc)
        if screen_dc:
            user.ReleaseDC(handle, screen_dc)
