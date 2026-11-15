# -*- coding: utf-8 -*-
"""
editor.py —— 《影之刃零》存档修改核心逻辑

三个功能：
    skills       一键升级全部技能（两套能力树共 64 个节点全部点亮）
    bells_map    一键开当前小地图的全部铃铛（按当前所在地的铃铛分组）
    bells_world  一键开大地图的全部铃铛（13 个区、194 个铃铛 + 世界地图遮罩）

GUI(gui.py) 和命令行共用这里的函数。
"""
from __future__ import annotations

import collections
import copy
import datetime
import json
import os
import re
import shutil
import struct
import subprocess
from pathlib import Path

import pbzcore

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
DEFAULT_SAVE = Path(
    r"D:\SteamLibrary\steamapps\common\PBZ\ProjectPBZ\Saved\SaveGames"
    r"\Steam\76561198792977447\PBZSaveGame.sav"
)
SAVE_SLOT_PREFIX = ("AutoSave", "ManualSave")
NM_PREFIX = "NightmareRun"          # 二周目（六十六天）存档
INTERNAL_PREFIX = ("NightmareCycleStart", "NightmareLineStart")


def is_save_slot(name):
    """是不是「载入存档」界面里能看到的槽。"""
    return bool(name) and (name.startswith(SAVE_SLOT_PREFIX) or name.startswith(NM_PREFIX))

ACTION_LABELS = {
    "skills": "升级全部技能",
    "bells_map": "开当前小地图全部铃铛",
    "bells_world": "开大地图全部铃铛",
}


