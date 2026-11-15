# -*- coding: utf-8 -*-
"""
build_loc.py —— 从游戏本地化表里抽出存档界面需要的文本，存成小文件。

抽出内容：
  Bell_*        铃铛（存档点）显示名 —— 存档界面的大标题
  Levelname_*   关卡名
  UI_Setting_*  难度名
  Difficulty_*  难度名（若有）

产物：data/loc_zh.json / data/loc_en.json
游戏更新后重跑一次即可。
"""
import json
import sys
from pathlib import Path

REPO = Path(r"C:\Users\root\Documents\PBZ-Editor")
sys.path.insert(0, str(REPO))

from pbz_editor.core import OodleCodec
from pbz_editor.resources_reader import localization
from pbz_editor.pak_reader import PakReader

GAME = Path(r"D:\SteamLibrary\steamapps\common\PBZ")
PAK = GAME / "ProjectPBZ/Content/Paks/ProjectPBZ-Windows.pak"
OUT = Path(__file__).resolve().parents[1] / "data"

PREFIXES = ("Bell_", "Levelname_", "UI_Setting_", "UI_SelectDifficutly_",
            "UI_LoadGame_", "UI_Ending_", "UI_BonusCollection_",
            "Difficulty_", "Diffculty_", "Option_Difficulty", "UI_Difficulty")


def grab(loc):
    out = {}
    for k, v in loc.items():
        if not isinstance(v, str) or not v:
            continue
        if any(k.startswith(p) for p in PREFIXES):
            out[k] = v
    return out


def main():
    codec = OodleCodec()
    pak = PakReader(PAK, codec)
    for lang, name in (("zh-Hans", "loc_zh.json"), ("en-US", "loc_en.json")):
        try:
            loc = localization(pak.read_file(
                f"ProjectPBZ/Content/Localization/Game/{lang}/Game.locres"))
        except Exception as ex:
            print(f"  {lang} 读取失败: {ex}")
            continue
        sub = grab(loc)
        dst = OUT / name
        dst.write_text(json.dumps(sub, ensure_ascii=False, indent=0), encoding="utf-8")
        print(f"  {lang}: 全表 {len(loc)} 条 → 抽出 {len(sub)} 条 → {dst}")

    # 顺便看看难度关键字长什么样
    loc = localization(pak.read_file(
        "ProjectPBZ/Content/Localization/Game/zh-Hans/Game.locres"))
    print("\n=== UI_Setting_* 全部条目 ===")
    for k in sorted(x for x in loc if x.startswith("UI_Setting_")):
        print(f"  {k} = {loc[k]}")
    print("\n=== Bell_* 数量 ===")
    bells = [k for k in loc if k.startswith("Bell_")]
    print(f"  {len(bells)} 条")
    print("\n=== Levelname_* 数量 ===")
    lv = [k for k in loc if k.startswith("Levelname_")]
    print(f"  {len(lv)} 条")


if __name__ == "__main__":
    main()
