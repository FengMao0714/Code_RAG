"""parser + chunker 模块测试。

覆盖：
- Python class / function 解析
- module_summary / class / function / doc chunk 生成
- chunk metadata 完整性
- 长函数超过 max_chunk_tokens 时二次切分
- 语法错误文件不崩溃
"""

from __future__ import annotations

from pathlib import Path

from code_rag.indexer.chunker import CodeChunker, count_tokens
from code_rag.indexer.parser import CodeParser, ParsedSymbol

# ---------------------------------------------------------------------------
# Parser 测试
# ---------------------------------------------------------------------------


class TestCodeParser:
    """测试 tree-sitter 代码解析。"""

    def test_parse_python_function(self, tmp_path: Path) -> None:
        """Python 顶层函数能被正确解析。"""
        src = "def hello(name: str) -> str:\n    return f'hi {name}'\n"
        file = tmp_path / "mod.py"
        file.write_text(src, encoding="utf-8")

        symbols = CodeParser().parse_file(file, "python", "mod.py")
        funcs = [s for s in symbols if s.chunk_type == "function"]
        assert len(funcs) >= 1
        assert funcs[0].name == "hello"
        assert funcs[0].parent is None
        assert funcs[0].start_line >= 1
        assert funcs[0].end_line >= funcs[0].start_line

    def test_parse_python_class_with_methods(self, tmp_path: Path) -> None:
        """Python 类和方法能被正确解析。"""
        src = (
            "class Greeter:\n"
            '    """A greeter class."""\n'
            "    def greet(self) -> str:\n"
            "        return 'hello'\n"
            "\n"
            "def standalone() -> int:\n"
            "    return 42\n"
        )
        file = tmp_path / "mod.py"
        file.write_text(src, encoding="utf-8")

        symbols = CodeParser().parse_file(file, "python", "mod.py")
        classes = [s for s in symbols if s.chunk_type == "class"]
        functions = [s for s in symbols if s.chunk_type == "function"]

        assert len(classes) >= 1
        assert classes[0].name == "Greeter"
        assert classes[0].parent is None

        # 方法：parent == "Greeter"
        methods = [s for s in functions if s.parent == "Greeter"]
        assert len(methods) >= 1
        assert methods[0].name == "greet"

        # 顶层函数：parent is None
        top_funcs = [s for s in functions if s.parent is None]
        assert any(s.name == "standalone" for s in top_funcs)

    def test_parse_empty_file(self, tmp_path: Path) -> None:
        """空文件应返回空列表。"""
        file = tmp_path / "empty.py"
        file.write_text("", encoding="utf-8")

        symbols = CodeParser().parse_file(file, "python", "empty.py")
        assert symbols == []

    def test_parse_whitespace_only_file(self, tmp_path: Path) -> None:
        """只含空白的文件应返回空列表。"""
        file = tmp_path / "blank.py"
        file.write_text("   \n\n  \n", encoding="utf-8")

        symbols = CodeParser().parse_file(file, "python", "blank.py")
        assert symbols == []

    def test_parse_no_symbols_generates_module_summary(self, tmp_path: Path) -> None:
        """无法提取符号时，至少生成一个 module_summary。"""
        src = "x = 1\ny = 2\nz = 3\n"
        file = tmp_path / "vars.py"
        file.write_text(src, encoding="utf-8")

        symbols = CodeParser().parse_file(file, "python", "vars.py")
        assert len(symbols) >= 1
        assert symbols[0].chunk_type == "module_summary"
        assert symbols[0].file_path == "vars.py"

    def test_parse_syntax_error_no_crash(self, tmp_path: Path) -> None:
        """语法错误文件不能导致崩溃。"""
        src = "def broken(\n    pass\n!!!invalid\n"
        file = tmp_path / "broken.py"
        file.write_text(src, encoding="utf-8")

        # 不应抛出异常
        symbols = CodeParser().parse_file(file, "python", "broken.py")
        assert isinstance(symbols, list)

    def test_parse_text_method(self) -> None:
        """parse_text 无需文件也能解析。"""
        src = "def add(a, b):\n    return a + b\n"
        symbols = CodeParser().parse_text(src, "python", "<test>")
        funcs = [s for s in symbols if s.chunk_type == "function"]
        assert any(s.name == "add" for s in funcs)

    def test_parse_unsupported_language(self, tmp_path: Path) -> None:
        """不支持的语言应返回空列表。"""
        file = tmp_path / "test.xyz"
        file.write_text("some content\n", encoding="utf-8")

        symbols = CodeParser().parse_file(file, "unknown_lang", "test.xyz")
        assert symbols == []


# ---------------------------------------------------------------------------
# Chunker 测试
# ---------------------------------------------------------------------------


