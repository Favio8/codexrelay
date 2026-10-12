"""Windows generic credentials; secrets never enter the settings database."""
import ctypes
import os
from ctypes import wintypes


class Credential(ctypes.Structure):
    _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]


class Vault:
    def __init__(self):
        if os.name != "nt":
            raise RuntimeError("此版本需要 Windows 凭据管理器")
        self.api = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        self.api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(Credential))]
        self.api.CredReadW.restype = wintypes.BOOL
        self.api.CredWriteW.argtypes = [ctypes.POINTER(Credential), wintypes.DWORD]
        self.api.CredWriteW.restype = wintypes.BOOL
        self.api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self.api.CredDeleteW.restype = wintypes.BOOL
        self.api.CredFree.argtypes = [ctypes.c_void_p]

    def get(self, identifier):
        pointer = ctypes.POINTER(Credential)()
        if not self.api.CredReadW("CodexRelay/" + identifier, 1, 0, ctypes.byref(pointer)):
            if ctypes.get_last_error() == 1168:
                return ""
            raise RuntimeError("无法读取 Windows 凭据")
        try:
            return ctypes.string_at(pointer.contents.CredentialBlob, pointer.contents.CredentialBlobSize).decode("utf-8")
        finally:
            self.api.CredFree(pointer)

    def set(self, identifier, secret):
        encoded = secret.encode("utf-8")
        if len(encoded) > 2560:
            raise ValueError("API Key 超过凭据存储长度限制")
        blob = (ctypes.c_ubyte * len(encoded))(*encoded)
        cred = Credential(Type=1, TargetName="CodexRelay/" + identifier,
                          CredentialBlobSize=len(encoded), CredentialBlob=blob,
                          Persist=2, UserName="CodexRelay")
        if not self.api.CredWriteW(ctypes.byref(cred), 0):
            raise RuntimeError("无法保存 Windows 凭据")

    def delete(self, identifier):
        if not self.api.CredDeleteW("CodexRelay/" + identifier, 1, 0) and ctypes.get_last_error() != 1168:
            raise RuntimeError("无法删除 Windows 凭据")
