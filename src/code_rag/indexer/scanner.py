"""仓库文件扫描、.gitignore 过滤、语言检测。

负责遍历目标仓库目录，按 .gitignore 规则过滤文件，
根据文件扩展名检测编程语言，并计算文件 SHA-256 哈希。
"""

from __future__ import annotations

import fnmatch
import hashlib
import logging
import os as _os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 语言注册表
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LanguageSpec:
    """一门编程语言的元信息，用于 scanner 与 parser 之间的桥接。"""

    name: str
    """人类可读语言名，如 'python'、'javascript'。"""
    extensions: tuple[str, ...]
    """关联的文件扩展名（含点号），如 ('.py',)。"""
    module_name: str
    """tree-sitter 语言绑定的 Python 模块名，如 'tree_sitter_python'。"""
    language_func: str
    """模块内返回 Language capsule 的函数名，通常为 'language'。"""


LANGUAGES: dict[str, LanguageSpec] = {}


def _register(*specs: LanguageSpec) -> None:
    """将 LanguageSpec 注册到全局字典，key 为小写扩展名。"""
    for spec in specs:
        for ext in spec.extensions:
            LANGUAGES[ext.lower()] = spec


# --- 注册所有支持的语言 ---

_register(
    LanguageSpec("python", (".py",), "tree_sitter_python", "language"),
    LanguageSpec("javascript", (".js", ".mjs", ".cjs"), "tree_sitter_javascript", "language"),
    LanguageSpec("typescript", (".ts",), "tree_sitter_typescript", "language_typescript"),
    LanguageSpec("tsx", (".tsx",), "tree_sitter_typescript", "language_tsx"),
    LanguageSpec("java", (".java",), "tree_sitter_java", "language"),
    LanguageSpec("go", (".go",), "tree_sitter_go", "language"),
    LanguageSpec("c", (".c", ".h"), "tree_sitter_c", "language"),
    LanguageSpec(
        "cpp",
        (".cpp", ".cxx", ".cc", ".hpp", ".hxx", ".hh"),
        "tree_sitter_cpp",
        "language",
    ),
    LanguageSpec("rust", (".rs",), "tree_sitter_rust", "language"),
    LanguageSpec("ruby", (".rb",), "tree_sitter_ruby", "language"),
    LanguageSpec("php", (".php",), "tree_sitter_php", "language_php"),
    LanguageSpec("c_sharp", (".cs",), "tree_sitter_c_sharp", "language"),
    LanguageSpec("swift", (".swift",), "tree_sitter_swift", "language"),
    LanguageSpec("lua", (".lua",), "tree_sitter_lua", "language"),
    LanguageSpec("shell", (".sh", ".bash", ".zsh"), "tree_sitter_bash", "language"),
)

# 文档类扩展（不经过 AST 解析，作为 doc chunk 直接入库）
DOC_EXTENSIONS: frozenset[str] = frozenset(
    {
        # 纯文档
        ".md",
        ".rst",
        ".txt",
        ".adoc",
        # 项目配置文件
        ".toml",
        ".yaml",
        ".yml",
        ".json",
    }
)

# 默认忽略的目录名（即使没有 .gitignore 也应跳过）
_DEFAULT_IGNORE_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        "node_modules",
        ".venv",
        "venv",
        "env",
        ".env",
        "dist",
        "build",
        ".next",
        "coverage",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".idea",
        ".vscode",
        ".code-rag",
        ".chroma",
        ".indexes",
        "chroma_data",
        "target",  # Rust / Java
        "vendor",  # Go / PHP
    }
)

# 默认忽略的文件名
_DEFAULT_IGNORE_FILENAMES: frozenset[str] = frozenset(
    {
        ".env",
        ".env.local",
        ".env.production",
        ".env.development",
        ".DS_Store",
        "Thumbs.db",
    }
)

