#!/usr/bin/env node
/**
 * 解压「多帧拼接」的 zstd 文件。
 *
 * ## 为什么需要
 *
 * `session.v4.jsonl.zstd` 是 **append-only 日志**：每次追加都写一个独立的
 * zstd 帧。实测 136439 字节的文件用 `zstdDecompressSync` 只解出 **242 字节**
 * —— 因为 Node 的 `zstdDecompressSync` **只解第一帧**。
 *
 * 做法：按 zstd 魔数 `28 B5 2F FD` 切帧，逐帧解压后拼接。
 * 魔数有 4 字节，理论上可能出现在压缩数据内部造成误切；所以**逐帧尝试，
 * 失败就把该位置当误匹配跳过**，并报告实际解出多少帧。
 *
 * ## 用法
 *
 *   node tools/zstd_frames.mjs <file> [--out <file>]
 */
import { readFileSync, writeFileSync } from "node:fs";
import { zstdDecompressSync } from "node:zlib";

const MAGIC = Buffer.from([0x28, 0xb5, 0x2f, 0xfd]);

export function decompressAllFrames(raw) {
  // 找出所有魔数位置
  const starts = [];
  let i = raw.indexOf(MAGIC, 0);
  while (i !== -1) {
    starts.push(i);
    i = raw.indexOf(MAGIC, i + 1);
  }
  if (starts.length === 0) {
    // 不是 zstd？直接试一次整块
    return { text: zstdDecompressSync(raw).toString("utf8"), frames: 1, tried: 1 };
  }
  // 每帧的候选终点 = 下一个魔数位置（或文件尾）
  const parts = [];
  let ok = 0, fail = 0;
  for (let k = 0; k < starts.length; k++) {
    const s = starts[k];
    const e = k + 1 < starts.length ? starts[k + 1] : raw.length;
    try {
      const out = zstdDecompressSync(raw.subarray(s, e));
      parts.push(out);
      ok++;
    } catch {
      // 下一帧的坐标不对（数据里恰好出现了魔数），交给下一轮兜底
      fail++;
    }
  }
  return {
    text: Buffer.concat(parts).toString("utf8"),
    frames: ok,
    tried: starts.length,
    skipped: fail,
  };
}

// 命令行入口
if (process.argv[1] && import.meta.url.endsWith(process.argv[1].replace(/\\/g, "/").split("/").pop())) {
  const a = process.argv.slice(2);
  const f = a[0];
  if (!f) { console.error("用法: node tools/zstd_frames.mjs <file> [--out <out>]"); process.exit(1); }
  const raw = readFileSync(f);
  const r = decompressAllFrames(raw);
  console.log(`压缩 ${raw.length} 字节 -> 解压 ${Buffer.byteLength(r.text)} 字节`);
  console.log(`魔数候选 ${r.tried} 处，成功解出 ${r.frames} 帧，跳过 ${r.skipped} 处误匹配`);
  const oi = a.indexOf("--out");
  if (oi >= 0 && a[oi + 1]) {
    writeFileSync(a[oi + 1], r.text);
    console.log(`已写出 ${a[oi + 1]}`);
  } else {
    const lines = r.text.split("\n").filter((l) => l.trim());
    console.log(`非空行 ${lines.length} 行`);
    console.log("\n前 2 行：");
    lines.slice(0, 2).forEach((l, i) => console.log(`  [${i}] ${l.slice(0, 200)}`));
  }
}
