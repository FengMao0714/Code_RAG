"""CLI 入口模块。

使用 typer + rich 提供美观的命令行交互界面。
"""

import logging
from pathlib import Path

import typer
from rich.console import Console
from rich.logging import RichHandler

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
    # TODO: 调用 indexer pipeline
    console.print("[bold green]✅ 索引完成！[/bold green]")


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
    # TODO: 调用 retriever + generator pipeline
    console.print("[dim]（功能开发中）[/dim]")


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

    while True:
        try:
            question = console.input("[bold green]> [/bold green]")
            if question.strip().lower() in ("exit", "quit", "q"):
                console.print("[dim]再见！[/dim]")
                break
            if not question.strip():
                continue
            # TODO: 调用 retriever + generator pipeline
            console.print("[dim]（功能开发中）[/dim]\n")
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]再见！[/dim]")
            break


@app.command(name="list")
def list_repos() -> None:
    """列出所有已索引的代码仓库。"""
    console.print("[bold blue]📋 已索引的仓库：[/bold blue]")
    # TODO: 从 index_tracker 读取
    console.print("[dim]（暂无已索引的仓库）[/dim]")


@app.command()
def status(
    repo_path: Path = typer.Argument(..., help="代码仓库路径"),
) -> None:
    """查看仓库的索引状态。"""
    repo_path = repo_path.resolve()
    console.print(f"[bold blue]📊 仓库索引状态: {repo_path}[/bold blue]")
    # TODO: 显示索引统计信息
    console.print("[dim]（功能开发中）[/dim]")


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

    # TODO: 删除 ChromaDB collection + tracker.json
    console.print(f"[bold green]✅ 已删除 {repo_path} 的索引数据[/bold green]")


if __name__ == "__main__":
    app()
