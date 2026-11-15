# -*- coding: utf-8 -*-
"""
build_collect.py —— 生成收集相关的全集清单 data/collect.json

  codex_enemies : DT_CodexEnemyDataTable 的 176 行行名 → 解析成蓝图类路径
  bonus_art     : DT_BonusArt 全部行名
  bonus_music   : DT_BonusMusic 全部行名

特典解锁用的 gameplay tag（achievment.* / zlgw.* 之类）是烘焙进包里的，
游戏目录里也没有 ini，枚举不出来，所以不在这个文件里。
"""
import json
import struct
import sys
from pathlib import Path

REPO = Path(r"C:\Users\root\Documents\PBZ-Editor")
sys.path.insert(0, str(REPO))
from pbz_editor.core import OodleCodec
from pbz_editor.resources_reader import IoStore
from pbz_editor.catalog_builder import name_batch

GAME = Path(r"D:\SteamLibrary\steamapps\common\PBZ")
PAKS = GAME / "ProjectPBZ/Content/Paks"
OUT = Path(__file__).resolve().parents[1] / "data" / "collect.json"

TABLES = {
    "codex_enemies": "ProjectPBZ/Content/PBZContent/DataTable/DT_CodexEnemyDataTable.uasset",
    "bonus_art": "ProjectPBZ/Content/PBZContent/DataTable/UI/DT_BonusArt.uasset",
    "bonus_music": "ProjectPBZ/Content/PBZContent/DataTable/UI/DT_BonusMusic.uasset",
}


def row_names(data):
    """宽松版：允许解出行数比声明少 1。返回行名列表。"""
    names, _ = name_batch(data)
    header = struct.unpack_from("<I", data, 4)[0]
    start = header + 14
    schema = data[start + 8:start + 10]
    starts = []
    for p in range(start, len(data) - 12):
        index, num = struct.unpack_from("<II", data, p)
        if index < len(names) and num == 0 and data[p + 8:p + 10] == schema:
            starts.append(p)
    out = []
    for p in starts:
        idx, num = struct.unpack_from("<II", data, p)
        out.append(names[idx] + (f"_{num - 1}" if num else ""))
    return out


def to_mount_path(pkg: str) -> str:
    """
    把 pak 里的资源路径转成存档里用的挂载点形式：
      ProjectPBZ/Plugins/Secret/PBZCombatAsset/Content/X  ->  /PBZCombatAsset/X
      ProjectPBZ/Plugins/<Name>/Content/X                 ->  /<Name>/X
      ProjectPBZ/Content/X                                ->  /Game/X
      Engine/Content/X                                    ->  /Engine/X
    """
    p = pkg
    for pre in ("ProjectPBZ/Plugins/Secret/", "ProjectPBZ/Plugins/"):
        if p.startswith(pre):
            rest = p[len(pre):]
            plugin, _, tail = rest.partition("/")
            if tail.startswith("Content/"):
                tail = tail[len("Content/"):]
            return f"/{plugin}/{tail}"
    if p.startswith("ProjectPBZ/Content/"):
        return "/Game/" + p[len("ProjectPBZ/Content/"):]
    if p.startswith("Engine/Content/"):
        return "/Engine/" + p[len("Engine/Content/"):]
    return "/" + p


def main():
    codec = OodleCodec()
    store = IoStore(PAKS / "ProjectPBZ-Windows.utoc", codec)
    lookup = {p.casefold(): p for p in store.files}
    by_base = {}
    for p in store.files:
        if p.endswith(".uasset"):
            base = p.rsplit("/", 1)[-1][: -len(".uasset")]
            by_base.setdefault(base, []).append(p)

    result = {}
    for key, path in TABLES.items():
        data = store.read_file(lookup[path.casefold()])
        names = row_names(data)
        result[key] = names
        print(f"  {key}: {len(names)} 行")

    # 行名 → 类路径
    enemies, miss = [], []
    for rn in result["codex_enemies"]:
        base = rn.rsplit("_", 1)[0] if rn.endswith(tuple(f"_{i}" for i in range(10))) else rn
        cands = by_base.get(rn) or by_base.get(base)
        if not cands:
            miss.append(rn)
            continue
        # 优先 PBZCombatAsset（存档里已发现敌人多数在这个根下）
        cands.sort(key=lambda p: (0 if "PBZCombatAsset" in p else 1, len(p)))
        pkg = cands[0][: -len(".uasset")]
        name = pkg.rsplit("/", 1)[-1]
        enemies.append(f"{to_mount_path(pkg)}.{name}_C")
    print(f"  敌人类路径: {len(enemies)} 个，未找到 {len(miss)} 个")
    for m in miss[:6]:
        print(f"    缺: {m}")
    result["codex_enemy_paths"] = enemies

    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n已写入 {OUT}  ({OUT.stat().st_size/1024:.1f} KB)")
    print("样例:")
    for e in enemies[:4]:
        print("   ", e)


if __name__ == "__main__":
    main()
