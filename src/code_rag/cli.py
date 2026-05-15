"""CLI 入口模块。

使用 typer + rich 提供美观的命令行交互界面。
"""

import logging
from pathlib import Path

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from code_rag.config import get_settings
from code_rag.generator import LLMClient
from code_rag.indexer import chunker as chunker_mod
from code_rag.indexer import embedder as embedder_mod
from code_rag.indexer import parser as parser_mod
from code_rag.indexer import scanner as scanner_mod
from code_rag.retriever import Retriever
from code_rag.store import index_tracker as tracker_mod
from code_rag.store import vector_store as store_mod

app = typer.Typer(
    name="code-rag",
    help="代码知识库 RAG 问答助手 — 基于代码仓库的智能问答 CLI 工具",
    add_completion=False,
)
console = Console()


def setup_logging(verbose: bool = False) -> None:
    """配置日志输出。"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )


@app.command()
def index(
    repo_path: Path = typer.Argument(..., help="代码仓库路径"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="显示详细日志"),
) -> None:
    """索引代码仓库（首次全量，后续增量更新）。"""
    setup_logging(verbose)
    repo_path = repo_path.resolve()

    if not repo_path.exists():
        console.print(f"[red]错误：路径不存在: {repo_path}[/red]")
        raise typer.Exit(1)

    console.print(f"[bold blue]📂 开始索引仓库: {repo_path}[/bold blue]")

    try:
        settings = get_settings()
        tracker = tracker_mod.IndexTracker(settings)
        store = store_mod.ChromaStore(settings)
        collection_name = store_mod.ChromaStore.get_collection_name(repo_path)

        # 1. 扫描文件
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("扫描文件...", total=None)
            file_scanner = scanner_mod.RepoScanner(repo_path)
            file_entries = file_scanner.scan()
            progress.update(task, description=f"扫描完成: {len(file_entries)} 个文件")

        # 2. 检测变更
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("检测变更...", total=None)
            changes = tracker.get_changes(repo_path, file_entries)
            added = len(changes.added)
            modified = len(changes.modified)
            deleted = len(changes.deleted)
            progress.update(
                task,
                description=f"变更检测: +{added} ~{modified} -{deleted}",
            )

        if not changes.has_changes:
            console.print("[bold green]✅ 仓库无变更，无需更新索引[/bold green]")
            return

        # 3. 删除已删除文件的 chunks
        if changes.deleted:
            deleted_paths = [entry.rel_path for entry in changes.deleted]
            store.delete_by_files(collection_name, deleted_paths)
            console.print(f"[dim]已删除 {len(deleted_paths)} 个文件的索引[/dim]")

        # 4. 解析 + 切片 + Embedding
        files_to_process = changes.added + changes.modified
        code_parser = parser_mod.CodeParser()
        chunker = chunker_mod.CodeChunker(max_chunk_tokens=settings.max_chunk_tokens)
        embedder = embedder_mod.Embedder.get_instance(settings)

        all_chunks = []
        all_embeddings = []

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("({task.completed}/{task.total})"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("解析代码...", total=len(files_to_process))

            for entry in files_to_process:
                progress.update(task, advance=0, description=f"解析: {entry.rel_path}")

                # 文档文件
                if entry.is_doc:
                    try:
                        source = entry.abs_path.read_text(encoding="utf-8", errors="replace")
                        from code_rag.indexer.parser import ParsedSymbol

                        doc_sym = ParsedSymbol(
                            file_path=entry.rel_path,
                            language="doc",
                            chunk_type="doc",
                            name=entry.rel_path,
                            start_line=1,
                            end_line=source.count("\n") + 1,
                            parent=None,
                            source=source,
                        )
                        file_chunks = chunker.chunk([doc_sym], entry.file_hash, full_source=source)
                    except Exception as exc:
                        console.print(f"[yellow]警告: 无法读取 {entry.rel_path}: {exc}[/yellow]")
                        progress.advance(task)
                        continue

                # 代码文件
                elif entry.is_code and entry.language:
                    symbols = code_parser.parse_file(entry.abs_path, entry.language, entry.rel_path)
                    if not symbols:
                        progress.advance(task)
                        continue
                    try:
                        source = entry.abs_path.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        source = None
                    file_chunks = chunker.chunk(symbols, entry.file_hash, full_source=source)
                else:
                    progress.advance(task)
                    continue

                all_chunks.extend(file_chunks)
                progress.advance(task)

        if not all_chunks:
            console.print("[yellow]警告: 未生成任何代码切片[/yellow]")
            tracker.update_tracker(repo_path, file_entries)
            return

        # 5. 生成 Embedding
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(f"生成 Embedding ({len(all_chunks)} 个切片)...", total=None)
            texts = [chunk.source for chunk in all_chunks]
            all_embeddings = embedder.embed_texts(texts)
            progress.update(task, description=f"Embedding 完成: {len(all_embeddings)} 个向量")

        # 6. 写入 ChromaDB
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("写入向量数据库...", total=None)
            store.upsert_chunks(collection_name, all_chunks, all_embeddings)
            progress.update(task, description="写入完成")

        # 7. 更新追踪记录
        tracker.update_tracker(repo_path, file_entries)

        console.print(
            f"[bold green]✅ 索引完成！[/bold green] "
            f"处理 {len(files_to_process)} 个文件，生成 {len(all_chunks)} 个切片"
        )

    except Exception as exc:
        console.print(f"[red]错误: {exc}[/red]")
        if verbose:
            console.print_exception()
        raise typer.Exit(1)


@app.command()
def ask(
    repo_path: Path = typer.Argument(..., help="代码仓库路径"),
    question: str = typer.Argument(..., help="你的问题"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="显示详细日志"),
) -> None:
    """对已索引的代码仓库提问。"""
    setup_logging(verbose)
    repo_path = repo_path.resolve()

    console.print(f"[bold blue]🔍 正在检索: {question}[/bold blue]")

    try:
        settings = get_settings()

        # 检索
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("检索相关代码...", total=None)
            retriever = Retriever(settings)
            result = retriever.retrieve_with_context(question, repo_path)
            progress.update(task, description=f"检索到 {len(result.chunks)} 个相关片段")

        if not result.context:
            console.print("[yellow]未找到相关代码，请确认仓库已索引[/yellow]")
            return

        # 生成回答（流式输出）
        console.print("\n[bold]💬 回答：[/bold]\n")
        llm = LLMClient(settings)
        for chunk in llm.generate_stream(result.context, question):
            console.print(chunk.content, end="")

        console.print("\n")

    except ValueError as exc:
        console.print(f"[red]配置错误: {exc}[/red]")
        raise typer.Exit(1)
    except RuntimeError as exc:
        console.print(f"[red]LLM 调用失败: {exc}[/red]")
        raise typer.Exit(1)
    except Exception as exc:
        console.print(f"[red]错误: {exc}[/red]")
        if verbose:
            console.print_exception()
        raise typer.Exit(1)


@app.command()
def chat(
    repo_path: Path = typer.Argument(..., help="代码仓库路径"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="显示详细日志"),
) -> None:
    """进入交互式对话模式。"""
    setup_logging(verbose)
    repo_path = repo_path.resolve()

    console.print(f"[bold blue]💬 进入交互模式 — 仓库: {repo_path}[/bold blue]")
    console.print("[dim]输入 'exit' 或 'quit' 退出[/dim]\n")

    try:
        settings = get_settings()
        retriever = Retriever(settings)
        llm = LLMClient(settings)
    except Exception as exc:
        console.print(f"[red]初始化失败: {exc}[/red]")
        raise typer.Exit(1)

    while True:
        try:
            question = console.input("[bold green]> [/bold green]")
            if question.strip().lower() in ("exit", "quit", "q"):
                console.print("[dim]再见！[/dim]")
                break
            if not question.strip():
                continue

            # 检索
            result = retriever.retrieve_with_context(question, repo_path)

            if not result.context:
                console.print("[yellow]未找到相关代码[/yellow]\n")
                continue

            # 流式生成
            console.print()
            for chunk in llm.generate_stream(result.context, question):
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

    # 遍历 indexes 目录
    indexes_dir = settings.index_tracker_path
    if not indexes_dir.exists():
        console.print("[dim]暂无已索引的仓库[/dim]")
        return

    repos = []
    for hash_dir in indexes_dir.iterdir():
        tracker_file = hash_dir / "tracker.json"
        if tracker_file.is_file():
            import json

            try:
                data = json.loads(tracker_file.read_text(encoding="utf-8"))
                repos.append((hash_dir.name, len(data)))
            except Exception:
                continue

    if not repos:
        console.print("[dim]暂无已索引的仓库[/dim]")
        return

    console.print("[bold blue]📋 已索引的仓库：[/bold blue]")
    for hash_name, file_count in repos:
        console.print(f"  • {hash_name} ({file_count} 个文件)")


@app.command()
def status(
    repo_path: Path = typer.Argument(..., help="代码仓库路径"),
) -> None:
    """查看仓库的索引状态。"""
    repo_path = repo_path.resolve()
    console.print(f"[bold blue]📊 仓库索引状态: {repo_path}[/bold blue]")

    settings = get_settings()
    store = store_mod.ChromaStore(settings)
    collection_name = store_mod.ChromaStore.get_collection_name(repo_path)

    # 向量库统计
    stats = store.get_stats(collection_name)
    if not stats.get("exists"):
        console.print("[yellow]该仓库尚未索引[/yellow]")
        return

    console.print(f"  仓库路径: {repo_path}")
    console.print(f"  Collection: {collection_name}")
    console.print(f"  总切片数: {stats['total_chunks']}")

    if stats.get("chunk_types"):
        console.print("  切片类型分布:")
        for ctype, count in stats["chunk_types"].items():
            console.print(f"    • {ctype}: {count}")


@app.command()
def remove(
    repo_path: Path = typer.Argument(..., help="代码仓库路径"),
    confirm: bool = typer.Option(False, "--yes", "-y", help="跳过确认"),
) -> None:
    """删除仓库的索引数据。"""
    repo_path = repo_path.resolve()

    if not confirm:
        confirmed = typer.confirm(f"确定要删除 {repo_path} 的索引数据吗？")
        if not confirmed:
            console.print("[dim]已取消[/dim]")
            return

    settings = get_settings()
    store = store_mod.ChromaStore(settings)
    collection_name = store_mod.ChromaStore.get_collection_name(repo_path)

    # 删除 ChromaDB collection
    store.delete_collection(collection_name)

    # 删除 tracker 文件
    hash_suffix = collection_name.replace("code-rag-", "")
    tracker_path = settings.index_tracker_path / hash_suffix
    import shutil

    if tracker_path.exists():
        shutil.rmtree(tracker_path)

    console.print(f"[bold green]✅ 已删除 {repo_path} 的索引数据[/bold green]")


if __name__ == "__main__":
    app()
