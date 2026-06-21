"""CLI 入口模块。

使用 typer + rich 提供美观的命令行交互界面。
CLI 只负责参数解析和 Rich 展示，业务编排由
:mod:`code_rag.services` 提供。

本模块支持两种仓库输入源：

- **本地路径**（向后兼容）：``code-rag index /path/to/repo``
- **Git 仓库 URL**（新增）：``code-rag index https://github.com/owner/repo --ref main``

所有 ``source`` 参数同时接受本地路径与 Git URL，远程仓库会被自动
clone 到 ``repo_cache_dir`` 缓存目录后再走原有索引 / 检索链路。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Windows GBK 终端兼容：强制 stdout/stderr 使用 UTF-8，避免非 ASCII 字符崩溃
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from code_rag.config import get_settings
from code_rag.embedding_profiles import list_embedding_profiles, resolve_embedding_profile
from code_rag.generator import LLMClient
from code_rag.repository import (
    CacheManager,
    GitRepositoryError,
    canonicalize_git_url,
    identity_key_for_source,
    parse_repo_source,
    redact_url,
    resolve_repo,
)
from code_rag.retriever.modes import SearchMode
from code_rag.services import IndexService, ManifestService, QueryService
from code_rag.store import vector_store as store_mod

app = typer.Typer(
    name="code-rag",
    help="代码知识库 RAG 问答助手 — 基于代码仓库的智能问答 CLI 工具",
    add_completion=False,
)
cache_app = typer.Typer(
    name="cache",
    help="管理远程仓库的本地缓存（list / prune）",
    add_completion=False,
)
embeddings_app = typer.Typer(
    name="embeddings",
    help="查看和选择内置 Embedding profiles",
    add_completion=False,
)
app.add_typer(cache_app)
app.add_typer(embeddings_app)
console = Console()
console.legacy_windows = False  # 禁用旧版渲染器，避免 GBK 编码崩溃


def setup_logging(verbose: bool = False) -> None:
    """配置日志输出。"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )


def _settings_with_embedding_profile(embedding_profile: str | None):
    """Load settings and optionally override the active embedding profile."""
    settings = get_settings()
    if embedding_profile:
        settings = settings.model_copy(update={"embedding_profile": embedding_profile})
    return settings


# ---------------------------------------------------------------------------
# 共用 source 选项
# ---------------------------------------------------------------------------


def _source_options(
    ref: str | None,
    refresh: bool,
) -> tuple[str | None, bool]:
    """把 ``--ref`` / ``--refresh`` 透传给 service。"""
    return ref, refresh


