"""违禁词实时检测：每识别出一句就扫描一次，秒级告警，不等大模型。

词库按《广告法》极限词、化妆品功效宣称、医疗用语、站外引流等分类。
注意避免误报：例如单独的"最""第一"太常见（"最后""第一场"），只收录组合词。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

BUILTIN = {
    "极限用语": {
        "reason": "《广告法》第九条禁止使用国家级、最高级、最佳等用语",
        "severity": "high",
        "words": ["全网最低", "史上最低", "最便宜", "最好用", "最有效", "效果最好", "全网第一", "销量第一",
                  "排名第一", "第一品牌", "行业第一", "国家级", "世界级", "顶级", "绝无仅有", "独一无二",
                  "万能", "百分百", "100%有效", "永久", "绝对有效"],
    },
    "功效夸大": {
        "reason": "化妆品功效宣称须有依据，禁止绝对化、承诺性表述",
        "severity": "high",
        "words": ["全部改善", "彻底改善", "立竿见影", "永不反弹", "一次见效", "根除", "逆龄", "回到18岁",
                  "皱纹全没", "无副作用", "零刺激"],
    },
    "医疗用语": {
        "reason": "普通化妆品不得宣称医疗作用",
        "severity": "high",
        "words": ["治疗", "根治", "消炎", "抗炎", "杀菌", "药妆", "医学护肤", "处方", "修复受损"],
    },
    "站外引流": {
        "reason": "平台禁止引导私下交易",
        "severity": "high",
        "words": ["加微信", "加我微信", "vx", "私下交易", "线下转账"],
    },
}


@dataclass
class Hit:
    word: str
    category: str
    reason: str
    severity: str


class BannedWordChecker:
    def __init__(self, extra: list[dict] | None = None):
        self.table: dict[str, tuple[str, str, str]] = {}
        for cat, cfg in BUILTIN.items():
            for w in cfg["words"]:
                self.table[w.lower()] = (cat, cfg["reason"], cfg["severity"])
        for item in extra or []:
            self.table[item["word"].lower()] = (
                item.get("category", "自定义"),
                item.get("reason", "框架中配置的违禁词"),
                item.get("severity", "medium"),
            )
        # 长词优先匹配，避免"根治"先于更长的词命中
        words = sorted(self.table, key=len, reverse=True)
        self.pattern = re.compile("|".join(re.escape(w) for w in words), re.IGNORECASE) if words else None

    def check(self, text: str) -> list[Hit]:
        if not self.pattern:
            return []
        hits, seen = [], set()
        for m in self.pattern.finditer(text):
            w = m.group(0).lower()
            if w in seen:
                continue
            seen.add(w)
            cat, reason, sev = self.table[w]
            hits.append(Hit(word=m.group(0), category=cat, reason=reason, severity=sev))
        return hits


def apply_corrections(text: str, corrections: dict[str, str]) -> str:
    for wrong, right in corrections.items():
        text = text.replace(wrong, right)
    return text
