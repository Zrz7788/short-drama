# -*- coding: utf-8 -*-
r"""尝试抓中文官方来源，复核那 18 条领域事实。

## 为什么

档案自己写着来源局限：

> 取的是**英文维基**，不是中方官方标准（`mca.gov.cn` 证书自签，抓取失败）。
> 中国殡仪馆的具体做法可能与英美有差异。

**这是真欠账**：题材是中国县级殡仪馆，结论却建立在英文资料上。

## 抓什么（按对剧本的杀伤力排序）

1. **死亡证明谁签发**（F-22/F-23/F-24）——第 1 集核心钩子就建立在这条上，
   最需要中文依据。相关：《死亡医学证明》由医疗机构出具、
   《殡葬管理条例》要求火化需死亡证明。
2. **火化流程**（F-21）——是否凭证明火化、炉温与炉门。
3. **遗体整容/防腐**（F-12/F-13/F-14）——中国殡仪馆的实际做法。
4. **身份标识**（F-20）——脚牌/手牌/尸袋标签的实际用法。

## 已知障碍

- `mca.gov.cn`（民政部）证书自签，标准 urllib 会拒
- 本机直连只能到国内；部分政府站可能限流
- 上次 `web_fetch` 工具本身坏，所以走 `tools/fetch_page.py`
"""
import os
import subprocess
import sys

sys.path.insert(0, ".")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, ".tools", "research-cn")

# 中文官方/权威来源候选（政府、法规库、官方媒体）
TARGETS = [
    ("殡葬管理条例（国务院）全文",
     "https://www.gov.cn/gongbao/content/2012/content_2205121.htm"),
    ("民政部 殡葬管理",
     "https://www.mca.gov.cn/"),
    ("国家法律法规数据库 殡葬管理条例",
     "https://flk.npc.gov.cn/"),
    ("死亡医学证明 管理规定（卫健委）",
     "https://www.nhc.gov.cn/"),
    ("中国殡葬协会",
     "http://www.chinabz.org.cn/"),
    ("火化 需凭死亡证明",
     "https://www.baidu.com/s?wd=%E7%81%AB%E5%8C%96%20%E5%87%AD%E6%AD%BB%E4%BA%A1%E8%AF%81%E6%98%8E%20%E8%A7%84%E5%AE%9A"),
    ("遗体整容 殡仪馆 流程",
     "https://www.baidu.com/s?wd=%E6%AE%A1%E4%BB%AA%E9%A6%86%20%E9%81%97%E4%BD%93%E6%95%B4%E5%AE%B9%20%E6%B5%81%E7%A8%8B%20%E8%A7%84%E8%8C%83"),
    ("死亡医学证明 签发 单位",
     "https://www.baidu.com/s?wd=%E6%AD%BB%E4%BA%A1%E5%8C%BB%E5%AD%A6%E8%AF%81%E6%98%8E%20%E7%AD%BE%E5%8F%91%20%E5%8D%95%E4%BD%8D%20%E8%A7%84%E5%AE%9A"),
]

os.makedirs(OUT, exist_ok=True)
print("输出目录：%s\n" % OUT)
print("=" * 74)
print("抓取中文来源（每个超时 90 秒，失败不阻塞）")
print("=" * 74)

ok = 0
for i, (name, url) in enumerate(TARGETS, 1):
    fn = os.path.join(OUT, "cn-%02d.txt" % i)
    print("\n[%d/%d] %s" % (i, len(TARGETS), name))
    print("      %s" % url[:96])
    try:
        p = subprocess.run(
            [sys.executable, os.path.join(ROOT, "tools", "fetch_page.py"), url,
             "--out", fn],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=180)
        out = ((p.stdout or "") + (p.stderr or "")).strip().splitlines()
        for ln in out[-3:]:
            print("      %s" % ln[:100])
        if os.path.exists(fn):
            sz = os.path.getsize(fn)
            print("      已存 %d 字节" % sz)
            if sz > 400:
                ok += 1
    except subprocess.TimeoutExpired:
        print("      ⏱ 超时")
    except Exception as e:
        print("      ✗ %s: %s" % (type(e).__name__, str(e)[:70]))

print("\n" + "=" * 74)
print("结果：%d/%d 有内容" % (ok, len(TARGETS)))
print("=" * 74)
if os.path.isdir(OUT):
    for f in sorted(os.listdir(OUT)):
        p = os.path.join(OUT, f)
        print("  %-24s %8d B" % (f, os.path.getsize(p)))
