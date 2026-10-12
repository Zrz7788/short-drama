# -*- coding: utf-8 -*-
r"""查 WorkBuddy 的缓存目录是否可配置（界面没有「更改」按钮，但可能有配置文件）。

同时确认那个设置页到底属于谁 —— 图里的左侧栏有「记忆与进化 / 智能体 / 模型」，
像 DSH 的设置；但路径写的是 `.workbuddy` 和 `WorkBuddy`，像 WorkBuddy 的。
**先弄清是谁的设置，再谈怎么改。**
"""
import json
import os

HOME = os.path.expanduser("~")
APPDATA = os.environ.get("APPDATA", "")
LOCAL = os.environ.get("LOCALAPPDATA", "")

print("=" * 74)
print("1) .workbuddy 里是什么")
print("=" * 74)
wb = os.path.join(HOME, ".workbuddy")
if os.path.isdir(wb):
    for name in sorted(os.listdir(wb)):
        p = os.path.join(wb, name)
        if os.path.isdir(p):
            n = sum(len(fs) for _, _, fs in os.walk(p))
            s = 0
            for root, _d, fs in os.walk(p):
                for f in fs:
                    try:
                        s += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
            print("  [D] %-32s %8.1f MB  %d 文件" % (name, s / 1048576.0, n))
        else:
            print("      %-32s %8d B" % (name, os.path.getsize(p)))

print("\n" + "=" * 74)
print("2) 顶层配置文件（可改的关键）")
print("=" * 74)
for fn in ("config.json", "settings.json", "preferences.json",
           "app-config.json", "workbuddy.json", ".config.json"):
    for base in (wb, os.path.join(APPDATA, "WorkBuddy"),
                 os.path.join(LOCAL, "WorkBuddy")):
        p = os.path.join(base, fn)
        if os.path.exists(p):
            print("  ✅ %s (%d B)" % (p, os.path.getsize(p)))
            try:
                d = json.load(open(p, encoding="utf-8"))
                for k in d:
                    if any(t in k.lower() for t in
                           ("cache", "dir", "path", "home", "workspace", "storage")):
                        print("       %s = %s" % (k, str(d[k])[:100]))
            except Exception as e:
                print("       (非 JSON: %s)" % e)

print("\n" + "=" * 74)
print("3) app.asar 里搜缓存目录的判定逻辑（G:\\WorkBuddy）")
print("=" * 74)
asar = r"G:\WorkBuddy\resources\app.asar"
print("  app.asar: %s (%s)" % (asar, "存在" if os.path.exists(asar) else "不存在"))
print("  ⚠️ 213 MB，全量扫描太慢；改用 strings 思路只找关键片段")

if os.path.exists(asar):
    import re
    keys = [b".workbuddy", b"WORKBUDDY_CACHE", b"cacheDir", b"cache_dir",
            b"systemCache", b"storagePath", b"workspacePath"]
    hits = {k: 0 for k in keys}
    CHUNK = 4 << 20
    with open(asar, "rb") as f:
        while True:
            buf = f.read(CHUNK)
            if not buf:
                break
            for k in keys:
                hits[k] += buf.count(k)
    for k, n in hits.items():
        print("    %-22s 出现 %d 次" % (k.decode(), n))

print("\n" + "=" * 74)
print("4) 判断这个设置页属于谁")
print("=" * 74)
# 图里左侧栏项：通用/个人主页/套餐与积分/外观/快捷键/个性化/记忆与进化/智能体/模型/助理/数据管理/应用管理/安全中心
dsh_markers = ["记忆与进化", "智能体", "套件自动更新"]
for label, base in (("DSH", os.path.join(HOME, ".dsh")),
                    ("WorkBuddy", wb)):
    print("  %-10s -> %s" % (label, base))
print()
print("  线索：设置页提到「套件自动更新：自动更新已安装的套件」")
print("  → 「套件」是 DSH 对 plugin/bundle 的叫法，WorkBuddy 叫「技能」")
print("  但缓存路径写成 .workbuddy / WorkBuddy …… 需要看截图对应的进程")
import subprocess
p = subprocess.run(["tasklist"], capture_output=True, text=True,
                   encoding="utf-8", errors="replace")
for ln in (p.stdout or "").splitlines():
    if any(k in ln for k in ("DeepSeek", "WorkBuddy", "Harness")):
        print("  运行中: %s" % ln.strip()[:90])
