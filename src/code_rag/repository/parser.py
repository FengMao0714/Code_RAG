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
# 显式 git 协议：``git://``、``git+ssh://``、``ssh://``。
_EXPLICIT_GIT_SCHEMES = {"git", "git+ssh", "ssh"}
# 显式 http / https / file / ftp 一律走 URL 解析流程。
_URL_SCHEMES = {"http", "https", "file", "ftp"} | _EXPLICIT_GIT_SCHEMES

# SSH 形式 ``git@host:owner/repo.git``，也支持 ``user@host:path``。
_SSH_RE = re.compile(r"^([a-zA-Z0-9_.-]+)@([a-zA-Z0-9_.-]+):(.*)$")


class InvalidRepoSourceError(ValueError):
    """用户输入既不是合法本地路径，也不是合法 git URL。"""


def parse_repo_source(raw: str) -> RepoSource:
    """解析用户输入为 :class:`RepoSource`。

    Args:
        raw: 原始用户输入字符串。

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

    # 1. 先尝试识别为 git URL
    if _looks_like_git_url(text):
        return RepoSource(raw=text, kind=SOURCE_TYPE_GIT)

    # 2. 否则按本地路径处理
    return RepoSource(raw=text, kind=SOURCE_TYPE_LOCAL, ref=None)


def _looks_like_git_url(text: str) -> bool:
    """判断字符串是否为 git URL。

    支持的格式：

    - ``https://github.com/owner/repo[.git]``
    - ``http://...``
    - ``git://...``
    - ``ssh://...``
    - ``git@github.com:owner/repo.git``（scp-like）
    """
    # SCP-like SSH 形式：先看是否包含 ``@host:`` 形式且无 ``://``
    if "://" not in text and (text.startswith("git@") or text.startswith("ssh@")):
        return True
    if "://" not in text and _SSH_RE.match(text):
        # 形如 ``user@host:path`` 且包含 ``:`` 但没有 ``://``，可能是 scp-like
        return True

    # 否则尝试 URL 解析
    parsed = urlparse(text)
    if parsed.scheme and parsed.scheme.lower() in _URL_SCHEMES:
        return True

    return False
