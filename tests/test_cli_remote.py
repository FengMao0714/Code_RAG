"""CLI 新增命令的测试 — 远程仓库支持 / cache 子命令。

只覆盖新增路径，本地路径的兼容路径由 :mod:`tests.test_cli` 覆盖。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from code_rag.cli import app
from code_rag.config import Settings
from code_rag.repository import CacheManager, GitRepositoryProvider, RepoSource
from tests.conftest import FakeEmbedder

runner = CliRunner()


def _git_available() -> bool:
    try:
        subprocess.run(["git", "--version"], check=True, capture_output=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def _make_bare(tmp_path: Path) -> Path:
    """在 tmp_path 下创建 bare repo 作为模拟远程。"""
    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(work), check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=str(work), check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(work), check=True)
    (work / "a.py").write_text("def hi():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(work), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(work), check=True)
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "clone", "--bare", str(work), str(bare)], check=True)
    return bare


# ---------------------------------------------------------------------------
# cache 子命令
# ---------------------------------------------------------------------------


class TestCacheListCommand:
    def test_cache_list_empty(self, tmp_path: Path) -> None:
        settings = Settings(
            chroma_persist_dir=str(tmp_path / "chroma"),
            index_tracker_dir=str(tmp_path / "indexes"),
            repo_cache_dir=str(tmp_path / "repos"),
            llm_api_key="x",
            llm_base_url="http://x",
            llm_model="m",
            embedding_model="e",
        )
        with patch("code_rag.cli.get_settings", return_value=settings):
            result = runner.invoke(app, ["cache", "list"])
        assert result.exit_code == 0
        assert "无远程仓库缓存" in result.output

    def test_cache_list_with_entry(self, tmp_path: Path) -> None:
        settings = Settings(
            chroma_persist_dir=str(tmp_path / "chroma"),
            index_tracker_dir=str(tmp_path / "indexes"),
            repo_cache_dir=str(tmp_path / "repos"),
            llm_api_key="x",
            llm_base_url="http://x",
            llm_model="m",
            embedding_model="e",
        )
        # 手工创建一个模拟缓存条目
        cache_dir = settings.repo_cache_path / "demo_aabbccdd"
        cache_dir.mkdir(parents=True)
        (cache_dir / "metadata.json").write_text(
            '{"canonical_url": "https://example.com/demo.git", '
            '"ref": "main", "commit": "deadbeef1234", '
            '"cloned_at": "2026-01-01T00:00:00", '
            '"updated_at": "2026-01-02T00:00:00", "metadata": {}}',
            encoding="utf-8",
        )
        with patch("code_rag.cli.get_settings", return_value=settings):
            result = runner.invoke(app, ["cache", "list"])
        assert result.exit_code == 0
        assert "demo.git" in result.output
        assert "main" in result.output

    def test_cache_prune_confirm(self, tmp_path: Path) -> None:
        settings = Settings(
            chroma_persist_dir=str(tmp_path / "chroma"),
            index_tracker_dir=str(tmp_path / "indexes"),
            repo_cache_dir=str(tmp_path / "repos"),
            llm_api_key="x",
            llm_base_url="http://x",
            llm_model="m",
            embedding_model="e",
        )
        cache_dir = settings.repo_cache_path / "demo_aabbccdd"
        cache_dir.mkdir(parents=True)
        (cache_dir / "metadata.json").write_text(
            '{"canonical_url": "https://example.com/demo.git", '
            '"ref": "main", "commit": "x", "cloned_at": null, '
            '"updated_at": null, "metadata": {}}',
            encoding="utf-8",
        )
        with patch("code_rag.cli.get_settings", return_value=settings):
            result = runner.invoke(app, ["cache", "prune", "--yes"])
        assert result.exit_code == 0
        assert "已清理" in result.output
        assert not cache_dir.exists()


# ---------------------------------------------------------------------------
# status / remove / list 命令的 source 兼容性
# ---------------------------------------------------------------------------


class TestRemoteStatusCommand:
    """``status`` 命令对 git URL 走 resolve_repo 流程。"""

    def test_status_no_clone_for_unknown_url(self, tmp_path: Path) -> None:
        """对不存在的 HTTPS URL 调用 status 不触发 GitRepositoryProvider.resolve()。"""
        settings = Settings(
            chroma_persist_dir=str(tmp_path / "chroma"),
            index_tracker_dir=str(tmp_path / "indexes"),
            repo_cache_dir=str(tmp_path / "repos"),
            llm_api_key="x",
            llm_base_url="http://x",
            llm_model="m",
            embedding_model="e",
        )
        url = "https://github.com/example/nonexistent-repo.git"
        with (
            patch("code_rag.cli.get_settings", return_value=settings),
            patch(
                "code_rag.services.manifest_service.resolve_repo",
                side_effect=AssertionError("resolve_repo should not be called"),
            ),
        ):
            result = runner.invoke(app, ["status", url])
        assert result.exit_code == 0, result.output
        assert "尚未索引" in result.output

    def test_status_indexed_git(self, tmp_path: Path, tmp_settings) -> None:
        # 已索引 git 仓库：写一份 manifest 之后调用 status，
        # 验证能正确显示 git 标签。
        from code_rag.services import ManifestService

        bare = _make_bare(tmp_path)
        # 需要 allow_file_remote=True 才能解析 file:// URL
        settings = Settings(
            chroma_persist_dir=str(tmp_path / "chroma"),
            index_tracker_dir=str(tmp_path / "indexes"),
            repo_cache_dir=str(tmp_path / "repos"),
            llm_api_key="x",
            llm_base_url="http://x",
            llm_model="m",
            embedding_model="e",
            allow_file_remote=True,
        )
        cache = CacheManager(settings.repo_cache_dir)
        provider = GitRepositoryProvider(cache, clone_depth=1, allow_file=True)
        resolved = provider.resolve(RepoSource(raw=bare.as_uri(), kind="git"))

        mservice = ManifestService(settings)
        mservice.update_manifest(
            repo_path=resolved,
            file_count=1,
            chunk_count=0,
            resolved=resolved,
        )

        with patch("code_rag.cli.get_settings", return_value=settings):
            result = runner.invoke(app, ["status", bare.as_uri()])
        # status 应当返回 0，且展示 git 标签
        assert result.exit_code == 0, result.output
        assert "git" in result.output.lower() or "Git" in result.output


class TestRemoteSearchCommand:
    """``search`` 对未索引远程仓库应只查索引状态，不触发 clone/fetch。"""

    def test_search_no_clone_for_unknown_url(self, tmp_path: Path) -> None:
        settings = Settings(
            chroma_persist_dir=str(tmp_path / "chroma"),
            index_tracker_dir=str(tmp_path / "indexes"),
            repo_cache_dir=str(tmp_path / "repos"),
            llm_api_key="x",
            llm_base_url="http://x",
            llm_model="m",
            embedding_model="e",
        )
        url = "https://github.com/example/nonexistent-repo.git"

        with (
            patch("code_rag.cli.get_settings", return_value=settings),
            patch(
                "code_rag.cli.resolve_repo",
                side_effect=AssertionError("resolve_repo should not be called"),
            ),
        ):
            result = runner.invoke(app, ["search", url, "anything"])

        assert result.exit_code == 0, result.output
        assert "尚未索引" in result.output
        assert not settings.repo_cache_path.exists()

    def test_remove_no_clone_for_unknown_url(self, tmp_path: Path) -> None:
        settings = Settings(
            chroma_persist_dir=str(tmp_path / "chroma"),
            index_tracker_dir=str(tmp_path / "indexes"),
            repo_cache_dir=str(tmp_path / "repos"),
            llm_api_key="x",
            llm_base_url="http://x",
            llm_model="m",
            embedding_model="e",
        )
        url = "https://github.com/example/nonexistent-repo.git"

        with (
            patch("code_rag.cli.get_settings", return_value=settings),
            patch(
                "code_rag.cli.resolve_repo",
                side_effect=AssertionError("resolve_repo should not be called"),
            ),
        ):
            result = runner.invoke(app, ["remove", url, "--yes"])

        assert result.exit_code == 0, result.output
        assert "已删除" in result.output
        assert not settings.repo_cache_path.exists()


# ---------------------------------------------------------------------------
# 端到端：本地 bare repo → index → status → list → remove
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _git_available(), reason="git CLI 不可用")
class TestRemoteEndToEnd:
    """完整的 git 仓库索引 → 状态 → 删除流程。"""

    def test_full_lifecycle(self, tmp_path: Path, tmp_settings) -> None:
        bare = _make_bare(tmp_path)
        # 用一个独立的 tmp_settings，cache_dir 指向 tmp
        from code_rag.config import Settings

        settings = Settings(
            chroma_persist_dir=str(tmp_path / "chroma"),
            index_tracker_dir=str(tmp_path / "indexes"),
            repo_cache_dir=str(tmp_path / "repos"),
            llm_api_key="x",
            llm_base_url="http://x",
            llm_model="m",
            embedding_model="e",
            allow_file_remote=True,
        )
        url = bare.as_uri()
        fake_emb = FakeEmbedder()

        # 1) index — 远程 URL
        with (
            patch("code_rag.cli.get_settings", return_value=settings),
            patch("code_rag.indexer.embedder.Embedder.get_instance", return_value=fake_emb),
        ):
            result = runner.invoke(app, ["index", url])
        assert result.exit_code == 0, result.output
        assert "索引完成" in result.output

        # 2) list — 应能列出
        with patch("code_rag.cli.get_settings", return_value=settings):
            result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "git" in result.output

        # 3) status — 显示 git 信息
        with patch("code_rag.cli.get_settings", return_value=settings):
            result = runner.invoke(app, ["status", url])
        assert result.exit_code == 0
        assert "git" in result.output

        # 4) remove — 删索引
        with patch("code_rag.cli.get_settings", return_value=settings):
            result = runner.invoke(app, ["remove", url, "--yes"])
        assert result.exit_code == 0
        assert "已删除" in result.output

        # 5) list — 重新为空
        with patch("code_rag.cli.get_settings", return_value=settings):
            result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "暂无" in result.output

        # 6) cache list — 缓存仍然存在（默认不删缓存）
        with patch("code_rag.cli.get_settings", return_value=settings):
            result = runner.invoke(app, ["cache", "list"])
        assert result.exit_code == 0
        # 输出包含截断的 URL（不一定含 remote.git）但要能识别
        assert "URL" in result.output  # 表头

        # 7) cache prune — 清理
        with patch("code_rag.cli.get_settings", return_value=settings):
            result = runner.invoke(app, ["cache", "prune", "--yes"])
        assert result.exit_code == 0
        assert "已清理" in result.output
