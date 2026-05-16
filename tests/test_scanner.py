"""scanner 模块测试。

覆盖：
- 默认忽略目录（node_modules / .git / __pycache__ / venv / dist / build）
- 应入库文件扩展名（.py / .md / .toml / .json）
- 语言检测
- 文件哈希
- .gitignore 过滤
- 跨平台路径（Windows 反斜杠兼容）
"""

from __future__ import annotations

from pathlib import Path

from code_rag.indexer.scanner import (
    FileEntry,
    RepoScanner,
    detect_language,
)

# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _create_file(path: Path, content: str = "# placeholder") -> None:
    """在指定路径创建文件，自动创建父目录。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _rel_paths(entries: list[FileEntry]) -> list[str]:
    """从 FileEntry 列表中提取 rel_path 列表。"""
    return [e.rel_path for e in entries]


def _names(entries: list[FileEntry]) -> list[str]:
    """从 FileEntry 列表中提取文件名（basename）列表。"""
    return [Path(e.rel_path).name for e in entries]


# ---------------------------------------------------------------------------
# 默认忽略目录
# ---------------------------------------------------------------------------


class TestDefaultIgnoreDirs:
    """测试默认忽略目录过滤。"""

    def test_ignores_node_modules(self, tmp_path: Path) -> None:
        _create_file(tmp_path / "node_modules" / "pkg" / "index.js", "var x = 1;")
        _create_file(tmp_path / "app.py", "def main(): pass")

        entries = RepoScanner(tmp_path).scan()
        assert _names(entries) == ["app.py"]

    def test_ignores_git(self, tmp_path: Path) -> None:
        _create_file(tmp_path / ".git" / "config", "[core]")
        _create_file(tmp_path / "main.py", "x = 1")

        entries = RepoScanner(tmp_path).scan()
        assert _names(entries) == ["main.py"]

    def test_ignores_pycache(self, tmp_path: Path) -> None:
        _create_file(tmp_path / "__pycache__" / "mod.cpython-312.pyc", "bytes")
        _create_file(tmp_path / "mod.py", "y = 2")

        entries = RepoScanner(tmp_path).scan()
        assert _names(entries) == ["mod.py"]

    def test_ignores_venv(self, tmp_path: Path) -> None:
        _create_file(tmp_path / "venv" / "lib" / "pkg.py", "z = 3")
        _create_file(tmp_path / "app.py", "w = 4")

        entries = RepoScanner(tmp_path).scan()
        assert _names(entries) == ["app.py"]

    def test_ignores_dist(self, tmp_path: Path) -> None:
        _create_file(tmp_path / "dist" / "bundle.js", "var z;")
        _create_file(tmp_path / "index.js", "console.log(1)")

        entries = RepoScanner(tmp_path).scan()
        assert _names(entries) == ["index.js"]

    def test_ignores_build(self, tmp_path: Path) -> None:
        _create_file(tmp_path / "build" / "output.so", "binary")
        _create_file(tmp_path / "lib.rs", "fn main() {}")

        entries = RepoScanner(tmp_path).scan()
        assert _names(entries) == ["lib.rs"]

    def test_ignores_multiple_at_once(self, tmp_path: Path) -> None:
        """多个忽略目录同时存在，只保留正常文件。"""
        _create_file(tmp_path / ".git" / "HEAD", "ref: refs/heads/main")
        _create_file(tmp_path / "node_modules" / "a.js", "x")
        _create_file(tmp_path / "__pycache__" / "b.pyc", "x")
        _create_file(tmp_path / "venv" / "c.py", "x")
        _create_file(tmp_path / "dist" / "d.js", "x")
        _create_file(tmp_path / "build" / "e.so", "x")
        _create_file(tmp_path / "src" / "main.py", "print('hi')")

        entries = RepoScanner(tmp_path).scan()
        assert _names(entries) == ["main.py"]
        assert _rel_paths(entries) == ["src/main.py"]


# ---------------------------------------------------------------------------
# 应入库文件扩展名
# ---------------------------------------------------------------------------


class TestScannableExtensions:
    """测试各种应入库文件的扫描。"""

    def test_scans_python_files(self, tmp_path: Path) -> None:
        _create_file(tmp_path / "app.py", "def main(): pass")
        entries = RepoScanner(tmp_path).scan()
        assert len(entries) == 1
        assert entries[0].language == "python"
        assert entries[0].is_code is True
        assert entries[0].is_doc is False

    def test_scans_markdown_files(self, tmp_path: Path) -> None:
        _create_file(tmp_path / "README.md", "# Hello")
        entries = RepoScanner(tmp_path).scan()
        assert len(entries) == 1
        assert entries[0].language == "doc"
        assert entries[0].is_doc is True
        assert entries[0].is_code is False

    def test_scans_toml_files(self, tmp_path: Path) -> None:
        _create_file(tmp_path / "pyproject.toml", "[project]\nname = 'test'")
        entries = RepoScanner(tmp_path).scan()
        assert len(entries) == 1
        assert entries[0].language == "doc"

    def test_scans_json_files(self, tmp_path: Path) -> None:
        _create_file(tmp_path / "package.json", '{"name": "test"}')
        entries = RepoScanner(tmp_path).scan()
        assert len(entries) == 1
        assert entries[0].language == "doc"

    def test_scans_yaml_files(self, tmp_path: Path) -> None:
        _create_file(tmp_path / "config.yaml", "key: value")
        entries = RepoScanner(tmp_path).scan()
        assert len(entries) == 1
        assert entries[0].language == "doc"

    def test_scans_javascript_files(self, tmp_path: Path) -> None:
        _create_file(tmp_path / "index.js", "console.log('hi');")
        entries = RepoScanner(tmp_path).scan()
        assert len(entries) == 1
        assert entries[0].language == "javascript"

    def test_scans_typescript_files(self, tmp_path: Path) -> None:
        _create_file(tmp_path / "app.ts", "const x: number = 1;")
        entries = RepoScanner(tmp_path).scan()
        assert len(entries) == 1
        assert entries[0].language == "typescript"

    def test_ignores_binary_extensions(self, tmp_path: Path) -> None:
        """二进制/图片/压缩包等扩展名应被过滤。"""
        _create_file(tmp_path / "image.png", "binary")
        _create_file(tmp_path / "archive.zip", "binary")
        _create_file(tmp_path / "lib.so", "binary")
        _create_file(tmp_path / "app.exe", "binary")
        _create_file(tmp_path / "ok.py", "x = 1")

        entries = RepoScanner(tmp_path).scan()
        names = _names(entries)
        assert "ok.py" in names
        assert "image.png" not in names
        assert "archive.zip" not in names
        assert "lib.so" not in names
        assert "app.exe" not in names


# ---------------------------------------------------------------------------
# 语言检测
# ---------------------------------------------------------------------------


class TestLanguageDetection:
    """测试 detect_language 函数。"""

    def test_python(self) -> None:
        assert detect_language(".py") == "python"

    def test_javascript(self) -> None:
        assert detect_language(".js") == "javascript"

    def test_typescript(self) -> None:
        assert detect_language(".ts") == "typescript"

    def test_rust(self) -> None:
        assert detect_language(".rs") == "rust"

    def test_go(self) -> None:
        assert detect_language(".go") == "go"

    def test_markdown_is_doc(self) -> None:
        assert detect_language(".md") == "doc"

    def test_toml_is_doc(self) -> None:
        assert detect_language(".toml") == "doc"

    def test_unknown_returns_none(self) -> None:
        assert detect_language(".xyz") is None


# ---------------------------------------------------------------------------
# 文件哈希
# ---------------------------------------------------------------------------


class TestFileHash:
    """测试 FileEntry 的 file_hash 字段。"""

    def test_hash_is_sha256(self, tmp_path: Path) -> None:
        _create_file(tmp_path / "a.py", "x = 1")
        entries = RepoScanner(tmp_path).scan()
        assert len(entries) == 1
        assert len(entries[0].file_hash) == 64  # SHA-256 hex = 64 chars
        assert all(c in "0123456789abcdef" for c in entries[0].file_hash)

    def test_same_content_same_hash(self, tmp_path: Path) -> None:
        _create_file(tmp_path / "a.py", "x = 1")
        _create_file(tmp_path / "b.py", "x = 1")
        entries = RepoScanner(tmp_path).scan()
        hashes = [e.file_hash for e in entries]
        assert hashes[0] == hashes[1]

    def test_different_content_different_hash(self, tmp_path: Path) -> None:
        _create_file(tmp_path / "a.py", "x = 1")
        _create_file(tmp_path / "b.py", "x = 2")
        entries = RepoScanner(tmp_path).scan()
        hashes = [e.file_hash for e in entries]
        assert hashes[0] != hashes[1]


# ---------------------------------------------------------------------------
# .gitignore 过滤
# ---------------------------------------------------------------------------


class TestGitignoreFilter:
    """测试 .gitignore 规则解析与过滤。"""

    def test_ignores_simple_pattern(self, tmp_path: Path) -> None:
        _create_file(tmp_path / ".gitignore", "*.log\n")
        _create_file(tmp_path / "debug.log", "log content")
        _create_file(tmp_path / "app.py", "x = 1")

        entries = RepoScanner(tmp_path).scan()
        names = _names(entries)
        assert "app.py" in names
        assert "debug.log" not in names

    def test_negation_pattern(self, tmp_path: Path) -> None:
        _create_file(tmp_path / ".gitignore", "*.log\n!important.log\n")
        _create_file(tmp_path / "debug.log", "debug")
        _create_file(tmp_path / "important.log", "important")
        _create_file(tmp_path / "app.py", "x = 1")

        entries = RepoScanner(tmp_path).scan()
        names = _names(entries)
        assert "important.log" in names
        assert "debug.log" not in names

    def test_ignores_directory_pattern(self, tmp_path: Path) -> None:
        _create_file(tmp_path / ".gitignore", "docs/\n")
        _create_file(tmp_path / "docs" / "guide.md", "# Guide")
        _create_file(tmp_path / "src" / "main.py", "x = 1")

        entries = RepoScanner(tmp_path).scan()
        names = _names(entries)
        assert "main.py" in names
        assert "guide.md" not in names


# ---------------------------------------------------------------------------
# 跨平台路径
# ---------------------------------------------------------------------------


class TestPathHandling:
    """测试路径处理的跨平台兼容性。"""

    def test_rel_path_uses_posix_separator(self, tmp_path: Path) -> None:
        """rel_path 应使用 / 分隔符（即使在 Windows 上）。"""
        _create_file(tmp_path / "src" / "lib" / "utils.py", "x = 1")
        entries = RepoScanner(tmp_path).scan()
        assert len(entries) == 1
        assert "\\" not in entries[0].rel_path
        assert entries[0].rel_path == "src/lib/utils.py"

    def test_nested_directory_structure(self, tmp_path: Path) -> None:
        """嵌套目录结构能被正确扫描。"""
        _create_file(tmp_path / "a" / "b" / "c" / "deep.py", "x = 1")
        _create_file(tmp_path / "shallow.py", "y = 2")
        entries = RepoScanner(tmp_path).scan()
        paths = _rel_paths(entries)
        assert "a/b/c/deep.py" in paths
        assert "shallow.py" in paths

    def test_scan_returns_sorted_paths(self, tmp_path: Path) -> None:
        """结果按 rel_path 排序。"""
        _create_file(tmp_path / "z.py", "a")
        _create_file(tmp_path / "a.py", "b")
        _create_file(tmp_path / "m.py", "c")
        entries = RepoScanner(tmp_path).scan()
        paths = _rel_paths(entries)
        assert paths == sorted(paths)


# ---------------------------------------------------------------------------
# 边界情况
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """测试边界情况。"""

    def test_empty_directory(self, tmp_path: Path) -> None:
        entries = RepoScanner(tmp_path).scan()
        assert entries == []

    def test_empty_file_skipped(self, tmp_path: Path) -> None:
        """空文件（0 字节）应被跳过。"""
        (tmp_path / "empty.py").touch()
        _create_file(tmp_path / "nonempty.py", "x = 1")
        entries = RepoScanner(tmp_path).scan()
        assert _names(entries) == ["nonempty.py"]

    def test_dotenv_files_ignored(self, tmp_path: Path) -> None:
        """.env 文件应被默认忽略。"""
        _create_file(tmp_path / ".env", "SECRET=abc")
        _create_file(tmp_path / ".env.local", "SECRET=def")
        _create_file(tmp_path / "app.py", "x = 1")
        entries = RepoScanner(tmp_path).scan()
        assert _names(entries) == ["app.py"]

    def test_entry_properties(self, tmp_path: Path) -> None:
        """FileEntry 的属性值正确。"""
        _create_file(tmp_path / "test.py", "x = 1")
        entries = RepoScanner(tmp_path).scan()
        assert len(entries) == 1
        e = entries[0]
        assert e.abs_path == (tmp_path / "test.py").resolve()
        assert e.extension == ".py"
        assert e.size > 0
        assert e.language == "python"
        assert e.is_code is True
        assert e.is_doc is False
