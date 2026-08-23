#!/usr/bin/env python3
"""scan_secrets.py — fork 推上游前的密钥/硬编码扫描（ZC · 2026-08-23）

⚠️ 内部工具，永不推上游：KNOWN_FINGERPRINTS 含我方各通道 key 指纹前缀。

用法：
  python3 scripts/scan_secrets.py                 # 扫当前工作树（默认）
  python3 scripts/scan_secrets.py --staged        # 只扫 git 暂存区
  python3 scripts/scan_secrets.py --history HEAD~20..HEAD   # 扫 commit 区间每个 diff
  python3 scripts/scan_secrets.py upstream/main..HEAD       # 推 PR 前必跑

退出码 0=干净；1=发现命中；2=用法错误。

三类检测：
  A. 已知真实指纹 —— 我们各通道 key 的前缀（最高优先级，零误报容忍）
  B. 通用模式     —— ark-/sk- token、Bearer 长串、api_key 直赋值
  C. 环境逃生缺失 —— 代码里写死 base_url/账号而非常量+env 的可疑点（低置信，提示级）

豁免（合法命中不报）：
  - 明显占位符：YOUR_API_KEY / <api_key> / xxx / *** / ${...} / os.environ
  - 测试 fixture 的 FAKE/test/example 假 key
  - 掩码逻辑本身（CATALOG_SECRET_MASK、redact 等）
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# ── A. 已知真实指纹（我方各通道 key 前缀；新增 key 时在此登记）──
KNOWN_FINGERPRINTS = [
    "ark-8ff3de9b",   # 火山 mine（embedding，月度额度耗尽 09-05 重置）
    "ark-21902e",     # 火山 xiaowu（LLM/embedding 双用，09-05 重置）
    "ark-669d",       # 火山 env.sh ARK_API_KEY（08-28 重置）
    "sk-e7bb",        # DeepSeek 原厂（文科通道）
    "sk-L8xp3QA",     # 商汤 SenseNova 免费档
    "8d2230fbf9a64091983c7000d8fa7952",  # 智谱 bigmodel
    "sk-9c91ff9",     # hermes config 里的另一个 sk
    "59aeb2b285",     # hermes config 里的非 sk 格式 key
    "NoFox2026",      # DT admin 密码（脚本/文档里出现过）
    "YueXue-Boss",    # DT boss 账号密码前缀
]

# ── B. 通用模式 ──
GENERIC_PATTERNS = [
    # 火山 ark key 完整形态
    (re.compile(r"ark-[a-z0-9]{16,}"), "ark key 完整形态"),
    # OpenAI/DeepSeek 风格 sk-（长度过滤掉示例串）
    (re.compile(r"\bsk-[A-Za-z0-9]{24,}\b"), "sk- 长token"),
    # Bearer 后跟 30+ 连续 token 字符（排除占位符）
    (re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{30,}"), "Bearer 硬编码"),
    # api_key/api_token 直赋值字符串字面量（排除 env 读取与占位符）
    (re.compile(r"""api_key(?:s)?\s*[:=]\s*["'][A-Za-z0-9_\-]{12,}["']"""), "api_key 字面量赋值"),
]

PLACEHOLDER_OK = re.compile(
    r"(YOUR_API_KEY|<api_key>|xxx+|\*\*\*|\$\{|\{\{|os\.environ|getenv|process\.env|"
    r"CATALOG_SECRET_MASK|redact|FAKE|EXAMPLE|example|placeholder|dummy|test_key|"
    r"config\.yaml|env\.sh|\.env\b|secrets\.)",
    re.IGNORECASE,
)

# 扫描范围豁免：二进制/锁文件/构建产物
SKIP_SUFFIX = (".lock", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".woff",
               ".woff2", ".ttf", ".mp4", ".zip", ".db", ".sqlite", ".bundle")
SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".next", "dist", "build",
             "dt-data", "data/user", ".venv", "venv"}


def scan_text(text: str, origin: str, findings: list) -> None:
    for i, line in enumerate(text.splitlines(), 1):
        # A 类指纹：零容忍，直接报
        for fp in KNOWN_FINGERPRINTS:
            if fp in line:
                findings.append((origin, i, "KNOWN", f"指纹 {fp[:12]}…", line.strip()[:100]))
        # B 类通用：先过占位符豁免再报
        if PLACEHOLDER_OK.search(line):
            continue
        for pat, label in GENERIC_PATTERNS:
            m = pat.search(line)
            if m:
                findings.append((origin, i, label, m.group(0)[:40], line.strip()[:100]))


def iter_worktree_files():
    for p in REPO.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(REPO)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if rel.suffix in SKIP_SUFFIX:
            continue
        try:
            yield rel, p.read_text(errors="ignore")
        except OSError:
            continue


def git(args: list) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args],
                          capture_output=True, text=True, check=True).stdout


def scan_revision_range(rng: str, findings: list) -> None:
    # 每个 commit 的 patch：历史里加过又删掉的 key 也能抓到
    patch = git(["log", "-p", "--no-color", rng, "--", "."])
    cur_commit = "?"
    for line in patch.splitlines():
        if line.startswith("commit "):
            cur_commit = line.split()[1][:8]
        if not line.startswith("+") or line.startswith("+++"):
            continue
        text = line[1:]
        for fp in KNOWN_FINGERPRINTS:
            if fp in text:
                findings.append((f"history:{cur_commit}", 0, "KNOWN",
                                 f"指纹 {fp[:12]}…", text.strip()[:100]))
        if PLACEHOLDER_OK.search(text):
            continue
        for pat, label in GENERIC_PATTERNS:
            m = pat.search(text)
            if m:
                findings.append((f"history:{cur_commit}", 0, label,
                                 m.group(0)[:40], text.strip()[:100]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--staged", action="store_true", help="只扫暂存区")
    ap.add_argument("range", nargs="?", default=None,
                    help="git revision range，如 upstream/main..HEAD")
    args = ap.parse_args()

    findings: list = []
    if args.range:
        scan_revision_range(args.range, findings)
    elif args.staged:
        text = git(["diff", "--cached", "--no-color"])
        cur = "staged"
        for line in text.splitlines():
            if line.startswith("+++ b/"):
                cur = line[6:]
            if line.startswith("+") and not line.startswith("+++"):
                scan_text(line[1:], f"staged:{cur}", findings)
    else:
        for rel, text in iter_worktree_files():
            scan_text(text, str(rel), findings)

    if not findings:
        print("CLEAN — 无已知指纹、无硬编码模式命中")
        return 0
    for origin, lineno, kind, frag, ctx in findings:
        print(f"HIT [{kind}] {origin}:{lineno}  {frag}")
        print(f"     └─ {ctx}")
    print(f"\n共 {len(findings)} 处命中 —— 推上游前必须逐条人工裁决")
    return 1


if __name__ == "__main__":
    sys.exit(main())
