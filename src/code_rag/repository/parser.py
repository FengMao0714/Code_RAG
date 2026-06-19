"""仓库输入解析器。

把用户输入的字符串解析成 :class:`RepoSource`：

- ``.``、``/abs/path``、``E:\\code\\xxx`` 视为本地路径。
- ``https://github.com/owner/repo[.git]`` / ``git@github.com:owner/repo.git`` 视为 git URL。
- 其他输入抛出 :class:`InvalidRepoSourceError`。
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from code_rag.repository.models import (
    SOURCE_TYPE_GIT,
    SOURCE_TYPE_LOCAL,
    RepoSource,
)

# Windows 盘符路径，例如 ``C:\path``、``D:/path``、``E:\code\Code_RAG``。
_WINDOWS_DRIVE_RE = re.compile(r"^[a-zA-Z]:[\\/]")
# 默认允许的安全 scheme：https 和 ssh（含 git+ssh）。
_SAFE_GIT_SCHEMES = {"https", "ssh", "git+ssh"}
# 仅当显式允许时才接受 file://。
_FILE_SCHEME = {"file"}

# SSH 形式 ``git@host:owner/repo.git``，也支持 ``user@host:path``。
_SSH_RE = re.compile(r"^([a-zA-Z0-9_.-]+)@([a-zA-Z0-9_.-]+):(.*)$")


class InvalidRepoSourceError(ValueError):
    """用户输入既不是合法本地路径，也不是合法 git URL。"""


def parse_repo_source(raw: str, *, allow_file: bool = False) -> RepoSource:
    """解析用户输入为 :class:`RepoSource`。

    Args:
        raw: 原始用户输入字符串。
        allow_file: 是否允许 ``file://`` 协议（默认拒绝，仅测试使用）。

    Returns:
        解析后的 :class:`RepoSource`。

    Raises:
        InvalidRepoSourceError: 输入既不是本地路径也不是合法 git URL。
    """
    if raw is None:
        raise InvalidRepoSourceError("仓库来源不能为空")
    text = raw.strip()
    if not text:
        raise InvalidRepoSourceError("仓库来源不能为空")

    # Windows 盘符路径会被 urlparse 误判为 scheme，先保护本地路径。
    if _WINDOWS_DRIVE_RE.match(text):
        return RepoSource(raw=text, kind=SOURCE_TYPE_LOCAL, ref=None)

    # 明确的 URL scheme 必须显式允许，避免不安全 URL 落入 local 分支。
    if "://" in text:
        parsed = urlparse(text)
        scheme = parsed.scheme.lower()
        if scheme not in _SAFE_GIT_SCHEMES and not (allow_file and scheme in _FILE_SCHEME):
            raise InvalidRepoSourceError(
                f"不支持的 URL 协议: {scheme}；"
                "仅支持 https://、ssh://、git+ssh://，file:// 需显式启用"
            )

    # 1. 先尝试识别为 git URL
    if _looks_like_git_url(text, allow_file=allow_file):
        return RepoSource(raw=text, kind=SOURCE_TYPE_GIT)

    # 2. 否则按本地路径处理
    return RepoSource(raw=text, kind=SOURCE_TYPE_LOCAL, ref=None)


def _looks_like_git_url(text: str, *, allow_file: bool = False) -> bool:
    """判断字符串是否为 git URL。

    支持的格式：

    - ``https://github.com/owner/repo[.git]``
    - ``ssh://git@github.com/owner/repo.git``
    - ``git@github.com:owner/repo.git``（scp-like）
    - ``file:///path/to/repo.git``（仅当 *allow_file=True* 时允许）

    默认拒绝 ``http://``、``ftp://``、``git://`` 等不安全 scheme。
    """
    # SCP-like SSH 形式：先看是否包含 ``@host:`` 形式且无 ``://``
    if "://" not in text and (text.startswith("git@") or text.startswith("ssh@")):
        return True
    if "://" not in text and _SSH_RE.match(text):
        # 形如 ``user@host:path`` 且包含 ``:`` 但没有 ``://``，可能是 scp-like
        return True

    # 否则尝试 URL 解析
    parsed = urlparse(text)
    scheme = parsed.scheme.lower() if parsed.scheme else ""
    if scheme in _SAFE_GIT_SCHEMES:
        return True
    if allow_file and scheme in _FILE_SCHEME:
        return True

    return False