@app.command()
def index(
    source: str = typer.Argument(..., help="代码仓库路径或 Git 仓库 URL"),
    ref: str | None = typer.Option(None, "--ref", help="git ref（仅 git URL 生效）"),
    refresh: bool = typer.Option(
        False, "--refresh", help="强制刷新远程仓库缓存（仅 git URL 生效）"
    ),
    embedding_profile: str | None = typer.Option(
        None,
        "--embedding-profile",
        help="Embedding profile: baseline / bge-m3 / e5-base / custom",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="显示详细日志"),
) -> None:
    """索引代码仓库（首次全量，后续增量更新）。支持本地路径与 Git URL。"""
    setup_logging(verbose)

    try:
        settings = _settings_with_embedding_profile(embedding_profile)
        profile = resolve_embedding_profile(settings)
        kind = parse_repo_source(source, allow_file=settings.allow_file_remote).kind
        console.print(
            f"[bold blue]>> 开始索引仓库 ({kind}): {source}"
            + (f" @ {ref}" if ref else "")
            + "[/bold blue]"
        )
        console.print(f"[dim]Embedding: {profile.profile_id} ({profile.model_name})[/dim]")
        service = IndexService(settings)

        with Progress(
            SpinnerColumn(spinner_name="line"),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("索引中...", total=None)

            def _cb(stage: str, message: str) -> None:
                progress.update(task, description=message)

            result = service.run_index(source, progress=_cb, ref=ref, refresh=refresh)

        if not result.had_changes:
            console.print("[bold green][OK] 仓库无变更，无需更新索引[/bold green]")
            return

        _print_index_result(result)
        console.print(
            f"[bold green][OK] 索引完成！[/bold green] "
            f"处理 {result.added + result.modified} 个文件，"
            f"生成 {result.chunks_generated} 个切片"
        )

    except FileNotFoundError as exc:
        console.print(f"[red]错误：{exc}[/red]")
        raise typer.Exit(1) from None
    except NotADirectoryError as exc:
        console.print(f"[red]错误：{exc}[/red]")
        raise typer.Exit(1) from None
    except GitRepositoryError as exc:
        console.print(f"[red]Git 错误：{exc}[/red]")
        raise typer.Exit(1) from None
    except Exception as exc:
        console.print(f"[red]错误: {exc}[/red]")
        if verbose:
            console.print_exception()
        raise typer.Exit(1) from None


@app.command()
def ask(
    source: str = typer.Argument(..., help="代码仓库路径或 Git 仓库 URL"),
    question: str = typer.Argument(..., help="你的问题"),
    ref: str | None = typer.Option(None, "--ref", help="git ref（仅 git URL 生效）"),
    mode: str = typer.Option(
        SearchMode.DEFAULT,
        "--mode",
        "-m",
        help="检索模式: vector / lexical / hybrid",
    ),
    embedding_profile: str | None = typer.Option(
        None,
        "--embedding-profile",
        help="Embedding profile: baseline / bge-m3 / e5-base / custom",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="显示详细日志"),
) -> None:
    """对已索引的代码仓库提问。支持本地路径与 Git URL。"""
    setup_logging(verbose)
    console.print(f"[bold blue]>> 正在检索: {question}[/bold blue]")

    try:
        settings = _settings_with_embedding_profile(embedding_profile)
        service = QueryService(settings)

        with Progress(
            SpinnerColumn(spinner_name="line"),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("检索相关代码...", total=None)
            result = service.ask(question, source, ref=ref, mode=mode)
            progress.update(task, description=f"检索到 {len(result.retrieval.chunks)} 个相关片段")

        if not result.retrieval.context:
            console.print("[yellow]未找到相关代码，请确认仓库已索引[/yellow]")
            return

        if result.low_confidence:
            console.print(f"[yellow]提示: 检索置信度偏低 — {result.reason}[/yellow]")

        console.print("\n[bold]>> 回答：[/bold]\n")
        llm = LLMClient(settings)
        for chunk in llm.generate_stream(result.retrieval.context, question):
            console.print(chunk.content, end="")

        console.print("\n")

    except ValueError as exc:
        console.print(f"[red]配置错误: {exc}[/red]")
        raise typer.Exit(1) from None
    except RuntimeError as exc:
        console.print(f"[red]LLM 调用失败: {exc}[/red]")
        raise typer.Exit(1) from None
    except FileNotFoundError as exc:
        console.print(f"[red]错误：{exc}[/red]")
        raise typer.Exit(1) from None
    except Exception as exc:
        console.print(f"[red]错误: {exc}[/red]")
        if verbose:
            console.print_exception()
        raise typer.Exit(1) from None


@app.command()
def chat(
    source: str = typer.Argument(..., help="代码仓库路径或 Git 仓库 URL"),
    ref: str | None = typer.Option(None, "--ref", help="git ref（仅 git URL 生效）"),
    mode: str = typer.Option(
        SearchMode.DEFAULT,
        "--mode",
        "-m",
        help="检索模式: vector / lexical / hybrid",
    ),
    embedding_profile: str | None = typer.Option(
        None,
        "--embedding-profile",
        help="Embedding profile: baseline / bge-m3 / e5-base / custom",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="显示详细日志"),
) -> None:
    """进入交互式对话模式。支持本地路径与 Git URL。"""
    setup_logging(verbose)
    console.print(f"[bold blue]>> 进入交互模式 — 仓库: {source}[/bold blue]")
    console.print("[dim]输入 'exit' 或 'quit' 退出[/dim]\n")

    try:
        settings = _settings_with_embedding_profile(embedding_profile)
        service = QueryService(settings)
    except Exception as exc:
        console.print(f"[red]初始化失败: {exc}[/red]")
        raise typer.Exit(1) from None

    while True:
        try:
            question = console.input("[bold green]> [/bold green]")
            if question.strip().lower() in ("exit", "quit", "q", "/exit", "/quit"):
                console.print("[dim]再见！[/dim]")
                break
            if not question.strip():
                continue

            result = service.ask(question, source, ref=ref, mode=mode)

            if not result.retrieval.context:
                console.print("[yellow]未找到相关代码[/yellow]\n")
                continue

            console.print()
            llm = LLMClient(settings)
            for chunk in llm.generate_stream(result.retrieval.context, question):
                console.print(chunk.content, end="")
            console.print("\n")

        except KeyboardInterrupt:
            console.print("\n[dim]再见！[/dim]")
            break
        except EOFError:
            console.print("\n[dim]再见！[/dim]")
            break
        except Exception as exc:
            console.print(f"[red]错误: {exc}[/red]\n")


@app.command(name="list")
def list_repos() -> None:
    """列出所有已索引的代码仓库。"""
    settings = get_settings()
    service = ManifestService(settings)
    entries = service.list_manifests()

    if not entries:
        console.print("[dim]暂无已索引的仓库[/dim]")
        return

    table = Table(title="已索引的仓库", show_header=True, header_style="bold")
    table.add_column("类型", style="cyan", no_wrap=True)
    table.add_column("来源", style="bold")
    table.add_column("Ref", style="green")
    table.add_column("文件", justify="right")
    table.add_column("切片", justify="right")
    table.add_column("最后索引", style="dim")

    for entry in entries:
        ref = entry.ref or "-"
        src = entry.canonical_source or entry.repo_path
        if entry.source_type == "git":
            display = redact_url(src)
            if len(display) > 60:
                display = "..." + display[-57:]
        else:
            display = entry.repo_path
        table.add_row(
            entry.source_type,
            display,
            ref,
            str(entry.file_count),
            str(entry.chunk_count),
            entry.last_indexed_at,
        )
    console.print(table)


@app.command()
def status(
    source: str = typer.Argument(..., help="代码仓库路径或 Git 仓库 URL"),
    ref: str | None = typer.Option(None, "--ref", help="git ref（仅 git URL 生效）"),
    embedding_profile: str | None = typer.Option(
        None,
        "--embedding-profile",
        help="Embedding profile: baseline / bge-m3 / e5-base / custom",
    ),
) -> None:
    """查看仓库的索引状态。支持本地路径与 Git URL。"""
    console.print(f"[bold blue]>> 仓库索引状态: {source}[/bold blue]")

    settings = _settings_with_embedding_profile(embedding_profile)
    service = ManifestService(settings)
    manifest, store_stats = service.get_status(source, ref=ref)

    if not store_stats.get("exists"):
        console.print("[yellow]该仓库尚未索引[/yellow]")
        return

    if manifest:
        console.print(f"  类型: {manifest.source_type}")
        src_display = manifest.canonical_source or manifest.repo_path
        if manifest.source_type == "git":
            src_display = redact_url(src_display)
        console.print(f"  来源: {src_display}")
        if manifest.display_name:
            console.print(f"  名称: {manifest.display_name}")
        if manifest.ref:
            console.print(f"  Ref: {manifest.ref}")
        if manifest.commit:
            console.print(f"  Commit: {manifest.commit}")
        if manifest.cache_path:
            console.print(f"  缓存目录: {manifest.cache_path}")
        console.print(f"  本地路径: {manifest.repo_path}")
        console.print(f"  Collection: {manifest.collection_name}")
        console.print(f"  最后索引: {manifest.last_indexed_at}")
        console.print(f"  Embedding Profile: {manifest.embedding_profile}")
        console.print(f"  Embedding 模型: {manifest.embedding_model}")
        console.print(f"  LLM 模型: {manifest.llm_model}")
        console.print(f"  Tracker 文件数: {manifest.file_count}")
        console.print(f"  检索 top_k: {manifest.retrieval_top_k}")
        console.print(f"  检索阈值: {manifest.retrieval_score_threshold}")
    console.print(f"  ChromaDB 总切片数: {store_stats['total_chunks']}")

    types_to_show = (manifest.chunk_types if manifest else store_stats.get("chunk_types", {})) or {}
    if types_to_show:
        console.print("  切片类型分布:")
        for ctype, count in types_to_show.items():
            console.print(f"    - {ctype}: {count}")


@app.command()
def remove(
    source: str = typer.Argument(..., help="代码仓库路径或 Git 仓库 URL"),
    ref: str | None = typer.Option(None, "--ref", help="git ref（仅 git URL 生效）"),
    embedding_profile: str | None = typer.Option(
        None,
        "--embedding-profile",
        help="Embedding profile: baseline / bge-m3 / e5-base / custom",
    ),
    confirm: bool = typer.Option(False, "--yes", "-y", help="跳过确认"),
    with_cache: bool = typer.Option(
        False,
        "--with-cache",
        help="同时删除远程仓库缓存（仅 git URL 生效）",
    ),
) -> None:
    """删除仓库的索引数据（默认不删除远程仓库缓存）。"""
    if not confirm:
        confirmed = typer.confirm(f"确定要删除 {source} 的索引数据吗？")
        if not confirmed:
            console.print("[dim]已取消[/dim]")
            return

    settings = _settings_with_embedding_profile(embedding_profile)
    store = store_mod.ChromaStore(settings)
    manifest_service = ManifestService(settings)

    # 仅计算 key，不触发 clone
    collection_key = identity_key_for_source(source, ref, settings=settings)
    collection_name = store_mod.ChromaStore.get_collection_name_from_key(collection_key)

    # 删除 ChromaDB collection
    store.delete_collection(collection_name)

    # 删除 tracker 目录
    import shutil

    tracker_dir = settings.index_tracker_path / collection_key
    if tracker_dir.exists():
        shutil.rmtree(tracker_dir, ignore_errors=True)

    # 删除 manifest
    manifest_service.remove_manifest_by_key(collection_key)

    # 可选：同时删除缓存（仅当缓存已存在时）
    if with_cache:
        repo_source = parse_repo_source(source, allow_file=settings.allow_file_remote)
        if repo_source.kind == "git":
            canonical = canonicalize_git_url(source)
            cache = CacheManager(settings.repo_cache_dir)
            cache_entry = cache.get(canonical)
            if cache_entry is not None:
                cache.remove(canonical)
                console.print(f"[dim]已同时删除远程仓库缓存: {cache_entry.cache_dir}[/dim]")

    console.print(f"[bold green][OK] 已删除 {source} 的索引数据[/bold green]")


@app.command()
def search(
    source: str = typer.Argument(..., help="代码仓库路径或 Git 仓库 URL"),
    query: str = typer.Argument(..., help="检索查询"),
    top_k: int = typer.Option(8, "--top-k", "-k", help="最大返回结果数"),
    mode: str = typer.Option(
        SearchMode.DEFAULT,
        "--mode",
        "-m",
        help="检索模式: vector / lexical / hybrid",
    ),
    ref: str | None = typer.Option(None, "--ref", help="git ref（仅 git URL 生效）"),
    embedding_profile: str | None = typer.Option(
        None,
        "--embedding-profile",
        help="Embedding profile: baseline / bge-m3 / e5-base / custom",
    ),
    explain: bool = typer.Option(False, "--explain", help="显示每条结果来自哪个检索阶段和耗时"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="显示详细日志"),
) -> None:
    """调试检索：只显示召回结果，不调用 LLM。支持本地路径与 Git URL。"""
    setup_logging(verbose)
    console.print(f"[bold blue]>> 检索调试: {query}[/bold blue]")

    try:
        settings = _settings_with_embedding_profile(embedding_profile)
        mode = SearchMode.normalize(mode)
        store = store_mod.ChromaStore(settings)
        collection_key = identity_key_for_source(source, ref, settings=settings)
        coll_name = store_mod.ChromaStore.get_collection_name_from_key(collection_key)
        stats = store.get_stats(coll_name)

        if not stats["exists"]:
            console.print("[yellow]该仓库尚未索引，请先运行 index 命令[/yellow]")
            return

        resolved = resolve_repo(source, ref=ref, settings=settings)
        from code_rag.services.query_service import build_retriever

        retriever_fn = build_retriever(mode, settings, resolved)
        results = retriever_fn(query, top_k, None)

        if not results:
            console.print("[yellow]未检索到任何结果[/yellow]")
            return

        # 输出结果
        console.print(f"\n[bold]检索到 {len(results)} 条结果[/bold] (mode={mode})\n")
        for i, result in enumerate(results, 1):
            chunk = result.chunk
            extras = []
            if explain:
                stage = getattr(result, "stage", mode)
                extras.append(f"stage={stage}")
            extra_str = "  ".join(extras)
            line = (
                f"  [{i}] score={result.score:.4f}  "
                f"type={chunk.chunk_type}  "
                f"name={chunk.name}  "
                f"file={chunk.file_path}  "
                f"lines={chunk.start_line}-{chunk.end_line}"
            )
            if extra_str:
                line += f"  ({extra_str})"
            console.print(line)
            if chunk.parent:
                console.print(f"      parent={chunk.parent}")
        console.print()

    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None

    except FileNotFoundError as exc:
        console.print(f"[red]错误：{exc}[/red]")
        raise typer.Exit(1) from None
    except GitRepositoryError as exc:
        console.print(f"[red]Git 错误：{exc}[/red]")
        raise typer.Exit(1) from None
    except Exception as exc:
        console.print(f"[red]错误: {exc}[/red]")
        if verbose:
            console.print_exception()
        raise typer.Exit(1) from None


@app.command()
def eval(
    source: str = typer.Argument(..., help="代码仓库路径或 Git 仓库 URL"),
    dataset: Path = typer.Option(
        "evals/code_rag_golden.yaml",
        "--dataset",
        "-d",
        help="Golden query 数据集 YAML 路径",
    ),
    top_k: int = typer.Option(8, "--top-k", "-k", help="检索 top_k"),
    mode: str = typer.Option(
        SearchMode.DEFAULT,
        "--mode",
        "-m",
        help="检索模式: vector / lexical / hybrid",
    ),
    embedding_profile: str | None = typer.Option(
        None,
        "--embedding-profile",
        help="Embedding profile: baseline / bge-m3 / e5-base / custom",
    ),
    ref: str | None = typer.Option(None, "--ref", help="git ref（仅 git URL 生效）"),
    output: Path | None = typer.Option(None, "--output", "-o", help="JSON 报告输出路径"),
    markdown: Path | None = typer.Option(None, "--markdown", help="Markdown 报告输出路径"),
    compare_modes: str | None = typer.Option(
        None,
        "--compare-modes",
        help="逗号分隔的检索模式对比，如 vector,lexical,hybrid",
    ),
    compare_embeddings: str | None = typer.Option(
        None,
        "--compare-embeddings",
        help="逗号分隔的 Embedding profiles 对比，如 baseline,bge-m3,e5-base",
    ),
    auto_index: bool = typer.Option(
        False,
        "--auto-index",
        help="compare-embeddings 时自动为缺失 profile 建索引",
    ),
) -> None:
    """对 golden query 数据集运行检索评测（不调用 LLM）。"""
    try:
        from code_rag.services import EvalService
    except ImportError as exc:
        console.print(f"[red]无法加载 EvalService: {exc}[/red]")
        raise typer.Exit(1) from None

    settings = _settings_with_embedding_profile(embedding_profile)
    service = EvalService(settings)

    console.print("[bold blue]>> 检索评测[/bold blue]")
    console.print(f"  仓库: {source}")
    console.print(f"  数据集: {dataset}")
    console.print(f"  top_k: {top_k}, mode: {mode}")
    profile = resolve_embedding_profile(settings)
    console.print(f"  embedding: {profile.profile_id} ({profile.model_name})")

    try:
        ds = service.load(dataset)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None

    if not ds.queries:
        console.print("[yellow]数据集为空[/yellow]")
        return

    console.print(f"  loaded: {len(ds.queries)} 条 golden query")

    if compare_modes and compare_embeddings:
        console.print("[red]--compare-modes 与 --compare-embeddings 不能同时使用[/red]")
        raise typer.Exit(1)

    if compare_embeddings:
        profiles = [item.strip() for item in compare_embeddings.split(",") if item.strip()]
        if not profiles:
            console.print("[red]--compare-embeddings 至少需要一个 profile[/red]")
            raise typer.Exit(1)

        mode = SearchMode.normalize(mode)
        console.print("\n[bold]Embedding 对比[/bold]")
        comparison = service.compare_embeddings(
            ds,
            repo_path=source,
            profiles=profiles,
            top_k=top_k,
            mode=mode,
            ref=ref,
            auto_index=auto_index,
        )
        table = Table(show_header=True, header_style="bold")
        table.add_column("Profile", style="cyan")
        table.add_column("Model", style="bold")
        table.add_column("Status")
        table.add_column("Recall@1", justify="right")
        table.add_column("Recall@3", justify="right")
        table.add_column("Recall@8", justify="right")
        table.add_column("MRR", justify="right")
        table.add_column("Latency", justify="right")
        for result in comparison.values():
            if result.summary is None:
                table.add_row(
                    result.profile_id,
                    result.model_name,
                    "missing",
                    "-",
                    "-",
                    "-",
                    "-",
                    "-",
                )
                continue
            summary = result.summary
            table.add_row(
                result.profile_id,
                result.model_name,
                "indexed",
                f"{summary.recall_at_1:.2%}",
                f"{summary.recall_at_3:.2%}",
                f"{summary.recall_at_8:.2%}",
                f"{summary.mrr:.4f}",
                f"{summary.avg_latency_ms:.1f}ms",
            )
        console.print(table)
        if output or markdown:
            paths = service.write_embedding_comparison_reports(
                comparison,
                dataset_name=ds.name,
                repo_path=str(source),
                top_k=top_k,
                mode=mode,
                output_json=str(output) if output else None,
                output_markdown=str(markdown) if markdown else None,
            )
            if paths.json_path:
                console.print(f"  JSON 报告: {paths.json_path}")
            if paths.markdown_path:
                console.print(f"  Markdown 报告: {paths.markdown_path}")
        return

    if compare_modes:
        try:
            modes = SearchMode.parse_csv(compare_modes)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from None

        console.print("\n[bold]模式对比[/bold]")
        comparison = service.compare_modes(
            ds,
            repo_path=source,
            top_k=top_k,
            modes=modes,
            ref=ref,
        )
        table = Table(show_header=True, header_style="bold")
        table.add_column("Mode", style="cyan")
        table.add_column("Recall@1", justify="right")
        table.add_column("Recall@3", justify="right")
        table.add_column("Recall@8", justify="right")
        table.add_column("MRR", justify="right")
        table.add_column("File Hit", justify="right")
        table.add_column("Symbol Hit", justify="right")
        table.add_column("Latency", justify="right")
        for mode_name, summary in comparison.items():
            table.add_row(
                mode_name,
                f"{summary.recall_at_1:.2%}",
                f"{summary.recall_at_3:.2%}",
                f"{summary.recall_at_8:.2%}",
                f"{summary.mrr:.4f}",
                f"{summary.file_hit_rate:.2%}",
                f"{summary.symbol_hit_rate:.2%}",
                f"{summary.avg_latency_ms:.1f}ms",
            )
        console.print(table)
        if output or markdown:
            paths = service.write_comparison_reports(
                comparison,
                dataset_name=ds.name,
                repo_path=str(source),
                top_k=top_k,
                output_json=str(output) if output else None,
                output_markdown=str(markdown) if markdown else None,
            )
            if paths.json_path:
                console.print(f"  JSON 报告: {paths.json_path}")
            if paths.markdown_path:
                console.print(f"  Markdown 报告: {paths.markdown_path}")
        return

    with Progress(
        SpinnerColumn(spinner_name="line"),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("评测中...", total=None)
        summary = service.run(
            ds,
            repo_path=source,
            top_k=top_k,
            mode=mode,
            ref=ref,
        )
        progress.update(task, description="评测完成")

    # 输出概览
    console.print(
        f"\n  [bold]Recall@1={summary.recall_at_1:.2%}  "
        f"Recall@3={summary.recall_at_3:.2%}  "
        f"Recall@8={summary.recall_at_8:.2%}  "
        f"MRR={summary.mrr:.4f}[/bold]"
    )
    console.print(
        f"  file_hit={summary.file_hit_rate:.2%}  "
        f"symbol_hit={summary.symbol_hit_rate:.2%}  "
        f"avg_latency={summary.avg_latency_ms:.1f}ms"
    )

    # 失败样例
    failed = [
        q
        for q in summary.per_query
        if q.has_expected_target and not q.file_hit and not q.symbol_hit
    ]
    if failed:
        console.print(f"\n  [yellow]未命中样例 ({len(failed)}):[/yellow]")
        for q in failed[:5]:
            console.print(f"    - {q.query_id}: {q.question}")

    # 写报告
    json_path = str(output) if output else None
    md_path = str(markdown) if markdown else None
    if json_path or md_path:
        paths = service.write_reports(
            summary,
            dataset_name=ds.name,
            repo_path=str(source),
            top_k=top_k,
            mode=mode,
            output_json=json_path,
            output_markdown=md_path,
        )
        if paths.json_path:
            console.print(f"  JSON 报告: {paths.json_path}")
        if paths.markdown_path:
            console.print(f"  Markdown 报告: {paths.markdown_path}")


# ---------------------------------------------------------------------------
# agent 子命令 — 轻量 Code Agent（只读分析 / 修改计划）
# ---------------------------------------------------------------------------


@app.command()
def agent(
    source: str = typer.Argument(..., help="代码仓库路径或 Git 仓库 URL"),
    task: str = typer.Argument(..., help="Agent 任务描述（自然语言）"),
    ref: str | None = typer.Option(None, "--ref", help="git ref（仅 git URL 生效）"),
    embedding_profile: str | None = typer.Option(
        None,
        "--embedding-profile",
        help="Embedding profile: baseline / bge-m3 / e5-base / custom",
    ),
    plan_only: bool = typer.Option(
        True,
        "--plan-only/--no-plan-only",
        help="只生成修改计划（默认开启，不调用 LLM 也不修改文件）",
    ),
    top_k: int = typer.Option(5, "--top-k", "-k", help="每个子问题的检索条数"),
    output: Path | None = typer.Option(None, "--output", "-o", help="写出 Agent 报告路径"),
    report_format: str = typer.Option(
        "markdown",
        "--format",
        help="Agent 报告格式: markdown / json",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="显示详细日志"),
) -> None:
    """轻量 Code Agent：拆解任务 → 检索 → 汇总计划（只读）。"""
    setup_logging(verbose)
    console.print(f"[bold blue]>> Agent 任务: {task}[/bold blue]")
    console.print(f"[dim]仓库: {source}{(' @ ' + ref) if ref else ''}[/dim]")

    try:
        settings = _settings_with_embedding_profile(embedding_profile)
        resolved = resolve_repo(source, ref=ref, settings=settings)
        from code_rag.agent import AgentTask, CodeAgent

        agent_obj = CodeAgent(settings=settings)
        report = agent_obj.run(AgentTask(task=task, resolved=resolved, plan_only=plan_only))
        if output is not None:
            from code_rag.agent import write_agent_report

            written = write_agent_report(report, output, fmt=report_format)
            console.print(f"[dim]Agent 报告已写入: {written}[/dim]")
    except FileNotFoundError as exc:
        console.print(f"[red]错误：{exc}[/red]")
        raise typer.Exit(1) from None
    except GitRepositoryError as exc:
        console.print(f"[red]Git 错误：{exc}[/red]")
        raise typer.Exit(1) from None
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None
    except Exception as exc:
        console.print(f"[red]错误: {exc}[/red]")
        if verbose:
            console.print_exception()
        raise typer.Exit(1) from None

    # 渲染报告
    _print_agent_report(report, top_k=top_k)


def _print_agent_report(report, top_k: int = 5) -> None:  # type: ignore[no-untyped-def]
    """Rich 渲染 :class:`AgentReport`。"""
    console.print("\n[bold]>> 任务理解[/bold]")
    console.print(f"  {report.understanding}")

    console.print("\n[bold]>> 计划拆解[/bold]")
    for i, step in enumerate(report.plan.steps, 1):
        console.print(f"  {i}. {step.question}")
        if step.rationale:
            console.print(f"     [dim]理由: {step.rationale}[/dim]")

    console.print("\n[bold]>> 关键文件[/bold]")
    if report.key_files:
        for fp in report.key_files:
            console.print(f"  - {fp}")
    else:
        console.print("  [dim](无)[/dim]")

    console.print("\n[bold]>> 修改建议（只读，不会自动应用）[/bold]")
    for change in report.suggested_changes:
        console.print(f"  - {change}")

    if report.risks:
        console.print("\n[bold]>> 风险点[/bold]")
        for risk in report.risks:
            console.print(f"  - {risk}")

    if report.suggested_tests:
        console.print("\n[bold]>> 建议运行的测试[/bold]")
        for test in report.suggested_tests:
            console.print(f"  - {test}")

    if report.references:
        console.print(f"\n[bold]>> 引用证据（最多 {top_k} 条/子问题）[/bold]")
        for ref in report.references[: top_k * max(1, len(report.plan.steps))]:
            console.print(f"  - {ref}")

    if report.insufficient_evidence:
        console.print("\n[yellow][!] 证据不足：请确认仓库已索引或补充任务描述[/yellow]")
    if report.review_note:
        console.print(f"\n[dim]Reviewer: {report.review_note}[/dim]")
    console.print()


# ---------------------------------------------------------------------------
# cache 子命令
# ---------------------------------------------------------------------------


@embeddings_app.command("list")
def embeddings_list() -> None:
    """列出内置 Embedding profiles。"""
    table = Table(title="Embedding Profiles", show_header=True, header_style="bold")
    table.add_column("Profile", style="cyan", no_wrap=True)
    table.add_column("Model", style="bold")
    table.add_column("Query Prefix", style="green")
    table.add_column("Document Prefix", style="green")
    table.add_column("Rationale")
    for profile in list_embedding_profiles():
        table.add_row(
            profile.profile_id,
            profile.model_name,
            profile.query_prefix or "-",
            profile.document_prefix or "-",
            profile.rationale,
        )
    console.print(table)


@cache_app.command("list")
def cache_list() -> None:
    """列出所有缓存的远程仓库。"""
    settings = get_settings()
    cache = CacheManager(settings.repo_cache_dir)
    entries = cache.list_entries()

    if not entries:
        console.print(f"[dim]无远程仓库缓存（根目录：{settings.repo_cache_path}）[/dim]")
        return

    table = Table(
        title=f"远程仓库缓存（{settings.repo_cache_path}）",
        show_header=True,
        header_style="bold",
    )
    table.add_column("URL", style="bold")
    table.add_column("Ref", style="green")
    table.add_column("Commit", style="dim")
    table.add_column("更新时间", style="dim")

    for entry in entries:
        url = redact_url(entry.canonical_url)
        if len(url) > 60:
            url = "..." + url[-57:]
        table.add_row(
            url,
            entry.ref or "-",
            (entry.commit or "-")[:12],
            entry.updated_at or "-",
        )
    console.print(table)


@cache_app.command("prune")
def cache_prune(
    confirm: bool = typer.Option(False, "--yes", "-y", help="跳过确认"),
) -> None:
    """清理所有远程仓库缓存。"""
    settings = get_settings()
    cache = CacheManager(settings.repo_cache_dir)
    entries = cache.list_entries()

    if not entries:
        console.print("[dim]无远程仓库缓存，无需清理[/dim]")
        return

    if not confirm:
        confirmed = typer.confirm(f"将删除 {len(entries)} 个远程仓库缓存，确定？")
        if not confirmed:
            console.print("[dim]已取消[/dim]")
            return

    removed = cache.prune()
    console.print(f"[bold green][OK] 已清理 {len(removed)} 个远程仓库缓存[/bold green]")


# ---------------------------------------------------------------------------
# 内部：展示索引结果
# ---------------------------------------------------------------------------


def _print_index_result(result) -> None:  # type: ignore[no-untyped-def]
    """打印索引结果摘要，对 git 仓库展示额外信息。"""
    if result.source_type == "git":
        console.print("  [cyan]类型[/cyan]: git")
        console.print(f"  [cyan]URL[/cyan]: {redact_url(result.canonical_source)}")
        if result.ref:
            console.print(f"  [cyan]Ref[/cyan]: {result.ref}")
        if result.commit:
            console.print(f"  [cyan]Commit[/cyan]: {result.commit}")
        if result.cache_path:
            console.print(f"  [cyan]缓存目录[/cyan]: {result.cache_path}")


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    app()
