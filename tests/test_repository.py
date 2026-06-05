"""仓库输入源抽象的单元测试。

覆盖：

- parser：本地路径 / HTTPS / SSH / .git / 非法 URL 分类。
- models：数据类的合法性校验。
- cache：缓存目录命名 + 稳定 key。
- local provider：本地路径解析。
- git provider：使用本地 bare repo 模拟远程 clone / fetch / refresh / ref 错误。
- resolver：统一入口按 kind 派发。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from code_rag.repository import (
    CacheManager,
    GitRepositoryProvider,
    InvalidRepoSourceError,
    LocalRepositoryProvider,
    RepoIdentity,
    RepoSource,
    cache_dir_name_for,
    canonicalize_git_url,
    collection_key_for_git,
    collection_key_for_local,
    parse_repo_source,
    resolve_repo,
)
from code_rag.repository.models import (
    SOURCE_TYPE_GIT,
    SOURCE_TYPE_LOCAL,
    ResolvedRepo,
)

# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


class TestParseRepoSource:
    def test_local_relative(self) -> None:
        src = parse_repo_source(".")
        assert src.kind == SOURCE_TYPE_LOCAL
        assert src.raw == "."

    def test_local_absolute_unix(self) -> None:
        src = parse_repo_source("/abs/path/to/repo")
        assert src.kind == SOURCE_TYPE_LOCAL

    def test_local_windows_path(self) -> None:
        src = parse_repo_source("E:\\code\\Code_RAG")
        assert src.kind == SOURCE_TYPE_LOCAL
        src = parse_repo_source("E:/code/Code_RAG")
        assert src.kind == SOURCE_TYPE_LOCAL

    def test_https_github(self) -> None:
        src = parse_repo_source("https://github.com/owner/repo")
        assert src.kind == SOURCE_TYPE_GIT

    def test_https_github_with_git(self) -> None:
        src = parse_repo_source("https://github.com/owner/repo.git")
        assert src.kind == SOURCE_TYPE_GIT

    def test_http_url(self) -> None:
        src = parse_repo_source("http://gitlab.example.com/owner/repo.git")
        assert src.kind == SOURCE_TYPE_GIT

    def test_scp_like_ssh(self) -> None:
        src = parse_repo_source("git@github.com:owner/repo.git")
        assert src.kind == SOURCE_TYPE_GIT

    def test_ssh_protocol(self) -> None:
        src = parse_repo_source("ssh://git@github.com/owner/repo.git")
        assert src.kind == SOURCE_TYPE_GIT

    def test_git_protocol(self) -> None:
        src = parse_repo_source("git://github.com/owner/repo.git")
        assert src.kind == SOURCE_TYPE_GIT

    def test_empty_raises(self) -> None:
        with pytest.raises(InvalidRepoSourceError):
            parse_repo_source("")
        with pytest.raises(InvalidRepoSourceError):
            parse_repo_source("   ")


# ---------------------------------------------------------------------------
# canonicalize_git_url
# ---------------------------------------------------------------------------


class TestCanonicalizeGitUrl:
    def test_https(self) -> None:
        assert (
            canonicalize_git_url("https://github.com/owner/repo.git")
            == "https://github.com/owner/repo.git"
        )

    def test_https_trailing_slash_normalized(self) -> None:
        assert (
            canonicalize_git_url("https://github.com/owner/repo.git/")
            == "https://github.com/owner/repo.git"
        )

    def test_scp_to_ssh(self) -> None:
        assert (
            canonicalize_git_url("git@github.com:owner/repo.git")
            == "ssh://git@github.com/owner/repo.git"
        )

    def test_host_lowercased(self) -> None:
        out = canonicalize_git_url("https://GitHub.COM/owner/repo.git")
        assert out == "https://github.com/owner/repo.git"


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------


class TestModels:
    def test_repo_source_invalid_kind(self) -> None:
        with pytest.raises(ValueError):
            RepoSource(raw="x", kind="bogus")

    def test_repo_identity_empty_key_raises(self) -> None:
        with pytest.raises(ValueError):
            RepoIdentity(
                source_type="local",
                display_name="x",
                canonical_source="/p",
                ref=None,
                commit=None,
                collection_key="",
            )


# ---------------------------------------------------------------------------
# cache helpers
# ---------------------------------------------------------------------------


class TestCacheHelpers:
    def test_cache_dir_name_for_https(self) -> None:
        # 取 base 最后一段 + 8 位 hex 摘要，hex 长度固定
        out = cache_dir_name_for("https://github.com/owner/repo.git")
        assert out.startswith("repo_")
        assert len(out) == len("repo_") + 8

    def test_cache_dir_name_for_ssh(self) -> None:
        out = cache_dir_name_for("ssh://git@github.com/owner/repo.git")
        assert out.startswith("repo_")
        assert len(out) == len("repo_") + 8

    def test_cache_dir_name_for_scp(self) -> None:
        out = cache_dir_name_for("git@github.com:owner/repo.git")
        assert out.startswith("repo_")
        assert len(out) == len("repo_") + 8

    def test_cache_dir_name_for_same_url_stable(self) -> None:
        a = cache_dir_name_for("https://github.com/owner/repo.git")
        b = cache_dir_name_for("https://github.com/owner/repo.git")
        assert a == b

    def test_cache_dir_name_for_different_url_different(self) -> None:
        a = cache_dir_name_for("https://github.com/owner/repo.git")
        b = cache_dir_name_for("https://github.com/owner/other.git")
        assert a != b

    def test_collection_key_for_git_stable(self) -> None:
        k1 = collection_key_for_git("https://github.com/owner/repo.git", "main")
        k2 = collection_key_for_git("https://github.com/owner/repo.git", "main")
        k3 = collection_key_for_git("https://github.com/owner/repo.git", "dev")
        assert k1 == k2
        assert k1 != k3

    def test_collection_key_for_local_is_12_hex(self) -> None:
        k = collection_key_for_local("/abs/path/repo")
        assert len(k) == 12
        assert all(c in "0123456789abcdef" for c in k)


# ---------------------------------------------------------------------------
# cache manager
# ---------------------------------------------------------------------------


class TestCacheManager:
    def test_list_and_prune(self, tmp_path: Path) -> None:
        cache = CacheManager(tmp_path)
        # 创建两个模拟缓存目录
        (tmp_path / "github_com_a_b").mkdir()
        (tmp_path / "github_com_a_b" / "metadata.json").write_text(
            '{"canonical_url": "https://github.com/a/b.git", "ref": "main", '
            '"commit": "abc", "cloned_at": "2026-01-01T00:00:00", '
            '"updated_at": "2026-01-01T00:00:00", "metadata": {}}',
            encoding="utf-8",
        )
        (tmp_path / "no_metadata").mkdir()

        entries = cache.list_entries()
        assert len(entries) == 1
        assert entries[0].canonical_url == "https://github.com/a/b.git"

        removed = cache.prune()
        assert len(removed) == 2
        assert not (tmp_path / "github_com_a_b").exists()


# ---------------------------------------------------------------------------
# local provider
# ---------------------------------------------------------------------------


class TestLocalProvider:
    def test_resolve_local_dir(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "a.py").write_text("x = 1", encoding="utf-8")

        provider = LocalRepositoryProvider()
        src = RepoSource(raw=str(repo), kind="local")
        resolved = provider.resolve(src)

        assert isinstance(resolved, ResolvedRepo)
        assert resolved.root_path == repo.resolve()
        assert resolved.identity.source_type == "local"
        assert resolved.identity.collection_key == collection_key_for_local(str(repo.resolve()))
        assert resolved.cache_path is None

    def test_resolve_local_not_exists(self, tmp_path: Path) -> None:
        provider = LocalRepositoryProvider()
        src = RepoSource(raw=str(tmp_path / "nope"), kind="local")
        with pytest.raises(FileNotFoundError):
            provider.resolve(src)

    def test_resolve_local_not_dir(self, tmp_path: Path) -> None:
        file = tmp_path / "file.txt"
        file.write_text("x", encoding="utf-8")
        provider = LocalRepositoryProvider()
        src = RepoSource(raw=str(file), kind="local")
        with pytest.raises(NotADirectoryError):
            provider.resolve(src)


# ---------------------------------------------------------------------------
# git provider（使用本地 bare repo 模拟远程）
# ---------------------------------------------------------------------------


def _git_available() -> bool:
    try:
        subprocess.run(["git", "--version"], check=True, capture_output=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


@pytest.mark.skipif(not _git_available(), reason="git CLI 不可用")
class TestGitProviderLocal:
    """使用本地 bare repo 模拟远程，所有测试都离线完成。"""

    def _create_bare_remote(self, tmp_path: Path) -> Path:
        """在 tmp_path 创建 bare repo + 一个 commit，作为模拟远程。"""
        work = tmp_path / "work"
        work.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(work), check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"], cwd=str(work), check=True
        )
        subprocess.run(["git", "config", "user.name", "Test"], cwd=str(work), check=True)
        (work / "README.md").write_text("hello\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=str(work), check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(work), check=True)

        # 创建分支 feature
        subprocess.run(["git", "checkout", "-q", "-b", "feature"], cwd=str(work), check=True)
        (work / "feature.txt").write_text("f\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=str(work), check=True)
        subprocess.run(["git", "commit", "-q", "-m", "feature"], cwd=str(work), check=True)
        subprocess.run(["git", "checkout", "-q", "main"], cwd=str(work), check=True)

        bare = tmp_path / "remote.git"
        subprocess.run(["git", "clone", "--bare", str(work), str(bare)], check=True)
        return bare

    def test_clone_then_reuse(self, tmp_path: Path) -> None:
        bare = self._create_bare_remote(tmp_path)
        cache_root = tmp_path / "cache"
        cache = CacheManager(cache_root)
        provider = GitRepositoryProvider(cache, clone_depth=1)

        url = bare.as_uri()  # file://... 形式
        src = RepoSource(raw=url, kind="git")
        resolved1 = provider.resolve(src)
        assert resolved1.root_path.is_dir()
        assert (resolved1.root_path / "README.md").exists()
        assert resolved1.identity.source_type == "git"
        assert resolved1.cache_path is not None
        first_commit = resolved1.identity.commit
        assert first_commit and len(first_commit) >= 7

        # 第二次：复用缓存
        resolved2 = provider.resolve(src)
        assert resolved2.identity.commit == first_commit

    def test_checkout_ref(self, tmp_path: Path) -> None:
        bare = self._create_bare_remote(tmp_path)
        cache_root = tmp_path / "cache"
        cache = CacheManager(cache_root)
        provider = GitRepositoryProvider(cache, clone_depth=1)

        url = bare.as_uri()
        src = RepoSource(raw=url, kind="git", ref="feature")
        resolved = provider.resolve(src)
        assert (resolved.root_path / "feature.txt").exists()

    def test_invalid_ref_raises(self, tmp_path: Path) -> None:
        bare = self._create_bare_remote(tmp_path)
        cache = CacheManager(tmp_path / "cache")
        provider = GitRepositoryProvider(cache, clone_depth=1)
        src = RepoSource(raw=bare.as_uri(), kind="git", ref="nonexistent-branch")
        with pytest.raises(Exception):
            provider.resolve(src)

    def test_refresh_creates_new_metadata(self, tmp_path: Path) -> None:
        bare = self._create_bare_remote(tmp_path)
        cache = CacheManager(tmp_path / "cache")
        provider = GitRepositoryProvider(cache, clone_depth=1)
        src = RepoSource(raw=bare.as_uri(), kind="git")

        resolved1 = provider.resolve(src)
        first_commit = resolved1.identity.commit
        # 强制 refresh：应能跑通（fetch + checkout，不重新 clone）
        resolved2 = provider.resolve(src, refresh=True)
        # commit 应保持不变（无新提交）
        assert resolved2.identity.commit == first_commit
        # 缓存目录仍然存在
        assert (tmp_path / "cache").is_dir()


# ---------------------------------------------------------------------------
# resolver
# ---------------------------------------------------------------------------


class TestResolver:
    def test_resolve_local(self, tmp_path: Path) -> None:
        repo = tmp_path / "r"
        repo.mkdir()
        (repo / "a.py").write_text("x = 1", encoding="utf-8")
        resolved = resolve_repo(str(repo))
        assert resolved.identity.source_type == "local"
        assert resolved.root_path == repo.resolve()

    def test_resolve_invalid_local(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            resolve_repo(str(tmp_path / "nope"))

    def test_resolve_git_requires_url(self, tmp_path: Path) -> None:
        # git 解析时如果 clone 失败会抛 GitRepositoryError
        # 我们不真正连外网，但 URL 必须是 file://
        # 用一个不存在的路径
        from code_rag.repository.git import GitRepositoryError

        cache = tmp_path / "cache"
        # 这里用 settings 把 cache 指向 tmp
        from code_rag.config import Settings

        settings = Settings(repo_cache_dir=str(cache))
        with pytest.raises(GitRepositoryError):
            resolve_repo(
                "https://invalid.example.invalid/owner/repo.git",
                settings=settings,
            )
