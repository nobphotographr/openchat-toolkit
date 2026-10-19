"""Read-only Windows session diagnostic. Does not lock/unlock or change settings."""
import ctypes
from ctypes import wintypes as w
import json
import os

def probe():
    if os.name != "nt":
        return {"ready": False, "reason": "windows_required"}
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user = ctypes.WinDLL("user32", use_last_error=True)
    wts = ctypes.WinDLL("wtsapi32", use_last_error=True)
    session_id = w.DWORD()
    kernel.ProcessIdToSessionId.argtypes = [w.DWORD, ctypes.POINTER(w.DWORD)]
    kernel.ProcessIdToSessionId.restype = w.BOOL
    if not kernel.ProcessIdToSessionId(os.getpid(), ctypes.byref(session_id)):
        return {"ready": False, "reason": "session_id_unavailable"}
    wts.WTSQuerySessionInformationW.argtypes = [w.HANDLE, w.DWORD, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(w.DWORD)]
    wts.WTSQuerySessionInformationW.restype = w.BOOL
    wts.WTSFreeMemory.argtypes = [ctypes.c_void_p]
    data, size = ctypes.c_void_p(), w.DWORD()
    if not wts.WTSQuerySessionInformationW(None, session_id.value, 8, ctypes.byref(data), ctypes.byref(size)):
        return {"ready": False, "reason": "session_state_unavailable"}
    try:
        state = ctypes.cast(data, ctypes.POINTER(ctypes.c_int))[0]
    finally:
        wts.WTSFreeMemory(data)
    if state != 0:
        return {"ready": False, "reason": "session_not_active", "wts_state": state}
    user.OpenInputDesktop.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    user.OpenInputDesktop.restype = w.HANDLE
    user.CloseDesktop.argtypes = [w.HANDLE]
    user.GetUserObjectInformationW.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)]
    user.GetUserObjectInformationW.restype = w.BOOL
    desktop = user.OpenInputDesktop(0, False, 1)  # DESKTOP_READOBJECTS only
    if not desktop:
        return {"ready": False, "reason": "input_desktop_unavailable"}
    try:
        name = ctypes.create_unicode_buffer(256)
        needed = w.DWORD()
        if not user.GetUserObjectInformationW(desktop, 2, name, ctypes.sizeof(name), ctypes.byref(needed)):
            return {"ready": False, "reason": "desktop_name_unavailable"}
        return {"ready": name.value == "Default",
                "reason": "active_default_desktop" if name.value == "Default" else "secure_or_other_desktop",
                "note": "Session prerequisite only; does not establish LINE login or room identity."}
    finally:
        user.CloseDesktop(desktop)

if __name__ == "__main__":
    print(json.dumps(probe(), ensure_ascii=False, indent=2))
