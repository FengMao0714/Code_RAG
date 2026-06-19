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
    GitRepositoryError,
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
    redact_url,
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

    @pytest.mark.parametrize(
        "url",
        [
            "http://gitlab.example.com/owner/repo.git",
            "ftp://example.com/repo.git",
            "git://github.com/owner/repo.git",
            "file:///tmp/repo.git",
        ],
    )
    def test_unsafe_url_schemes_rejected_by_default(self, url: str) -> None:
        """不安全或本地文件 URL 默认应清晰拒绝，不能落入 local 分支。"""
        with pytest.raises(InvalidRepoSourceError, match="不支持的 URL 协议"):
            parse_repo_source(url)

    def test_ftp_url_rejected(self) -> None:
        """ftp:// 始终被拒绝。"""
        with pytest.raises(InvalidRepoSourceError):
            parse_repo_source("ftp://example.com/repo.git")

    def test_git_protocol_rejected(self) -> None:
        """git:// 默认被拒绝。"""
        with pytest.raises(InvalidRepoSourceError):
            parse_repo_source("git://github.com/owner/repo.git")

    def test_file_url_rejected_by_default(self) -> None:
        """file:// 默认被拒绝。"""
        with pytest.raises(InvalidRepoSourceError):
            parse_repo_source("file:///tmp/repo.git")

    def test_file_url_allowed_when_explicit(self) -> None:
        """allow_file=True 时接受 file://。"""
        src = parse_repo_source("file:///tmp/repo.git", allow_file=True)
        assert src.kind == SOURCE_TYPE_GIT

    def test_scp_like_ssh(self) -> None:
        src = parse_repo_source("git@github.com:owner/repo.git")
        assert src.kind == SOURCE_TYPE_GIT

    def test_ssh_protocol(self) -> None:
        src = parse_repo_source("ssh://git@github.com/owner/repo.git")
        assert src.kind == SOURCE_TYPE_GIT

    def test_git_protocol(self) -> None:
        """git:// 默认被拒绝（不安全 scheme）。"""
        with pytest.raises(InvalidRepoSourceError):
            parse_repo_source("git://github.com/owner/repo.git")

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

    def test_https_with_token_rejected(self) -> None:
        """HTTPS URL 包含 token 时应抛出错误。"""
        with pytest.raises(GitRepositoryError, match="凭据"):
            canonicalize_git_url("https://token123@github.com/owner/repo.git")

    def test_https_with_userinfo_rejected(self) -> None:
        """HTTPS URL 包含 user:password 时应抛出错误。"""
        with pytest.raises(GitRepositoryError, match="凭据"):
            canonicalize_git_url("https://user:pass@github.com/owner/repo.git")

    def test_ssh_with_userinfo_allowed(self) -> None:
        """SSH URL 包含 git@ 用户名是正常的，应允许。"""
        out = canonicalize_git_url("ssh://git@github.com/owner/repo.git")
        assert out == "ssh://git@github.com/owner/repo.git"


# ---------------------------------------------------------------------------
# redact_url
# ---------------------------------------------------------------------------


