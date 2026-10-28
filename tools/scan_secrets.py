#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""发布前密钥扫描

在把仓库公开或打包提交之前跑一遍，确认没有凭据泄漏。

背景：AGH 的 `agh export --raw` 会明确警告"导出未脱敏（secrets, paths, PII）"，
所以会话导出的证据文件必须单独扫过才能公开。

用法：
    python tools/scan_secrets.py                 # 扫描当前仓库
    python tools/scan_secrets.py --root 某目录
    python tools/scan_secrets.py --fail-on-hit   # 有命中就退出码 3（可用于 CI / 提交前钩子）
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".agh", "vendor", "_submit"}
SKIP_SUFFIX = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".zip", ".pyc", ".exe", ".dll", ".so"}

# 只应存在于本地、绝不应公开的文件。命中这里只提示，不计为失败。
LOCAL_ONLY = {".env", ".env.local", ".env.production"}

# (标签, 正则, 是否算作真实风险)
PATTERNS = [
    ("API Key（sk- 前缀）", re.compile(r"sk-[A-Za-z0-9_\-]{16,}"), True),
    ("Bearer 令牌", re.compile(r"[Bb]earer\s+[A-Za-z0-9_\-\.]{20,}"), True),
    ("JWT", re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"), True),
    ("Anthropic / OpenAI 风格键", re.compile(r"(sk-ant-|sk-proj-)[A-Za-z0-9_\-]{10,}"), True),
    ("私钥文件头", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), True),
    ("明文凭据赋值", re.compile(
        r"(?i)(api[_-]?key|access[_-]?token|secret[_-]?key|password)\s*[:=]\s*[\"']?([A-Za-z0-9_\-]{16,})"), True),
    ("AGH 凭据引用（安全，仅计数）", re.compile(r"secret://[^\s\"']+"), False),
    ("长 hex 串（多为哈希，仅计数）", re.compile(r"\b[0-9a-fA-F]{48,}\b"), False),
]

# 已知的无害命中（公开常量），命中这些不算风险
ALLOWLIST = re.compile(
    r"b77a5c561934e089"          # .NET mscorlib 公开程序集标识
    r"|cc7b13ffcd2ddd51"         # .NET 其它公开程序集标识
)


def iter_files(root: Path):
    """遍历文件。用 os.walk 并在进入前剪枝，避免白白走进 vendor / node_modules。"""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            f = Path(dirpath) / name
            if f.suffix.lower() in SKIP_SUFFIX:
                continue
            yield f


def scan(root: Path) -> tuple[int, list[tuple[str, Path, int, str]]]:
    files = list(iter_files(root))
    print(f"扫描目录：{root}")
    print(f"扫描文件：{len(files)} 个")
    print("=" * 78)

    real_hits: list[tuple[str, Path, int, str]] = []
    local_only_hits: list[tuple[str, Path, int, str]] = []
    for label, pat, risky in PATTERNS:
        hits = []
        for f in files:
            is_local_only = f.name in LOCAL_ONLY
            try:
                txt = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for m in pat.finditer(txt):
                frag = m.group(0)
                if ALLOWLIST.search(frag):
                    continue
                line_no = txt[: m.start()].count("\n") + 1
                hits.append((f, line_no, frag, is_local_only))
        shown = [h for h in hits if not h[3]]
        marked = "!!" if (risky and shown) else "  "
        kind = "需处理" if risky else "仅计数"
        print(f"{marked} [{label}] {len(shown)} 处（{kind}）")
        for f, ln, frag, _ in shown[:8]:
            rel = f.relative_to(root) if f.is_relative_to(root) else f
            show = frag if len(frag) <= 70 else frag[:67] + "..."
            print(f"      {rel}:{ln}  {show}")
        if len(shown) > 8:
            print(f"      …… 另有 {len(shown) - 8} 处")
        if risky:
            real_hits += [(label, f, ln, frag) for f, ln, frag, _ in shown]
        local_only_hits += [(label, f, ln, frag) for f, ln, frag, lo in hits if lo]

    if local_only_hits:
        print()
        print("-- 以下命中位于只应存在于本地的文件（已在 .gitignore，不参与公开）--")
        seen = set()
        for label, f, ln, _ in local_only_hits:
            if f in seen:
                continue
            seen.add(f)
            rel = f.relative_to(root) if f.is_relative_to(root) else f
            print(f"   {rel}  ← 含凭据，确认它没有被打包 / 提交即可")

    print("=" * 78)
    return len(files), real_hits


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="发布前密钥扫描")
    ap.add_argument("--root", default=".", help="要扫描的目录，默认当前目录")
    ap.add_argument("--fail-on-hit", action="store_true", help="有真实命中时以退出码 3 结束")
    args = ap.parse_args(argv)

    n, hits = scan(Path(args.root).resolve())

    if hits:
        print(f"发现 {len(hits)} 处需要处理的命中：")
        for label, f, ln, _ in hits:
            print(f"  - {label}  {f}:{ln}")
        print("\n请先清除或替换这些内容，再公开仓库 / 打包提交。")
        return 3 if args.fail_on_hit else 0

    print("结论：未发现凭据泄漏。")
    print("提醒：本工具只能查已知特征；公开前仍请人工确认演示截图里没有凭据页面。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
