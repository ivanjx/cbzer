import ctypes
import os
import sys


class _Timestamp(ctypes.Structure):
    _fields_ = [
        ("seconds", ctypes.c_int64),
        ("nanoseconds", ctypes.c_uint32),
        ("reserved", ctypes.c_int32),
    ]


class _Statx(ctypes.Structure):
    # Linux statx has a stable 256-byte ABI; birth time starts at byte 80.
    # https://man7.org/linux/man-pages/man2/statx.2.html
    _fields_ = [
        ("mask", ctypes.c_uint32),
        ("block_size", ctypes.c_uint32),
        ("attributes", ctypes.c_uint64),
        ("links", ctypes.c_uint32),
        ("uid", ctypes.c_uint32),
        ("gid", ctypes.c_uint32),
        ("mode", ctypes.c_uint16),
        ("reserved", ctypes.c_uint16),
        ("inode", ctypes.c_uint64),
        ("size", ctypes.c_uint64),
        ("blocks", ctypes.c_uint64),
        ("attributes_mask", ctypes.c_uint64),
        ("access_time", _Timestamp),
        ("birth_time", _Timestamp),
        ("remaining", ctypes.c_ubyte * 160),
    ]


_statx = getattr(ctypes.CDLL(None), "statx", None) if sys.platform == "linux" else None
if _statx is not None:
    _statx.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_uint, ctypes.POINTER(_Statx)]
    _statx.restype = ctypes.c_int


def folder_creation_time(entry: os.DirEntry) -> float | None:
    if sys.platform != "linux":
        return getattr(entry.stat(follow_symlinks=False), "st_birthtime", None)
    if _statx is None:
        return None

    result = _Statx()
    # AT_FDCWD, AT_SYMLINK_NOFOLLOW | AT_NO_AUTOMOUNT, STATX_BTIME.
    status = _statx(-100, os.fsencode(entry.path), 0x900, 0x800, ctypes.byref(result))
    if status != 0 or not result.mask & 0x800:
        return None
    return result.birth_time.seconds + result.birth_time.nanoseconds / 1_000_000_000