# 默认忽略的文件扩展名（二进制 / 图片 / 视频 / 压缩包等）
_DEFAULT_IGNORE_EXTENSIONS: frozenset[str] = frozenset(
    {
        # 二进制 / 编译产物
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".o",
        ".a",
        ".lib",
        ".pyd",
        ".pyc",
        ".pyo",
        ".class",
        ".jar",
        ".war",
        # 图片
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".ico",
        ".svg",
        ".webp",
        # 视频 / 音频
        ".mp4",
        ".avi",
        ".mov",
        ".mp3",
        ".wav",
        ".flac",
        # 压缩包
        ".zip",
        ".tar",
        ".gz",
        ".bz2",
        ".xz",
        ".7z",
        ".rar",
        # 字体
        ".ttf",
        ".otf",
        ".woff",
        ".woff2",
        ".eot",
        # 锁文件
        ".lock",
    }
)

# 单文件大小上限（字节）— 默认 1 MB
MAX_FILE_SIZE: int = 1 * 1024 * 1024

# ---------------------------------------------------------------------------
# .gitignore 解析器
# ---------------------------------------------------------------------------


class GitignoreFilter:
    """轻量级 .gitignore 过滤器。

    支持 gitignore 的核心语法：
    - 空行和 ``#`` 注释
    - 以 ``/`` 结尾的模式仅匹配目录
    - ``*`` 通配符（单路径段）
    - ``**`` 通配符（跨目录）
    - 以 ``!`` 开头的否定模式
    - 模式无前导 ``/`` 时匹配任意层级
    - 模式有前导 ``/`` 时仅从 .gitignore 所在目录匹配

    不依赖第三方库，仅使用标准库实现。
    """

    def __init__(self) -> None:
        self._rules: list[tuple[str, bool]] = []
        """(pattern, negated) 列表，按文件中出现的顺序排列。"""

    def add_gitignore(self, gitignore_path: Path) -> None:
        """解析一个 .gitignore 文件并追加规则。

        Args:
            gitignore_path: .gitignore 文件的绝对路径。
        """
        try:
            text = gitignore_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            negated = False
            if line.startswith("!"):
                negated = True
                line = line[1:]
            self._rules.append((line, negated))

    def is_ignored(self, rel_path: str, is_dir: bool = False) -> bool:
        """判断 ``rel_path`` 是否应被忽略。

        Args:
            rel_path: 相对于仓库根目录的路径（用 ``/`` 分隔）。
            is_dir: 是否为目录。

        Returns:
            ``True`` 表示该路径应被 **排除**。
        """
        matched = False
        for raw_pattern, negated in self._rules:
            # 匹配时使用去除 ! 前缀的纯模式
            pattern = raw_pattern[1:] if raw_pattern.startswith("!") else raw_pattern
            if _match_gitignore_pattern(pattern, rel_path, is_dir):
                matched = not negated
        return matched


def _match_gitignore_pattern(pattern: str, rel_path: str, is_dir: bool) -> bool:
    """将单条 gitignore 模式与相对路径进行匹配。"""
    dir_only = pattern.endswith("/")
    if dir_only:
        pattern = pattern[:-1]

    if dir_only and not is_dir:
        return False

    # 检测是否锚定：前导 / 或模式本身包含 /（如 foo/bar）
    anchored = pattern.startswith("/") or "/" in pattern
    if pattern.startswith("/"):
        pattern = pattern[1:]

    path_parts = rel_path.replace("\\", "/").split("/")
    pattern_parts = pattern.replace("\\", "/").split("/")

    if anchored:
        # 锚定模式从根目录匹配；允许前缀匹配（目录内容）
        return _fnmatch_segments(path_parts, pattern_parts, allow_prefix=True)

    # 非锚定：尝试匹配路径的任意后缀段
    for i in range(len(path_parts)):
        if _fnmatch_segments(path_parts[i:], pattern_parts, allow_prefix=True):
            return True
    return False


