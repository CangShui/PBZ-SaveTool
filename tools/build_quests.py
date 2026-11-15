# -*- coding: utf-8 -*-
"""
build_quests.py —— 从游戏任务资源生成完整任务表 data/quests.json

  1. 遍历 pak 里 PBZstory/PBZQuest/<类别>/*.uasset
  2. 在资源字节里找 Quest* 形式的本地化 key
  3. 排除 Des / Details；遇到 SubTitle 就把它去掉当标题 key 用
     （主线资源里只存 QuestStory_SubTitle_01_3，标题其实是 QuestStory_01_3）
  4. 用 Game.locres（zh-Hans）查出中文名
  5. 主线补上章节归属和顺序
"""
import json
import re
import sys
import collections
from pathlib import Path

REPO = Path(r"C:\Users\root\Documents\PBZ-Editor")
sys.path.insert(0, str(REPO))
from pbz_editor.core import OodleCodec
from pbz_editor.resources_reader import IoStore, localization
from pbz_editor.pak_reader import PakReader

GAME = Path(r"D:\SteamLibrary\steamapps\common\PBZ")
PAKS = GAME / "ProjectPBZ/Content/Paks"
OUT = Path(__file__).resolve().parents[1] / "data" / "quests.json"

CATS = ["Main", "Branch", "Collect", "Tutorial", "Simple"]
CAT_ZH = {"Main": "主线", "Branch": "支线", "Collect": "收集",
          "Tutorial": "教程", "Simple": "传闻"}

# 主线顺序与章节（人工核对：QuestStory_01_1..01_4 / 02_1..02_3 / 03_*）
MAIN_ORDER = [
    ("MainCh1-NiLiuErShang", 1, "第一章 逆流而上"),
    ("MainCh1-LinZhongShaJi", 2, "第一章 逆流而上"),
    ("MainCh1-ShiXinCun", 3, "第一章 逆流而上"),
    ("MainCh1-2-GangTieJuTa", 4, "第一章 逆流而上"),
    ("MainCh2-SiZhen", 5, "第二章 林中血，镜中蛇"),
    ("MainCh2-SiDaSheWu", 6, "第二章 林中血，镜中蛇"),
    ("MainCh2-XinZhiZongJi", 7, "第二章 林中血，镜中蛇"),
    ("MainCh3-DieXueZhiLu", 8, "第三章 英雄？罪人？"),
    ("MainCh3-LongYuZuo", 9, "第三章 英雄？罪人？"),
    ("MainCh3-PoJu", 10, "第三章 英雄？罪人？"),
]

KEY = re.compile(rb"Quest[A-Za-z]*(?:_[A-Za-z]+)*_\d{1,3}(?:_\d{1,3})?")


def keys_from(data):
    raw = []
    for m in KEY.finditer(data):
        k = m.group().decode("ascii", "ignore")
        if "Des" in k or "Details" in k:
            continue
        if k not in raw:
            raw.append(k)
    # SubTitle -> 标题
    titles, subs = [], []
    for k in raw:
        if "SubTitle" in k:
            subs.append(k.replace("_SubTitle", ""))
        else:
            titles.append(k)
    return list(dict.fromkeys(titles + subs))


def main():
    codec = OodleCodec()
    pak = PakReader(PAKS / "ProjectPBZ-Windows.pak", codec)
    loc = localization(pak.read_file(
        "ProjectPBZ/Content/Localization/Game/zh-Hans/Game.locres"))
    store = IoStore(PAKS / "ProjectPBZ-Windows.utoc", codec)
    lookup = {p.casefold(): p for p in store.files}

    order_map = {q: (o, c) for q, o, c in MAIN_ORDER}

    out, miss = [], []
    for cat in CATS:
        prefix = f"ProjectPBZ/Content/PBZstory/PBZQuest/{cat}/"
        names = [p for p in store.files
                 if p.startswith(prefix) and p.endswith(".uasset")
                 and "/" not in p[len(prefix):]]
        for path in sorted(names):
            qid = path[len(prefix):-len(".uasset")]
            keys = keys_from(store.read_file(lookup[path.casefold()]))
            title = next((loc[k] for k in keys if loc.get(k)), "")
            if not title:
                miss.append((cat, qid, keys[:4]))
            order, chapter = order_map.get(qid, (999, ""))
            out.append({"category": cat, "category_zh": CAT_ZH.get(cat, cat),
                        "id": qid, "title": title, "order": order,
                        "chapter": chapter, "loc_keys": keys[:4],
                        "path": f"/Game/PBZstory/PBZQuest/{cat}/{qid}.{qid}_C"})

    out.sort(key=lambda x: (CATS.index(x["category"]), x["order"], x["id"]))
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"共 {len(out)} 个任务 → {OUT}")
    print("分类:", dict(collections.Counter(x["category"] for x in out)))
    print(f"取到中文名 {sum(1 for x in out if x['title'])}/{len(out)}")
    if miss:
        print(f"\n没名字的 {len(miss)} 个：")
        for cat, qid, keys in miss[:25]:
            print(f"  {cat:9s} {qid:28s} keys={keys}")
    print("\n主线：")
    for x in out:
        if x["category"] == "Main":
            print(f"  {x['order']:2d} {x['chapter']:18s} {x['id']:24s} {x['title']}")


if __name__ == "__main__":
    main()
