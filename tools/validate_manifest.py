# -*- coding: utf-8 -*-
"""流水线清单校验（薄封装）。

**解析器只有一份**，在 `pipeline/manifest.py`。这里原来有一套独立的解析
（用正则凑的），跟执行层各写各的——两份必然分叉。而第一版就因为不认折行
和行尾注释误报了一堆假问题。现在统一到同一个解析器。

这个脚本只做校验与展示，不碰数据库。

用法： python tools/validate_manifest.py [清单路径]
退出码 0 = 通过。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import manifest as M   # noqa: E402


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else M.DEFAULT_PATH
    try:
        m = M.load(path, use_cache=False)
    except M.ManifestError as e:
        print("清单解析失败:", e)
        return 1

    print("清单:", os.path.relpath(path, M.ROOT))
    print("名称 %s  v%s" % (m.name, m.version))
    print("单集预算 ¥%s  硬顶 ¥%s  每阶段最多改 %s 次"
          % (m.budget_default, m.hard_cap, m.max_revisions))
    print("阶段 %d 个，运行单元 %d 个，登记技能 %d 个"
          % (len(m.stages), len(m.run_units()), len(m.required_skills)))

    print("\n%-16s %-5s %-30s %s" % ("运行单元", "门禁", "产出", "子阶段"))
    print("-" * 76)
    for u in m.run_units():
        subs = ",".join(x.name for x in u.substages) if u.substages else "-"
        print("%-16s %-5s %-30s %s"
              % (u.key, "人批" if u.human_approval else "自动",
                 ",".join(u.produces) or "-", subs))

    probs, warns = m.validate()
    print("\n人工门禁 %d 道：%s" % (len(m.gate_names), ", ".join(m.gate_names)))
    print("\n" + "=" * 64)
    for w in warns:
        print("待办:", w)
    for p in probs:
        print("问题:", p)
    if probs:
        print("共 %d 个问题 -> **有失败**" % len(probs))
        return 1
    print("共 0 个问题 -> 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
