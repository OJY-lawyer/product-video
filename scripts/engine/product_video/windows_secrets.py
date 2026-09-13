"""Current-user DPAPI protection; no plaintext credential files on Windows."""
import ctypes
from ctypes import wintypes

from .common import VideoError


class DataBlob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def crypt(data, *, decrypt=False):
    crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    storage = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source = DataBlob(len(data), storage)
    result = DataBlob()
    operation = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    operation.argtypes = [ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob)]
    operation.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    # CRYPTPROTECT_UI_FORBIDDEN; user scope, never machine-wide encryption.
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise VideoError('Windows 当前用户凭据保护失败；请在同一用户会话中重新配置。')
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel32.LocalFree(result.data)
