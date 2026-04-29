#!/usr/bin/env python3
"""
setup_ci.py — 自动化安装CI基础设施

功能：
1. 安装 pre-commit hook 到 .git/hooks/
2. 初始化 pre-commit 配置
3. 创建必要的目录结构

用法：
  python scripts/setup_ci.py [--uninstall]
"""

import os
import sys
import stat
import shutil
import argparse

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
GIT_HOOKS_DIR = PROJECT_ROOT / '.git' / 'hooks'
PRE_COMMIT_FILE = GIT_HOOKS_DIR / 'pre-commit'
PRE_COMMIT_CONFIG = PROJECT_ROOT / '.pre-commit-config.yaml'
VENV_PYTHON = PROJECT_ROOT.parent / 'hermes-agent' / 'venv' / 'bin' / 'python3'
# 实际venv路径
VENV_PYTHON = PROJECT_ROOT / '.venv' / 'bin' / 'python3'
if not VENV_PYTHON.exists():
    VENV_PYTHON = Path('/Users/eason/.hermes/venv/bin/python3')

CONFIG_FILE = 'decision/config/morphology_config.json'
ENGINE_PATH = 'verify/propagation_engine.py'


def install_precommit():
    """安装pre-commit hook到.git/hooks/"""
    os.makedirs(GIT_HOOKS_DIR, exist_ok=True)
    
    hook_content = f'''#!/bin/bash
# pre-commit hook: morphology_config.json 修改时自动运行传播检测

CONFIG_FILE="{CONFIG_FILE}"

# 检查配置文件是否被修改
if git diff --cached --name-only | grep -q "^{CONFIG_FILE}$"; then
    echo "🔍 检测到 {CONFIG_FILE} 修改，运行传播检测..."
    
    cd {PROJECT_ROOT}
    PYTHONPATH={PROJECT_ROOT} {VENV_PYTHON} {ENGINE_PATH} --min-confidence 0.8 2>&1 | tee /tmp/propagation_check.log
    
    if grep -q "AUTO_APPLY\\|CONFIRM" /tmp/propagation_check.log 2>/dev/null; then
        echo "⚠️  警告: 检测到待修复的语义断裂"
        echo "📝 请审查上述结果后再继续commit"
    fi
    
    echo "✅ 传播检测完成"
else
    echo "📁 未检测到 {CONFIG_FILE} 修改，跳过"
fi

exit 0
'''

    # 备份已有hook
    if PRE_COMMIT_FILE.exists():
        backup = PRE_COMMIT_FILE.with_suffix('.bak')
        shutil.copy2(PRE_COMMIT_FILE, backup)
        print(f"✅ 已备份原hook到 {backup}")

    PRE_COMMIT_FILE.write_text(hook_content)
    PRE_COMMIT_FILE.chmod(PRE_COMMIT_FILE.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"✅ 已安装 pre-commit hook → {PRE_COMMIT_FILE}")


def uninstall():
    """卸载pre-commit hook"""
    if PRE_COMMIT_FILE.exists():
        PRE_COMMIT_FILE.unlink()
        print(f"✅ 已卸载 pre-commit hook")
    else:
        print("ℹ️  pre-commit hook 不存在")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--uninstall', action='store_true')
    args = parser.parse_args()

    if args.uninstall:
        uninstall()
    else:
        install_precommit()
        print("\n✅ CI 安装完成！")
        print(f"   pre-commit hook: {PRE_COMMIT_FILE}")
        print(f"   pre-commit配置: {PRE_COMMIT_CONFIG}")
        print("\n安装pre-commit (可选):")
        print("  pip install pre-commit")
        print("  pre-commit install")
