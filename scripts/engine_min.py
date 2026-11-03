# -*- coding: utf-8 -*-
"""包内最小违禁词引擎（本技能自带，不依赖第三方技能）。

用途：当可选的增强词库 ad-redline-guardian 不存在时，本模块提供等价的最小能力，
使合规闸门仍能独立工作，而不是因为缺依赖整体失效。

对外接口与增强引擎保持一致，调用方无需分支：
    CATEGORY_RISK : dict[str, str]     类别 -> 风险级别
    parse_wordlist(path) -> dict       解析 md 词库
    normalize(text) -> str             归一化文本

设计取舍：
- 只做**初筛**，不做判定。命中即"需人工复核"，不等于违规成立。
- 词表刻意收窄为高精度词（如用「销量第一」而非裸「第一」），宁可漏也不滥报，
  避免把「第一步」「最基础的用法」这类正常表述判成违规。
"""

import re
import unicodedata

# 类别 -> 风险级别。类别名必须与词库文件的 "## 标题" 去序号后一致。
CATEGORY_RISK = {
    "绝对化用语": "high",
    "权威背书类": "high",
    "欺诈误导类": "high",
    "功效承诺类": "high",
    "投资回报类": "high",
    "迷信风水类": "medium",
}

# markdown 表格的分隔行，如 |---|---|
_SEP_ROW = re.compile(r"^\|[\s:\-|]+\|$")
# 命中词单元格里可能塞了备注，如「最佳（无据）」
_PAREN = re.compile(r"（[^）]*）|\([^)]*\)")


def normalize(text):
    """把全角/兼容字符折成半角，并去掉零宽字符，减少「看起来一样却不命中」。"""
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", text)
    for ch in ("\u200b", "\u200c", "\u200d", "\ufeff"):
        s = s.replace(ch, "")
    return s


def parse_wordlist(md_path):
    """解析词库 md，返回 {类别: [命中词, ...]}。

    只认 `## 类别` 标题下面表格的第一列；表头「命中词」和分隔行跳过；
    一个单元格里用顿号/逗号串联的多个词会被拆开。
    文件不存在或读不出内容时返回 {}，由调用方决定是否据此判失败。
    """
    categories = {}
    cur = None
    try:
        with open(md_path, encoding="utf-8") as f:
            lines = f.readlines()
    except (OSError, UnicodeDecodeError):
        return {}

    for raw in lines:
        line = raw.rstrip("\r\n")

        if line.startswith("## "):
            title = line[3:].strip()
            title = re.sub(r"^[一二三四五六七八九十]+、\s*", "", title)
            title = _PAREN.sub("", title).strip()
            cur = title
            categories.setdefault(cur, [])
            continue

        if not cur or not line.startswith("|") or _SEP_ROW.match(line):
            continue

        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells or not cells[0] or cells[0] == "命中词":
            continue

        for token in re.split(r"[、，,]", cells[0]):
            token = _PAREN.sub("", token).strip()
            if token:
                categories[cur].append(token)

    return categories
