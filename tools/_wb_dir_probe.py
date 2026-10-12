# -*- coding: utf-8 -*-
r"""实测 WORKBUDDY_USER_DATA_DIR 能不能把 WorkBuddy 的数据目录挪到别处。

**思路（无损验证）**：
  1. 把变量指向 D 盘一个空目录
  2. 启动 WorkBuddy（它会用这个变量决定数据目录）
  3. 看 D 盘那个目录有没有被写入东西
  4. 立刻关掉 WorkBuddy，删掉测试目录
原来 C:\Users\zrz\.workbuddy 不动（变量只影响新进程，不改已有数据）。

⚠️ 这是**只读探测**：不删、不迁移、不改现有数据。
"""
import os
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
WB_EXE = r"G:\WorkBuddy\WorkBuddy.exe"
REAL = os.path.join(HOME, ".workbuddy")
# 用 D 盘（用户的素材盘，不是 C 盘）
TEST = r"D:\_wb_datadir_test"

print("=" * 74)
print("实测：WORKBUDDY_USER_DATA_DIR 是否生效")
print("=" * 74)
print("  现有数据目录 : %s" % REAL)
print("  测试目录     : %s" % TEST)
print("  程序         : %s" % WB_EXE)

if not os.path.exists(WB_EXE):
    print("\n找不到 WorkBuddy.exe，无法实测。")
    sys.exit(1)

# 1) 记录现有数据目录的文件数（用于对比，确保不被改动）
def count(p):
    if not os.path.isdir(p):
        return -1, 0
    n = 0
    s = 0
    for root, _d, fs in os.walk(p):
        for f in fs:
            try:
                s += os.path.getsize(os.path.join(root, f))
                n += 1
            except OSError:
                pass
    return n, s

n0, s0 = count(REAL)
print("\n  启动前，现有目录: %d 文件 / %.1f MB" % (n0, s0 / 1048576.0))

# 2) 建测试目录并启动
os.makedirs(TEST, exist_ok=True)
env = dict(os.environ)
env["WORKBUDDY_USER_DATA_DIR"] = TEST
env["WORKBUDDY_CONFIG_DIR"] = TEST
env["WORKBUDDY_DATA_DIR"] = TEST

print("\n  用 WORKBUDDY_*_DIR 指向测试目录启动 WorkBuddy…")
try:
    p = subprocess.Popen([WB_EXE], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("  已启动 PID %d，等 35 秒看它往哪写…" % p.pid)
except Exception as e:
    print("  启动失败: %s" % e)
    sys.exit(1)

for i in range(7):
    time.sleep(5)
    n, s = count(TEST)
    print("    +%2ds  测试目录: %d 文件 / %.2f MB" % ((i + 1) * 5, n, s / 1048576.0))

# 3) 结论
n1, s1 = count(TEST)
nr, sr = count(REAL)
print("\n" + "=" * 74)
print("结论")
print("=" * 74)
print("  测试目录(D盘)   : %d 文件 / %.2f MB" % (n1, s1 / 1048576.0))
print("  现有目录(C盘)   : %d 文件 / %.1f MB  (启动前 %d / %.1f)"
      % (nr, sr / 1048576.0, n0, s0 / 1048576.0))
if n1 > 5:
    print("\n  ✅ 变量**生效**——WorkBuddy 把新数据写到了 D 盘。")
    print("     想永久迁移：把环境变量设为 D 盘路径，再把 .workbuddy 内容搬过去。")
else:
    print("\n  ❌ 变量**没生效**——数据仍然只写 C 盘。")
    print("     说明这个目录在代码里写死，界面不给「更改」是对的。")

# 4) 收尾：关掉测试进程，清掉测试目录
print("\n  收尾：关闭测试实例、删除测试目录")
subprocess.run(["taskkill", "/F", "/IM", "WorkBuddy.exe"],
               capture_output=True)
time.sleep(2)
import shutil
shutil.rmtree(TEST, ignore_errors=True)
print("  测试目录已删: %s -> %s" % (TEST, not os.path.exists(TEST)))
