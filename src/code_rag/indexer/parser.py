"""tree-sitter 多语言 AST 解析器。

根据 :mod:`indexer.scanner` 检测出的编程语言，加载对应的 tree-sitter
grammar 并解析源文件，提取函数、类、方法等语义符号。

支持的语言（与 scanner 保持一致）：
Python, JavaScript, TypeScript, TSX, Java, Go, C, C++, Rust,
Ruby, PHP, C#, Swift, Lua, Shell。
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass
from pathlib import Path

from tree_sitter import Language, Node, Parser

from code_rag.indexer.scanner import LANGUAGES, LanguageSpec

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 解析结果数据类
# ---------------------------------------------------------------------------


@dataclass
class ParsedSymbol:
    """AST 解析后提取的单个语义符号。"""

    file_path: str
    """相对于仓库根目录的路径。"""
    language: str
    """编程语言名称。"""
    chunk_type: str
    """切片类型：'function' / 'class' / 'module_summary'。"""
    name: str
    """符号名称（函数名 / 类名 / 文件名）。"""
    start_line: int
    """起始行号（1-indexed）。"""
    end_line: int
    """结束行号（1-indexed）。"""
    parent: str | None
    """所属类名（仅方法级别符号有值）。"""
    source: str
    """完整的源代码文本。"""


# ---------------------------------------------------------------------------
# 语言节点类型配置
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NodeConfig:
    """描述 tree-sitter AST 节点到语义符号的映射关系。

    Attributes:
        node_type: tree-sitter 节点类型名，如 ``'function_definition'``。
        kind: 映射到的切片类型，``'function'`` 或 ``'class'``。
        name_fields: 按优先级排列的字段名列表，用于提取符号名称。
            parser 会依次尝试 ``node.child_by_field_name(f)``。
        name_child_types: 当 name_fields 均未命中时，
            按子节点类型匹配提取名称的后备列表。
        descend: 是否递归进入该节点的子树继续提取更细粒度的符号。
            典型用法：进入 class 节点提取其方法。
    """

    node_type: str
    kind: str
    name_fields: tuple[str, ...] = ()
    name_child_types: tuple[str, ...] = ()
    descend: bool = False


# ---------------------------------------------------------------------------
# 各语言的节点配置
# ---------------------------------------------------------------------------

_PYTHON_NODES: list[NodeConfig] = [
    NodeConfig(
        "function_definition", "function", name_fields=("name",), name_child_types=("identifier",)
    ),
    NodeConfig(
        "class_definition",
        "class",
        name_fields=("name",),
        name_child_types=("identifier",),
        descend=True,
    ),
]

_JS_TS_NODES: list[NodeConfig] = [
    NodeConfig(
        "function_declaration", "function", name_fields=("name",), name_child_types=("identifier",)
    ),
    NodeConfig(
        "class_declaration",
        "class",
        name_fields=("name",),
        name_child_types=("identifier",),
        descend=True,
    ),
    NodeConfig(
        "class", "class", name_fields=("name",), name_child_types=("identifier",), descend=True
    ),
    NodeConfig(
        "method_definition",
        "function",
        name_fields=("name",),
        name_child_types=("property_identifier", "identifier"),
    ),
]

_JAVA_NODES: list[NodeConfig] = [
    NodeConfig(
        "class_declaration",
        "class",
        name_fields=("name",),
        name_child_types=("identifier",),
        descend=True,
    ),
    NodeConfig(
        "interface_declaration",
        "class",
        name_fields=("name",),
        name_child_types=("identifier",),
        descend=True,
    ),
    NodeConfig(
        "method_declaration", "function", name_fields=("name",), name_child_types=("identifier",)
    ),
    NodeConfig(
        "constructor_declaration",
        "function",
        name_fields=("name",),
        name_child_types=("identifier",),
    ),
]

_GO_NODES: list[NodeConfig] = [
    NodeConfig(
        "function_declaration", "function", name_fields=("name",), name_child_types=("identifier",)
    ),
    NodeConfig(
        "method_declaration",
        "function",
        name_fields=("name",),
        name_child_types=("field_identifier",),
    ),
    NodeConfig(
        "type_declaration", "class", name_fields=("name",), name_child_types=("type_identifier",)
    ),
]

_C_NODES: list[NodeConfig] = [
    NodeConfig("function_definition", "function", name_child_types=("identifier",)),
    NodeConfig("type_definition", "class", name_child_types=("type_identifier",)),
    NodeConfig("struct_specifier", "class", name_child_types=("type_identifier",)),
]

_CPP_NODES: list[NodeConfig] = [
    NodeConfig("function_definition", "function", name_child_types=("identifier",)),
    NodeConfig("declaration", "function", name_child_types=("identifier",)),
    NodeConfig("class_specifier", "class", name_child_types=("type_identifier",), descend=True),
    NodeConfig("struct_specifier", "class", name_child_types=("type_identifier",), descend=True),
]

_RUST_NODES: list[NodeConfig] = [
    NodeConfig(
        "function_item", "function", name_fields=("name",), name_child_types=("identifier",)
    ),
    NodeConfig(
        "struct_item", "class", name_fields=("name",), name_child_types=("type_identifier",)
    ),
    NodeConfig("enum_item", "class", name_fields=("name",), name_child_types=("type_identifier",)),
    NodeConfig(
        "impl_item",
        "class",
        name_fields=("type",),
        name_child_types=("type_identifier",),
        descend=True,
    ),
    NodeConfig(
        "trait_item",
        "class",
        name_fields=("name",),
        name_child_types=("type_identifier",),
        descend=True,
    ),
]

_RUBY_NODES: list[NodeConfig] = [
    NodeConfig("method", "function", name_fields=("name",), name_child_types=("identifier",)),
    NodeConfig(
        "singleton_method", "function", name_fields=("name",), name_child_types=("identifier",)
    ),
    NodeConfig(
        "class", "class", name_fields=("name",), name_child_types=("constant",), descend=True
    ),
    NodeConfig(
        "module", "class", name_fields=("name",), name_child_types=("constant",), descend=True
    ),
]

_PHP_NODES: list[NodeConfig] = [
    NodeConfig(
        "function_definition", "function", name_fields=("name",), name_child_types=("name",)
    ),
    NodeConfig("method_declaration", "function", name_fields=("name",), name_child_types=("name",)),
    NodeConfig(
        "class_declaration",
        "class",
        name_fields=("name",),
        name_child_types=("name",),
        descend=True,
    ),
]

_CSHARP_NODES: list[NodeConfig] = [
    NodeConfig(
        "class_declaration",
        "class",
        name_fields=("name",),
        name_child_types=("identifier",),
        descend=True,
    ),
    NodeConfig(
        "interface_declaration",
        "class",
        name_fields=("name",),
        name_child_types=("identifier",),
        descend=True,
    ),
    NodeConfig(
        "struct_declaration",
        "class",
        name_fields=("name",),
        name_child_types=("identifier",),
        descend=True,
    ),
    NodeConfig(
        "method_declaration", "function", name_fields=("name",), name_child_types=("identifier",)
    ),
    NodeConfig(
        "constructor_declaration",
        "function",
        name_fields=("name",),
        name_child_types=("identifier",),
    ),
]

_SWIFT_NODES: list[NodeConfig] = [
    NodeConfig(
        "function_declaration",
        "function",
        name_fields=("name",),
        name_child_types=("simple_identifier",),
    ),
    NodeConfig(
        "class_declaration",
        "class",
        name_fields=("name",),
        name_child_types=("type_identifier",),
        descend=True,
    ),
    NodeConfig(
        "struct_declaration",
        "class",
        name_fields=("name",),
        name_child_types=("type_identifier",),
        descend=True,
    ),
]

_LUA_NODES: list[NodeConfig] = [
    NodeConfig(
        "function_declaration", "function", name_fields=("name",), name_child_types=("identifier",)
    ),
    NodeConfig(
        "local_function", "function", name_fields=("name",), name_child_types=("identifier",)
    ),
    NodeConfig(
        "method_declaration",
        "function",
        name_fields=("name",),
        name_child_types=("identifier", "dot_index_expression"),
    ),
]

_SHELL_NODES: list[NodeConfig] = [
    NodeConfig(
        "function_definition", "function", name_fields=("name",), name_child_types=("word",)
    ),
]

_LANG_NODE_CONFIGS: dict[str, list[NodeConfig]] = {
    "python": _PYTHON_NODES,
    "javascript": _JS_TS_NODES,
    "typescript": _JS_TS_NODES,
    "tsx": _JS_TS_NODES,
    "java": _JAVA_NODES,
    "go": _GO_NODES,
    "c": _C_NODES,
    "cpp": _CPP_NODES,
    "rust": _RUST_NODES,
    "ruby": _RUBY_NODES,
    "php": _PHP_NODES,
    "c_sharp": _CSHARP_NODES,
    "swift": _SWIFT_NODES,
    "lua": _LUA_NODES,
    "shell": _SHELL_NODES,
}


# ---------------------------------------------------------------------------
# Parser 缓存
# ---------------------------------------------------------------------------

_PARSER_CACHE: dict[str, Parser] = {}


def _get_parser(language: str) -> Parser:
    """获取或创建指定语言的 tree-sitter Parser（带缓存）。

    Args:
        language: 语言名称，如 ``'python'``。

    Returns:
        配置好的 :class:`tree_sitter.Parser` 实例。

    Raises:
        ValueError: 当语言未注册或对应模块加载失败时。
    """
    if language in _PARSER_CACHE:
        return _PARSER_CACHE[language]

    spec: LanguageSpec | None = None
    for s in LANGUAGES.values():
        if s.name == language:
            spec = s
            break

    if spec is None:
        raise ValueError(f"未注册的语言: {language}")

    try:
        mod = importlib.import_module(spec.module_name)
        lang_func = getattr(mod, spec.language_func)
        lang = Language(lang_func())
        parser = Parser(lang)
    except (ImportError, AttributeError, TypeError) as exc:
        raise ValueError(f"无法加载语言 '{language}' 的 tree-sitter grammar: {exc}") from exc

    _PARSER_CACHE[language] = parser
    logger.debug("已缓存 tree-sitter parser: %s (模块 %s)", language, spec.module_name)
    return parser


def _reset_cache() -> None:
    """清空 parser 缓存（用于测试）。"""
    _PARSER_CACHE.clear()


# ---------------------------------------------------------------------------
# 名称提取辅助
# ---------------------------------------------------------------------------


def _extract_node_name(node: Node, config: NodeConfig) -> str:
    """从 AST 节点中提取符号名称。

    按优先级依次尝试 ``name_fields`` 和 ``name_child_types``。

    Args:
        node: tree-sitter AST 节点。
        config: 该节点类型对应的 :class:`NodeConfig`。

    Returns:
        提取到的名称字符串；若无法提取则返回 ``"<unknown>"``。
    """
    # 优先通过 field name 获取
    for field_name in config.name_fields:
        child = node.child_by_field_name(field_name)
        if child is not None:
            return child.text.decode("utf-8", errors="replace")

    # 后备：在子树中搜索匹配类型的节点（最多深度 3 层）
    for child_type in config.name_child_types:
        name = _find_child_by_type(node, child_type, max_depth=3)
        if name is not None:
            return name

    return "<unknown>"


def _find_child_by_type(node: Node, target_type: str, *, max_depth: int) -> str | None:
    """在节点子树中递归查找指定类型的第一个命名子节点。

    Args:
        node: 起始 tree-sitter 节点。
        target_type: 要查找的节点类型。
        max_depth: 最大递归深度。

    Returns:
        找到的节点的文本内容，未找到返回 ``None``。
    """
    if max_depth <= 0:
        return None
    for child in node.children:
        if child.type == target_type and child.is_named:
            return child.text.decode("utf-8", errors="replace")
    # 递归：对非叶子中间节点继续向下搜索
    for child in node.children:
        if child.is_named and child.child_count > 0:
            result = _find_child_by_type(child, target_type, max_depth=max_depth - 1)
            if result is not None:
                return result
    return None


# ---------------------------------------------------------------------------
# 核心解析器
# ---------------------------------------------------------------------------


class CodeParser:
    """tree-sitter 多语言代码解析器。

    负责将源代码文件解析为 AST，并提取函数、类等语义符号。

    用法::

        parser = CodeParser()
        symbols = parser.parse_file(Path("src/main.py"), "python", "src/main.py")
        for sym in symbols:
            print(sym.name, sym.chunk_type, sym.start_line)
    """

    def __init__(self) -> None:
        """初始化解析器。"""
        self._node_configs = _LANG_NODE_CONFIGS

    def parse_file(
        self,
        abs_path: Path,
        language: str,
        rel_path: str,
    ) -> list[ParsedSymbol]:
        """解析单个源文件并提取所有语义符号。

        Args:
            abs_path: 文件的绝对路径。
            language: 编程语言名称（如 ``'python'``）。
            rel_path: 相对于仓库根目录的路径。

        Returns:
            :class:`ParsedSymbol` 列表，按出现顺序排列。
            包含一个 ``module_summary`` 类型的条目和所有提取到的函数 / 类条目。
        """
        configs = self._node_configs.get(language)
        if configs is None:
            logger.warning("语言 '%s' 无节点配置，跳过解析: %s", language, rel_path)
            return []

        # 构建 node_type → config 的查找表
        config_map: dict[str, NodeConfig] = {}
        for cfg in configs:
            config_map[cfg.node_type] = cfg

        # 读取文件并解析
        try:
            source = abs_path.read_bytes()
        except OSError:
            logger.warning("无法读取文件: %s", rel_path)
            return []

        if not source.strip():
            return []

        try:
            parser = _get_parser(language)
            tree = parser.parse(source)
        except ValueError as exc:
            logger.warning("解析失败 (%s): %s — %s", language, rel_path, exc)
            return []

        root = tree.root_node
        source_text = source.decode("utf-8", errors="replace")
        symbols: list[ParsedSymbol] = []

        # 递归遍历 AST 提取符号
        self._walk_node(
            node=root,
            config_map=config_map,
            source_text=source_text,
            rel_path=rel_path,
            language=language,
            parent_name=None,
            symbols=symbols,
        )

        # 如果没有提取到任何符号，至少生成一个 module_summary
        if not symbols:
            total_lines = source_text.count("\n") + 1
            symbols.append(
                ParsedSymbol(
                    file_path=rel_path,
                    language=language,
                    chunk_type="module_summary",
                    name=Path(rel_path).name,
                    start_line=1,
                    end_line=total_lines,
                    parent=None,
                    source=source_text,
                )
            )

        return symbols

    def _walk_node(
        self,
        node: Node,
        config_map: dict[str, NodeConfig],
        source_text: str,
        rel_path: str,
        language: str,
        parent_name: str | None,
        symbols: list[ParsedSymbol],
    ) -> None:
        """递归遍历 AST 节点，提取匹配的符号。

        Args:
            node: 当前遍历的 tree-sitter 节点。
            config_map: 节点类型 → NodeConfig 的查找表。
            source_text: 完整的源代码文本。
            rel_path: 文件相对路径。
            language: 语言名称。
            parent_name: 当前作用域的父符号名称（用于方法归属）。
            symbols: 输出列表，将符号追加到此列表中。
        """
        config = config_map.get(node.type)

        if config is not None and node.is_named:
            name = _extract_node_name(node, config)
            start_line = node.start_point[0] + 1  # 转为 1-indexed
            end_line = node.end_point[0] + 1

            # 截取节点对应的源码
            node_source = source_text[node.start_byte : node.end_byte]

            symbols.append(
                ParsedSymbol(
                    file_path=rel_path,
                    language=language,
                    chunk_type=config.kind,
                    name=name,
                    start_line=start_line,
                    end_line=end_line,
                    parent=parent_name,
                    source=node_source,
                )
            )

            # 如果需要 descend（如 class → 方法），递归进入子树
            if config.descend:
                for child in node.children:
                    self._walk_node(
                        node=child,
                        config_map=config_map,
                        source_text=source_text,
                        rel_path=rel_path,
                        language=language,
                        parent_name=name,
                        symbols=symbols,
                    )
            # 对于 class 节点，不继续向上遍历其子节点（已在 descend 中处理）
            return

        # 未命中配置的节点：继续遍历子节点
        for child in node.children:
            self._walk_node(
                node=child,
                config_map=config_map,
                source_text=source_text,
                rel_path=rel_path,
                language=language,
                parent_name=parent_name,
                symbols=symbols,
            )

    def parse_text(
        self,
        source: str,
        language: str,
        rel_path: str = "<string>",
    ) -> list[ParsedSymbol]:
        """解析给定的源代码文本并提取语义符号。

        主要用于测试和临时分析。

        Args:
            source: 源代码文本。
            language: 编程语言名称。
            rel_path: 标识用的文件路径（默认 ``'<string>'``）。

        Returns:
            :class:`ParsedSymbol` 列表。
        """
        configs = self._node_configs.get(language)
        if configs is None:
            logger.warning("语言 '%s' 无节点配置", language)
            return []

        config_map: dict[str, NodeConfig] = {}
        for cfg in configs:
            config_map[cfg.node_type] = cfg

        if not source.strip():
            return []

        try:
            parser = _get_parser(language)
            tree = parser.parse(source.encode("utf-8"))
        except ValueError as exc:
            logger.warning("解析失败 (%s): %s", language, exc)
            return []

        root = tree.root_node
        symbols: list[ParsedSymbol] = []

        self._walk_node(
            node=root,
            config_map=config_map,
            source_text=source,
            rel_path=rel_path,
            language=language,
            parent_name=None,
            symbols=symbols,
        )

        if not symbols:
            total_lines = source.count("\n") + 1
            symbols.append(
                ParsedSymbol(
                    file_path=rel_path,
                    language=language,
                    chunk_type="module_summary",
                    name=Path(rel_path).name if rel_path != "<string>" else "<string>",
                    start_line=1,
                    end_line=total_lines,
                    parent=None,
                    source=source,
                )
            )

        return symbols
