"""大模型话术分析：每隔 interval 秒，把最近 window 分钟的转写 + 本地指标 + 话术框架交给 LLM，
输出结构化 JSON（覆盖了哪些阶段、有哪些问题、给主播的提示）。

使用 OpenAI 兼容接口，DeepSeek / 通义千问 / Kimi 等只需改 .env 中的 base_url 和 model。
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import httpx

from .asr import Segment

ROOT = Path(__file__).resolve().parent.parent

ISSUE_CODES = """\
- missing:<阶段id>      某个必要阶段在窗口内缺失或讲得不到位
- rule:<序号>           违反框架中的节奏要求（序号从 1 开始）
- compliance:efficacy   功效宣称夸大、无依据
- compliance:extreme    极限用语
- compliance:false      虚构事实、虚假紧迫、价格误导
- compliance:other      其他合规风险
- content:chaotic       逻辑混乱、信息前后矛盾
- content:repetitive    长时间车轱辘话，没有新信息
- content:pressure      过度施压逼单，体验差
- content:other         其他内容问题"""

SYSTEM_PROMPT = f"""你是抖音直播间的资深运营，正在实时盯播，帮助场控及时发现主播讲解中的问题。
你会收到：话术框架、最近一段时间的主播讲解转写（语音识别结果，可能有少量错别字，不要因错别字判定问题）、本地计算的表达指标。

请对照话术框架分析，并只输出一个 JSON 对象，格式如下：
{{
  "summary": "一句话概括这段时间主播在讲什么（不超过40字）",
  "current_stage": "当前所处阶段的 id",
  "stage_coverage": [{{"id": "阶段id", "covered": true, "note": "讲得怎么样，不超过20字"}}],
  "issues": [{{
    "code": "问题编码，必须从下方列表中选",
    "severity": "high | medium | low",
    "title": "问题标题，不超过12字",
    "detail": "具体说明，不超过50字",
    "quote": "转写中的原文证据（缺失类问题可为空）",
    "suggestion": "可以直接转告主播的一句话提示，不超过30字",
    "asr_suspect": "布尔值：这个问题是否可能只是语音识别错误（数字、单位、同音字识别错）造成的"
  }}],
  "score": 0到100的整数，话术质量综合评分
}}

问题编码列表：
{ISSUE_CODES}

要求：
1. 只报真正影响转化或有合规风险的问题，最多 5 条，按严重程度排序；没有问题就返回空数组。
2. 语速、冷场、口头禅已由本地规则检测，不要重复报告。
3. stage_coverage 必须覆盖框架中的全部阶段。
4. quote 必须是转写原文中真实存在的片段。
5. 转写来自语音识别，数字、量词、单位、品牌名经常识别错（如"100毫升"被识别成"100瓶""100秒"，"399"被识别成"3998"）。
   遇到数字或规格看起来自相矛盾时，优先认为是识别错误，不要据此报告"信息混乱"或"价格误导"；只有语义上明确前后矛盾才报告。"""


_HINTS = {
    401: "密钥无效，请检查 .env 中的 LLM_API_KEY",
    402: "账户余额不足，请到模型平台充值",
    429: "请求过于频繁或额度用尽，请稍后重试",
}


class LLMError(Exception):
    """把模型接口的错误翻译成场控看得懂的提示。"""

    def __init__(self, resp: httpx.Response):
        self.status = resp.status_code
        try:
            detail = resp.json().get("error", {}).get("message", "")
        except ValueError:
            detail = resp.text[:200]
        # 去掉每次都不同的 request_id，界面才能把同一错误识别为重复
        detail = re.sub(r"\s*\(request_id:[^)]*\)", "", detail)
        hint = _HINTS.get(self.status, "模型接口调用失败")
        super().__init__(f"{hint}（HTTP {self.status}：{detail}）")


def load_env() -> None:
    """极简 .env 读取，避免额外依赖。已存在的环境变量优先。"""
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def format_transcript(segments: list[Segment], origin: float) -> str:
    return "\n".join(
        f"[{int((s.start - origin) // 60):02d}:{int((s.start - origin) % 60):02d}] {s.text}" for s in segments
    )


def format_framework(fw: dict) -> str:
    lines = [f"框架：{fw.get('name', '')}"]
    product = fw.get("product") or {}
    if product:
        lines.append(f"商品：{product.get('brand', '')} {product.get('name', '')}")
        for sp in product.get("selling_points", []):
            lines.append(f"  卖点：{sp}")
        if product.get("notes"):
            lines.append(f"  注意：{product['notes']}")
    lines.append("阶段：")
    for st in fw["stages"]:
        lines.append(f"  - {st['id']}（{st['name']}）：{st.get('must', '')}")
    if fw["rules"]:
        lines.append("节奏要求：")
        for i, r in enumerate(fw["rules"], 1):
            lines.append(f"  {i}. {r}")
    return "\n".join(lines)


class LLMAnalyzer:
    def __init__(self):
        load_env()
        self.base_url = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
        self.api_key = os.environ.get("LLM_API_KEY", "")
        self.model = os.environ.get("LLM_MODEL", "deepseek-flash")
        self.client = httpx.Client(timeout=60)

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def analyze(self, fw: dict, segments: list[Segment], metrics: dict, window_start: float) -> dict:
        metric_view = {k: metrics[k] for k in ("rate_cpm", "talk_ratio", "max_gap", "filler_per_100", "repeat_ratio")}
        user = (
            f"{format_framework(fw)}\n\n"
            f"本地指标（最近 {(metrics['end'] - metrics['start']) / 60:.0f} 分钟）：{json.dumps(metric_view, ensure_ascii=False)}\n\n"
            f"主播讲解转写：\n{format_transcript(segments, window_start) or '（这段时间没有识别到讲话）'}"
        )
        t0 = time.perf_counter()
        resp = self.client.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}],
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
            },
        )
        if resp.status_code >= 400:
            raise LLMError(resp)
        body = resp.json()
        result = json.loads(body["choices"][0]["message"]["content"])
        # 模型自检：可能由识别错误导致的问题直接丢弃，降低误报
        issues = result.get("issues") or []
        result["issues"] = [i for i in issues if i.get("asr_suspect") is not True]
        result["dropped_asr_suspect"] = [i.get("title") for i in issues if i.get("asr_suspect") is True]
        usage = body.get("usage") or {}
        result["_meta"] = {
            "latency": round(time.perf_counter() - t0, 1),
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            # 系统提示词 + 框架每次相同，可命中服务端缓存（价格约为未命中的 1/50）
            "cache_hit_tokens": usage.get("prompt_cache_hit_tokens"),
            "model": body.get("model", self.model),
        }
        return result
