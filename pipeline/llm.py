# -*- coding: utf-8 -*-
"""LLM 共用层：OpenAI 兼容端点的调用、JSON 抽取与校验。

刻意不引入任何第三方依赖——用 urllib 直接打 /chat/completions，
所以同一套代码可指向火山方舟、DeepSeek 或任何 OpenAI 兼容网关。

环境变量（或命令行参数）：
  LLM_BASE_URL   默认 https://ark.cn-beijing.volces.com/api/v3
  LLM_API_KEY    未设置时回退 ARK_API_KEY
  LLM_MODEL      必填，不设默认值（避免猜错模型 ID）
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request


class LLMError(Exception):
    pass


# 校验用哨兵：标记"该字符串字段允许为空"。
# 例如钩子场常常没有台词，硬要求非空会把合法输出拒掉。
OPTIONAL_STR = object()


DEFAULT_BASE = "https://ark.cn-beijing.volces.com/api/v3"


def resolve_config(base_url=None, api_key=None, model=None):
    base = (base_url or os.environ.get("LLM_BASE_URL") or DEFAULT_BASE).rstrip("/")
    key = api_key or os.environ.get("LLM_API_KEY") or os.environ.get("ARK_API_KEY") or ""
    mdl = model or os.environ.get("LLM_MODEL") or ""
    return base, key, mdl


def require_config(base_url=None, api_key=None, model=None):
    base, key, mdl = resolve_config(base_url, api_key, model)
    problems = []
    if not mdl:
        problems.append("LLM_MODEL 未设置（模型 ID，如 doubao-seed-1-6-... 或 deepseek-chat）")
    if not key:
        problems.append("LLM_API_KEY 未设置（或 ARK_API_KEY）")
    if problems:
        raise LLMError("LLM 配置不完整：\n  - " + "\n  - ".join(problems) +
                       "\n\n设置方式（示例）：\n"
                       "  $env:LLM_API_KEY='...'; $env:LLM_MODEL='deepseek-chat'\n"
                       "或写进工作区 .env（已被 .gitignore 挡住）")
    return base, key, mdl


# ⚠️ 默认超时是**实测出来的**，不是拍的。
#
# 原来写 180 秒。真实的实体提取（3957 字符提示词）用
# doubao-seed-2-1-pro-260915 要跑 **307.5 秒**——180 和 300 都会超时。
# 原因是这类模型是**推理模型**，内部思考很久（一个极简请求就能吐 885 个
# 输出 token）。而自检里的假服务是秒回的，所以这个坑一直没暴露。
#
# 可用 LLM_TIMEOUT 覆盖（秒）。
DEFAULT_TIMEOUT = int(os.environ.get("LLM_TIMEOUT") or 600)

# ---------------------------------------------------------------- 用量台账
#
# ⚠️ 为什么必须记：**文本 LLM 的调用一直没进账本**。
# 结果就是"项目账本 ¥197，用户看到的账单 ¥400"——**差的钱没人说得清**（实测）。
# 图片/视频有 `cost_ledger`，文本调用却只进不记，是这套账最大的盲区。
#
# 这里只做**收集**（每次 chat 追加一条），由调用方在收尾时
# `flush_usage(con, episode_id)` 落库——**不在这里直接写库**：
# 阶段脚本才知道自己在写哪一集，而且自检用的临时库也要能接管。
USAGE = []


def take_usage():
    """取出并清空本次进程累计的用量。"""
    global USAGE
    out, USAGE = USAGE, []
    return out


def price_of(con, model_id, kind):
    """从 price_book 取单价（元/百万 token）。取不到返回 None。"""
    try:
        r = con.execute("SELECT unit_price FROM price_book WHERE model_id=? AND variant=?",
                        (model_id, kind)).fetchone()
    except Exception:
        return None
    if not r:
        return None
    return float(r[0] if not hasattr(r, "keys") else r["unit_price"])


def flush_usage(con, episode_id=None, note="LLM"):
    """把累计用量写进 cost_ledger。

    单价取不到时**照记 token、金额记 0 并在 note 里写明**——
    宁可记一条「价格未知」，也不要静默丢掉一次花费（「查不到价就记 0」是踩过的坑）。
    返回 (条数, 合计元)。
    """
    rows = take_usage()
    total, n = 0.0, 0
    for u in rows:
        pin = price_of(con, u["model"], "推理输入")
        pout = price_of(con, u["model"], "推理输出")
        tin = u.get("prompt_tokens") or 0
        tout = u.get("completion_tokens") or 0
        if pin is None or pout is None:
            amount, tag = 0.0, "（**价格未登记**，仅记 token）"
        else:
            amount = tin / 1e6 * pin + tout / 1e6 * pout
            tag = ""
        con.execute(
            "INSERT INTO cost_ledger (category, model_id, quantity, unit, unit_price,"
            " amount, episode_id, shot_id, note) VALUES ('llm',?,?, 'mtoken',?,?,?,NULL,?)",
            (u["model"], (tin + tout) / 1e6, (pin or 0.0), amount, episode_id,
             "%s in=%d out=%d%s" % (note, tin, tout, tag)))
        total += amount
        n += 1
    if n:
        con.commit()
    return n, total


def chat(messages, base_url=None, api_key=None, model=None,
         temperature=0.7, max_tokens=8192, retries=3, timeout=None):
    """调用 /chat/completions，返回助手文本。"""
    timeout = timeout if timeout is not None else DEFAULT_TIMEOUT
    base, key, mdl = require_config(base_url, api_key, model)
    payload = json.dumps({
        "model": mdl,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }).encode("utf-8")
    url = base + "/chat/completions"

    last = None
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(
            url, data=payload, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + key})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
            # 用量台账：有就记，没有（假服务/精简响应）就记 0，不因此报错
            u = data.get("usage") or {}
            USAGE.append({"model": data.get("model") or mdl,
                          "prompt_tokens": u.get("prompt_tokens") or 0,
                          "completion_tokens": u.get("completion_tokens") or 0})
            return data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            # 4xx 里只有 429 值得重试，其余是配置问题，重试无意义
            if e.code != 429 and 400 <= e.code < 500:
                raise LLMError("HTTP %s：%s" % (e.code, body))
            last = "HTTP %s：%s" % (e.code, body)
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as e:
            last = "%s: %s" % (type(e).__name__, e)
        if attempt < retries:
            time.sleep(min(2 ** attempt, 10))
    raise LLMError("调用失败（重试 %d 次）：%s" % (retries, last))


def extract_json(text):
    """从模型输出里抽出 JSON。

    现实里模型会给三种东西：纯 JSON、```json 围栏里的 JSON、
    或夹着解释文字的 JSON。三种都要能处理，否则脚本会在最不该失败的地方失败。
    """
    if text is None:
        raise LLMError("模型返回空内容")
    s = text.strip()

    # 1) 直接能解析
    try:
        return json.loads(s)
    except ValueError:
        pass

    # 2) 去掉 ``` 围栏
    m = re.search(r"```(?:json|JSON)?\s*(.*?)```", s, re.S)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except ValueError:
            s = m.group(1).strip()

    # 3) 扫出第一个配平的 {...} 或 [...]
    for opener, closer in (("{", "}"), ("[", "]")):
        start = s.find(opener)
        if start < 0:
            continue
        depth, in_str, esc = 0, False, False
        for i in range(start, len(s)):
            ch = s[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(s[start:i + 1])
                    except ValueError:
                        break
    raise LLMError("无法从模型输出中解析出 JSON。原始输出前 400 字：\n" + s[:400])


def validate(obj, spec, where="结果"):
    """极简结构校验。

    spec 形如 {"title": str, "scenes": [{"seq": int, "beat": str}]}。
    只校验必需键与类型——够用，且不引第三方依赖。
    """
    problems = []

    def check(node, sp, path):
        if isinstance(sp, dict):
            if not isinstance(node, dict):
                problems.append("%s 应为对象，实际 %s" % (path or "根", type(node).__name__))
                return
            for k, sub in sp.items():
                if k not in node:
                    problems.append("缺少字段 %s%s" % (path, k))
                else:
                    check(node[k], sub, "%s%s." % (path, k))
        elif isinstance(sp, list):
            if not isinstance(node, list):
                problems.append("%s 应为数组，实际 %s" % (path, type(node).__name__))
                return
            if not node:
                problems.append("%s 是空数组" % path)
                return
            for i, item in enumerate(node):
                check(item, sp[0], "%s[%d]." % (path, i))
        elif sp is str:
            if not isinstance(node, str) or not node.strip():
                problems.append("%s 应为非空字符串" % path)
        elif sp is OPTIONAL_STR:
            if not isinstance(node, str):
                problems.append("%s 应为字符串（可为空）" % path)
        elif sp is int:
            if not isinstance(node, int) or isinstance(node, bool):
                problems.append("%s 应为整数" % path)
        elif sp is float:
            if not isinstance(node, (int, float)) or isinstance(node, bool):
                problems.append("%s 应为数字" % path)

    check(obj, spec, "")
    if problems:
        raise LLMError("%s 校验未通过（%d 项）：\n  - %s"
                       % (where, len(problems), "\n  - ".join(problems[:12])))
    return obj


def chat_json(messages, spec, where="结果", **kw):
    text = chat(messages, **kw)
    obj = extract_json(text)
    return validate(obj, spec, where)


def load_prompt(path, **vars_):
    """加载 prompt 模板并做 $变量 替换。

    用 string.Template（$var）而不是 str.format：模板里必然包含 JSON 示例，
    那些花括号会被 format 当成占位符炸掉。
    """
    from string import Template
    with open(path, encoding="utf-8") as f:
        tpl = Template(f.read())
    try:
        return tpl.substitute(**vars_)
    except KeyError as e:
        raise LLMError("prompt 模板 %s 缺少变量 %s（可用：%s）"
                       % (os.path.basename(path), e, ", ".join(sorted(vars_))))


def add_llm_args(parser):
    parser.add_argument("--base-url", default=None, help="LLM 端点（默认取 LLM_BASE_URL）")
    parser.add_argument("--model", default=None, help="模型 ID（默认取 LLM_MODEL）")
    parser.add_argument("--from-json", default=None,
                        help="跳过调用，直接读入外部产出的 JSON（agent 驱动模式）")
    parser.add_argument("--dump-prompt", action="store_true",
                        help="只打印将要发送的 prompt，不调用模型")
    return parser
