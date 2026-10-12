#!/usr/bin/env node
/**
 * 读 DSH 的会话记录（session.v4.jsonl.zstd）。
 *
 * ## 为什么写这个
 *
 * 用户问「dsh-honcho 能不能让你读其他会话」。核查后发现：
 * **DSH 把每个会话都记在 `~/.dsh/sessions/<工作目录>/` 里**，
 * 压缩成 `session.v4.jsonl.zstd`。本机已经有 4 份（含今天上午那个会话）。
 * 所以"读其他会话"这件事**不需要云端插件**——本机就有。
 *
 * 障碍只是解压：Python 没装 zstd 模块、系统也没有 zstd 命令。
 * 但 **Node 24 内置 `zlib.zstdDecompressSync`**，而本机有 Node 24.21.0。
 *
 * ## 用法
 *
 *   node tools/read_session.mjs --list
 *   node tools/read_session.mjs --index 1 --tail 40
 *   node tools/read_session.mjs --index 1 --grep "视觉设定"
 *   node tools/read_session.mjs --index 1 --users        # 只看用户说了什么
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { decompressAllFrames } from "./zstd_frames.mjs";

const SESS = join(homedir(), ".dsh", "sessions");

function listFiles() {
  const out = [];
  const walk = (d) => {
    for (const e of readdirSync(d, { withFileTypes: true })) {
      const p = join(d, e.name);
      if (e.isDirectory()) walk(p);
      else if (e.name.endsWith(".zstd") || e.name.includes("session")) {
        const st = statSync(p);
        out.push({ path: p, size: st.size, mtime: st.mtime });
      }
    }
  };
  try { walk(SESS); } catch { /* 目录不存在 */ }
  // 注意：不按大小排序，按时间倒序——"最近那个"才是用户要找的
  return out.sort((a, b) => b.mtime - a.mtime);
}

function load(file) {
  const raw = readFileSync(file.path);
  // ⚠️ 文件是 **多帧拼接** 的 zstd（append-only 日志）。
  //    用 zstdDecompressSync 只解第一帧——实测 136 KB 的文件只解出 242 字节，
  //    看起来"没有对话内容"。必须逐帧解压再拼接。
  const text = decompressAllFrames(raw).text;
  const rows = [];
  for (const line of text.split("\n")) {
    const s = line.trim();
    if (!s) continue;
    try { rows.push(JSON.parse(s)); } catch { /* 跳过坏行 */ }
  }
  return rows;
}

/** 从一条记录里抽出可读文本（尽量不假设字段名）。 */
function textOf(row) {
  const out = [];
  const push = (v) => {
    if (typeof v === "string" && v.trim()) out.push(v);
    else if (Array.isArray(v)) v.forEach(push);
    else if (v && typeof v === "object") {
      // 消息内容常见是 parts 数组，每项带 text / type
      for (const k of ["text", "content", "body", "value", "message",
                       "parts", "result", "output", "prompt"]) {
        if (k in v) push(v[k]);
      }
    }
  };
  push(row.data);
  push(row.content);
  push(row.text);
  push(row.message?.content);
  return out.join("\n").trim();
}

function roleOf(row) {
  return row.role || row.type || row.data?.role || row.data?.kind
    || row.message?.role || row.kind || "?";
}

/** 只看对话记录（用户/助手），忽略工具调用与心跳。 */
const CONV = /^(user|assistant)\/message$/;

function main() {
  const a = process.argv.slice(2);
  const arg = (n, d) => {
    const i = a.indexOf(n);
    return i >= 0 && a[i + 1] ? a[i + 1] : d;
  };
  const files = listFiles();

  if (a.includes("--list") || files.length === 0) {
    console.log(`会话记录目录：${SESS}`);
    console.log(`共 ${files.length} 份（按时间倒序）：\n`);
    files.forEach((f, i) => {
      // ⚠️ 用本地时间显示。早先用 toISOString()（UTC），显示出的时间比实际
      //    早 8 小时，会把"23:13 的会话"显示成"15:13"，对不上文件时间戳。
      const t = new Date(f.mtime);
      const pad = (n) => String(n).padStart(2, "0");
      const stamp = `${t.getFullYear()}-${pad(t.getMonth() + 1)}-${pad(t.getDate())}`
        + ` ${pad(t.getHours())}:${pad(t.getMinutes())}:${pad(t.getSeconds())}`;
      console.log(`  [${i}] ${stamp}  ${(f.size / 1048576).toFixed(2)} MB`);
      console.log(`      ${f.path}`);
    });
    if (files.length === 0) process.exit(1);
    console.log("\n用 --index N 读第 N 份");
    return;
  }

  const idx = Number(arg("--index", "0"));
  const f = files[idx];
  if (!f) {
    console.error(`没有第 ${idx} 份（共 ${files.length} 份）`);
    process.exit(1);
  }
  const rows = load(f);
  console.log(`读取 [${idx}] ${f.path}`);
  console.log(`  ${(f.size / 1048576).toFixed(2)} MB -> ${rows.length} 条记录\n`);

  // 先看有哪些字段，便于适配格式变化
  const keys = new Set();
  rows.slice(0, 400).forEach((r) => Object.keys(r || {}).forEach((k) => keys.add(k)));
  console.log(`记录字段：${[...keys].join(", ")}\n`);

  const grep = arg("--grep", null);
  const usersOnly = a.includes("--users");
  const all = a.includes("--all");
  const tail = Number(arg("--tail", "0"));

  // 默认只看对话（user/assistant message），--all 才带上工具调用等
  let picked = all ? rows : rows.filter((r) => CONV.test(roleOf(r)));
  if (usersOnly) {
    picked = picked.filter((r) => /^user/i.test(roleOf(r)));
  }
  if (grep) {
    picked = picked.filter((r) => textOf(r).includes(grep));
  }
  if (tail > 0) picked = picked.slice(-tail);

  console.log(`对话记录 ${picked.length} 条`
    + `${grep ? `（含「${grep}」）` : ""}`
    + `${usersOnly ? "（仅用户）" : ""}`
    + `${all ? "（含工具调用）" : ""}\n`);
  console.log("=".repeat(74));
  picked.forEach((r) => {
    const t = textOf(r);
    if (!t) return;
    console.log(`\n--- [${roleOf(r)}] ---`);
    console.log(t.length > 1400 ? t.slice(0, 1400) + "\n…（截断）" : t);
  });
}

main();
