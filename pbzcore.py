# -*- coding: utf-8 -*-
"""
PBZS 容器完整实现（解码 / 重编码），带 Oodle 支持。

格式（逆向确认）：
    文件头 12 字节: "PBZS" | u8 ver=3 | u8 flags=1 | u16 槽数 | u32 目录字节数
    密封目录:        data[12 : 12+目录字节数]
    数据区:          data[12+目录字节数 : ]
    每槽:            u16 名长 | 名称(utf8) | u8 算法 | u8 flag=1 | u32 原长 | u32 偏移 | u32 块长

    密封块 = "PBZS\\x01\\x01\\x00\\x00"(8) + nonce(12) + tag(16) + 密文
    解密   = AESGCM(key).decrypt(nonce, 密文+tag, None)

    算法: 0=原样  1=zlib  2=Oodle
"""
import ctypes
import hashlib
import json
import os
import struct
import zlib
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

HERE = Path(__file__).resolve().parent
KEY_FILE = HERE / "pbz_key.txt"
NATIVE = HERE / "native"

SLOT_HEAD = b"PBZS\x01\x01\x00\x00"


class Oodle:
    """oozextract 编译出的 pbz_codec.dll，只负责解 Oodle。"""

    def __init__(self, directory=NATIVE):
        meta = json.loads((Path(directory) / "manifest.json").read_text(encoding="utf-8-sig"))
        self.path = Path(directory) / meta["library"]
        if hashlib.sha256(self.path.read_bytes()).hexdigest() != meta["sha256"]:
            raise RuntimeError("pbz_codec.dll 校验失败")
        self.dll = ctypes.CDLL(str(self.path.resolve()))
        self.dll.pbz_codec_version.restype = ctypes.c_uint32
        if self.dll.pbz_codec_version() != 1:
            raise RuntimeError("codec ABI 不符")
        self.fn = self.dll.pbz_decode
        self.fn.restype = ctypes.c_ssize_t
        self.fn.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t]

    def decompress(self, data: bytes, size: int) -> bytes:
        if size == 0:
            return b""
        out = ctypes.create_string_buffer(size)
        n = self.fn(data, len(data), out, size)
        if n != size:
            raise RuntimeError(f"Oodle 解压失败 ({n} != {size})")
        return out.raw

    @staticmethod
    def compress_stored(data: bytes) -> bytes:
        """
        不依赖官方编码器：拼 Oodle 的「存储块」——\xcc\x06 头 + 原样数据。
        游戏自带的解码器能正常读出。块上限 0x40000。
        """
        return b"".join(b"\xcc\x06" + data[p:p + 0x40000] for p in range(0, len(data), 0x40000))


class PBZS:
    def __init__(self, key: bytes, oodle: Oodle | None = None):
        if len(key) != 32:
            raise ValueError("key 必须 32 字节")
        self.key = key
        self.oodle = oodle

    # ── 密封 ──
    def unseal(self, block: bytes) -> bytes:
        if len(block) < 36 or block[:8] != SLOT_HEAD:
            raise ValueError("密封块头无效")
        return AESGCM(self.key).decrypt(block[8:20], block[36:] + block[20:36], None)

    def seal(self, clear: bytes) -> bytes:
        nonce = os.urandom(12)
        c = AESGCM(self.key).encrypt(nonce, clear, None)
        return SLOT_HEAD + nonce + c[-16:] + c[:-16]

    # ── 解压 ──
    def decompress(self, data: bytes, algo: int, size: int) -> bytes:
        if algo == 0:
            raw = data
        elif algo == 1:
            raw = zlib.decompress(data)
        elif algo == 2:
            if not self.oodle:
                raise RuntimeError("需要 Oodle 解码库")
            raw = self.oodle.decompress(data, size)
        else:
            raise ValueError(f"未知算法 {algo}")
        if len(raw) != size:
            raise ValueError(f"解压长度不符 {len(raw)} != {size}")
        return raw

    def compress(self, data: bytes, algo: int) -> bytes:
        if algo == 0:
            return data
        if algo == 1:
            return zlib.compress(data)
        if algo == 2:
            out = Oodle.compress_stored(data)
            if self.oodle and self.oodle.decompress(out, len(data)) != data:
                raise RuntimeError("Oodle 存储块往返校验失败")
            return out
        raise ValueError(f"未知算法 {algo}")

    # ── 解析 / 重建 ──
    def decode(self, data: bytes) -> list:
        if data[:4] != b"PBZS":
            raise ValueError("不是 PBZS")
        ver, flags, count, dsize = struct.unpack_from("<BBHI", data, 4)
        directory = self.unseal(data[12:12 + dsize])
        if directory[:12] != data[:12]:
            raise ValueError("目录头副本校验失败")
        cursor, entries, end = 12, [], 0
        for _ in range(count):
            nlen = struct.unpack_from("<H", directory, cursor)[0]
            cursor += 2
            name = directory[cursor:cursor + nlen].decode("utf-8")
            cursor += nlen
            algo, flag, size, offset, block_size = struct.unpack_from("<BBIII", directory, cursor)
            cursor += 14
            block = data[12 + dsize + offset: 12 + dsize + offset + block_size]
            raw = self.decompress(self.unseal(block), algo, size)
            entries.append({"name": name, "algo": algo, "flag": flag, "raw": raw,
                            "json": json.loads(raw), "block": block,
                            "size": size, "offset": offset, "block_size": block_size})
            end = offset + block_size
        if 12 + dsize + end != len(data):
            raise ValueError("尾部有未使用字节")
        return entries

    def encode(self, entries, replacements: dict, keep_algo=True) -> bytes:
        rows, chunks, offset = [], [], 0
        for e in entries:
            name = e["name"].encode("utf-8")
            algo, raw, block = e["algo"], e["raw"], e["block"]
            if e["name"] in replacements:
                raw = replacements[e["name"]]
                json.loads(raw)                       # 确保仍是合法 JSON
                if not keep_algo:
                    algo = 0
                block = self.seal(self.compress(raw, algo))
            rows.append(struct.pack("<H", len(name)) + name +
                        struct.pack("<BBIII", algo, 1, len(raw), offset, len(block)))
            chunks.append(block)
            offset += len(block)
        dsize = 12 + sum(len(r) for r in rows) + 36
        header = b"PBZS" + struct.pack("<BBHI", 3, 1, len(entries), dsize)
        return header + self.seal(header + b"".join(rows)) + b"".join(chunks)


def load_key() -> bytes:
    return bytes.fromhex(KEY_FILE.read_text().strip())


def open_save(path, with_oodle=True):
    key = load_key()
    od = Oodle() if with_oodle else None
    c = PBZS(key, od)
    data = open(path, "rb").read()
    return c, data, c.decode(data)
