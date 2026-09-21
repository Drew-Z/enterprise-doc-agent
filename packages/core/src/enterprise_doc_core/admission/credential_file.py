from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from enterprise_doc_core.admission.contracts import PreparedAdmissionCredential, credential_digest


def validate_credential_path(path: Path) -> None:
    if not path.is_absolute() or not path.parent.is_dir():
        raise OSError("credential_file_requires_existing_absolute_parent")
    if os.name == "nt" and (
        path.drive.startswith("\\")
        or str(path).startswith(("\\\\?\\", "\\\\.\\"))
        or path.is_reserved()
        or any(":" in part or part.rstrip(" .") != part for part in path.parts[1:])
    ):
        raise OSError("credential_file_requires_regular_local_path")


def write_private_credential(path: Path, credential: PreparedAdmissionCredential) -> None:
    """Create once with private permissions before writing any secret bytes.

    Failed/partial files are deliberately retained: the caller must not confuse a
    filesystem failure with a confirmed database rollback or overwrite a credential.
    """
    validate_credential_path(path)
    credential_digest(credential.token)
    payload = json.dumps(
        {
            "format": "tenant-admission/v1",
            "state": "prepared",
            "grantId": str(credential.grant_id),
            "token": credential.token.get_secret_value(),
        },
        separators=(",", ":"),
    ).encode("utf-8")
    descriptor = (
        _windows_private_descriptor(path)
        if os.name == "nt"
        else os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    )
    with os.fdopen(descriptor, "wb") as output:
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())


def _windows_private_descriptor(path: Path) -> int:
    if sys.platform == "win32":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        class SecurityAttributes(ctypes.Structure):
            _fields_ = [
                ("length", wintypes.DWORD),
                ("descriptor", wintypes.LPVOID),
                ("inherit", wintypes.BOOL),
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        security = ctypes.WinDLL("advapi32", use_last_error=True)
        convert = security.ConvertStringSecurityDescriptorToSecurityDescriptorW
        convert.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.LPVOID),
            ctypes.POINTER(wintypes.DWORD),
        ]
        convert.restype = wintypes.BOOL
        create = kernel.CreateFileW
        create.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(SecurityAttributes),
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        create.restype = wintypes.HANDLE
        kernel.LocalFree.argtypes = [wintypes.LPVOID]
        kernel.LocalFree.restype = wintypes.LPVOID
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        volume_information = kernel.GetVolumeInformationByHandleW
        volume_information.argtypes = [
            wintypes.HANDLE,
            wintypes.LPWSTR,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPWSTR,
            wintypes.DWORD,
        ]
        volume_information.restype = wintypes.BOOL
        descriptor = wintypes.LPVOID()
        # Protected DACL: only OWNER RIGHTS has access. chmod(0600) does not provide
        # this guarantee on Windows, and adding ACLs after writing would race readers.
        if not convert("D:P(A;;FA;;;OW)", 1, ctypes.byref(descriptor), None):
            raise OSError("credential_file_private_acl_failed")
        try:
            attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, False)
            handle = create(str(path), 0x40000000, 0, ctypes.byref(attributes), 1, 0x80, None)
        finally:
            kernel.LocalFree(descriptor)
        if handle == ctypes.c_void_p(-1).value:
            raise OSError("credential_file_exclusive_create_failed")
        try:
            flags = wintypes.DWORD()
            if (
                not volume_information(handle, None, 0, None, None, ctypes.byref(flags), None, 0)
                or not flags.value & 0x00000008  # FILE_PERSISTENT_ACLS
            ):
                raise OSError("credential_file_requires_persistent_acls")
            return msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
        except BaseException:
            kernel.CloseHandle(handle)
            raise
    else:
        raise OSError("credential_file_windows_only")