class TestRedactUrl:
    def test_https_no_userinfo_unchanged(self) -> None:
        assert (
            redact_url("https://github.com/owner/repo.git") == "https://github.com/owner/repo.git"
        )

    def test_https_with_token_redacted(self) -> None:
        out = redact_url("https://token123@github.com/owner/repo.git")
        assert out == "https://***@github.com/owner/repo.git"
        assert "token123" not in out

    def test_ssh_git_at_preserved(self) -> None:
        """SSH URL 中的 git@ 是 SSH 用户名，不做替换。"""
        out = redact_url("ssh://git@github.com/owner/repo.git")
        assert out == "ssh://git@github.com/owner/repo.git"

    def test_scp_like_preserved(self) -> None:
        """scp-like 形式不做替换。"""
        out = redact_url("git@github.com:owner/repo.git")
        assert out == "git@github.com:owner/repo.git"


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
        provider = GitRepositoryProvider(cache, clone_depth=1, allow_file=True)

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
        provider = GitRepositoryProvider(cache, clone_depth=1, allow_file=True)

        url = bare.as_uri()
        src = RepoSource(raw=url, kind="git", ref="feature")
        resolved = provider.resolve(src)
        assert (resolved.root_path / "feature.txt").exists()

    def test_invalid_ref_raises(self, tmp_path: Path) -> None:
        bare = self._create_bare_remote(tmp_path)
        cache = CacheManager(tmp_path / "cache")
        provider = GitRepositoryProvider(cache, clone_depth=1, allow_file=True)
        src = RepoSource(raw=bare.as_uri(), kind="git", ref="nonexistent-branch")
        with pytest.raises(Exception):
            provider.resolve(src)

    def test_refresh_creates_new_metadata(self, tmp_path: Path) -> None:
        bare = self._create_bare_remote(tmp_path)
        cache = CacheManager(tmp_path / "cache")
        provider = GitRepositoryProvider(cache, clone_depth=1, allow_file=True)
        src = RepoSource(raw=bare.as_uri(), kind="git")

        resolved1 = provider.resolve(src)
        first_commit = resolved1.identity.commit
        # 强制 refresh：应能跑通（fetch + checkout，不重新 clone）
        resolved2 = provider.resolve(src, refresh=True)
        # commit 应保持不变（无新提交）
        assert resolved2.identity.commit == first_commit
        # 缓存目录仍然存在
        assert (tmp_path / "cache").is_dir()

    def _push_new_commit(self, bare: Path, tmp_path: Path) -> str:
        """向 bare repo 推送一个新 commit，返回新 commit SHA。"""
        work2 = tmp_path / "push_work"
        work2.mkdir()
        subprocess.run(["git", "clone", "-q", str(bare), str(work2)], check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"], cwd=str(work2), check=True
        )
        subprocess.run(["git", "config", "user.name", "Test"], cwd=str(work2), check=True)
        (work2 / "new_file.txt").write_text("new content\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=str(work2), check=True)
        subprocess.run(["git", "commit", "-q", "-m", "second"], cwd=str(work2), check=True)
        subprocess.run(["git", "push", "-q"], cwd=str(work2), check=True)
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(work2), capture_output=True, text=True, check=True
        )
        return result.stdout.strip()

    def test_no_refresh_keeps_old_commit(self, tmp_path: Path) -> None:
        """refresh=False 时复用本地缓存，不拉取远端新 commit。"""
        bare = self._create_bare_remote(tmp_path)
        cache = CacheManager(tmp_path / "cache")
        provider = GitRepositoryProvider(cache, clone_depth=0, allow_file=True)
        src = RepoSource(raw=bare.as_uri(), kind="git")

        resolved1 = provider.resolve(src)
        old_commit = resolved1.identity.commit

        # 向远端推送新 commit
        new_commit = self._push_new_commit(bare, tmp_path)
        assert new_commit != old_commit

        # refresh=False：应保持旧 commit
        resolved2 = provider.resolve(src, refresh=False)
        assert resolved2.identity.commit == old_commit

    def test_refresh_updates_to_new_commit(self, tmp_path: Path) -> None:
        """refresh=True 时 fetch 并更新到远端新 commit。"""
        bare = self._create_bare_remote(tmp_path)
        cache = CacheManager(tmp_path / "cache")
        provider = GitRepositoryProvider(cache, clone_depth=0, allow_file=True)
        src = RepoSource(raw=bare.as_uri(), kind="git")

        resolved1 = provider.resolve(src)
        old_commit = resolved1.identity.commit

        new_commit = self._push_new_commit(bare, tmp_path)
        assert new_commit != old_commit

        # refresh=True：应更新到新 commit
        resolved2 = provider.resolve(src, refresh=True)
        assert resolved2.identity.commit == new_commit

    def test_refresh_removes_untracked_files(self, tmp_path: Path) -> None:
        """缓存 worktree 中手动放入的未跟踪文件在 refresh 后应消失。"""
        bare = self._create_bare_remote(tmp_path)
        cache = CacheManager(tmp_path / "cache")
        provider = GitRepositoryProvider(cache, clone_depth=1, allow_file=True)
        src = RepoSource(raw=bare.as_uri(), kind="git")

        resolved = provider.resolve(src)
        untracked = resolved.root_path / "untracked_junk.txt"
        untracked.write_text("should be cleaned\n", encoding="utf-8")
        assert untracked.exists()

        provider.resolve(src, refresh=True)
        assert not untracked.exists()


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