def _fnmatch_segments(
    path_parts: list[str],
    pattern_parts: list[str],
    *,
    allow_prefix: bool = False,
) -> bool:
    """逐段匹配路径和模式，支持 ``**`` 通配符。

    使用双指针 + 回溯算法，与 git 的匹配行为一致。

    Args:
        path_parts: 路径的各段。
        pattern_parts: 模式的各段。
        allow_prefix: 当 ``True`` 时，模式匹配路径的前缀也算匹配
            （用于非锚定模式匹配目录内容）。
    """
    pi, pj = 0, 0
    star_pi, star_pj = -1, -1

    while pi < len(path_parts) and pj < len(pattern_parts):
        if pattern_parts[pj] == "**":
            star_pi = pi
            star_pj = pj
            pj += 1
        elif fnmatch.fnmatch(path_parts[pi], pattern_parts[pj]):
            pi += 1
            pj += 1
        elif star_pj >= 0:
            star_pi += 1
            pi = star_pi
            pj = star_pj + 1
        else:
            return False

    # 处理尾部的 ** 通配符
    while pj < len(pattern_parts) and pattern_parts[pj] == "**":
        pj += 1

    # 完全匹配
    if pj == len(pattern_parts):
        return True

    # 允许前缀匹配：模式已完全消耗，且路径还有剩余段（模式匹配了某个目录）
    if allow_prefix and pj == len(pattern_parts):
        return True

    return False


# ---------------------------------------------------------------------------
# 扫描结果数据类
# ---------------------------------------------------------------------------


@dataclass
class FileEntry:
    """扫描阶段产出的单个文件条目。"""

    abs_path: Path
    """文件的绝对路径。"""
    rel_path: str
    """相对于仓库根目录的路径（用 ``/`` 分隔）。"""
    language: str | None
    """检测到的编程语言名称；文档文件为 ``'doc'``；不支持则为 ``None``。"""
    extension: str
    """小写文件扩展名（含点号）。"""
    size: int
    """文件大小（字节）。"""
    file_hash: str
    """文件内容的 SHA-256 哈希（十六进制）。"""

    @property
    def is_code(self) -> bool:
        """是否为可解析的源代码文件。"""
        return self.language is not None and self.language != "doc"

    @property
    def is_doc(self) -> bool:
        """是否为文档文件。"""
        return self.language == "doc"


# ---------------------------------------------------------------------------
# 仓库扫描器
# ---------------------------------------------------------------------------