class TestCodeChunker:
    """测试语义切片。"""

    def test_generates_module_summary(self) -> None:
        """代码文件始终生成 module_summary chunk。"""
        src = "import os\n\ndef main():\n    pass\n"
        sym = ParsedSymbol(
            file_path="app.py",
            language="python",
            chunk_type="function",
            name="main",
            start_line=3,
            end_line=4,
            parent=None,
            source="def main():\n    pass\n",
        )
        chunker = CodeChunker(max_chunk_tokens=512)
        chunks = chunker.chunk([sym], "hash1", full_source=src)

        summaries = [c for c in chunks if c.chunk_type == "module_summary"]
        assert len(summaries) >= 1
        assert "import os" in summaries[0].source

    def test_generates_class_chunk(self) -> None:
        """类生成 class chunk（不含方法体）和独立的 function chunk。"""
        class_src = (
            "class Calculator:\n"
            '    """A simple calculator."""\n'
            "\n"
            "    def add(self, a: int, b: int) -> int:\n"
            "        return a + b\n"
        )
        sym_class = ParsedSymbol(
            file_path="calc.py",
            language="python",
            chunk_type="class",
            name="Calculator",
            start_line=1,
            end_line=5,
            parent=None,
            source=class_src,
        )
        sym_method = ParsedSymbol(
            file_path="calc.py",
            language="python",
            chunk_type="function",
            name="add",
            start_line=4,
            end_line=5,
            parent="Calculator",
            source="    def add(self, a: int, b: int) -> int:\n        return a + b\n",
        )

        chunker = CodeChunker(max_chunk_tokens=512)
        chunks = chunker.chunk([sym_class, sym_method], "hash2", full_source=class_src)

        class_chunks = [c for c in chunks if c.chunk_type == "class"]
        assert len(class_chunks) >= 1
        assert class_chunks[0].name == "Calculator"
        assert "def add" in class_chunks[0].source
        assert "return a + b" not in class_chunks[0].source

        method_chunks = [
            c for c in chunks if c.chunk_type == "function" and c.parent == "Calculator"
        ]
        assert len(method_chunks) >= 1
        assert method_chunks[0].name == "add"

    def test_generates_function_chunk(self) -> None:
        """顶层函数生成 function chunk。"""
        func_src = "def greet(name: str) -> str:\n    return f'hi {name}'\n"
        sym = ParsedSymbol(
            file_path="utils.py",
            language="python",
            chunk_type="function",
            name="greet",
            start_line=1,
            end_line=2,
            parent=None,
            source=func_src,
        )
        chunker = CodeChunker(max_chunk_tokens=512)
        chunks = chunker.chunk([sym], "hash3")

        func_chunks = [c for c in chunks if c.chunk_type == "function"]
        assert len(func_chunks) >= 1
        assert func_chunks[0].name == "greet"
        assert func_chunks[0].parent is None

    def test_generates_doc_chunk(self) -> None:
        """doc 类型符号生成 doc chunk。"""
        doc_src = "# Project Title\n\nSome description.\n"
        sym = ParsedSymbol(
            file_path="README.md",
            language="doc",
            chunk_type="doc",
            name="README.md",
            start_line=1,
            end_line=3,
            parent=None,
            source=doc_src,
        )
        chunker = CodeChunker(max_chunk_tokens=512)
        chunks = chunker.chunk([sym], "hash4")

        doc_chunks = [c for c in chunks if c.chunk_type == "doc"]
        assert len(doc_chunks) == 1
        assert doc_chunks[0].source == doc_src
        assert doc_chunks[0].language == "doc"

    def test_empty_input(self) -> None:
        """空输入返回空列表。"""
        chunker = CodeChunker(max_chunk_tokens=512)
        assert chunker.chunk([], "hash") == []
        assert chunker.chunk([], "hash", full_source=None) == []


# ---------------------------------------------------------------------------
# Chunk metadata 完整性
# ---------------------------------------------------------------------------


