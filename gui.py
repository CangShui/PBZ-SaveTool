# -*- coding: utf-8 -*-
"""
gui.py —— 《影之刃零》存档工具 图形界面

布局：
    存档路径 → 选择要改的存档槽（默认勾选游戏自己的 LastContinueSlot）
    一键功能：技能 / 武器满级 / 铃铛 / 地图 / 获得全部武器 / 获得全部道具
    任务：从选中的槽读出任务列表，选一条直接标记完成

写盘前自动备份。游戏运行时会给提示（本作只在手动存档时写盘）。
"""
import datetime
import os
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import editor
import pbzcore

BG = "#12151c"
PANEL = "#1a1f2b"
FG = "#e6ecf7"
DIM = "#8b98ae"
ACCENT = "#4ade80"
ACCENT2 = "#60a5fa"
WARN = "#fbbf24"
ERR = "#f87171"

FEATURES = [
    ("skills", "一键升级全部技能",
     "两套能力树 64 个节点全部点亮（黑石 / 沙齿琥珀 / 忆花 / 榕树之心）"),
    ("weapons_max", "一键升级全部武器 / 影之武装到满级",
     f"已有的主武器与影之武装全部提到 {editor.WEAPON_MAX_LEVEL} 级"),
    ("bells_map", "一键开当前小地图全部铃铛",
     "只开当前所在地那一组的铃铛，不动其它区域"),
    ("bells_world", "一键开大地图全部铃铛",
     "13 个区 194 个铃铛 + 世界地图遮罩全开"),
    ("obtain_weapons", "一键获得全部武器 / 影之武装",
     "按游戏物品表补齐 33 把主武器 + 27 件影之武装"),
    ("obtain_items", "一键获得全部道具 / 读物",
     "按游戏物品表补齐 640 件道具（含 15 件读物 Page_*）"),
]


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.save = tk.StringVar(value=str(editor.DEFAULT_SAVE))
        self.busy = False
        self.quests = []
        self.story = []
        self._last_actions = []
        self.slot_vars = {}
        self.slot_meta = {}
        self.last_continue = None

        root.title("影之刃零 · 存档工具")
        root.geometry("980x940")
        root.minsize(900, 800)
        root.configure(bg=BG)

        style = ttk.Style(root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=FG, font=("Microsoft YaHei UI", 10))
        style.configure("Dim.TLabel", background=BG, foreground=DIM, font=("Microsoft YaHei UI", 9))
        style.configure("Title.TLabel", background=BG, foreground=FG,
                        font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background="#1a1f2b", foreground=DIM,
                        padding=(20, 8), font=("Microsoft YaHei UI", 10, "bold"))
        style.map("TNotebook.Tab", background=[("selected", "#243044")],
                  foreground=[("selected", FG)])
        style.configure("Big.TButton", font=("Microsoft YaHei UI", 11, "bold"),
                        padding=(8, 9), background="#243044", foreground=FG, borderwidth=0)
        style.map("Big.TButton", background=[("active", "#2f3f5a"), ("disabled", "#1b2130")],
                  foreground=[("disabled", "#5a667c")])
        style.configure("Accent.TButton", font=("Microsoft YaHei UI", 11, "bold"),
                        padding=(8, 9), background="#1d4d33", foreground="#c9f7dd", borderwidth=0)
        style.map("Accent.TButton", background=[("active", "#256845"), ("disabled", "#1b2130")],
                  foreground=[("disabled", "#5a667c")])
        style.configure("Treeview", background="#0d1017", fieldbackground="#0d1017",
                        foreground="#c7d2e4", borderwidth=0, rowheight=24,
                        font=("Microsoft YaHei UI", 9))
        style.configure("Treeview.Heading", background="#243044", foreground=FG,
                        borderwidth=0, font=("Microsoft YaHei UI", 9, "bold"))
        style.map("Treeview", background=[("selected", "#2f4f7a")])

        # ── 顶部 ──
        head = ttk.Frame(root)
        head.pack(fill="x", padx=18, pady=(14, 6))
        ttk.Label(head, text="影之刃零 · 存档工具", style="Title.TLabel").pack(side="left")
        self.lbl_game = ttk.Label(head, text="", style="Dim.TLabel")
        self.lbl_game.pack(side="right")

        row = ttk.Frame(root)
        row.pack(fill="x", padx=18, pady=(0, 4))
        ttk.Label(row, text="存档文件", style="Dim.TLabel").pack(side="left")
        tk.Entry(row, textvariable=self.save, bg=PANEL, fg=FG, insertbackground=FG,
                 relief="flat", font=("Consolas", 9)).pack(
            side="left", fill="x", expand=True, padx=8, ipady=5)
        ttk.Button(row, text="浏览", command=self.pick_save).pack(side="left", padx=(0, 6))
        ttk.Button(row, text="打开目录", command=self.open_dir).pack(side="left")

        self.lbl_save = ttk.Label(root, text="", style="Dim.TLabel")
        self.lbl_save.pack(fill="x", padx=20, pady=(0, 6))

        # ── 存档槽选择 ──
        slotbox = tk.Frame(root, bg=PANEL)
        slotbox.pack(fill="x", padx=18, pady=(0, 8))
        titlebar = tk.Frame(slotbox, bg=PANEL)
        titlebar.pack(fill="x", padx=12, pady=(8, 2))
        tk.Label(titlebar, text="要编辑的存档槽", bg=PANEL, fg=FG,
                 font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
        self.lbl_slot = tk.Label(titlebar, text="", bg=PANEL, fg=DIM,
                                 font=("Microsoft YaHei UI", 9))
        self.lbl_slot.pack(side="left", padx=10)
        ttk.Button(titlebar, text="刷新", width=6, command=self.refresh_slots).pack(side="right")
        ttk.Button(titlebar, text="全选", width=6,
                   command=lambda: self._set_all_slots(True)).pack(side="right", padx=(0, 6))
        ttk.Button(titlebar, text="全不选", width=7,
                   command=lambda: self._set_all_slots(False)).pack(side="right", padx=(0, 6))
        tk.Button(titlebar, text="删除选中", bg="#5b2130", fg="#ffd7de",
                  activebackground="#7a2b3f", activeforeground="#ffffff",
                  relief="flat", bd=0, padx=10, pady=3,
                  font=("Microsoft YaHei UI", 9, "bold"),
                  command=self.delete_selected_slots).pack(side="right", padx=(0, 10))

        self.slot_frame = tk.Frame(slotbox, bg=PANEL)
        self.slot_frame.pack(fill="x", padx=12, pady=(2, 10))

        # ── 分页 ──
        self.nb = ttk.Notebook(root)
        self.nb.pack(fill="both", expand=True, padx=18, pady=(0, 8))
        self.tab_main = ttk.Frame(self.nb)
        self.tab_quest = ttk.Frame(self.nb)
        self.tab_story = ttk.Frame(self.nb)
        self.tab_end = ttk.Frame(self.nb)
        self.nb.add(self.tab_main, text="一键功能")
        self.nb.add(self.tab_quest, text="任务")
        self.nb.add(self.tab_story, text="主线")
        self.nb.add(self.tab_end, text="结局/收集")
        self._build_main_tab()
        self._build_quest_tab()
        self._build_story_tab()
        self._build_end_tab()

        # ── 底部 ──
        foot = ttk.Frame(root)
        foot.pack(fill="x", padx=18, pady=(0, 8))
        ttk.Button(foot, text="全部执行（不含任务）", style="Accent.TButton",
                   command=self.run_all).pack(side="left", padx=(0, 10))
        ttk.Button(foot, text="还原最近备份", command=self.restore).pack(side="left", padx=(0, 10))
        ttk.Button(foot, text="试运行（不写盘）", command=self.dry).pack(side="left")
        self.lbl_state = ttk.Label(foot, text="", style="Dim.TLabel")
        self.lbl_state.pack(side="right")

        # ── 日志 ──
        logbox = ttk.Frame(root)
        logbox.pack(fill="both", expand=True, padx=18, pady=(0, 14))
        self.log = tk.Text(logbox, bg="#0d1017", fg="#c7d2e4", insertbackground=FG,
                           relief="flat", font=("Consolas", 9), wrap="word",
                           padx=10, pady=8, height=11)
        sb = ttk.Scrollbar(logbox, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        for tag, col in (("ok", ACCENT), ("warn", WARN), ("err", ERR),
                         ("dim", DIM), ("head", ACCENT2)):
            self.log.tag_config(tag, foreground=col)

        self.write("选好存档槽再点按钮。改完重进游戏读档即可。\n", "dim")
        self.refresh_status()
        self.root.after(400, self.refresh_slots)
        self.root.after(2000, self._tick)

    # ── 页面 ──
    def _build_main_tab(self):
        for action, title, desc in self._feat_specs():
            f = tk.Frame(self.tab_main, bg=PANEL)
            f.pack(fill="x", pady=3, padx=2)
            left = tk.Frame(f, bg=PANEL)
            left.pack(side="left", fill="x", expand=True, padx=(14, 8), pady=9)
            tk.Label(left, text=title, bg=PANEL, fg=FG,
                     font=("Microsoft YaHei UI", 11, "bold"), anchor="w").pack(fill="x")
            tk.Label(left, text=desc, bg=PANEL, fg=DIM,
                     font=("Microsoft YaHei UI", 9), anchor="w", justify="left").pack(fill="x")
            ttk.Button(f, text="执行", style="Big.TButton", width=7,
                       command=lambda a=action: self.run([a])).pack(
                side="right", padx=(0, 12), pady=8)

    @staticmethod
    def _feat_specs():
        order = ["skills", "weapons_max", "bells_map", "bells_world",
                 "obtain_weapons", "obtain_items"]
        by = {f[0]: f for f in FEATURES}
        return [by[a] for a in order]

    def _build_quest_tab(self):
        top = ttk.Frame(self.tab_quest)
        top.pack(fill="x", pady=(8, 6))
        ttk.Button(top, text="读取全部任务", command=self.load_quests).pack(side="left", padx=(2, 8))
        ttk.Button(top, text="完成选中", style="Accent.TButton",
                   command=self.complete_selected).pack(side="left", padx=(0, 8))
        ttk.Button(top, text="完成全部进行中", command=self.complete_all).pack(side="left")
        tk.Button(top, text="全部标完成（含未开始）", bg="#5b2130", fg="#ffd7de",
                  activebackground="#7a2b3f", activeforeground="#ffffff",
                  relief="flat", bd=0, padx=10, pady=4,
                  font=("Microsoft YaHei UI", 9, "bold"),
                  command=self.complete_all_full).pack(side="left", padx=(8, 0))
        ttk.Button(top, text="仅看未完成", command=self.filter_todo).pack(side="left", padx=(8, 0))
        ttk.Button(top, text="全部", command=lambda: self.filter_todo(False)).pack(side="left", padx=(6, 0))
        self.lbl_quest = ttk.Label(top, text="", style="Dim.TLabel")
        self.lbl_quest.pack(side="right")

        cols = ("cat", "title", "id", "state")
        self.tv = ttk.Treeview(self.tab_quest, columns=cols, show="headings", height=10)
        for c, w, t in (("cat", 70, "类别"), ("title", 220, "任务"),
                        ("id", 300, "内部 ID"), ("state", 150, "状态")):
            self.tv.heading(c, text=t)
            self.tv.column(c, width=w, anchor="w")
        vsb = ttk.Scrollbar(self.tab_quest, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y", pady=(0, 4), padx=(0, 2))
        self.tv.pack(fill="both", expand=True, pady=(0, 4))
        self.tv.tag_configure("done", foreground="#4a5568")
        self.tv.tag_configure("todo", foreground="#e6ecf7")
        self.tv.tag_configure("tracked", background="#24406b")

        ttk.Label(self.tab_quest, style="Dim.TLabel",
                  text="这里列出游戏里全部 83 个任务（主线10 / 支线15 / 收集10 / 教程12 / 传闻36）。"
                       "标记完成只改存档里的任务状态，已发过的奖励不会重发。").pack(anchor="w", pady=(0, 6))

    # ── 存档槽 ──
    def _set_all_slots(self, on):
        for v in self.slot_vars.values():
            v.set(on)

    def selected_slots(self):
        return [n for n, v in self.slot_vars.items() if v.get()]

    def refresh_slots(self):
        if self.busy:
            return
        p = Path(self.save.get())
        if not p.is_file():
            return
        self.lbl_slot.config(text="读取中…")

        def work():
            try:
                last, slots = editor.list_slots(p)
                self.root.after(0, lambda: self._slots_ready(last, slots))
            except Exception as ex:
                self.root.after(0, lambda: self.lbl_slot.config(text=f"读取失败: {ex}"))

        threading.Thread(target=work, daemon=True).start()

    def _slots_ready(self, last, slots):
        self.last_continue = last
        self.slot_meta = {s["name"]: s for s in slots}
        prev = {n for n, v in self.slot_vars.items() if v.get()}
        for w in self.slot_frame.winfo_children():
            w.destroy()
        self.slot_vars = {}
        if not slots:
            self.lbl_slot.config(text="没有可编辑的存档槽")
            return
        for i, s in enumerate(slots):
            var = tk.BooleanVar(value=(s["name"] == last) if not prev else (s["name"] in prev))
            self.slot_vars[s["name"]] = var
            extra = ""
            if s["nightmare"]:
                extra = f"    ·  轮回 {s['cycle']}    ·  剩余 {s['days']} 天"
            title = s["title"]
            if s.get("region") and s["region"] != title:
                title = f"{title}（{s['region']}）"
            text = (f"{s['label']}   {title}    ·  {s['difficulty']}"
                    f"{extra}    ·  {s['playtime']}    ·  {s['when']}")
            tk.Checkbutton(
                self.slot_frame, variable=var, bg=PANEL,
                fg="#f0b6c4" if s["nightmare"] else FG,
                selectcolor="#243044",
                activebackground=PANEL, activeforeground=FG, anchor="w",
                font=("Microsoft YaHei UI", 9), highlightthickness=0, bd=0,
                text=text).grid(row=i, column=0, sticky="w", pady=1)
        self.lbl_slot.config(text=f"游戏「继续」用的是 {last}")
        self.write(f"  读到你 {len(slots)} 个存档槽，默认勾选 {last}\n", "dim")

    def delete_selected_slots(self):
        """删除勾选的存档槽（PBZS 目录重建，删掉条目再重新编码）。"""
        if self.busy:
            return
        p = Path(self.save.get())
        if not p.is_file():
            messagebox.showerror("存档不存在", f"找不到:\n{p}")
            return
        targets = self.selected_slots()
        if not targets:
            messagebox.showwarning("没选存档槽", "先勾选要删除的存档槽。")
            return
        if len(targets) >= len(self.slot_vars):
            messagebox.showerror("不能全删", "至少保留一个存档槽。")
            return
        detail = "\n".join(
            f"  · {s['label']}  {s['title']}  {s['playtime']}  {s['when']}"
            for n, s in self.slot_meta.items() if n in targets)
        if not messagebox.askyesno(
                "确认删除存档槽（不可撤销）",
                f"将从存档文件里删掉这 {len(targets)} 个槽：\n\n{detail}\n\n"
                "· 游戏里的「载入存档」列表会少这几条\n"
                "· 会自动备份一份 .bak-时间戳，但删除本身不可逆\n\n"
                "确定删除？"):
            return
        if editor.game_running():
            if not messagebox.askyesno("游戏还在运行",
                                       "游戏正在运行。它下次手动存档可能把文件重写回去。\n\n仍要删除？"):
                return
        self.busy = True
        self.lbl_state.config(text="删除中…")
        self.write(f"\n▶ 删除存档槽: {', '.join(targets)}\n", "head")

        def work():
            try:
                info, bak = editor.delete_slots(p, targets)
                self.root.after(0, lambda: self._deleted(info, bak))
            except Exception as ex:
                self.root.after(0, lambda: self.fail(ex))

        threading.Thread(target=work, daemon=True).start()

    def _deleted(self, info, bak):
        self.busy = False
        self.lbl_state.config(text="")
        self.write(f"  已删除: {', '.join(info['deleted'])}\n", "ok")
        self.write(f"  保留  : {', '.join(info['remaining'])}\n", "dim")
        for n in info["notes"]:
            self.write(f"  {n}\n", "dim")
        self.write(f"  大小  : {info['size_before']:,} → {info['size_after']:,} 字节\n", "dim")
        if bak:
            self.write(f"  已备份 → {bak.name}\n", "dim")
        self.refresh_slots()
        self.refresh_status()
        messagebox.showinfo("完成", "存档槽已删除。\n\n重进游戏「载入存档」就会少这几条。")

    def _guard(self, dry_run):
        p = Path(self.save.get())
        if not p.is_file():
            messagebox.showerror("存档不存在", f"找不到:\n{p}")
            return None
        if not self.selected_slots():
            messagebox.showwarning("没选存档槽", "先在上面勾选要编辑的存档槽。")
            return None
        if editor.game_running() and not dry_run:
            ok = messagebox.askyesno(
                "游戏还在运行",
                "本作只在「手动存档」时写盘，所以现在改不会被立刻冲掉。\n\n"
                "但游戏自己的状态还在内存里：\n"
                "· 你一旦在游戏里手动存档，这次改动会被整份覆盖\n"
                "· 游戏也不会读你刚改的文件，要重进游戏才生效\n\n"
                "确定现在写入吗？")
            if not ok:
                return None
        return p

    def _build_story_tab(self):
        top = ttk.Frame(self.tab_story)
        top.pack(fill="x", pady=(8, 6))
        ttk.Button(top, text="读取主线进度", command=self.load_story).pack(side="left", padx=(2, 8))
        ttk.Button(top, text="设为追踪", command=self.track_selected).pack(side="left", padx=(0, 8))
        ttk.Button(top, text="推进到此", style="Accent.TButton",
                   command=self.jump_selected).pack(side="left")
        self.lbl_story = ttk.Label(top, text="", style="Dim.TLabel")
        self.lbl_story.pack(side="right")

        cols = ("order", "chapter", "title", "state")
        self.tvs = ttk.Treeview(self.tab_story, columns=cols, show="headings", height=10)
        for c, w, t in (("order", 50, "顺序"), ("chapter", 190, "章节"),
                        ("title", 180, "任务"), ("state", 220, "状态")):
            self.tvs.heading(c, text=t)
            self.tvs.column(c, width=w, anchor="w" if c != "order" else "center")
        vsb = ttk.Scrollbar(self.tab_story, orient="vertical", command=self.tvs.yview)
        self.tvs.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y", pady=(0, 4), padx=(0, 2))
        self.tvs.pack(fill="both", expand=True, pady=(0, 4))
        self.tvs.tag_configure("done", foreground="#4a5568")
        self.tvs.tag_configure("started", foreground="#e6ecf7")
        self.tvs.tag_configure("none", foreground="#6b7688")
        self.tvs.tag_configure("tracked", background="#24406b")

        ttk.Label(self.tab_story, style="Dim.TLabel",
                  justify="left",
                  text="「设为追踪」只改任务指针，不动任何进度，最安全。\n"
                       "「推进到此」会把前面所有主线标为完成、目标设为进行中——"
                       "但世界状态（关卡、剧情标记、已生成的角色）还停在原处，很可能导致任务无法实际推进，"
                       "务必先备份。").pack(anchor="w", pady=(0, 6))

    def _build_end_tab(self):
        top = ttk.Frame(self.tab_end)
        top.pack(fill="x", pady=(8, 6))
        ttk.Button(top, text="读取当前记录", command=self.load_endings).pack(side="left", padx=(2, 12))
        ttk.Button(top, text="收集度改为 100", command=self.set_completion).pack(side="left")
        self.lbl_end = ttk.Label(top, text="", style="Dim.TLabel")
        self.lbl_end.pack(side="right")

        box = tk.Frame(self.tab_end, bg=PANEL)
        box.pack(fill="x", pady=(4, 8), padx=2)
        tk.Label(box, text="修改记录里的结局", bg=PANEL, fg=FG,
                 font=("Microsoft YaHei UI", 11, "bold"), anchor="w").pack(
            fill="x", padx=14, pady=(10, 2))
        tk.Label(box, bg=PANEL, fg=DIM, anchor="w", justify="left",
                 font=("Microsoft YaHei UI", 9),
                 text="只改「查看轮回记录」里显示的结果，不会触发结局演出。"
                      "结局由决战结束时剧情判定。").pack(fill="x", padx=14)
        row = tk.Frame(box, bg=PANEL)
        row.pack(fill="x", padx=14, pady=(8, 12))
        for i, name in enumerate(["龙吟", "湮灭", "决战", "饮恨"]):
            ttk.Button(row, text=name, style="Big.TButton", width=10,
                       command=lambda n=i, s=name: self.set_ending(n, s)).pack(
                side="left", padx=(0, 10))

        # ── 游戏时长 ──
        tb = tk.Frame(self.tab_end, bg=PANEL)
        tb.pack(fill="x", pady=(0, 8), padx=2)
        tk.Label(tb, text="修改游戏时长", bg=PANEL, fg=FG,
                 font=("Microsoft YaHei UI", 11, "bold")).pack(side="left", padx=(14, 12), pady=10)
        self.play_var = tk.StringVar(value="")
        tk.Entry(tb, textvariable=self.play_var, bg="#0d1017", fg=FG, insertbackground=FG,
                 relief="flat", width=16, font=("Consolas", 10)).pack(side="left", ipady=4)
        tk.Label(tb, text="  支持 12h34m56s / 1:02:03 / 3600（秒）", bg=PANEL, fg=DIM,
                 font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(8, 12))
        ttk.Button(tb, text="写入时长", command=self.set_playtime).pack(side="left")

        self.end_text = tk.Text(self.tab_end, bg="#0d1017", fg="#c7d2e4", relief="flat",
                                font=("Consolas", 10), height=8, padx=12, pady=8,
                                insertbackground=FG)
        self.end_text.pack(fill="both", expand=True, padx=2, pady=(0, 4))
        self.end_text.insert("end", "点「读取当前记录」查看这一轮和首次通关记录的结局/收集度。\n")
        self.end_text.configure(state="disabled")

        ttk.Label(self.tab_end, style="Dim.TLabel", justify="left",
                  text="说明：「收集度」是存档里记录的一个数字（CycleRecords[*].Completion / "
                       "FirstClearRecord.Completion），改它只影响显示。\n"
                       "真正的收集内容在图鉴（Codex.DiscoveredEnemies）、特典（BonusCollection.UnlockedTags）、"
                       "Boss 记录里，那些需要一条条解锁，没法一个数字带过。").pack(anchor="w", pady=(0, 6))

    def _end_show(self, text):
        self.end_text.configure(state="normal")
        self.end_text.delete("1.0", "end")
        self.end_text.insert("end", text)
        self.end_text.configure(state="disabled")

    def load_endings(self):
        if self.busy:
            return
        p = Path(self.save.get())
        if not p.is_file():
            messagebox.showerror("存档不存在", f"找不到:\n{p}")
            return
        slots = self.selected_slots()
        target = slots[0] if slots else None
        self.busy = True
        self.lbl_end.config(text="读取中…")

        def work():
            try:
                slot, cycles, first = editor.load_endings(p, target)
                self.root.after(0, lambda: self._end_ready(slot, cycles, first))
            except Exception as ex:
                self.busy = False
                self.root.after(0, lambda: messagebox.showerror("读取失败", str(ex)))

        threading.Thread(target=work, daemon=True).start()

    def _end_ready(self, slot, cycles, first):
        self.busy = False
        self.lbl_end.config(text=slot or "")
        lines = [f"存档槽: {slot}", ""]
        if cycles:
            lines.append("轮回记录:")
            for c in cycles:
                lines.append(f"  第 {c['cycle']} 轮   结局：{c['ending_name']}"
                             f"   收集度：{c['completion']}%   难度索引：{c['difficulty']}")
        else:
            lines.append("轮回记录: 无")
        if first:
            lines.append("")
            lines.append(f"首次通关记录:  结局：{first['ending_name']}   "
                         f"收集度：{first['completion']}%")
        self._end_show("\n".join(lines))
        self.write("\n▶ 读取结局/收集记录\n", "head")
        self.write(f"  {slot}: {len(cycles)} 轮记录\n", "dim")

    def set_ending(self, idx, name):
        if not messagebox.askyesno(
                "确认修改结局记录",
                f"把记录里的结局改成「{name}」？\n\n"
                "· 只改「查看轮回记录」里显示的结局\n"
                "· 不会重放结局动画，也不会解锁对应剧情\n"
                "· 会同时改当前轮的 CycleRecords 和首次通关 FirstClearRecord"):
            return
        self.run(["set_ending"], params={"ending": idx})
        self.root.after(2500, self.load_endings)

    def set_playtime(self):
        text = self.play_var.get().strip()
        sec = editor.parse_playtime(text)
        if sec is None:
            messagebox.showerror("格式不对",
                                 f"看不懂「{text}」。\n\n"
                                 "可用格式：12h34m56s、1:02:03、3600（秒）")
            return
        if not messagebox.askyesno(
                "确认修改游戏时长",
                f"把游戏时长改成 {editor._fmt_playtime(sec)}（{sec:,} 秒）？\n\n"
                "会同时改 HasGameTime 和 CurrentCycleGameTime。"):
            return
        self.run(["set_playtime"], params={"playtime": sec})
        self.root.after(2500, self.load_endings)

    def set_completion(self):
        if not messagebox.askyesno(
                "确认：收集拉满 + 收集度 100",
                "会做这些事：\n\n"
                "· 图鉴·已发现敌人 → 补全到 169 个\n"
                "· 特典·美术 → 148 条全部解锁\n"
                "· 特典·音乐 → 66 条全部解锁\n"
                "· Boss 讨伐 → 已有 boss 补齐所有难度记录\n"
                "· 收集度数字 → 100\n\n"
                "做不到的：特典解锁用的 gameplay tag 枚举不出来（游戏里烘焙进包里），"
                "所以「解锁标签」保持原样。\n\n确定？"):
            return
        self.run(["collect_full", "set_completion"], params={"completion": 100})
        self.root.after(3000, self.load_endings)

    # ── 状态 ──
    def write(self, text, tag=None):
        self.log.insert("end", text, tag or ())
        self.log.see("end")

    def refresh_status(self):
        running = editor.game_running()
        self.lbl_game.config(text="● 游戏运行中" if running else "● 游戏未运行",
                             foreground=WARN if running else ACCENT)
        p = Path(self.save.get())
        if p.is_file():
            st = p.stat()
            self.lbl_save.config(
                text=f"{st.st_size:,} 字节   最后修改 "
                     f"{datetime.datetime.fromtimestamp(st.st_mtime):%Y-%m-%d %H:%M:%S}")
        else:
            self.lbl_save.config(text="存档不存在")

    def _tick(self):
        if not self.busy:
            self.refresh_status()
        self.root.after(2000, self._tick)

    # ── 通用操作 ──
    def pick_save(self):
        p = filedialog.askopenfilename(title="选择 PBZSaveGame.sav",
                                       filetypes=[("影之刃零存档", "*.sav"), ("全部", "*.*")])
        if p:
            self.save.set(p)
            self.refresh_status()
            self.refresh_slots()

    def open_dir(self):
        d = Path(self.save.get()).parent
        if d.is_dir():
            subprocess.Popen(["explorer", str(d)])

    def run_all(self):
        self.run([a for a, *_ in self._feat_specs()])

    def dry(self):
        self.run([a for a, *_ in self._feat_specs()], dry_run=True)

    def run(self, actions, dry_run=False, params=None):
        if self.busy:
            return
        p = self._guard(dry_run)
        if p is None:
            return
        slots = self.selected_slots()
        self._last_actions = list(actions)
        titles = " + ".join(editor.ACTION_LABELS.get(a, a) for a in actions)
        self.busy = True
        self.lbl_state.config(text="处理中…")
        self.write(f"\n▶ {titles}{'（试运行）' if dry_run else ''}\n", "head")
        self.write(f"  目标槽: {', '.join(slots)}\n", "dim")

        def work():
            try:
                log, bak, size = editor.apply(p, actions, slots=slots,
                                              dry_run=dry_run, params=params)
                self.root.after(0, lambda: self.done(log, bak, size, dry_run))
            except Exception as ex:
                self.root.after(0, lambda: self.fail(ex))

        threading.Thread(target=work, daemon=True).start()

    def done(self, log, bak, size, dry_run):
        self.busy = False
        self.lbl_state.config(text="")
        for name, lines in log:
            self.write(f"  [{name}]\n")
            for ln in lines:
                self.write(f"     {ln}\n")
        if not log:
            self.write("  没有产生任何修改\n", "warn")
        if dry_run:
            self.write(f"  试运行完成，将输出 {size:,} 字节（未写盘）\n", "dim")
        elif bak is None:
            self.write("  没有需要修改的内容，存档未写盘。\n", "dim")
        else:
            self.write(f"  已备份 → {bak.name}\n", "dim")
            self.write(f"  已写入 {size:,} 字节 ✔\n", "ok")
            messagebox.showinfo("完成", "修改已写入。\n\n现在可以启动游戏读档了。")
        # 任务/主线类操作完自动刷新对应列表，省得再点一次
        acts = getattr(self, "_last_actions", [])
        if any(a in ("main_jump", "main_track") for a in acts):
            self.root.after(500, self.load_story)
        elif any(a.startswith("quest") for a in acts):
            self.root.after(500, self.load_quests)
        self.refresh_status()

    def fail(self, ex):
        self.busy = False
        self.lbl_state.config(text="")
        self.write(f"  失败: {ex}\n", "err")
        messagebox.showerror("修改失败", str(ex))

    # ── 任务页 ──
    def load_quests(self):
        if self.busy:
            return
        p = Path(self.save.get())
        if not p.is_file():
            messagebox.showerror("存档不存在", f"找不到:\n{p}")
            return
        slots = self.selected_slots()
        if not slots:
            messagebox.showwarning("没选存档槽", "先在上面勾选一个存档槽。")
            return
        target = slots[0]
        self.busy = True
        self.lbl_quest.config(text="读取中…")
        self.write(f"\n▶ 读取全部任务（{target}）\n", "head")

        def work():
            try:
                slot, qs = editor.load_all_quests(p, target)
                self.root.after(0, lambda: self._quests_ready(slot, qs))
            except Exception as ex:
                self.root.after(0, lambda: self._quests_fail(ex))

        threading.Thread(target=work, daemon=True).start()

    def _quests_ready(self, slot, qs):
        self.busy = False
        self.quests = qs
        self.quest_slot = slot
        self._render_quests()
        todo = sum(1 for q in qs if q["status"] != "done")
        self.lbl_quest.config(text=f"{slot} · 共 {len(qs)} 个，未完成 {todo} 个")
        self.write(f"  {slot}：共 {len(qs)} 个任务，未完成 {todo} 个\n", "ok")

    def _render_quests(self, only_todo=None):
        if only_todo is None:
            only_todo = getattr(self, "quest_only_todo", False)
        self.quest_only_todo = only_todo
        self.tv.delete(*self.tv.get_children())
        MARK = {"done": "✔ 已完成", "started": "○ 进行中", "none": "· 未开始"}
        shown = 0
        for q in self.quests:
            if only_todo and q["status"] == "done":
                continue
            state = MARK[q["status"]] + ("   ← 追踪" if q["tracked"] else "")
            tags = ("tracked",) if q["tracked"] else \
                   (("done",) if q["status"] == "done" else ("todo",))
            self.tv.insert("", "end", iid=q["path"],
                           values=(q["category_zh"], q["title"] or q["id"],
                                   q["id"], state), tags=tags)
            shown += 1
        if self.quests:
            self.lbl_quest.config(text=f"{self.quest_slot} · 显示 {shown}/{len(self.quests)}")

    def filter_todo(self, only=True):
        if not self.quests:
            messagebox.showinfo("还没读取", "先点「读取全部任务」。")
            return
        self._render_quests(bool(only))

    def _quests_fail(self, ex):
        self.busy = False
        self.lbl_quest.config(text="")
        self.write(f"  读取任务失败: {ex}\n", "err")
        messagebox.showerror("读取失败", str(ex))

    def complete_selected(self):
        sel = self.tv.selection()
        if not sel:
            messagebox.showinfo("没选任务", "先在上面列表里点一条任务。")
            return
        path = sel[0]
        q = next((x for x in self.quests if x["path"] == path), None)
        if q is None:
            return
        if q["status"] == "done":
            messagebox.showinfo("已完成", f"{q['title'] or q['id']} 已经是完成状态。")
            return
        name = q["title"] or q["id"]
        if not messagebox.askyesno(
                "确认完成任务",
                f"把这条任务标记为「已完成」？\n\n[{q['category_zh']}] {name}\n{q['id']}"):
            return
        self.run(["quest_path"], params={"quest_path": path})

    def complete_all(self):
        todo = [q for q in self.quests if q["status"] == "started"]
        if not todo:
            messagebox.showinfo("没有进行中任务", "先点「读取全部任务」。")
            return
        if not messagebox.askyesno(
                "确认完成全部进行中",
                f"把 {len(todo)} 个「进行中」任务标记完成？\n\n"
                "未开始的任务不动。主线强行标记可能影响剧情推进。"):
            return
        self.run(["quests_all"])

    def complete_all_full(self):
        if not self.quests:
            messagebox.showinfo("还没读取", "先点「读取全部任务」。")
            return
        todo = [q for q in self.quests if q["status"] != "done"]
        if not todo:
            messagebox.showinfo("全部已完成", "所有任务都已经是完成状态。")
            return
        bycat = {}
        for q in todo:
            bycat[q["category_zh"]] = bycat.get(q["category_zh"], 0) + 1
        detail = "  ".join(f"{k} {v} 个" for k, v in bycat.items())
        if not messagebox.askyesno(
                "确认：全部标完成（含未开始）",
                f"把剩下 {len(todo)} 个任务全部标记完成：\n\n{detail}\n\n"
                "· 未开始的任务会**新建**一条空分支的任务记录再标完成\n"
                "· 分支进度、任务计数都是伪造的，游戏里可能显示异常\n"
                "· 主线剧情推进大概率错乱\n\n"
                "建议先手动存一个新档再试。确定？"):
            return
        self.run(["quests_all_full"])

    # ── 主线页 ──
    def load_story(self):
        if self.busy:
            return
        p = Path(self.save.get())
        if not p.is_file():
            messagebox.showerror("存档不存在", f"找不到:\n{p}")
            return
        slots = self.selected_slots()
        if not slots:
            messagebox.showwarning("没选存档槽", "先在上面勾选一个存档槽。")
            return
        target = slots[0]
        self.busy = True
        self.lbl_story.config(text="读取中…")
        self.write(f"\n▶ 读取主线进度（{target}）\n", "head")

        def work():
            try:
                slot, rows = editor.load_story(p, target)
                self.root.after(0, lambda: self._story_ready(slot, rows))
            except Exception as ex:
                self.root.after(0, lambda: self._story_fail(ex))

        threading.Thread(target=work, daemon=True).start()

    def _story_ready(self, slot, rows):
        self.busy = False
        self.story = rows
        self.tvs.delete(*self.tvs.get_children())
        MARK = {"done": "✔ 已完成", "started": "○ 进行中", "none": "· 未开始"}
        for r in rows:
            state = MARK[r["status"]] + ("    ← 当前追踪" if r["tracked"] else "")
            tags = (("tracked",) if r["tracked"] else (r["status"],))
            self.tvs.insert("", "end", iid=str(r["order"]),
                            values=(r["order"], r["chapter"], r["title"], state),
                            tags=tags)
        done = sum(1 for r in rows if r["status"] == "done")
        self.lbl_story.config(text=f"{slot} · 主线 {len(rows)} 个，已完成 {done} 个")
        self.write(f"  {slot}：主线 {len(rows)} 个，已完成 {done} 个\n", "ok")

    def _story_fail(self, ex):
        self.busy = False
        self.lbl_story.config(text="")
        self.write(f"  读取主线失败: {ex}\n", "err")
        messagebox.showerror("读取失败", str(ex))

    def _story_pick(self):
        sel = self.tvs.selection()
        if not sel:
            messagebox.showinfo("没选任务", "先在列表里点一个主线。")
            return None
        order = int(sel[0])
        return next((r for r in self.story if r["order"] == order), None)

    def track_selected(self):
        r = self._story_pick()
        if r is None:
            return
        if not messagebox.askyesno("切换追踪",
                                   f"把当前追踪主线切到「{r['title']}」？\n\n"
                                   "只改任务指针，不动任务进度。"):
            return
        self.run(["main_track"], params={"main_order": r["order"]})

    def jump_selected(self):
        r = self._story_pick()
        if r is None:
            return
        if not messagebox.askyesno(
                "确认推进（高风险）",
                f"把主线推进到「{r['title']}」？\n\n"
                "会做三件事：\n"
                "1. 把前面所有主线标记为已完成\n"
                f"2. 把「{r['title']}」设为进行中\n"
                "3. 切换当前追踪\n\n"
                "⚠ 世界状态不会跟着跳：关卡、剧情标记、已生成的角色都还在原处，\n"
                "很可能出现任务指向不存在目标的情况。请先备份。"):
            return
        self.run(["main_jump"], params={"main_order": r["order"]})

    # ── 还原 ──
    def restore(self):
        if editor.game_running():
            if not messagebox.askyesno("游戏还在运行", "游戏正在运行，仍要还原？"):
                return
        p = Path(self.save.get())
        b = editor.latest_backup(p)
        if not b:
            messagebox.showinfo("没有备份", "这个存档旁边没有找到 .bak-* 备份。")
            return
        if not messagebox.askyesno("确认还原", f"用这个备份覆盖当前存档？\n\n{b.name}\n"
                                               f"（{b.stat().st_size:,} 字节）"):
            return
        try:
            editor.restore(p, b)
            self.write(f"\n已还原 ← {b.name}\n", "ok")
            messagebox.showinfo("完成", "已还原。")
        except Exception as ex:
            messagebox.showerror("还原失败", str(ex))
        self.refresh_status()


def selftest() -> int:
    base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) \
        else Path(__file__).resolve().parent
    out = base / "selftest.log"
    lines = []
    try:
        lines.append(f"python: {sys.version.split()[0]}")
        lines.append(f"MEIPASS: {getattr(sys, '_MEIPASS', '(未打包)')}")
        save = editor.DEFAULT_SAVE
        lines.append(f"存档: {save}  存在={Path(save).is_file()}")
        for rel in ("data/game_ids.json", "data/fog_by_code.json",
                    "data/catalog/catalog.json", "data/loc_zh.json",
                    "data/quests.json",
                    "native/pbz_codec.dll", "native/manifest.json"):
            lines.append(f"{rel}: {(editor.HERE / rel).is_file()}")
        bells = editor.all_bells()
        fog = editor.fog_by_code()
        cat = editor.load_catalog()
        lines.append(f"铃铛: {len(bells)} 个   迷雾区: {len(fog)} 个")
        lines.append("物品目录: " + ", ".join(f"{k}={len(v)}" for k, v in sorted(cat.items())))
        lines.append(f"密钥: {pbzcore.load_key().hex()[:16]}…")

        last, slots = editor.list_slots(save)
        lines.append(f"LastContinueSlot: {last}")
        for s in slots:
            extra = (f" · 轮回{s['cycle']} · 剩余{s['days']}天" if s["nightmare"] else "")
            title = s["title"]
            if s.get("region") and s["region"] != title:
                title = f"{title}（{s['region']}）"
            lines.append(f"  [{s['label']}] {title} · {s['difficulty']}{extra} · "
                         f"{s['playtime']} · {s['when']} · 原长 {s['size']}")

        cont, entries, repl, log, blob = editor.build(save, editor.DEFAULT_ALL, slots=[last])
        lines.append(f"目标槽: {last}")
        for name, ls in log:
            lines.append(f"  [{name}]")
            for ln in ls:
                lines.append(f"     {ln}")
        lines.append(f"重编码后大小: {len(blob)} 字节")
        decoded = cont.decode(blob)
        ok = len(decoded) == len(entries) and all(
            b["raw"] == repl[b["name"]] for b in decoded if b["name"] in repl)
        lines.append(f"回读校验: {'通过' if ok else '失败'}")

        slot, qs = editor.load_all_quests(save, last)
        import collections as _c
        bycat = _c.Counter(q["category_zh"] for q in qs)
        lines.append(f"任务({slot}): {len(qs)} 个 → " +
                     " / ".join(f"{k}{v}" for k, v in bycat.items()) +
                     f"；已完成 {sum(1 for q in qs if q['status'] == 'done')} 个")
        slot, rows = editor.load_story(save, last)
        lines.append(f"主线({slot}): {len(rows)} 个，已完成 "
                     f"{sum(1 for r in rows if r['status'] == 'done')} 个")
        for r in rows:
            lines.append(f"  {r['order']:2d} {r['chapter']} · {r['title']} · {r['status']}"
                         + ("  ← 追踪" if r["tracked"] else ""))
        lines.append("RESULT: " + ("OK" if ok else "FAIL"))
    except Exception:
        import traceback
        lines.append("RESULT: FAIL")
        lines.append(traceback.format_exc())
    out.write_text("\n".join(lines), encoding="utf-8")
    return 0


def main():
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    root = tk.Tk()
    try:
        root.iconbitmap(default="")
    except Exception:
        pass
    app = App(root)
    if "--tab" in sys.argv:
        try:
            want = sys.argv[sys.argv.index("--tab") + 1]
        except IndexError:
            want = ""
        if want == "quest":
            app.nb.select(app.tab_quest)
            root.after(1200, app.load_quests)
        elif want == "story":
            app.nb.select(app.tab_story)
            root.after(1200, app.load_story)
        elif want == "end":
            app.nb.select(app.tab_end)
            root.after(1200, app.load_endings)
    root.mainloop()


if __name__ == "__main__":
    main()
