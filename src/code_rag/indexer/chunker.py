"""语义切片器 — 将 AST 解析结果转换为带 metadata 的 chunk。

按 CLAUDE.md 定义的切片策略，将 :class:`ParsedSymbol` 列表转换为
:class:`CodeChunk` 列表。每个 chunk 携带完整的 metadata，可直接
送入 Embedding 环节。

切片类型：

- ``module_summary``: 文件级概要（路径 + imports + 顶层变量）
- ``class``: 类定义 + docstring + 方法签名列表（不含方法体）
- ``function``: 完整函数/方法代码（含 docstring）
- ``doc``: 文档文件内容（README / .md / .rst 等）

对超过 ``max_chunk_tokens`` 的函数做基于行边界语义的二次切分。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import tiktoken

from code_rag.indexer.parser import ParsedSymbol

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 默认 tokenizer 编码名称（与 OpenAI / BGE 系列兼容）
_DEFAULT_ENCODING = "cl100k_base"

# module_summary 中提取 imports 时的前缀匹配模式
_IMPORT_LINE_RE = re.compile(
    r"^\s*(import\s|from\s|#include\s|using\s|use\s|require\s|package\s)",
    re.IGNORECASE,
)

# 顶层变量赋值模式（Python / JS / Go / Rust 等）
_TOP_LEVEL_VAR_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*\s*[:=]",
)

# ---------------------------------------------------------------------------
# chunk 数据类
# ---------------------------------------------------------------------------


@dataclass
class CodeChunk:
    """单个语义切片，携带完整 metadata。

    字段与 CLAUDE.md 定义的 Chunk Metadata 结构一一对应。
    """

    file_path: str
    """相对于仓库根目录的路径。"""
    language: str
    """编程语言（如 'python'）或 'doc'。"""
    chunk_type: str
    """切片类型：module_summary / class / function / doc。"""
    name: str
    """函数/类名，或文件名。"""
    start_line: int
    """起始行号（1-indexed）。"""
    end_line: int
    """结束行号（1-indexed）。"""
    parent: str | None
    """所属类名（方法级别符号有值）。"""
    file_hash: str
    """文件 SHA-256 哈希（用于增量更新）。"""
    source: str
    """切片的源代码文本。"""
    token_count: int
    """切片的 token 数量。"""
    metadata: dict = field(default_factory=dict)
    """扩展元数据（如 sub_index 用于二次切分标记）。"""


# ---------------------------------------------------------------------------
# Token 计数器（带缓存）
# ---------------------------------------------------------------------------

_ENC_CACHE: dict[str, tiktoken.Encoding] = {}


def _get_encoding(name: str = _DEFAULT_ENCODING) -> tiktoken.Encoding:
    """获取或创建 tiktoken 编码器（带缓存）。"""
    if name not in _ENC_CACHE:
        _ENC_CACHE[name] = tiktoken.get_encoding(name)
    return _ENC_CACHE[name]


def count_tokens(text: str, encoding_name: str = _DEFAULT_ENCODING) -> int:
    """计算文本的 token 数量。

    Args:
        text: 待计数的文本。
        encoding_name: tiktoken 编码名称。

    Returns:
        token 数量。
    """
    enc = _get_encoding(encoding_name)
    return len(enc.encode(text))


# ---------------------------------------------------------------------------
# 切片器
# ---------------------------------------------------------------------------


class CodeChunker:
    """语义切片器。

    将 :class:`ParsedSymbol` 列表转换为 :class:`CodeChunk` 列表，
    同时为超长函数生成带 sub_index 的二次切分结果。

    用法::

        chunker = CodeChunker(max_chunk_tokens=512)
        chunks = chunker.chunk(symbols, file_hash="abc123")
        for c in chunks:
            print(c.chunk_type, c.name, c.token_count)

    参数:
        max_chunk_tokens: 单个 chunk 的 token 上限。超过此值的函数
            将被二次切分。
        encoding_name: tiktoken 编码名称，默认 ``cl100k_base``。
    """

    def __init__(
        self,
        *,
        max_chunk_tokens: int = 512,
        encoding_name: str = _DEFAULT_ENCODING,
    ) -> None:
        """初始化切片器。

        Args:
            max_chunk_tokens: 单 chunk token 上限。
            encoding_name: tiktoken 编码名称。
        """
        self._max_tokens = max_chunk_tokens
        self._encoding_name = encoding_name

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def chunk(
        self,
        symbols: list[ParsedSymbol],
        file_hash: str,
        *,
        full_source: str | None = None,
    ) -> list[CodeChunk]:
        """对一个文件的 ParsedSymbol 列表进行语义切片。

        根据符号类型分发到不同的切片方法：

        - ``module_summary``: 从 ``full_source`` 中提取 imports + 顶层变量
        - ``class``: 类签名 + docstring + 方法签名列表
        - ``function``: 完整函数代码（超长则二次切分）
        - ``doc``: 文档文件内容

        Args:
            symbols: 一个文件的 :class:`ParsedSymbol` 列表。
            file_hash: 文件的 SHA-256 哈希。
            full_source: 文件的完整源代码文本。传入后可自动生成
                ``module_summary``；对于 doc 文件也会使用此文本。

        Returns:
            :class:`CodeChunk` 列表。
        """
        if not symbols and not full_source:
            return []

        chunks: list[CodeChunk] = []

        # 检测是否有 doc 类型的符号
        has_doc = any(s.chunk_type == "doc" for s in symbols)

        # 生成 module_summary chunk（代码文件始终生成）
        if full_source and not has_doc:
            first = symbols[0] if symbols else None
            file_path = first.file_path if first else "<unknown>"
            language = first.language if first else "python"
            total_lines = full_source.count("\n") + 1
            summary_sym = ParsedSymbol(
                file_path=file_path,
                language=language,
                chunk_type="module_summary",
                name=Path(file_path).name,
                start_line=1,
                end_line=total_lines,
                parent=None,
                source=full_source,
            )
            chunks.extend(self._chunk_module_summary(summary_sym, file_hash))

        # 处理 parser 产出的符号
        for sym in symbols:
            if sym.chunk_type == "module_summary":
                chunks.extend(self._chunk_module_summary(sym, file_hash))
            elif sym.chunk_type == "doc":
                chunks.extend(self._chunk_doc(sym, file_hash))
            elif sym.chunk_type == "class":
                chunks.extend(self._chunk_class(sym, symbols, file_hash))
            elif sym.chunk_type == "function" and sym.parent is None:
                chunks.extend(self._chunk_function(sym, file_hash))

        # 方法级别 function（有 parent）在 _chunk_class 中已处理，
        # 此处跳过以避免重复
        return chunks

    # ------------------------------------------------------------------
    # module_summary 切片
    # ------------------------------------------------------------------

    def _chunk_module_summary(
        self,
        sym: ParsedSymbol,
        file_hash: str,
    ) -> list[CodeChunk]:
        """生成文件级概要 chunk。

        从完整源码中提取：文件路径、imports、顶层变量赋值。
        不包含函数/类的完整代码。
        """
        lines = sym.source.splitlines(keepends=True)
        summary_parts: list[str] = []

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if _IMPORT_LINE_RE.match(stripped):
                summary_parts.append(line)
            elif _TOP_LEVEL_VAR_RE.match(stripped) and not stripped.startswith(
                ("def ", "class ", "function ")
            ):
                summary_parts.append(line)

        # 如果提取内容过少，回退到源码前 N 行
        if len(summary_parts) < 2:
            preview_lines = lines[:20]
            summary_parts = preview_lines

        summary_source = "".join(summary_parts).strip()
        if not summary_source:
            summary_source = f"# File: {sym.file_path}"

        header = f"# File: {sym.file_path}\n"
        full_source = header + summary_source

        tokens = count_tokens(full_source, self._encoding_name)

        return [
            CodeChunk(
                file_path=sym.file_path,
                language=sym.language,
                chunk_type="module_summary",
                name=sym.name,
                start_line=1,
                end_line=sym.end_line,
                parent=None,
                file_hash=file_hash,
                source=full_source,
                token_count=tokens,
            )
        ]

    # ------------------------------------------------------------------
    # class 切片
    # ------------------------------------------------------------------

    def _chunk_class(
        self,
        sym: ParsedSymbol,
        all_symbols: list[ParsedSymbol],
        file_hash: str,
    ) -> list[CodeChunk]:
        """生成类定义 chunk。

        包含：类签名 + docstring + 方法签名列表（不含方法体）。
        """
        class_lines = sym.source.splitlines(keepends=True)
        result_lines: list[str] = []
        in_docstring = False
        method_indent: int | None = None

        for line in class_lines:
            stripped = line.strip()

            if method_indent is not None:
                indent = _indent_width(line)
                if not stripped or indent > method_indent:
                    continue
                method_indent = None

            # 处理 docstring（三引号字符串）
            if not in_docstring and _is_docstring_start(stripped):
                result_lines.append(line)
                if _is_docstring_end(stripped) and stripped.count('"""') >= 2:
                    pass  # 单行 docstring，已结束
                else:
                    in_docstring = True
                continue

            if in_docstring:
                result_lines.append(line)
                if _is_docstring_end(stripped):
                    in_docstring = False
                continue

            # 方法定义行：保留签名，跳过方法体
            if _is_method_def(stripped):
                result_lines.append(line)
                method_indent = _indent_width(line)
                continue

            # 类签名行、空行、注释、类级变量赋值
            result_lines.append(line)

        class_source = "".join(result_lines).strip()
        if not class_source:
            class_source = sym.source

        tokens = count_tokens(class_source, self._encoding_name)

        chunk = CodeChunk(
            file_path=sym.file_path,
            language=sym.language,
            chunk_type="class",
            name=sym.name,
            start_line=sym.start_line,
            end_line=sym.end_line,
            parent=None,
            file_hash=file_hash,
            source=class_source,
            token_count=tokens,
        )

        # 对类内方法做独立切片
        method_chunks: list[CodeChunk] = []
        for child_sym in all_symbols:
            if child_sym.chunk_type == "function" and child_sym.parent == sym.name:
                method_chunks.extend(self._chunk_function(child_sym, file_hash))

        return [chunk, *method_chunks]

    # ------------------------------------------------------------------
    # function 切片
    # ------------------------------------------------------------------

    def _chunk_function(
        self,
        sym: ParsedSymbol,
        file_hash: str,
    ) -> list[CodeChunk]:
        """生成函数/方法 chunk。

        如果 token 数量超过 ``max_chunk_tokens``，执行基于行边界的
        二次切分，每个子 chunk 携带 ``sub_index`` 元数据。
        """
        source = sym.source
        tokens = count_tokens(source, self._encoding_name)

        if tokens <= self._max_tokens:
            return [
                CodeChunk(
                    file_path=sym.file_path,
                    language=sym.language,
                    chunk_type="function",
                    name=sym.name,
                    start_line=sym.start_line,
                    end_line=sym.end_line,
                    parent=sym.parent,
                    file_hash=file_hash,
                    source=source,
                    token_count=tokens,
                )
            ]

        # 二次切分
        return self._split_oversized_function(sym, file_hash)

    def _split_oversized_function(
        self,
        sym: ParsedSymbol,
        file_hash: str,
    ) -> list[CodeChunk]:
        """对超长函数做基于行边界的二次切分。

        切分策略：
        1. 按行累积直到 token 上限
        2. 在 token 上限附近向前搜索最佳切分点：
           - 优先切在空行处
           - 其次切在以 ``}``、``end``、``pass``、``return``、``yield``
             结尾的行
        3. 每个子 chunk 的 ``name`` 追加 ``[part N]`` 后缀，
           metadata 中记录 ``sub_index`` 和 ``sub_total``
        """
        lines = sym.source.splitlines(keepends=True)
        if not lines:
            return [self._make_chunk(sym, file_hash, sym.source)]

        # 按 token 上限切分
        sub_chunks_raw: list[list[str]] = []
        current_lines: list[str] = []
        current_tokens = 0

        for line in lines:
            line_tokens = count_tokens(line, self._encoding_name)

            if current_tokens + line_tokens > self._max_tokens and current_lines:
                sub_chunks_raw.append(current_lines)
                current_lines = []
                current_tokens = 0

            current_lines.append(line)
            current_tokens += line_tokens

        if current_lines:
            sub_chunks_raw.append(current_lines)

        # 对每个子 chunk 尝试在语义边界处微调
        adjusted: list[list[str]] = []
        for i, chunk_lines in enumerate(sub_chunks_raw):
            if i < len(sub_chunks_raw) - 1:
                # 非末尾 chunk：尝试在尾部寻找更好的切分点
                best_cut = self._find_split_point(chunk_lines)
                if best_cut < len(chunk_lines):
                    # 将多余行推给下一个 chunk
                    overflow = chunk_lines[best_cut:]
                    chunk_lines = chunk_lines[:best_cut]
                    if i + 1 < len(sub_chunks_raw):
                        sub_chunks_raw[i + 1] = overflow + sub_chunks_raw[i + 1]
            adjusted.append(chunk_lines)

        total = len(adjusted)
        if total <= 1:
            # 切分后只有一个 chunk（极端情况），直接返回
            return [self._make_chunk(sym, file_hash, sym.source)]

        chunks: list[CodeChunk] = []
        line_offset = sym.start_line

        for idx, chunk_lines in enumerate(adjusted):
            chunk_source = "".join(chunk_lines)
            chunk_tokens = count_tokens(chunk_source, self._encoding_name)
            chunk_start = line_offset
            chunk_end = line_offset + len(chunk_lines) - 1

            chunk = CodeChunk(
                file_path=sym.file_path,
                language=sym.language,
                chunk_type="function",
                name=f"{sym.name} [part {idx + 1}/{total}]",
                start_line=chunk_start,
                end_line=chunk_end,
                parent=sym.parent,
                file_hash=file_hash,
                source=chunk_source,
                token_count=chunk_tokens,
                metadata={"sub_index": idx + 1, "sub_total": total},
            )
            chunks.append(chunk)
            line_offset = chunk_end + 1

        return chunks

    # ------------------------------------------------------------------
    # doc 切片
    # ------------------------------------------------------------------

    def _chunk_doc(
        self,
        sym: ParsedSymbol,
        file_hash: str,
    ) -> list[CodeChunk]:
        """对文档文件生成 chunk。

        如果文档过长，按 Markdown 标题（``#``）拆分为多个 chunk。
        """
        source = sym.source
        tokens = count_tokens(source, self._encoding_name)

        if tokens <= self._max_tokens:
            return [
                CodeChunk(
                    file_path=sym.file_path,
                    language="doc",
                    chunk_type="doc",
                    name=sym.name,
                    start_line=1,
                    end_line=source.count("\n") + 1,
                    parent=None,
                    file_hash=file_hash,
                    source=source,
                    token_count=tokens,
                )
            ]

        # 按 Markdown 标题拆分
        return self._split_doc_by_headings(sym, file_hash)

    def _split_doc_by_headings(
        self,
        sym: ParsedSymbol,
        file_hash: str,
    ) -> list[CodeChunk]:
        """按 Markdown 标题拆分过长文档。"""
        lines = sym.source.splitlines(keepends=True)
        sections: list[tuple[int, list[str]]] = []  # (start_line, lines)
        current_start = 1
        current_lines: list[str] = []

        for i, line in enumerate(lines, start=1):
            if re.match(r"^#{1,6}\s", line) and current_lines:
                sections.append((current_start, current_lines))
                current_start = i
                current_lines = []
            current_lines.append(line)

        if current_lines:
            sections.append((current_start, current_lines))

        if len(sections) <= 1:
            # 无法按标题拆分，回退到行级切分
            return self._split_oversized_generic(sym, file_hash)

        chunks: list[CodeChunk] = []
        for sec_start, sec_lines in sections:
            sec_source = "".join(sec_lines).strip()
            if not sec_source:
                continue
            sec_tokens = count_tokens(sec_source, self._encoding_name)

            if sec_tokens <= self._max_tokens:
                chunks.append(
                    CodeChunk(
                        file_path=sym.file_path,
                        language="doc",
                        chunk_type="doc",
                        name=sym.name,
                        start_line=sec_start,
                        end_line=sec_start + len(sec_lines) - 1,
                        parent=None,
                        file_hash=file_hash,
                        source=sec_source,
                        token_count=sec_tokens,
                    )
                )
            else:
                # 单个 section 仍然过长，回退到行级切分
                sub_sym = ParsedSymbol(
                    file_path=sym.file_path,
                    language=sym.language,
                    chunk_type=sym.chunk_type,
                    name=sym.name,
                    start_line=sec_start,
                    end_line=sec_start + len(sec_lines) - 1,
                    parent=None,
                    source=sec_source,
                )
                chunks.extend(self._split_oversized_generic(sub_sym, file_hash))

        return chunks

    def _split_oversized_generic(
        self,
        sym: ParsedSymbol,
        file_hash: str,
    ) -> list[CodeChunk]:
        """通用的行级超长切分（用于 doc 或无法按标题拆分的情况）。"""
        lines = sym.source.splitlines(keepends=True)
        if not lines:
            return [self._make_chunk(sym, file_hash, sym.source)]

        sub_chunks: list[list[str]] = []
        current_lines: list[str] = []
        current_tokens = 0

        for line in lines:
            line_tokens = count_tokens(line, self._encoding_name)
            if current_tokens + line_tokens > self._max_tokens and current_lines:
                sub_chunks.append(current_lines)
                current_lines = []
                current_tokens = 0
            current_lines.append(line)
            current_tokens += line_tokens

        if current_lines:
            sub_chunks.append(current_lines)

        total = len(sub_chunks)
        if total <= 1:
            return [self._make_chunk(sym, file_hash, sym.source)]

        chunks: list[CodeChunk] = []
        line_offset = sym.start_line

        for idx, chunk_lines in enumerate(sub_chunks):
            chunk_source = "".join(chunk_lines)
            chunk_tokens = count_tokens(chunk_source, self._encoding_name)
            chunk_start = line_offset
            chunk_end = line_offset + len(chunk_lines) - 1

            chunks.append(
                CodeChunk(
                    file_path=sym.file_path,
                    language=sym.language,
                    chunk_type=sym.chunk_type,
                    name=f"{sym.name} [part {idx + 1}/{total}]",
                    start_line=chunk_start,
                    end_line=chunk_end,
                    parent=sym.parent,
                    file_hash=file_hash,
                    source=chunk_source,
                    token_count=chunk_tokens,
                    metadata={"sub_index": idx + 1, "sub_total": total},
                )
            )
            line_offset = chunk_end + 1

        return chunks

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    def _make_chunk(
        self,
        sym: ParsedSymbol,
        file_hash: str,
        source: str,
    ) -> CodeChunk:
        """从 ParsedSymbol 快速构造 CodeChunk。"""
        return CodeChunk(
            file_path=sym.file_path,
            language=sym.language,
            chunk_type=sym.chunk_type,
            name=sym.name,
            start_line=sym.start_line,
            end_line=sym.end_line,
            parent=sym.parent,
            file_hash=file_hash,
            source=source,
            token_count=count_tokens(source, self._encoding_name),
        )

    @staticmethod
    def _find_split_point(lines: list[str]) -> int:
        """在行列表中寻找最佳切分点。

        从尾部向前搜索，优先切在空行或语句结束行之后。

        Args:
            lines: 行列表。

        Returns:
            切分点索引（从此处开始为下一个 chunk）。
            如果找不到更好的切分点，返回 ``len(lines)``。
        """
        # 从尾部 30% 范围内搜索
        search_start = max(0, len(lines) * 7 // 10)

        # 优先：空行
        for i in range(len(lines) - 1, search_start, -1):
            if not lines[i].strip():
                return i

        # 其次：语句结束行
        for i in range(len(lines) - 1, search_start, -1):
            stripped = lines[i].rstrip()
            if stripped and stripped[-1] in (";", "}", ")"):
                return i + 1

        return len(lines)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _is_docstring_start(stripped: str) -> bool:
    """判断行是否以 docstring 起始标记开头。"""
    return stripped.startswith(('"""', "'''", 'r"""', "r'''"))


def _is_docstring_end(stripped: str) -> bool:
    """判断行是否包含 docstring 结束标记。"""
    for marker in ('"""', "'''"):
        count = stripped.count(marker)
        # 包含结束标记：出现次数为奇数（新开始 + 结束）或恰好一次（结束）
        if count >= 1:
            return True
    return False


def _is_method_def(stripped: str) -> bool:
    """判断行是否为方法/函数定义。"""
    return bool(
        re.match(
            r"^(def\s|function\s|func\s|fn\s|pub\s+fn\s|private\s+fn\s"
            r"|public\s|private\s|protected\s|static\s).*[\(:{]\s*$",
            stripped,
        )
    ) or bool(
        re.match(
            r"^(def\s|function\s|func\s|fn\s)\w+.*:\s*$",
            stripped,
        )
    )


def _indent_width(line: str) -> int:
    """计算行首缩进宽度，tab 按 4 个空格近似处理。"""
    width = 0
    for char in line:
        if char == " ":
            width += 1
        elif char == "\t":
            width += 4
        else:
            break
    return width


def _is_indented(stripped: str) -> bool:
    """判断行是否有缩进（属于方法/函数体）。"""
    return bool(stripped) and stripped[0] in (" ", "\t")
