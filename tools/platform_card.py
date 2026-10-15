# -*- coding: utf-8 -*-
r"""片头合规卡 + 拼到成片前面。

## 为什么必须有

《人工智能生成合成内容标识办法》要求 **AI 生成内容做出显著标识**——
**只在封面标是不够的，成片本体里也要标**。此外红果一类平台要求
**备案号标在作品明显位置**。

所以每集成片前面加一张 2 秒卡（黑底 + 剧名 + 集名 + 「本片由 AI 生成」+ 备案号），
备案号**参数化**：拿号之前不显示，拿到后一条命令重出，不用重剪。

## 用法

```bash
# 只出卡（看看长什么样）
python tools/platform_card.py --episode-id 1

# 出卡并拼到成片前面（推荐）
python tools/platform_card.py --episode-id 1 \
    --video D:/dsh-film/assets/final/E01_blur_ln.mp4 \
    --out   D:/dsh-film/assets/final/E01_发布_9x16.mp4 \
    --filing-no "（X）网微剧备字（2026）第 000000 号"
```

⚠️ 片头卡是**静音**的，但要带一条静音 aac 轨，否则跟正片拼接时音轨对不齐
（concat 要求两段的流结构一致）。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from pipeline import common as C  # noqa: E402

FONT = r"C:\Windows\Fonts\msyh.ttc"
FONT_B = r"C:\Windows\Fonts\msyhbd.ttc"
W, H = 1080, 1920


def font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.truetype(FONT, size)


def build_card(ep, out_png, filing_no=None, bg=None):
    """卡片：**干净底色**（默认竖向渐变），不要拿做好的封面当底——
    封面上本来就压了字，套上去会**字重两遍**（实测踩过）。"""
    if bg and os.path.exists(bg):
        im = Image.open(bg).convert("RGB").resize((W, H), Image.LANCZOS)
        im = Image.eval(im, lambda v: int(v * 0.38))
    else:
        im = Image.new("RGB", (W, H), (12, 12, 14))
        d0 = ImageDraw.Draw(im)
        for i in range(H):                      # 上深下浅的竖向渐变
            t = i / float(H)
            d0.line([(0, i), (W, i)],
                    fill=(int(14 + 26 * t), int(16 + 28 * t), int(20 + 30 * t)))
    d = ImageDraw.Draw(im)
    d.text((W // 2, 560), "《%s》" % (ep.get("project") or ""), font=font(FONT_B, 92),
           fill=(255, 255, 255), anchor="mm", stroke_width=3, stroke_fill=(0, 0, 0))
    d.text((W // 2, 700), "第 %s 集 · %s" % (ep["seq"], ep.get("ep_name") or ""),
           font=font(FONT, 54), fill=(255, 226, 150), anchor="mm",
           stroke_width=2, stroke_fill=(0, 0, 0))
    d.line([(W // 2 - 180, 800), (W // 2 + 180, 800)], fill=(120, 120, 120), width=2)
    d.text((W // 2, 960), "本片由 AI 生成", font=font(FONT_B, 64),
           fill=(255, 255, 255), anchor="mm", stroke_width=3, stroke_fill=(0, 0, 0))
    d.text((W // 2, 1050), "画面与声音由人工智能生成；人物与情节均为虚构",
           font=font(FONT, 34), fill=(210, 210, 210), anchor="mm")
    if filing_no:
        d.text((W // 2, H - 300), "备案号：" + filing_no, font=font(FONT, 38),
               fill=(235, 235, 235), anchor="mm")
    os.makedirs(os.path.dirname(out_png), exist_ok=True)
    im.save(out_png)
    return out_png


def main():
    ap = argparse.ArgumentParser(description="片头合规卡（AI 生成标注 + 备案号）")
    ap.add_argument("--episode-id", type=int, required=True)
    ap.add_argument("--video", default=None, help="要拼的成片；不给就只出卡")
    ap.add_argument("--out", default=None, help="输出成片路径")
    ap.add_argument("--filing-no", default=None, help="备案号（拿到后再给）")
    ap.add_argument("--bg", default=None, help="卡片底图（默认用封面）")
    ap.add_argument("--secs", type=float, default=2.0)
    C.add_common_args(ap)
    args = ap.parse_args()

    C.require_ffmpeg()
    con = C.connect(args.db)
    ep = C.episode_info(con, args.episode_id)
    if not ep:
        print("找不到集:", args.episode_id)
        return 2
    row = con.execute("SELECT title FROM episode WHERE id=?", (args.episode_id,)).fetchone()
    con.close()
    # episode.title 形如「EP001：死人先开口」→ 集名取冒号后半段
    full = row["title"] or ""
    ep["ep_name"] = full.split("：")[-1].strip() if "：" in full else full
    ep["project"] = ep.get("project") or ""

    tmp = os.path.join(C.ROOT, ".tools", "tmp", "card")
    os.makedirs(tmp, exist_ok=True)
    # 默认**不给底图**（干净渐变）；要底图就传一张**没有压过字**的干净帧
    png = build_card(ep, os.path.join(tmp, "card.png"), args.filing_no, args.bg)
    print("卡片已生成:", os.path.relpath(png, C.ROOT))

    if not args.video:
        out = os.path.join(C.ROOT, "out", "cover_片头卡预览.png")
        Image.open(png).resize((540, 960), Image.LANCZOS).save(out)
        print("预览:", os.path.relpath(out, C.ROOT))
        return 0

    clip = os.path.join(tmp, "card.mp4")
    r = C.run_ffmpeg(["-loop", "1", "-i", png, "-f", "lavfi",
                      "-i", "anullsrc=r=48000:cl=stereo", "-t", str(args.secs),
                      "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
                      "-r", "30", "-c:a", "aac", "-ar", "48000", "-ac", "2",
                      "-shortest", clip], cwd=C.ROOT)
    if r.returncode != 0:
        print("卡片转视频失败:", C.tail(r.stderr, 200))
        return 2
    lst = os.path.join(tmp, "concat.txt")
    with open(lst, "w", encoding="utf-8", newline="\n") as f:
        for p in (clip, C.abs_path(args.video)):
            f.write("file '%s'\n" % p.replace("\\", "/"))
    out_rel = args.out or os.path.join(
        os.path.dirname(args.video), "E%02d_发布_含片头卡.mp4" % ep["seq"])
    # ffmpeg 只能写工作区 → 先写工作区再 python 跨盘搬
    stage = os.path.join(C.ROOT, ".tools", "tmp", "card", "joined.mp4")
    r2 = C.run_ffmpeg(["-f", "concat", "-safe", "0", "-i", lst, "-c", "copy",
                       "-movflags", "+faststart", stage], cwd=C.ROOT)
    if r2.returncode != 0:
        print("拼接失败:", C.tail(r2.stderr, 200))
        return 2
    import shutil
    shutil.copyfile(stage, C.abs_path(out_rel))
    d = C.duration(stage, cwd=C.ROOT)
    print("已输出 %s（%.1f 秒）" % (out_rel, d))
    return 0


if __name__ == "__main__":
    sys.exit(main())
