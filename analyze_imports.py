#!/usr/bin/env python3
"""
Analyze project structure and storage-related imports.
Run this in your Hyperliquid_TWAP_Tracker directory.

Usage:
    python analyze_imports.py
"""
import os
import re
from pathlib import Path
from collections import defaultdict


def find_python_files(root_dir: Path) -> list:
    """Find all Python files in the project."""
    py_files = []
    for path in root_dir.rglob("*.py"):
        # Skip venv, __pycache__, .git
        if any(skip in str(path) for skip in ['.venv', 'venv', '__pycache__', '.git', 'node_modules']):
            continue
        py_files.append(path)
    return sorted(py_files)


def extract_imports(file_path: Path) -> list:
    """Extract all import statements from a Python file."""
    imports = []
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Match 'import x' and 'from x import y'
        import_pattern = r'^(?:from\s+[\w.]+\s+)?import\s+.+'
        for line in content.split('\n'):
            line = line.strip()
            if re.match(import_pattern, line):
                imports.append(line)
    except Exception as e:
        imports.append(f"ERROR reading file: {e}")

    return imports


def find_storage_usage(file_path: Path) -> dict:
    """Find storage-related imports and usage in a file."""
    result = {
        'imports': [],
        'usages': []
    }

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            lines = content.split('\n')

        # Keywords to search for
        storage_keywords = [
            'SQLiteBackend', 'sqlite_backend', 'sqlite_storage',
            'storage', 'twap.db', '.db', 'sqlite3',
            'WhaleStorage', 'MarketStorage', 'TwapStorage'
        ]

        for i, line in enumerate(lines, 1):
            for keyword in storage_keywords:
                if keyword in line:
                    if 'import' in line.lower():
                        result['imports'].append(f"L{i}: {line.strip()}")
                    else:
                        result['usages'].append(f"L{i}: {line.strip()}")
                    break

    except Exception as e:
        result['error'] = str(e)

    return result


def get_directory_structure(root_dir: Path, max_depth: int = 3) -> str:
    """Generate a tree view of the directory structure."""
    output = []

    def walk_dir(path: Path, prefix: str = "", depth: int = 0):
        if depth > max_depth:
            return

        # Get items, filter out hidden and common excludes
        items = []
        try:
            for item in sorted(path.iterdir()):
                name = item.name
                if name.startswith('.') or name in ['__pycache__', 'venv', '.venv', 'node_modules']:
                    continue
                items.append(item)
        except PermissionError:
            return

        for i, item in enumerate(items):
            is_last = i == len(items) - 1
            current_prefix = "└── " if is_last else "├── "
            next_prefix = "    " if is_last else "│   "

            if item.is_file():
                output.append(f"{prefix}{current_prefix}{item.name}")
            else:
                output.append(f"{prefix}{current_prefix}{item.name}/")
                walk_dir(item, prefix + next_prefix, depth + 1)

    output.append(f"{root_dir.name}/")
    walk_dir(root_dir)

    return "\n".join(output)


def main():
    root_dir = Path('.')

    print("=" * 70)
    print("PROJECT STRUCTURE ANALYSIS")
    print("=" * 70)

    # 1. Directory structure
    print("\n📁 DIRECTORY STRUCTURE")
    print("-" * 70)
    print(get_directory_structure(root_dir))

    # 2. Find all Python files
    py_files = find_python_files(root_dir)
    print(f"\n📄 PYTHON FILES FOUND: {len(py_files)}")
    print("-" * 70)
    for f in py_files:
        print(f"  {f}")

    # 3. Analyze storage-related imports and usage
    print("\n🔍 STORAGE-RELATED CODE")
    print("-" * 70)

    files_with_storage = []

    for py_file in py_files:
        result = find_storage_usage(py_file)
        if result['imports'] or result['usages']:
            files_with_storage.append((py_file, result))

    if files_with_storage:
        for file_path, result in files_with_storage:
            print(f"\n📄 {file_path}")
            if result['imports']:
                print("  IMPORTS:")
                for imp in result['imports']:
                    print(f"    {imp}")
            if result['usages']:
                print("  USAGES:")
                for use in result['usages'][:10]:  # Limit to first 10
                    print(f"    {use}")
                if len(result['usages']) > 10:
                    print(f"    ... and {len(result['usages']) - 10} more")
    else:
        print("  No storage-related code found")

    # 4. Check for storage/__init__.py
    print("\n📦 STORAGE MODULE CHECK")
    print("-" * 70)

    storage_init = root_dir / "storage" / "__init__.py"
    if storage_init.exists():
        print(f"  ✅ {storage_init} exists")
        print("  Content:")
        with open(storage_init, 'r') as f:
            for line in f.readlines()[:20]:
                print(f"    {line.rstrip()}")
    else:
        print(f"  ❌ {storage_init} not found")

    # 5. Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Python files: {len(py_files)}")
    print(f"  Files using storage: {len(files_with_storage)}")

    # List the files that import storage
    if files_with_storage:
        print("\n  Files that need updating after refactor:")
        for file_path, result in files_with_storage:
            if result['imports']:
                print(f"    - {file_path}")
                for imp in result['imports']:
                    print(f"        {imp}")


if __name__ == "__main__":
    main()