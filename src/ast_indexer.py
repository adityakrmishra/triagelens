"""
Python AST function indexer and ChromaDB semantic search engine.
"""

from __future__ import annotations

import ast
import re
import textwrap
from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings

from src.embedder import embed_texts

COLLECTION_NAME = "triagelens_functions"


def _snake_to_words(name: str) -> str:
    return re.sub(r"_+", " ", name).strip()


def _format_annotation(node: ast.expr | None) -> str:
    if node is None:
        return ""
    return ast.unparse(node)


def _build_signature(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = func_node.args
    params: list[str] = []

    for i, arg in enumerate(args.args):
        annotation = _format_annotation(arg.annotation)
        default_offset = len(args.args) - len(args.defaults)
        default_str = ""
        if i >= default_offset:
            d = args.defaults[i - default_offset]
            default_str = f"={ast.unparse(d)}"
        params.append(
            f"{arg.arg}: {annotation}{default_str}" if annotation else f"{arg.arg}{default_str}"
        )

    if args.vararg:
        ann = _format_annotation(args.vararg.annotation)
        params.append(f"*{args.vararg.arg}: {ann}" if ann else f"*{args.vararg.arg}")

    for i, arg in enumerate(args.kwonlyargs):
        annotation = _format_annotation(arg.annotation)
        d = args.kw_defaults[i]
        default_str = f"={ast.unparse(d)}" if d is not None else ""
        params.append(
            f"{arg.arg}: {annotation}{default_str}" if annotation else f"{arg.arg}{default_str}"
        )

    if args.kwarg:
        ann = _format_annotation(args.kwarg.annotation)
        params.append(f"**{args.kwarg.arg}: {ann}" if ann else f"**{args.kwarg.arg}")

    ret = f" -> {_format_annotation(func_node.returns)}" if func_node.returns else ""
    prefix = "async def " if isinstance(func_node, ast.AsyncFunctionDef) else "def "
    return f"{prefix}{func_node.name}({', '.join(params)}){ret}"


def _extract_inline_comments(source_lines: list[str]) -> str:
    comments: list[str] = []
    for line in source_lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            comments.append(stripped.lstrip("# ").strip())
        elif "#" in line:
            comment_part = line[line.index("#"):].lstrip("# ").strip()
            if comment_part:
                comments.append(comment_part)
    return " ".join(comments)


def _build_search_document(
    func_name: str,
    module_name: str,
    param_names: list[str],
    docstring: str,
    inline_comments: str,
    source_code: str,
) -> str:
    human_name = _snake_to_words(func_name)
    human_module = _snake_to_words(module_name)

    parts: list[str] = [
        f"Function: {human_name}",
        f"Module: {human_module}",
    ]

    if param_names:
        parts.append(f"Parameters: {', '.join(_snake_to_words(p) for p in param_names)}")

    if docstring:
        parts.append(f"Description: {docstring}")

    if inline_comments:
        parts.append(f"Notes: {inline_comments}")

    condensed = "\n".join(
        line for line in source_code.splitlines()
        if not line.strip().startswith("#") or len(line.strip()) < 60
    )
    parts.append(f"Source:\n{condensed}")

    return "\n\n".join(parts)


def extract_functions_from_file(
    py_file: Path,
    repo_root: Path,
) -> list[dict]:
    source = py_file.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(source, filename=str(py_file))
    except SyntaxError as exc:
        print(f"[WARN] Syntax error in {py_file}: {exc}")
        return []

    source_lines = source.splitlines()

    try:
        rel_path = py_file.relative_to(repo_root).as_posix()
    except ValueError:
        rel_path = py_file.as_posix()

    module_name = py_file.stem
    results: list[dict] = []

    nodes_to_process: list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, str | None]] = []
    for top_node in tree.body:
        if isinstance(top_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            nodes_to_process.append((top_node, None))
        elif isinstance(top_node, ast.ClassDef):
            for class_item in top_node.body:
                if isinstance(class_item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    nodes_to_process.append((class_item, top_node.name))

    for node, parent_class in nodes_to_process:
        func_name = f"{parent_class}.{node.name}" if parent_class else node.name
        start_line = node.lineno
        end_line = node.end_lineno or start_line

        # AST line-span extraction: convert 1-indexed AST lines to 0-indexed slice
        func_lines = source_lines[start_line - 1: end_line]
        raw_source = "\n".join(func_lines)
        func_source = textwrap.dedent(raw_source)

        docstring = ast.get_docstring(node) or ""
        param_names = [a.arg for a in node.args.args if a.arg not in ("self", "cls")]
        signature = _build_signature(node)
        inline_comments = _extract_inline_comments(func_lines)

        search_doc = _build_search_document(
            func_name=func_name,
            module_name=module_name,
            param_names=param_names,
            docstring=docstring,
            inline_comments=inline_comments,
            source_code=func_source,
        )

        function_id = f"{rel_path}::{func_name}"

        results.append({
            "function_id": function_id,
            "function_name": func_name,
            "file_path": rel_path,
            "module_name": module_name,
            "start_line": start_line,
            "end_line": end_line,
            "signature": signature,
            "docstring": docstring,
            "source_code": func_source,
            "search_document": search_doc,
            "param_names": param_names,
        })

    return results


def _get_chroma_collection(persist_dir: str) -> tuple[Any, Any]:
    client = chromadb.PersistentClient(
        path=persist_dir,
        settings=Settings(anonymized_telemetry=False),
    )
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )
    return collection, client


def index_repository(
    repo_path: str = "demo_target_repo",
    persist_dir: str = "./chroma_db",
) -> list[dict]:
    repo_root = Path(repo_path).resolve()
    shopflow_dir = repo_root / "shopflow"

    if not shopflow_dir.exists():
        raise FileNotFoundError(
            f"shopflow/ directory not found inside {repo_root}. "
            "Run scripts/setup_demo_repo.py first."
        )

    all_functions: list[dict] = []
    for py_file in sorted(shopflow_dir.glob("*.py")):
        if py_file.name == "__init__.py":
            continue
        funcs = extract_functions_from_file(py_file, repo_root)
        all_functions.extend(funcs)

    if not all_functions:
        return []

    docs = [f["search_document"] for f in all_functions]
    embeddings = embed_texts(docs)

    collection, _ = _get_chroma_collection(persist_dir)

    ids = [f["function_id"] for f in all_functions]
    metadatas = [
        {
            "function_name": f["function_name"],
            "file_path": f["file_path"],
            "module_name": f["module_name"],
            "start_line": f["start_line"],
            "end_line": f["end_line"],
            "signature": f["signature"],
            "docstring": f["docstring"][:500],
        }
        for f in all_functions
    ]

    collection.upsert(
        ids=ids,
        embeddings=embeddings.tolist(),
        documents=[f["search_document"] for f in all_functions],
        metadatas=metadatas,
    )

    return all_functions


def search_suspect_functions(
    query_text: str,
    top_k: int = 3,
    persist_dir: str = "./chroma_db",
) -> list[dict]:
    collection, _ = _get_chroma_collection(persist_dir)

    if top_k <= 0 or collection.count() == 0:
        return []

    n_results = min(top_k, collection.count())
    query_embedding = embed_texts([query_text])

    raw = collection.query(
        query_embeddings=query_embedding.tolist(),
        n_results=n_results,
        include=["metadatas", "distances", "documents"],
    )

    results: list[dict] = []
    if not raw["ids"] or not raw["ids"][0]:
        return results

    for i, func_id in enumerate(raw["ids"][0]):
        meta = raw["metadatas"][0][i]
        # ChromaDB cosine space returns distance = 1 - cosine_similarity
        distance = raw["distances"][0][i]
        similarity = max(0.0, min(1.0, 1.0 - distance))

        results.append({
            "function_id": func_id,
            "function_name": meta.get("function_name", ""),
            "file_path": meta.get("file_path", ""),
            "module_name": meta.get("module_name", ""),
            "start_line": meta.get("start_line", 0),
            "end_line": meta.get("end_line", 0),
            "signature": meta.get("signature", ""),
            "docstring": meta.get("docstring", ""),
            "similarity_score": round(similarity, 4),
            "match_reason": (
                f"Semantic similarity {similarity:.2%} between cluster query and "
                f"'{meta.get('function_name', '')}' in '{meta.get('file_path', '')}'."
            ),
        })

    return results