class TestChunkMetadata:
    """测试每个 chunk 携带完整 metadata。"""

    def test_metadata_fields_present(self) -> None:
        """CodeChunk 包含所有必要字段。"""
        sym = ParsedSymbol(
            file_path="src/app.py",
            language="python",
            chunk_type="function",
            name="main",
            start_line=10,
            end_line=20,
            parent=None,
            source="def main():\n    pass\n",
        )
        chunker = CodeChunker(max_chunk_tokens=512)
        chunks = chunker.chunk([sym], "abc123")
        func_chunks = [c for c in chunks if c.chunk_type == "function"]
        assert len(func_chunks) >= 1
        c = func_chunks[0]

        assert c.file_path == "src/app.py"
        assert c.language == "python"
        assert c.chunk_type == "function"
        assert c.name == "main"
        assert c.start_line == 10
        assert c.end_line == 20
        assert c.parent is None
        assert c.file_hash == "abc123"
        assert c.token_count > 0
        assert c.source  # 非空

    def test_parent_populated_for_methods(self) -> None:
        """方法级 chunk 的 parent 字段正确。"""
        sym = ParsedSymbol(
            file_path="mod.py",
            language="python",
            chunk_type="function",
            name="do_something",
            start_line=5,
            end_line=8,
            parent="MyClass",
            source="    def do_something(self):\n        pass\n",
        )
        class_sym = ParsedSymbol(
            file_path="mod.py",
            language="python",
            chunk_type="class",
            name="MyClass",
            start_line=1,
            end_line=8,
            parent=None,
            source="class MyClass:\n    def do_something(self):\n        pass\n",
        )
        chunker = CodeChunker(max_chunk_tokens=512)
        chunks = chunker.chunk([class_sym, sym], "def456")
        method_chunks = [c for c in chunks if c.chunk_type == "function" and c.parent == "MyClass"]
        assert len(method_chunks) >= 1
        assert method_chunks[0].parent == "MyClass"


# ---------------------------------------------------------------------------
# 长函数二次切分
# ---------------------------------------------------------------------------


class TestOversizedFunctionSplit:
    """测试超过 max_chunk_tokens 的函数被二次切分。"""

    def test_long_function_split(self) -> None:
        """超过 token 上限的函数被切分为多个 sub-chunk。"""
        # 生成一个足够长的函数（约 200 行，每个 print ~6 tokens）
        lines = ["def long_func() -> None:\n"]
        for i in range(200):
            lines.append(f"    print('line number {i}')\n")
        src = "".join(lines)

        sym = ParsedSymbol(
            file_path="big.py",
            language="python",
            chunk_type="function",
            name="long_func",
            start_line=1,
            end_line=201,
            parent=None,
            source=src,
        )
        chunker = CodeChunker(max_chunk_tokens=100)
        chunks = chunker.chunk([sym], "hash5")

        func_chunks = [c for c in chunks if c.chunk_type == "function"]
        assert len(func_chunks) >= 2  # 应被切分为至少 2 个

        # 每个 sub-chunk 的 name 包含 [part N/total]
        for fc in func_chunks:
            assert "[part" in fc.name
            assert "long_func" in fc.name

        # metadata 中包含 sub_index 和 sub_total
        for fc in func_chunks:
            assert "sub_index" in fc.metadata
            assert "sub_total" in fc.metadata
            assert fc.metadata["sub_total"] == len(func_chunks)

    def test_short_function_not_split(self) -> None:
        """未超过上限的函数不被切分。"""
        src = "def short() -> int:\n    return 1\n"
        sym = ParsedSymbol(
            file_path="small.py",
            language="python",
            chunk_type="function",
            name="short",
            start_line=1,
            end_line=2,
            parent=None,
            source=src,
        )
        chunker = CodeChunker(max_chunk_tokens=512)
        chunks = chunker.chunk([sym], "hash6")

        func_chunks = [c for c in chunks if c.chunk_type == "function"]
        assert len(func_chunks) == 1
        assert "[part" not in func_chunks[0].name

    def test_sub_chunks_cover_full_source(self) -> None:
        """二次切分后所有 sub-chunk 的源码拼接应覆盖原始源码。"""
        lines = ["def huge() -> None:\n"]
        for i in range(150):
            lines.append(f"    x_{i} = {i}\n")
        src = "".join(lines)

        sym = ParsedSymbol(
            file_path="huge.py",
            language="python",
            chunk_type="function",
            name="huge",
            start_line=1,
            end_line=151,
            parent=None,
            source=src,
        )
        chunker = CodeChunker(max_chunk_tokens=80)
        chunks = chunker.chunk([sym], "hash7")
        func_chunks = [c for c in chunks if c.chunk_type == "function"]

        reconstructed = "".join(c.source for c in func_chunks)
        # 所有行都应出现
        for i in range(150):
            assert f"x_{i} = {i}" in reconstructed


# ---------------------------------------------------------------------------
# Token 计数
# ---------------------------------------------------------------------------


class TestTokenCounting:
    """测试 token 计数功能。"""

    def test_count_tokens_non_negative(self) -> None:
        assert count_tokens("hello world") >= 0

    def test_count_tokens_empty_string(self) -> None:
        assert count_tokens("") == 0

    def test_longer_text_more_tokens(self) -> None:
        short = count_tokens("hi")
        long = count_tokens("hello world " * 100)
        assert long > short
