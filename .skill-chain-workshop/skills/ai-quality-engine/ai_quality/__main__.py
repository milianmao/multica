"""
ai_quality/__main__.py

支持 `python3 -m ai_quality` 调用（零安装模式）。

使用方式：
    python3 -m ai_quality --version
    python3 -m ai_quality --help
    python3 -m ai_quality check <file> --format json
    python3 -m ai_quality check --diff --format json
    python3 -m ai_quality capabilities
"""

import sys

from ai_quality.cli.entry import main

if __name__ == "__main__":
    sys.exit(main())
