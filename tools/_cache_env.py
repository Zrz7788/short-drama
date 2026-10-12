# -*- coding: utf-8 -*-
r"""从 WorkBuddy 的 app.asar 里找：缓存目录能不能被环境变量覆盖。

界面只给了「打开目录」没给「更改」，所以想知道有没有后门。
思路：在 asar 原始字节里找 `.workbuddy` 附近的 ASCII 串，看有没有
  WORKBUDDY_* / *_HOME / *_DIR 这类变量名，以及是否用了环境变量优先。
"""
import re

ASAR = r"G:\WorkBuddy\resources\app.asar"
print("扫描 %s" % ASAR)

# 环境变量名的样子：全大写 + 下划线，含 WORKBUDDY / BUDDY / CODEBUDDY
ENV_RE = re.compile(rb"\b((?:WORKBUDDY|CODEBUDDY|WB|GENIE)[A-Z0-9_]{2,40})\b")
# 通用可疑的路径变量
GEN_RE = re.compile(rb"\b([A-Z][A-Z0-9_]{3,30}_(?:HOME|DIR|PATH|ROOT|CACHE))\b")

envs = {}
gens = {}
size = 0
CHUNK = 8 << 20
with open(ASAR, "rb") as f:
    while True:
        buf = f.read(CHUNK)
        if not buf:
            break
        size += len(buf)
        for m in ENV_RE.finditer(buf):
            k = m.group(1).decode("ascii", "ignore")
            envs[k] = envs.get(k, 0) + 1
        for m in GEN_RE.finditer(buf):
            k = m.group(1).decode("ascii", "ignore")
            gens[k] = gens.get(k, 0) + 1

print("扫描 %.0f MB\n" % (size / 1048576.0))

print("=== 含 WORKBUDDY/CODEBUDDY 的环境变量名 ===")
for k, n in sorted(envs.items(), key=lambda x: -x[1])[:40]:
    print("  %-44s ×%d" % (k, n))
if not envs:
    print("  （无）")

print("\n=== 通用的 *_HOME / *_DIR / *_PATH / *_CACHE 变量名 ===")
for k, n in sorted(gens.items(), key=lambda x: -x[1])[:30]:
    print("  %-44s ×%d" % (k, n))
if not gens:
    print("  （无）")

print("\n=== 结论 ===")
cand = [k for k in envs if any(t in k for t in
                               ("HOME", "DIR", "PATH", "ROOT", "CACHE", "DATA"))]
if cand:
    print("  有可能的覆盖变量：")
    for k in sorted(cand):
        print("    %s" % k)
else:
    print("  没找到明显的「缓存目录覆盖」环境变量。")
    print("  → 该目录路径很可能在代码里写死为 ~/.workbuddy，")
    print("    界面不给「更改」是对的。")
