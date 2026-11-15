# -*- coding: utf-8 -*-
"""
从 PBZ.exe 提取 PBZS 存档的 AES-256 密钥，并解析存档目录。

密钥算法（逆向自 PBZ-Editor 项目，MIT）：
    在 exe 里找 UTF-16LE 字符串 "sealed but block is PLAINTEXT\\0\\0"，
    从锚点后 0~15 字节的某个位置取 64 字节，
    前 32 字节 XOR 后 32 字节 => 32 字节 AES 密钥。
    用 AES-GCM 解存档目录，tag 通过即确认。
"""
import mmap
import struct
import sys
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

EXE = r"D:\SteamLibrary\steamapps\common\PBZ\platform\steam\PBZ.exe"
SAVE = r"D:\SteamLibrary\steamapps\common\PBZ\ProjectPBZ\Saved\SaveGames\Steam\76561198792977447\PBZSaveGame.sav"

ANCHOR = "sealed but block is PLAINTEXT".encode("utf-16-le") + b"\0\0"

save = open(SAVE, "rb").read()
print(f"存档 {len(save)} 字节")
if save[:4] != b"PBZS":
    sys.exit("不是 PBZS")
ver, flags, count, dsize = struct.unpack_from("<BBHI", save, 4)
print(f"版本={ver} flags={flags} 槽数={count} 目录长度={dsize}")

dir_block = save[12:12 + dsize]
print(f"目录块前 8 字节: {dir_block[:8].hex()}")

def try_key(key):
    if len(key) != 32:
        return None
    try:
        clear = AESGCM(key).decrypt(dir_block[8:20], dir_block[36:] + dir_block[20:36], None)
    except Exception:
        return None
    if clear[:12] == save[:12]:
        return clear
    return None

print(f"\n在 exe 里搜索锚点…")
found = None
with open(EXE, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as m:
    pos = 0
    n_anchor = 0
    while True:
        i = m.find(ANCHOR, pos)
        if i < 0:
            break
        n_anchor += 1
        pos = i + len(ANCHOR)
        for pad in range(16):
            off = pos + pad
            material = m[off:off + 64]
            if len(material) < 64:
                continue
            key = bytes(a ^ b for a, b in zip(material[:32], material[32:]))
            clear = try_key(key)
            if clear:
                found = (key, i, off, material)
                break
        if found:
            break
    print(f"  锚点出现 {n_anchor} 次")

if not found:
    sys.exit("没找到能通过校验的密钥")

key, anchor_off, material_off, material = found
print(f"\n✅ 密钥确认")
print(f"  锚点文件偏移 : {anchor_off:#x}")
print(f"  取材偏移     : {material_off:#x}  (锚点后 {material_off-anchor_off-len(ANCHOR)} 字节)")
print(f"  KEY          : {key.hex()}")
print(f"  material sha256: {__import__('hashlib').sha256(material).hexdigest()}")

# 解析目录
print(f"\n=== 目录内容 ===")
entries = []
cursor = 12
for k in range(count):
    nlen = struct.unpack_from("<H", clear, cursor)[0]
    cursor += 2
    name = clear[cursor:cursor + nlen].decode("utf-8")
    cursor += nlen
    algo, flag, size, offset, block_size = struct.unpack_from("<BBIII", clear, cursor)
    cursor += 14
    entries.append((name, algo, flag, size, offset, block_size))
    algo_name = {0: "原样", 1: "zlib", 2: "Oodle"}.get(algo, f"?{algo}")
    print(f"  {name:32s} 算法={algo_name:6s} 原长={size:8d} 偏移={offset:8d} 块长={block_size:8d}")

open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pbz_key.txt"), "w").write(key.hex())
print(f"\n密钥已存 pbz_key.txt")
