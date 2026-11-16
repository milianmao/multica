#!/usr/bin/env python3
"""
sensitive_filter — 顶级 CLI 入口

Usage:
  python sensitive_filter.py <file>           # 单文件脱敏
  python sensitive_filter.py --scan <dir>     # 批量扫描
  python sensitive_filter.py --help           # 帮助
"""
import sys
from pathlib import Path

# 确保可作为独立脚本运行
sys.path.insert(0, str(Path(__file__).parent))

from filters.__main__ import main

if __name__ == "__main__":
    main()
