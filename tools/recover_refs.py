# -*- coding: utf-8 -*-
"""一次性恢复：把"API 成功但没落盘"的图重新下载回来。

背景：`stage_refs` 原来把下载失败也写成 `succeeded`，于是 7 张图（¥2.10）
已经计费、磁盘上却一张都没有。但 `image_generation.image_url` 里存着
**模型返回的签名 URL（有效期 24h）**，所以可以直接重下，**不必重新生成、
不必再付费**。

它做四件事：
  1. 找出 status='succeeded' 但 local_path 为空/文件不存在的记录
  2. 用 image_url 重新下载，**按内容**定扩展名
  3. 回填 image_generation.local_path 与 entity_ref.path/sha256
  4. 状态不对的（下载仍失败）标成 'failed'，不假装成功

用法：python tools/recover_refs.py            # 只报告
      python tools/recover_refs.py --apply    # 真下载并回填
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import ark as A  # noqa: E402
from pipeline import common as C  # noqa: E402
from pipeline import stage_refs as SR  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="恢复没落盘的参考图")
    ap.add_argument("--apply", action="store_true", help="真下载并回填（默认只报告）")
    C.add_common_args(ap)
    args = ap.parse_args()

    con = C.connect(args.db)
    rows = [dict(r) for r in con.execute(
        "SELECT ig.id, ig.entity_id, ig.view, ig.image_url, ig.local_path, ig.cost,"
        "       e.name, e.kind, e.role "
        "FROM image_generation ig JOIN entity e ON e.id = ig.entity_id "
        "ORDER BY ig.id")]

    need = []
    for r in rows:
        p = r["local_path"]
        exists = bool(p) and os.path.exists(C.abs_path(p))
        if not exists:
            need.append(r)

    print("image_generation 共 %d 条，其中文件不存在/未落盘：%d 条"
          % (len(rows), len(need)))
    if not need:
        print("没有要恢复的。")
        con.close()
        return 0

    total = sum(r["cost"] or 0 for r in need)
    print("这些记录已计费合计 ¥%.2f\n" % total)
    for r in need:
        has_url = bool(r["image_url"])
        print("  #%-3s %-22s %-12s %s"
              % (r["id"], r["name"], r["view"],
                 "有 URL 可重下" if has_url else "**没有 URL，只能重新生成**"))
    if not args.apply:
        print("\n（只报告。加 --apply 真下载并回填。）")
        con.close()
        return 0

    ok = fail = skip = 0
    # out_path(ep, e, view) 需要 ep（它用 ep 拼目录，但不依赖 ep 的具体值）
    ep = con.execute("SELECT * FROM episode ORDER BY seq LIMIT 1").fetchone()
    ep = dict(ep) if ep else {"seq": 1}
    for r in need:
        tag = "#%s %s %s" % (r["id"], r["name"], r["view"])
        if not r["image_url"]:
            skip += 1
            print("  跳过 %s：没有 URL" % tag)
            continue
        # 落盘路径沿用 stage_refs 的规范（assets_root + refs/kind/名字_视角.ext）
        ent = {"id": r["entity_id"], "kind": r["kind"], "role": r["role"],
               "name": r["name"]}
        rel = SR.out_path(ep, ent, r["view"])
        try:
            A.download(r["image_url"], C.abs_path(rel))
        except Exception as ex:
            fail += 1
            print("  失败 %s：%s" % (tag, str(ex)[:80]))
            con.execute("UPDATE image_generation SET status='failed', error=? WHERE id=?",
                        ("恢复下载失败：%s" % str(ex)[:400], r["id"]))
            con.commit()
            continue
        # 按内容校正扩展名（Seedream 返回 JPEG）
        real = C.sniff_image_ext(C.abs_path(rel))
        if real and not rel.lower().endswith(real):
            new = rel[:rel.rfind(".")] + real
            try:
                os.replace(C.abs_path(rel), C.abs_path(new))
                rel = new
            except OSError:
                pass
        sha = C.file_sha256(C.abs_path(rel))
        size = os.path.getsize(C.abs_path(rel))
        con.execute("UPDATE image_generation SET local_path=?, status='succeeded',"
                    " error=NULL WHERE id=?", (rel, r["id"]))
        con.execute(
            "INSERT INTO entity_ref (entity_id, view, path, image_gen_id, is_primary,"
            " approved, sha256) VALUES (?,?,?,?,?,0,?) "
            "ON CONFLICT(entity_id, view) DO UPDATE SET path=excluded.path, "
            "image_gen_id=excluded.image_gen_id, sha256=excluded.sha256",
            (r["entity_id"], r["view"], rel, r["id"],
             1 if r["view"] == SR.PRIMARY_VIEW.get(r["kind"]) else 0, sha))
        con.commit()
        ok += 1
        print("  恢复 %s -> %s（%.0f KB）" % (tag, rel, size / 1024.0))

    print("\n恢复 %d 张，失败 %d 张，跳过 %d 张" % (ok, fail, skip))
    print("已计费的 ¥%.2f 没有白花。" % total if not fail else
          "⚠️ 有 %d 张仍失败，需要重新生成（会再计费）。" % fail)
    con.close()
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
