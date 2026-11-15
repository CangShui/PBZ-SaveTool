# -*- coding: utf-8 -*-
"""构建后处理：把 exe 的 PE 子系统改成 GUI(3)，彻底杜绝控制台窗口。

PyInstaller 打出的单文件 exe，即便带 --noconsole，PE 头里 Subsystem 仍可能是 2。
这里直接改写 PE 可选头里的 Subsystem 字段，保证双击只出 GUI。
"""
import struct
import sys
from pathlib import Path

SUBSYSTEM_GUI = 3


def patch(path: Path) -> int:
    data = bytearray(path.read_bytes())
    if data[:2] != b"MZ":
        raise SystemExit(f"{path} 不是 PE 文件")
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        raise SystemExit("PE 签名无效")
    magic = struct.unpack_from("<H", data, pe + 24)[0]
    if magic not in (0x10B, 0x20B):
        raise SystemExit(f"未知的可选头 magic {magic:#x}")
    off = pe + 24 + 68          # Subsystem 在 PE32 / PE32+ 都在可选头偏移 0x44
    old = struct.unpack_from("<H", data, off)[0]
    if old == SUBSYSTEM_GUI:
        print(f"   Subsystem 已是 {old}（GUI），无需修改")
        return old
    struct.pack_into("<H", data, off, SUBSYSTEM_GUI)
    path.write_bytes(data)
    print(f"   Subsystem {old} -> {SUBSYSTEM_GUI}（GUI）")
    return old


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("Release/PBZSaveTool.exe")
    if not target.is_file():
        raise SystemExit(f"找不到 {target}")
    patch(target)
