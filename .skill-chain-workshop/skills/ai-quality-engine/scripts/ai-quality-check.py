#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ai-quality-engine — 判定层入口脚本（对称 contract/scenario-oracle-builder 的 scripts/extract.py）

门禁调用方式（在项目根执行，指向本项目 skill 自带引擎，不依赖 pip 全局安装）：

    python3 skills/ai-quality-engine/scripts/ai-quality-check.py \
        check <业务入口源文件> \
        --profile code-verified \
        --oracle-dir tests/oracle \
        --format json

等价命令（进 skill 目录零安装）：
    cd skills/ai-quality-engine && python3 -m ai_quality check ... --oracle-dir <abs> ...

本入口把 skill 目录加入 sys.path 后委托 ai_quality.cli.entry.main，
保证门禁在任何 cwd 下都命中本项目 skill 内的引擎副本，而非外部 pip 安装的 ai-quality CLI。
"""
import sys
from pathlib import Path

_SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SKILL_ROOT))

from ai_quality.cli.entry import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