# ────────────────────── 基础 ──────────────────────
def game_running() -> bool:
    """
    用 Toolhelp32 枚举进程，判断 PBZ.exe 是否在跑。

    不用 tasklist 子进程：GUI 程序自己没有控制台，每次起 console 子进程
    Windows 都会给它分配一个 conhost，会闪控制台窗口。
    """
    import ctypes
    import ctypes.wintypes as w

    TH32CS_SNAPPROCESS = 0x00000002
    INVALID = ctypes.c_void_p(-1).value

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", w.DWORD),
            ("cntUsage", w.DWORD),
            ("th32ProcessID", w.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", w.DWORD),
            ("cntThreads", w.DWORD),
            ("th32ParentProcessID", w.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", w.DWORD),
            ("szExeFile", ctypes.c_char * 260),
        ]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = w.HANDLE
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID:
        return False
    try:
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(entry)
        if not k32.Process32First(snap, ctypes.byref(entry)):
            return False
        while True:
            if entry.szExeFile.lower() == b"pbz.exe":
                return True
            if not k32.Process32Next(snap, ctypes.byref(entry)):
                return False
    finally:
        k32.CloseHandle(snap)
    return False


def _json(name):
    p = DATA / name
    return json.loads(p.read_text(encoding="utf-8"))


def full_ranges(ids, cap=64):
    """把 ['Bell_X_001','Bell_X_005'] 补全成 X 下 001..005 的连续区间。"""
    BAD = ("UAID", "C_", "BP_", "SK_")
    groups = collections.defaultdict(int)
    for i in ids:
        if any(b in i for b in BAD):
            continue
        m = re.match(r"^(.*_)(\d{3})$", i)
        if m:
            groups[m.group(1)] = max(groups[m.group(1)], int(m.group(2)))
        else:
            groups[i] = 0
    out = set()
    for prefix, top in groups.items():
        if top == 0:
            out.add(prefix)
        elif top <= cap:
            for n in range(1, top + 1):
                out.add(f"{prefix}{n:03d}")
        else:
            out.update(x for x in ids if x.startswith(prefix))
    return sorted(out)


def all_bells():
    return full_ranges(_json("game_ids.json")["bells"])


def fog_by_code():
    return _json("fog_by_code.json")


# ────────────────────── 当前地图判定 ──────────────────────
def current_bell_prefix(js) -> tuple[str, str]:
    """
    推断当前所在地属于哪一组铃铛。
    返回 (铃铛前缀, 判定依据说明)。

    策略：
      1. CurrentLevelName（如 PBZ_Xigu_WP）→ 去壳后在 RecentlyInteractedBellIDByMap 里找
      2. 用 SaveBellID 的前缀（玩家上次存铃铛的位置，几乎必然在当前地图）
      3. 用 RecentlyInteractedBellID 的前缀
    """
    bm = js.get("BellManager", {})
    by_map = bm.get("RecentlyInteractedBellIDByMap") or {}

    level = str(js.get("PBZGameSystem", {}).get("CurrentLevelName") or "")
    m = re.match(r"^PBZ_(.+?)_WP$", level, re.I)
    key = m.group(1).lower() if m else ""
    if key:
        for name, bid in by_map.items():
            if name.lower() == key:
                g = re.match(r"Bell_([A-Z]+)_", bid or "")
                if g:
                    return g.group(1), f"当前地图 {name}（来自 CurrentLevelName）"

    for field, why in (("SaveBellID", "存档铃铛"), ("RecentlyInteractedBellID", "最近交互铃铛")):
        bid = bm.get(field) or ""
        g = re.match(r"Bell_([A-Z]+)_", bid)
        if g:
            return g.group(1), f"{why} {bid}"

    return "", "无法判定当前地图"


# ────────────────────── 三个功能 ──────────────────────
def act_skills(js, ctx) -> list[str]:
    out = []
    for key, val in js.items():
        if not key.startswith("AbilityNode") or not isinstance(val, dict):
            continue
        nodes = val.get("AbilityNodes")
        if not isinstance(nodes, dict):
            continue
        before = sum(1 for v in nodes.values() if v)
        for n in nodes:
            nodes[n] = 1
        out.append(f"{key}: {before}/{len(nodes)} → {len(nodes)}/{len(nodes)}")
    return out


def act_bells_map(js, ctx) -> list[str]:
    prefix, why = current_bell_prefix(js)
    if not prefix:
        return [f"跳过：{why}"]
    pool = [b for b in ctx["bells"] if b.startswith(f"Bell_{prefix}_")]
    bm = js.get("BellManager")
    if not isinstance(bm, dict) or not pool:
        return [f"跳过：没有匹配 {prefix} 的铃铛"]
    cur = bm.get("ActivateBellIDs")
    if not isinstance(cur, list):
        cur = []
    before = len(cur)
    bm["ActivateBellIDs"] = sorted(set(cur) | set(pool))
    return [f"判定依据：{why}",
            f"本区({prefix})铃铛 {len(pool)} 个",
            f"ActivateBellIDs: {before} → {len(bm['ActivateBellIDs'])} 个"]


def act_bells_world(js, ctx) -> list[str]:
    out = []
    bm = js.get("BellManager")
    if not isinstance(bm, dict):
        return ["跳过：没有 BellManager"]
    cur = bm.get("ActivateBellIDs")
    if not isinstance(cur, list):
        cur = []
    before = len(cur)
    bm["ActivateBellIDs"] = sorted(set(cur) | set(ctx["bells"]))
    out.append(f"ActivateBellIDs: {before} → {len(bm['ActivateBellIDs'])} 个")

    # 世界地图遮罩：区号 + 显示名别名全塞进去（无效名字游戏会忽略）
    ALIAS = {
        "XG": ["XiGu", "xg", "Xigu"],
        "XSSL": ["FuJiZhiLin", "XXSL", "xssl"],
        "FJZL": ["FuJiZhiLin_2", "fjzl"],
        "LJC": ["LuJiaCun", "ljc"],
        "MGH": ["MeiGuHu", "Meiguhu", "mgh"],
        "PZ": ["PangZhen", "Pangzhen", "pz"],
        "DDSD": ["DengDingShanDao", "ddsd"],
        "DH": ["DuanHunYa", "DuanHun"],
        "ZDDY": ["ZhengDaoDaYing", "Zhengdaodaying", "zddy"],
        "HJ": ["HouJie"],
        "XMSZ": ["XiMengShanZhuang", "XiMeng", "ximengshanzhuang"],
    }
    names = set()
    for code, al in ALIAS.items():
        names.add(code)
        names.update(al)

    for field in ("UnLockBigMapMaskWidgetNames", "NeedUnLockBigMapMaskWidgetNames"):
        cur = bm.get(field)
        if not isinstance(cur, list):
            cur = []
        b = len(cur)
        bm[field] = sorted(set(cur) | names)
        out.append(f"{field}: {b} → {len(bm[field])} 个")

    # 迷雾格按区号补全
    ms = js.get("MapSubsystem")
    if isinstance(ms, dict):
        infos = ms.setdefault("UnLockMaskInfos", {})
        new = 0
        for code, ids in ctx["fog"].items():
            if not ids:
                continue
            for k in [code] + ALIAS.get(code, []):
                cur = infos.get(k)
                if not isinstance(cur, dict):
                    infos[k] = {"0": list(ids)}
                    new += 1
                else:
                    for lvl in list(cur):
                        cur[lvl] = sorted(set(cur[lvl]) | set(ids))
        out.append(f"UnLockMaskInfos: 新增 {new} 个区域，共 {len(infos)} 个")
    return out


ACTIONS = {
    "skills": act_skills,
    "bells_map": act_bells_map,
    "bells_world": act_bells_world,
    "weapons_max": None,     # 下面注册
    "obtain_weapons": None,
    "obtain_items": None,
    "quest_one": None,
    "quests_all": None,
}


# ────────────────────── 物品目录（由游戏 DataTable 生成）──────────────────────
CATALOG_PATH = DATA / "catalog" / "catalog.json"
CATEGORY_FIELD = {
    "goods": "InventoryInfos",
    "weapon": "MainWeaponInfos",
    "phantom": "ShaChiWeaponInfo",
    "accessory": "AccessoryInfos",
    "medicine": "MedicineInfos",
}
WEAPON_MAX_LEVEL = 30        # 游戏内武器 / 影之武装满级


def load_catalog():
    if not CATALOG_PATH.is_file():
        raise FileNotFoundError(
            f"缺少物品目录 {CATALOG_PATH}\n"
            "请先用 tools 里的脚本从游戏数据生成（见 README「生成物品目录」）。")
    d = json.loads(CATALOG_PATH.read_text(encoding="utf-8-sig"))
    items = d.get("items", d)
    by_cat = collections.defaultdict(list)
    for it in items:
        if it.get("supported", True) and it.get("id"):
            by_cat[it["category"]].append(it)
    return by_cat


def _now():
    return datetime.datetime.now().strftime("%Y.%m.%d-%H.%M.%S")


def _inv(js):
    inv = js.get("Inventory")
    if not isinstance(inv, dict):
        raise ValueError("这个存档槽没有 Inventory")
    return inv


# ────────────────────── 4. 武器 / 影之武装 满级 ──────────────────────
def act_weapons_max(js, ctx):
    inv = _inv(js)
    out = []
    for field, lv_field in (("MainWeaponInfos", "WeaponLevel"),
                            ("ShaChiWeaponInfo", "ShaChiWeaponLevel")):
        d = inv.get(field)
        if not isinstance(d, dict):
            continue
        changed = 0
        for iid, rec in d.items():
            if not isinstance(rec, dict):
                continue
            cur = rec.get(lv_field, 1)
            if cur != WEAPON_MAX_LEVEL:
                rec[lv_field] = WEAPON_MAX_LEVEL
                rec["HasUIRedPoint"] = True
                changed += 1
        out.append(f"{field}: {len(d)} 件，其中 {changed} 件提到 {WEAPON_MAX_LEVEL} 级")
    return out


# ────────────────────── 5. 获得全部武器 / 影之武装 ──────────────────────
def act_obtain_weapons(js, ctx):
    inv = _inv(js)
    cat = ctx.get("catalog") or load_catalog()
    cycle = js.get("PBZGameSystem", {}).get("CycleCount", 0)
    ts = _now()
    out = []
    for category, field in (("weapon", "MainWeaponInfos"), ("phantom", "ShaChiWeaponInfo")):
        d = inv.get(field)
        if not isinstance(d, dict):
            d = {}
            inv[field] = d
        added = []
        for it in cat.get(category, []):
            iid = it["id"]
            if iid in d:
                continue
            if category == "weapon":
                d[iid] = {"DateTime": ts, "WeaponLevel": 1, "AlreadySnapSoul": 0,
                          "HasUIRedPoint": True, "AcquiredCycle": cycle, "FirstAcquiredCycle": cycle}
            else:
                d[iid] = {"ShaChiWeaponLevel": 1, "AcquiredCycle": cycle,
                          "FirstAcquiredCycle": cycle, "DirtyTime": ts,
                          "AlreadySnapSoul": False, "HasUIRedPoint": True}
            added.append(iid)
        out.append(f"{category}({field}): 新增 {len(added)} 件，现共 {len(d)} 件")
        if added[:6]:
            out.append("    例: " + ", ".join(added[:6]) + (" …" if len(added) > 6 else ""))
    return out


# ────────────────────── 6. 获得全部道具 / 读物 ──────────────────────
def act_obtain_items(js, ctx):
    inv = _inv(js)
    cat = ctx.get("catalog") or load_catalog()
    cycle = js.get("PBZGameSystem", {}).get("CycleCount", 0)
    ts = _now()
    d = inv.get("InventoryInfos")
    if not isinstance(d, dict):
        d = {}
        inv["InventoryInfos"] = d

    added, pages = [], 0
    for it in cat.get("goods", []):
        iid = it["id"]
        if iid in d:
            continue
        # 数量按最保守的 1 来，避免和游戏内的堆叠上限打架
        d[iid] = {"CurrentAmount": 1, "MaxAmount": 1,
                  "FirstAddTime": ts, "DirtyTime": ts, "AcquiredCycle": cycle}
        added.append(iid)
        if iid.startswith("Page_"):
            pages += 1
    out = [f"道具(InventoryInfos): 新增 {len(added)} 件（其中读物 Page_* {pages} 件），现共 {len(d)} 件"]
    if added[:6]:
        out.append("    例: " + ", ".join(added[:6]) + (" …" if len(added) > 6 else ""))
    return out


# ────────────────────── 7. 任务 ──────────────────────
QC_DONE = "EQuestCompletion::QC_Succeded"
QC_STARTED = "EQuestCompletion::QC_Started"
BRANCH_DONE = "EBranchType::BranchCompleted"


def _quest_info(q, idx):
    path = str(q.get("QuestClass", ""))
    short = path.rsplit("/", 1)[-1] if "/" in path else path
    qid = short.split(".")[0]
    kind = "支线"
    if "/Tutorial/" in path:
        kind = "教学"
    elif "/Main/" in path:
        kind = "主线"
    done = q.get("QuestCompletion") == QC_DONE
    return {"index": idx, "id": qid, "kind": kind, "done": done,
            "completion": q.get("QuestCompletion"), "path": path}


def quests_list(js):
    """读取当前存档里的任务列表。"""
    nar = js.get("Narrative", {})
    qs = nar.get("NarrativeSavedQuests") or []
    return [_quest_info(q, i) for i, q in enumerate(qs)]


def complete_quest(q):
    """把一条任务标记为完成：完成所有分支、所有任务计数补齐、状态置为成功。"""
    q["QuestCompletion"] = QC_DONE
    done_ids = []
    for b in q.get("QuestBranches", []) or []:
        if not isinstance(b, dict):
            continue
        b["BranchType"] = BRANCH_DONE
        b["DirtyTime"] = _now()
        done_ids.append(str(b.get("BranchID", "")))
        # TasksProgress 是各子任务的进度计数，统一推到「已达到」
        tp = b.get("TasksProgress")
        if isinstance(tp, list):
            for i, v in enumerate(tp):
                if isinstance(v, int) and v == 0:
                    # 没有真实需求数量时只标记达到 1，避免伪造离谱数字
                    tp[i] = max(v, 1)
    prev = q.get("CompletedBranches")
    if not isinstance(prev, list):
        prev = []
    q["CompletedBranches"] = sorted(set(map(str, prev)) | set(x for x in done_ids if x))
    return len(done_ids)


def _act_quest_impl(js, ctx, indexes):
    nar = js.get("Narrative")
    if not isinstance(nar, dict):
        return ["跳过：没有 Narrative"]
    qs = nar.get("NarrativeSavedQuests") or []
    infos = [_quest_info(q, i) for i, q in enumerate(qs)]
    if indexes is None:
        indexes = [i["index"] for i in infos if not i["done"]]
    out = []
    for idx in indexes:
        if not (0 <= idx < len(qs)):
            out.append(f"跳过：任务序号 {idx} 不存在")
            continue
        info = infos[idx]
        if info["done"]:
            out.append(f"{info['kind']} {info['id']}：已是完成状态")
            continue
        n = complete_quest(qs[idx])
        out.append(f"{info['kind']} {info['id']}：标记完成（{n} 个分支）")
    if not out:
        out.append("没有可完成的任务")
    return out


def act_quest_one(js, ctx):
    idx = ctx.get("quest_index")
    return _act_quest_impl(js, ctx, [idx] if idx is not None else None)


def act_quest_path(js, ctx):
    """按任务路径完成——存档里没有这条也照样补一条再标完成。"""
    path = ctx.get("quest_path")
    if not path:
        return ["跳过：没给任务路径"]
    catalog = {q["path"]: q for q in load_quest_catalog()}
    meta = catalog.get(path, {})
    name = meta.get("title") or meta.get("id") or path.split("/")[-1]
    nar = js.setdefault("Narrative", {})
    q, added = _ensure_quest_entry(js, path)
    if q.get("QuestCompletion") == QC_DONE:
        return [f"{name}：已经是完成状态"]
    n = complete_quest(q)
    return [f"{name}：标记完成（{n} 个分支{', 新建条目' if added else ''}）"]


def act_quests_all(js, ctx):
    return _act_quest_impl(js, ctx, None)


def act_quests_all_full(js, ctx):
    """
    把整个任务表（83 个）全部标成完成，包括存档里没有条目的「未开始」任务。
    新条目按空分支建立，分支数 0。
    """
    cats = ctx.get("quest_cats")
    js.setdefault("Narrative", {})
    done, created, already = [], 0, 0
    for m in load_quest_catalog():
        if cats and m["category"] not in cats:
            continue
        name = m.get("title") or m.get("id")
        q, is_new = _ensure_quest_entry(js, m["path"])
        if q.get("QuestCompletion") == QC_DONE:
            already += 1
            continue
        complete_quest(q)
        done.append(name)
        if is_new:
            created += 1
    out = [f"标记完成 {len(done)} 个任务（其中新建条目 {created} 个），原本已完成的 {already} 个跳过"]
    if done:
        head = "、".join(done[:8]) + (" …" if len(done) > 8 else "")
        out.append(f"    例: {head}")
    return out


ACTIONS.update({
    "weapons_max": act_weapons_max,
    "obtain_weapons": act_obtain_weapons,
    "obtain_items": act_obtain_items,
    "quest_one": act_quest_one,
    "quest_path": act_quest_path,
    "quests_all": act_quests_all,
    "quests_all_full": act_quests_all_full,
})

ACTION_LABELS.update({
    "weapons_max": "武器/影之武装满级",
    "obtain_weapons": "获得全部武器/影之武装",
    "obtain_items": "获得全部道具/读物",
    "quest_one": "完成任务",
    "quest_path": "完成任务",
    "quests_all": "完成全部进行中任务",
    "quests_all_full": "全部任务标完成（含未开始）",
})


# ────────────────────── 读写 ──────────────────────
def build(save_path, actions, slots=None, params=None):
    """在内存里改好，返回 (container, entries, replacements, log, blob)。不落盘。"""
    key = pbzcore.load_key()
    oodle = pbzcore.Oodle()
    cont = pbzcore.PBZS(key, oodle)
    data = Path(save_path).read_bytes()
    entries = cont.decode(data)

    names = [e["name"] for e in entries]
    targets = set(slots) if slots else {n for n in names if is_save_slot(n)}
    unknown = targets - set(names)
    if unknown:
        raise ValueError(f"存档里没有这些槽: {unknown}")

    params = params or {}
    ctx = {"bells": all_bells(), "fog": fog_by_code(),
           "quest_index": params.get("quest_index"),
           "quest_path": params.get("quest_path"),
           "main_order": params.get("main_order"),
           "ending": params.get("ending"),
           "completion": params.get("completion", 100),
           "playtime": params.get("playtime"),
           "collect": load_collect()}
    if any(a.startswith("obtain_") for a in actions):
        ctx["catalog"] = load_catalog()

    replacements, log = {}, []
    global_acts = [a for a in actions if a in GLOBAL_ACTIONS]
    for e in entries:
        if e["name"] not in targets:
            continue
        js = e["json"]
        lines = []
        for a in actions:
            try:
                lines += ACTIONS[a](js, ctx) or []
            except Exception as ex:
                lines.append(f"[{a}] 出错: {ex}")
        if not lines:
            continue
        raw = json.dumps(js, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if raw == e["raw"]:
            # 内容没变就别重编码，保留原始密封块，避免文件无故变大
            log.append((e["name"], lines + ["（内容无变化，保留原始压缩块）"]))
            continue
        replacements[e["name"]] = raw
        log.append((e["name"], lines))

    # 结局 / 收集度这类记录在 __Advanced__ 里，不属于任何存档槽
    if global_acts and "__Advanced__" not in targets:
        adv = next((e for e in entries if e["name"] == "__Advanced__"), None)
        if adv is not None:
            js = adv["json"]
            lines = []
            for a in global_acts:
                try:
                    lines += ACTIONS[a](js, ctx) or []
                except Exception as ex:
                    lines.append(f"[{a}] 出错: {ex}")
            if lines:
                raw = json.dumps(js, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                if raw != adv["raw"]:
                    replacements["__Advanced__"] = raw
                log.append(("__Advanced__", lines))

    if not replacements:
        return cont, entries, {}, log, data

    out = cont.encode(entries, replacements)
    # 立刻回读校验
    back = cont.decode(out)
    for b in back:
        if b["name"] in replacements and b["raw"] != replacements[b["name"]]:
            raise RuntimeError(f"回读校验失败: {b['name']}")
    return cont, entries, replacements, log, out


def write(save_path, blob, make_backup=True):
    save = Path(save_path)
    bak = None
    if make_backup:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        bak = save.with_suffix(save.suffix + f".bak-{stamp}")
        shutil.copy2(save, bak)
    tmp = save.with_suffix(save.suffix + ".tmp")
    tmp.write_bytes(blob)
    os.replace(tmp, save)
    return bak


def latest_backup(save_path):
    save = Path(save_path)
    items = sorted(save.parent.glob(save.name + ".bak-*"), key=lambda p: p.stat().st_mtime, reverse=True)
    return items[0] if items else None


def restore(save_path, backup=None):
    save = Path(save_path)
    b = Path(backup) if backup else latest_backup(save_path)
    if not b or not b.is_file():
        raise FileNotFoundError("没有找到备份")
    shutil.copy2(b, save)
    return b


def apply(save_path, actions, slots=None, dry_run=False, params=None):
    """一站式：改 → 校验 → 备份 → 写盘。返回 (log, backup_path, new_size)。"""
    cont, entries, repl, log, out = build(save_path, actions, slots, params)
    if not repl:
        # 没有任何槽真的变了，不写盘、不产生垃圾备份
        return log, None, Path(save_path).stat().st_size
    if dry_run:
        return log, None, len(out)
    bak = write(save_path, out)
    return log, bak, len(out)


def _latest_ts(js):
    """扫出这份槽里最新的时间戳，用来判断哪个槽是玩家最近的存档。"""
    best = ""
    stack = [js]
    seen = 0
    while stack and seen < 200000:
        o = stack.pop()
        seen += 1
        if isinstance(o, dict):
            for v in o.values():
                if isinstance(v, (dict, list)):
                    stack.append(v)
                elif isinstance(v, str) and len(v) >= 19 and v[:2] == "20" \
                        and (v[4] in ".-") and (v[10] in ".-"):
                    if v > best:
                        best = v
        elif isinstance(o, list):
            stack.extend(x for x in o if isinstance(x, (dict, list)))
    return best


LOC_ZH = DATA / "loc_zh.json"
# 难度阶梯（本地化表里的 key，按从低到高）
DIFF_KEYS = ["UI_Setting_13", "UI_Setting_05", "UI_Setting_06", "UI_Setting_11",
             "UI_SelectDifficutly_09"]


def load_loc():
    if not LOC_ZH.is_file():
        return {}
    return json.loads(LOC_ZH.read_text(encoding="utf-8"))


def difficulty_names():
    loc = load_loc()
    return [loc.get(k, k) for k in DIFF_KEYS]


def _slot_label(name):
    """
    AutoSave0    -> 自动 01（从 0 起）
    ManualSave1  -> 手动 01（从 1 起）
    NightmareRun0-> 六十六天 01（二周目）
    """
    m = re.match(r"(AutoSave|ManualSave|NightmareRun)(\d+)$", name or "")
    if not m:
        return name or ""
    n = int(m.group(2))
    kind = m.group(1)
    if kind == "AutoSave":
        return f"自动 {n + 1:02d}"
    if kind == "ManualSave":
        return f"手动 {n:02d}"
    return f"六十六天 {n + 1:02d}"


def parse_playtime(text):
    """
    把 12h34m56s / 1:02:03 / 纯秒数 解析成秒。解析不了返回 None。
    """
    if text is None:
        return None
    if isinstance(text, int):
        return text
    s = str(text).strip().lower()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    m = re.fullmatch(r"(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*(?:(\d+)\s*s)?", s)
    if m and any(m.groups()):
        h, mi, se = (int(x) if x else 0 for x in m.groups())
        return h * 3600 + mi * 60 + se
    parts = s.replace("：", ":").split(":")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        h, mi, se = map(int, parts)
        return h * 3600 + mi * 60 + se
    if len(parts) == 2 and all(p.isdigit() for p in parts):
        mi, se = map(int, parts)
        return mi * 60 + se
    return None


def _fmt_playtime(sec):
    try:
        sec = int(sec)
    except (TypeError, ValueError):
        return "—"
    h, rem = divmod(max(0, sec), 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m}m{s}s"


def _fmt_when(dt_string, g):
    """优先用 DateTimeString（2026.11.15-13.23.00），退化成 Year/Month/Day。"""
    s = dt_string or ""
    m = re.match(r"^(\d{4})\.(\d{2})\.(\d{2})-(\d{2})\.(\d{2})\.(\d{2})$", s)
    if m:
        y, mo, d, hh, mm, ss = m.groups()
        return f"{mo}/{d}/{y} {hh}:{mm}:{ss}"
    try:
        return f"{g.get('Month', 0):02d}/{g.get('Day', 0):02d}/{g.get('Year', 0)}"
    except Exception:
        return "—"


def load_regions():
    """regions.json 里的 map_id / 区域 id -> 中文区域名。"""
    p = DATA / "catalog" / "regions.json"
    if not p.is_file():
        return {}
    reg = json.loads(p.read_text(encoding="utf-8-sig"))
    out = {}
    for r in reg.get("regions", []):
        name = r.get("name")
        if not name:
            continue
        out[r["id"].casefold()] = name
        for m in r.get("map_ids", []):
            out[str(m).casefold()] = name
    return out


def region_of(level_name, regions):
    """
    把 CurrentLevelName（PBZ_Duanhun_WP）对到区域中文名。
    先精确匹配，再前缀匹配（Duanhun -> DuanHunShan）。
    """
    if not level_name:
        return ""
    key = str(level_name)
    for pre in ("PBZ_",):
        if key.startswith(pre):
            key = key[len(pre):]
    for suf in ("_WP", "_Wp", "_wp"):
        if key.endswith(suf):
            key = key[: -len(suf)]
    k = key.casefold()
    if k in regions:
        return regions[k]
    for mid, name in regions.items():
        if mid.startswith(k) or k.startswith(mid):
            return name
    return ""


def list_slots(save_path):
    """
    列出存档槽，字段与游戏内「载入存档」界面一致（含二周目 / 六十六天存档）。
    返回 (LastContinueSlot, [{name,label,size,latest,title,difficulty,playtime,when,cycle,days}...])
    """
    cont, entries, _r, _l, _b = build(save_path, [])
    adv = next((e for e in entries if e["name"] == "__Advanced__"), None)
    last = adv["json"].get("LastContinueSlot") if adv is not None else None

    loc = load_loc()
    regions = load_regions()
    diffs = difficulty_names()
    out = []
    for e in entries:
        if not is_save_slot(e["name"]):
            continue
        g = e["json"].get("PBZGameSystem", {}) or {}
        bell = g.get("LastSaveActorID") or ""
        bm = e["json"].get("BellManager", {}) or {}
        if not bell or bell == "None":
            bell = bm.get("SaveBellID") or ""
        region = region_of(g.get("CurrentLevelName"), regions)
        title = loc.get(bell) or region or (bell or "—")
        dbyte = g.get("DifficultModeByte", 0)
        diff = diffs[dbyte] if isinstance(dbyte, int) and 0 <= dbyte < len(diffs) else "—"
        recs = g.get("CycleRecords") or {}
        cyc = g.get("CycleCount", 0) or 0
        # 记录 key 是 0 起的轮次；CycleCount 是 1 起的当前轮
        cur = recs.get(str(cyc)) or recs.get(str(cyc - 1)) or {}
        if not cur and recs:
            cur = recs[max(recs, key=lambda k: int(k) if str(k).isdigit() else -1)]
        out.append({
            "name": e["name"],
            "label": _slot_label(e["name"]),
            "size": e["size"],
            "latest": _latest_ts(e["json"]),
            "title": title,
            "region": region,
            "bell": bell,
            "difficulty": diff,
            "playtime": _fmt_playtime(g.get("HasGameTime")),
            "when": _fmt_when(g.get("DateTimeString"), g),
            "cycle": g.get("CycleCount", 0),
            # 剩余天数只在「六十六天」难度下有意义
            "days": cur.get("RemainingDays") if e["name"].startswith(NM_PREFIX) else None,
            "nightmare": e["name"].startswith(NM_PREFIX),
        })
    out.sort(key=lambda x: x["latest"], reverse=True)
    if last not in {x["name"] for x in out}:
        last = out[0]["name"] if out else None
    return last, out


QUEST_CATALOG = DATA / "quests.json"
CAT_ORDER = ["Main", "Branch", "Collect", "Tutorial", "Simple"]


def load_quest_catalog():
    """游戏里全部 83 个任务（由 tools/build_quests.py 生成）。"""
    if not QUEST_CATALOG.is_file():
        return []
    return json.loads(QUEST_CATALOG.read_text(encoding="utf-8"))


def _quest_rows(js, categories=None):
    """把任务总表和存档里的实际进度合并。"""
    nar = js.get("Narrative", {}) or {}
    qs = nar.get("NarrativeSavedQuests") or []
    saved = {}
    for i, q in enumerate(qs):
        if isinstance(q, dict):
            saved[str(q.get("QuestClass", ""))] = (i, q)
    tracked = nar.get("TrackedQuestPath") or ""
    cur = nar.get("CurrentMainQuestPath") or ""

    out = []
    for m in load_quest_catalog():
        if categories and m["category"] not in categories:
            continue
        hit = saved.get(m["path"])
        if hit is None:
            status, idx = "none", None
        else:
            idx, q = hit
            status = "done" if q.get("QuestCompletion") == QC_DONE else "started"
        out.append({**m, "status": status, "index": idx,
                    "tracked": m["path"] in (tracked, cur)})
    return out


def main_quest_status(js):
    """主线（10 个），带章节和顺序。"""
    return _quest_rows(js, {"Main"})


def all_quest_status(js):
    """全部任务（83 个）。"""
    return _quest_rows(js)


def _ensure_quest_entry(js, path):
    """确保存档里有这条任务的条目，没有就补一条空的进行中记录。"""
    nar = js.setdefault("Narrative", {})
    qs = nar.setdefault("NarrativeSavedQuests", [])
    for q in qs:
        if q.get("QuestClass") == path:
            return q, False
    entry = {"QuestClass": path, "QuestBranches": [], "QuestCompletion": QC_STARTED,
             "CompletedBranches": [], "ReachedStateNames": []}
    qs.append(entry)
    return entry, True


def _act_main_jump_impl(js, ctx, mark_prev: bool):
    order = ctx.get("main_order")
    rows = main_quest_status(js)
    target = next((r for r in rows if r["order"] == order), None)
    if target is None:
        return [f"跳过：没有第 {order} 个主线"]
    nar = js.setdefault("Narrative", {})
    out = []

    if mark_prev:
        done = []
        for r in rows:
            if r["order"] >= target["order"]:
                continue
            if r["status"] == "done":
                continue
            q, added = _ensure_quest_entry(js, r["path"])
            n = complete_quest(q)
            done.append(f"{r['title']}{'(新建)' if added else ''}")
        if done:
            out.append("标记完成: " + "、".join(done))

    q, added = _ensure_quest_entry(js, target["path"])
    if q.get("QuestCompletion") == QC_DONE:
        out.append(f"{target['title']}：已经是完成状态，仅切换追踪")
    else:
        q["QuestCompletion"] = QC_STARTED
        out.append(f"{target['title']}：设为进行中{'(新建条目)' if added else ''}")

    nar["TrackedQuestPath"] = target["path"]
    nar["LastUpdatedMainQuestPath"] = target["path"]
    nar["CurrentMainQuestPath"] = target["path"]
    out.append(f"当前追踪 → {target['title']}")
    return out


def act_main_jump(js, ctx):
    """推进到某个主线：前面的主线标完成，目标设为进行中，并切换追踪。"""
    return _act_main_jump_impl(js, ctx, mark_prev=True)


def act_main_track(js, ctx):
    """只切换追踪指针，不动任何任务状态。"""
    return _act_main_jump_impl(js, ctx, mark_prev=False)


ACTIONS.update({
    "main_jump": act_main_jump,
    "main_track": act_main_track,
})

ACTION_LABELS.update({
    "main_jump": "推进到指定主线",
    "main_track": "切换追踪主线",
})


# ────────────────────── 结局 / 收集度 ──────────────────────
# 本地化表里 UI_Ending_06..09 依次是 龙吟 / 湮灭 / 决战 / 饮恨
ENDING_KEYS = ["UI_Ending_06", "UI_Ending_07", "UI_Ending_08", "UI_Ending_09"]
GLOBAL_ACTIONS = {"set_ending", "set_completion", "collect_full"}


def ending_names():
    loc = load_loc()
    return [loc.get(k, k) for k in ENDING_KEYS]


def load_endings(save_path, slot=None):
    """读取当前记录里的结局与收集度。返回 (槽名, 轮次列表, 首通记录)。"""
    cont, entries, _r, _l, _b = build(save_path, [])
    saves = [e for e in entries if is_save_slot(e["name"])]
    pick = next((e for e in saves if e["name"] == slot), saves[0] if saves else None)
    adv = next((e for e in entries if e["name"] == "__Advanced__"), None)
    names = ending_names()

    def fmt(rec):
        if not isinstance(rec, dict):
            return None
        e = rec.get("Ending")
        return {"ending": e,
                "ending_name": names[e] if isinstance(e, int) and 0 <= e < len(names) else "—",
                "completion": rec.get("Completion"),
                "difficulty": rec.get("Difficulty"),
                "gametime": rec.get("GameTime")}

    cycles = []
    if pick is not None:
        for k, rec in ((pick["json"].get("PBZGameSystem") or {}).get("CycleRecords") or {}).items():
            f = fmt(rec)
            if f:
                cycles.append({"cycle": k, **f})
    first = fmt(adv["json"].get("FirstClearRecord")) if adv is not None else None
    return (pick["name"] if pick else None), cycles, first


def load_collect():
    p = DATA / "collect.json"
    if not p.is_file():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def act_collect_full(js, ctx):
    """
    把收集相关的内容尽量拉满（跑在 __Advanced__ 上）：
      Codex.DiscoveredEnemies   补全 169 个敌人类路径（与已有取并集）
      BonusCollection.ViewedArt / ViewedMusic  填满美术/音乐条目
      BossRecord                已有 boss 补齐所有难度的讨伐记录
    特典解锁用的 UnlockedTags 枚举不出来，保持原样。
    """
    data = ctx.get("collect") or load_collect()
    out = []

    codex = js.get("Codex")
    if not isinstance(codex, dict):
        codex = {"DiscoveredEnemies": [], "RevealedEnemies": [], "EnemyKillMapIDs": {}}
        js["Codex"] = codex
    cur = codex.get("DiscoveredEnemies")
    if not isinstance(cur, list):
        cur = []
    before = len(cur)
    seen = {str(x) for x in cur}
    added = 0
    for path in data.get("codex_enemy_paths", []):
        if path not in seen:
            cur.append(path)
            seen.add(path)
            added += 1
    codex["DiscoveredEnemies"] = cur
    if not isinstance(codex.get("RevealedEnemies"), list):
        codex["RevealedEnemies"] = []
    if not isinstance(codex.get("EnemyKillMapIDs"), dict):
        codex["EnemyKillMapIDs"] = {}
    out.append(f"图鉴·已发现敌人：{before} → {len(cur)} 条（新增 {added}）")

    bonus = js.get("BonusCollection")
    if not isinstance(bonus, dict):
        bonus = {"UnlockedTags": [], "ViewedArt": [], "ViewedMusic": []}
        js["BonusCollection"] = bonus
    for field, key, label in (("ViewedArt", "bonus_art", "特典·美术"),
                              ("ViewedMusic", "bonus_music", "特典·音乐")):
        old = bonus.get(field)
        if not isinstance(old, list):
            old = []
        merged = list(dict.fromkeys(list(map(str, old)) + list(data.get(key, []))))
        bonus[field] = merged
        out.append(f"{label}：{len(old)} → {len(merged)} 条")
    tags = bonus.get("UnlockedTags")
    out.append(f"特典·解锁标签：保持 {len(tags) if isinstance(tags, list) else 0} 条"
               "（游戏内标签枚举不出来）")

    br = js.get("BossRecord")
    if isinstance(br, dict) and br:
        filled = 0
        for name, rec in br.items():
            if not isinstance(rec, dict):
                continue
            recs = rec.get("Records")
            if not isinstance(recs, dict):
                recs = {}
                rec["Records"] = recs
            for diff in range(5):
                k = str(diff)
                r = recs.get(k)
                if not isinstance(r, dict):
                    recs[k] = {"AttemptCount": 1, "KillCount": 1,
                               "BestTimeMs": 60000, "Cycle": 0}
                    filled += 1
                else:
                    if r.get("AttemptCount", 0) < 1:
                        r["AttemptCount"] = 1
                    if r.get("KillCount", 0) < 1:
                        r["KillCount"] = 1
                        filled += 1
        out.append(f"Boss 讨伐记录：{len(br)} 个 boss，补齐 {filled} 条难度记录")
    else:
        out.append("Boss 讨伐记录：无")
    return out


def act_set_playtime(js, ctx):
    """改游戏时长（秒）。"""
    sec = ctx.get("playtime")
    if not isinstance(sec, int) or sec < 0:
        return [f"跳过：时长 {sec!r} 无效"]
    g = js.get("PBZGameSystem")
    if not isinstance(g, dict):
        return ["跳过：没有 PBZGameSystem"]
    old = g.get("HasGameTime")
    g["HasGameTime"] = sec
    g["CurrentCycleGameTime"] = sec
    return [f"游戏时长：{_fmt_playtime(old)} → {_fmt_playtime(sec)}（{sec} 秒）"]


def act_set_ending(js, ctx):
    """
    改「轮回记录」里记录的结局。
    注意：这只是改记录，不会触发结局演出——结局是决战结束时由剧情判定的。
    """
    idx = ctx.get("ending")
    names = ending_names()
    if not isinstance(idx, int) or not 0 <= idx < len(names):
        return [f"跳过：结局序号 {idx!r} 无效（0-{len(names) - 1}）"]
    out, hit = [], 0
    g = js.get("PBZGameSystem")
    if isinstance(g, dict):
        for k, rec in (g.get("CycleRecords") or {}).items():
            if isinstance(rec, dict):
                old = rec.get("Ending")
                if old != idx:
                    rec["Ending"] = idx
                    hit += 1
                    out.append(f"第 {k} 轮：结局 {old} → {idx}（{names[idx]}）")
    fr = js.get("FirstClearRecord")
    if isinstance(fr, dict) and fr.get("Ending") != idx:
        out.append(f"首次通关记录：结局 {fr.get('Ending')} → {idx}（{names[idx]}）")
        fr["Ending"] = idx
        hit += 1
    return out or [f"结局已经是 {names[idx]}，无需改动"]


def act_set_completion(js, ctx):
    """把记录里的收集度数字改成 100（显示层）。"""
    target = ctx.get("completion", 100)
    out, hit = [], 0
    g = js.get("PBZGameSystem")
    if isinstance(g, dict):
        for k, rec in (g.get("CycleRecords") or {}).items():
            if isinstance(rec, dict):
                old = rec.get("Completion")
                if old != target:
                    rec["Completion"] = target
                    hit += 1
                    out.append(f"第 {k} 轮收集度：{old} → {target}")
    fr = js.get("FirstClearRecord")
    if isinstance(fr, dict) and fr.get("Completion") != target:
        out.append(f"首次通关收集度：{fr.get('Completion')} → {target}")
        fr["Completion"] = target
        hit += 1
    return out or [f"收集度已经是 {target}，无需改动"]


ACTIONS.update({
    "set_ending": act_set_ending,
    "set_completion": act_set_completion,
    "collect_full": act_collect_full,
    "set_playtime": act_set_playtime,
})

ACTION_LABELS.update({
    "set_ending": "修改记录里的结局",
    "set_completion": "收集度改为 100",
    "collect_full": "收集拉满（图鉴/特典/Boss）",
    "set_playtime": "修改游戏时长",
})


def load_story(save_path, slot=None):
    """读取主线进度。返回 (存档槽名, [主线信息])。"""
    return _load_rows(save_path, slot, main_quest_status)


def load_all_quests(save_path, slot=None):
    """读取全部任务（83 个）的进度。返回 (存档槽名, [任务信息])。"""
    return _load_rows(save_path, slot, all_quest_status)


def _load_rows(save_path, slot, fn):
    cont, entries, _r, _l, _b = build(save_path, [])
    saves = [e for e in entries if is_save_slot(e["name"])]
    if not saves:
        return None, []
    pick = next((e for e in saves if e["name"] == slot), saves[0])
    return pick["name"], fn(pick["json"])


def load_quests(save_path, slot=None):
    """只读取任务列表，不改任何东西。返回 (存档槽名, [任务信息])。"""
    cont, entries, _r, _l, _b = build(save_path, [])
    saves = [e for e in entries if is_save_slot(e["name"])]
    if not saves:
        return None, []
    pick = next((e for e in saves if e["name"] == slot), saves[0])
    return pick["name"], quests_list(pick["json"])


def delete_slots(save_path, slots, dry_run=False):
    """
    删除存档槽。

    PBZS 的目录是按 entries 重建的，所以删槽 = 过滤掉条目再编码。
    顺手处理：__Advanced__.LastContinueSlot 指向被删的槽时，改成剩下最新的那个。
    """
    save_path = Path(save_path)
    targets = {s.strip() for s in slots if s and s.strip()}
    protected = {"__Advanced__", "__Achievements__"} | {
        n for n in targets if n.startswith(INTERNAL_PREFIX)}
    hit = targets & protected
    if hit:
        raise ValueError(f"这几个不是存档槽，不能删: {sorted(hit)}")
    if not targets:
        raise ValueError("没有指定要删的槽")

    key = pbzcore.load_key()
    cont = pbzcore.PBZS(key, pbzcore.Oodle())
    data = save_path.read_bytes()
    entries = cont.decode(data)
    names = {e["name"] for e in entries}

    unknown = targets - names
    if unknown:
        raise ValueError(f"存档里没有这些槽: {sorted(unknown)}")

    keep = [e for e in entries if e["name"] not in targets]
    if len(keep) == len(entries):
        raise ValueError("没有匹配到要删的槽")

    remain = [e for e in keep if is_save_slot(e["name"])]
    if not remain:
        raise ValueError("不能把存档槽删光，至少留一个")

    repl = {}
    adv = next((e for e in keep if e["name"] == "__Advanced__"), None)
    note = []
    if adv is not None:
        js = copy.deepcopy(adv["json"])
        if js.get("LastContinueSlot") in targets:
            remain.sort(key=lambda e: _latest_ts(e["json"]), reverse=True)
            new_last = remain[0]["name"]
            note.append(f"LastContinueSlot: {js['LastContinueSlot']} → {new_last}")
            js["LastContinueSlot"] = new_last
            repl["__Advanced__"] = json.dumps(
                js, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    blob = cont.encode(keep, repl)
    back = cont.decode(blob)                       # 回读校验
    if len(back) != len(keep):
        raise RuntimeError(f"回读槽数不符 {len(back)} != {len(keep)}")
    left = {b["name"] for b in back}
    if targets & left:
        raise RuntimeError("回读后要删的槽还在")
    for b in back:
        if b["name"] in repl and b["raw"] != repl[b["name"]]:
            raise RuntimeError(f"回读校验失败: {b['name']}")

    info = {"deleted": sorted(targets), "remaining": sorted(left),
            "size_before": len(data), "size_after": len(blob), "notes": note}
    if dry_run:
        return info, None

    bak = save_path.with_suffix(save_path.suffix + ".bak-" +
                                datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(save_path, bak)
    tmp = save_path.with_suffix(save_path.suffix + ".tmp")
    tmp.write_bytes(blob)
    os.replace(tmp, save_path)
    return info, bak


# ────────────────────── 命令行 ──────────────────────
DEFAULT_ALL = ["skills", "bells_map", "bells_world", "weapons_max", "obtain_weapons", "obtain_items"]


def main():
    import argparse
    ap = argparse.ArgumentParser(description="影之刃零 存档修改")
    ap.add_argument("action", nargs="+",
                    choices=list(ACTIONS) + ["all", "info", "restore", "quests", "delete"],
                    help="info=查看结构  quests=列任务  all=除任务外的全部  delete=删除存档槽")
    ap.add_argument("--save", default=str(DEFAULT_SAVE))
    ap.add_argument("--slots", help="逗号分隔，默认全部存档槽")
    ap.add_argument("--quest", type=int, help="任务序号（配合 quest_one）")
    ap.add_argument("--main", type=int, help="主线顺序号（配合 main_jump / main_track）")
    ap.add_argument("--ending", type=int,
                    help="结局序号 0=龙吟 1=湮灭 2=决战 3=饮恨（配合 set_ending）")
    ap.add_argument("--playtime", help="游戏时长，如 12h34m56s 或秒数（配合 set_playtime）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    if "info" in a.action:
        _c, entries, _r, _l, _b = build(a.save, [])
        print(f"{a.save}\n{len(Path(a.save).read_bytes())} 字节, {len(entries)} 槽")
        for e in entries:
            print(f"  {e['name']:20s} 算法={e['algo']} 原长={e['size']:8d}")
        return

    if "quests" in a.action:
        _c, entries, _r, _l, _b = build(a.save, [])
        slot = next(e for e in entries if is_save_slot(e["name"]))
        print(f"存档槽 {slot['name']} 的任务列表：")
        for q in quests_list(slot["json"]):
            mark = "✔ 已完成" if q["done"] else "○ 进行中"
            print(f"  [{q['index']:2d}] {mark}  {q['kind']}  {q['id']}")
        return

    if "restore" in a.action:
        b = restore(a.save)
        print(f"已还原: {b.name}")
        return

    if "delete" in a.action:
        if not a.slots:
            raise SystemExit("delete 需要 --slots，例如 --slots AutoSave1,ManualSave1")
        if game_running() and not a.dry_run and not a.force:
            raise SystemExit("游戏正在运行，请先正常退出（或加 --force）。")
        targets = [s.strip() for s in a.slots.split(",") if s.strip()]
        info, bak = delete_slots(a.save, targets, a.dry_run)
        print(f"将删除: {', '.join(info['deleted'])}")
        print(f"保留  : {', '.join(info['remaining'])}")
        for n in info["notes"]:
            print(f"  {n}")
        print(f"大小  : {info['size_before']:,} → {info['size_after']:,} 字节")
        if a.dry_run:
            print("\n干跑完成，未写盘")
        else:
            print(f"\n已备份 {bak.name}")
            print(f"已写入 {a.save}")
        return

    acts = list(DEFAULT_ALL) if "all" in a.action else a.action
    if a.quest is not None and "quest_one" in acts:
        pass
    elif "quest_one" in acts and a.quest is None:
        raise SystemExit("quest_one 需要 --quest 序号（先用 quests 列出）")

    if game_running() and not a.dry_run and not a.force:
        raise SystemExit("游戏正在运行，请先正常退出（或加 --force）。")

    slots = [s.strip() for s in a.slots.split(",")] if a.slots else None
    log, bak, size = apply(a.save, acts, slots, a.dry_run,
                           {"quest_index": a.quest, "main_order": a.main,
                            "ending": a.ending,
                            "playtime": parse_playtime(a.playtime) if a.playtime else None})
    for name, lines in log:
        print(f"[{name}]")
        for ln in lines:
            print(f"   {ln}")
    if a.dry_run:
        print(f"\n干跑完成，将输出 {size} 字节（未写盘）")
    elif bak is None:
        print("\n没有需要修改的内容，存档未写盘。")
    else:
        print(f"\n已备份 {bak.name}")
        print(f"已写入 {a.save}（{size} 字节）")


if __name__ == "__main__":
    main()
