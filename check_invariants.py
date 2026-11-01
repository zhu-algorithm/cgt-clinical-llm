#!/usr/bin/env python3
"""本地/CI 通用的受保护不变量检查。

用法：
    python check_invariants.py            # 检查不变量，失败退出码 1
    python check_invariants.py 贡献文件.json 改动路径...   # 并受理一份贡献信封

CI 配置成功前，请在每次合并 PR 之前本地跑一次这个命令。
"""
import sys, json, pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent))

try:
    from fmed_gate.review import main
except ImportError as e:
    print(f"::error::无法导入 fmed_gate（{e}）。请先 pip install pynacl")
    raise SystemExit(2)

if __name__ == "__main__":
    rc = main(sys.argv[1:])
    if rc == 0 and len(sys.argv) == 1:
        print("\n受保护不变量全部保持。注意：CI 通过不等于已获同意——"
              "合并权在仓库所有者。")
    raise SystemExit(rc)