class RepoScanner:
    """扫描仓库目录，产出所有待索引的文件。

    工作流程：

    1. 递归遍历仓库目录
    2. 加载并应用各级 .gitignore 规则
    3. 跳过默认忽略的目录 / 文件 / 扩展名
    4. 对剩余文件检测语言、计算大小和 SHA-256 哈希
    5. 输出 :class:`FileEntry` 列表

    用法::

        scanner = RepoScanner("/path/to/repo")
        entries = scanner.scan()
        for entry in entries:
            print(entry.rel_path, entry.language)
    """

    def __init__(self, repo_path: str | Path, *, max_file_size: int = MAX_FILE_SIZE) -> None:
        """初始化扫描器。

        Args:
            repo_path: 仓库根目录的路径。
            max_file_size: 单文件大小上限（字节），超过此大小的文件将被跳过。

        Raises:
            ValueError: 当路径不存在或不是目录时。
        """
        self._repo_path = Path(repo_path).resolve()
        self._max_file_size = max_file_size

        if not self._repo_path.is_dir():
            raise ValueError(f"仓库路径不存在或不是目录: {self._repo_path}")

    @property
    def repo_path(self) -> Path:
        """仓库根目录的绝对路径。"""
        return self._repo_path

    def scan(self) -> list[FileEntry]:
        """扫描仓库并返回所有待索引的文件条目。

        Returns:
            按相对路径排序的 :class:`FileEntry` 列表。
        """
        entries: list[FileEntry] = []
        gitignore_filter = GitignoreFilter()

        # 加载根目录 .gitignore
        root_gitignore = self._repo_path / ".gitignore"
        if root_gitignore.is_file():
            gitignore_filter.add_gitignore(root_gitignore)
            logger.debug("已加载根 .gitignore: %s", root_gitignore)

        skipped: dict[str, int] = {
            "gitignore": 0,
            "default_dir": 0,
            "default_file": 0,
            "default_ext": 0,
            "unsupported_ext": 0,
            "size_limit": 0,
            "unreadable": 0,
        }

        for dirpath, dirnames, filenames in _os.walk(self._repo_path):
            current_dir = Path(dirpath)

            # --- 过滤子目录 ---
            filtered_dirs: list[str] = []
            for d in dirnames:
                abs_d = current_dir / d
                rel_d = abs_d.relative_to(self._repo_path).as_posix()

                if d in _DEFAULT_IGNORE_DIRS:
                    skipped["default_dir"] += 1
                    continue
                if gitignore_filter.is_ignored(rel_d, is_dir=True):
                    skipped["gitignore"] += 1
                    continue
                filtered_dirs.append(d)

                # 在子目录中加载额外的 .gitignore
                child_gitignore = abs_d / ".gitignore"
                if child_gitignore.is_file():
                    gitignore_filter.add_gitignore(child_gitignore)
                    logger.debug("已加载子目录 .gitignore: %s", child_gitignore)

            # 原地修改 dirnames 以控制 os.walk 的递归行为
            dirnames[:] = filtered_dirs

            # --- 过滤文件 ---
            for fname in filenames:
                abs_f = current_dir / fname
                rel_f = abs_f.relative_to(self._repo_path).as_posix()

                # 跳过默认忽略的文件名
                if fname in _DEFAULT_IGNORE_FILENAMES:
                    skipped["default_file"] += 1
                    continue

                # 跳过默认忽略的扩展名
                ext = _get_extension(fname).lower()
                if ext in _DEFAULT_IGNORE_EXTENSIONS:
                    skipped["default_ext"] += 1
                    continue

                # .gitignore 过滤
                if gitignore_filter.is_ignored(rel_f, is_dir=False):
                    skipped["gitignore"] += 1
                    continue

                # 文件大小检查
                try:
                    size = abs_f.stat().st_size
                except OSError:
                    skipped["unreadable"] += 1
                    continue
                if size > self._max_file_size:
                    skipped["size_limit"] += 1
                    logger.debug("跳过大文件 (%d bytes): %s", size, rel_f)
                    continue
                if size == 0:
                    continue

                # 语言检测
                language = detect_language(ext)
                if language is None:
                    skipped["unsupported_ext"] += 1
                    continue

                # 计算哈希
                try:
                    file_hash = _sha256_file(abs_f)
                except OSError:
                    skipped["unreadable"] += 1
                    logger.warning("无法读取文件，跳过: %s", rel_f)
                    continue

                entries.append(
                    FileEntry(
                        abs_path=abs_f,
                        rel_path=rel_f,
                        language=language,
                        extension=ext,
                        size=size,
                        file_hash=file_hash,
                    )
                )

        entries.sort(key=lambda e: e.rel_path)

        logger.info(
            "扫描完成：%d 个文件入选，跳过统计: %s",
            len(entries),
            skipped,
        )
        return entries


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def detect_language(extension: str) -> str | None:
    """根据文件扩展名检测编程语言。

    Args:
        extension: 小写文件扩展名（含点号），如 ``'.py'``。

    Returns:
        语言名称字符串，如 ``'python'``；
        对于文档扩展名返回 ``'doc'``；
        不支持的扩展名返回 ``None``。
    """
    ext = extension.lower()
    if ext in DOC_EXTENSIONS:
        return "doc"
    spec = LANGUAGES.get(ext)
    return spec.name if spec else None


def _get_extension(filename: str) -> str:
    """从文件名提取扩展名（含点号）。

    对于像 ``'.gitignore'`` 这类以点开头且无其他点的名称，
    返回完整名称（即 ``'.gitignore'``），视为隐藏文件扩展名。
    """
    idx = filename.rfind(".")
    if idx <= 0:
        return ""
    return filename[idx:]


def _sha256_file(path: Path) -> str:
    """计算文件的 SHA-256 哈希（十六进制字符串）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
