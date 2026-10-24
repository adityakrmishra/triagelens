"""
src/ast_indexer.py
------------------
Python AST-based function extractor and ChromaDB semantic indexer.

Workflow:
  1. Walk every .py file in demo_target_repo/shopflow/ (skip __init__.py).
  2. For each FunctionDef / AsyncFunctionDef, extract metadata + source.
  3. Build a rich `search_document` that maps plain-language complaint text
     to the right function via all-MiniLM-L6-v2 embeddings stored in ChromaDB.

Public API:
  index_repository(repo_path, persist_dir) -> list[dict]
  search_suspect_functions(query_text, top_k, persist_dir) -> list[dict]
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

# ---------------------------------------------------------------------------
# ChromaDB collection constants
# ---------------------------------------------------------------------------
COLLECTION_NAME = "triagelens_functions"

# ---------------------------------------------------------------------------
# AST extraction helpers
# ---------------------------------------------------------------------------

def _snake_to_words(name: str) -> str:
    """Convert snake_case identifier to space-separated human words.

    Examples:
        apply_promo          -> "apply promo"
        resolve_free_shipping -> "resolve free shipping"
        check_magic_link_expiry -> "check magic link expiry"
        prorate_upgrade_charge  -> "prorate upgrade charge"
    """
    return re.sub(r"_+", " ", name).strip()


def _format_annotation(node: ast.expr | None) -> str:
    """Return a short textual representation of a type annotation node."""
    if node is None:
        return ""
    return ast.unparse(node)


def _build_signature(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Reconstruct a readable function signature string from the AST node."""
    args = func_node.args
    params: list[str] = []

    # Regular args (positional / keyword)
    for i, arg in enumerate(args.args):
        annotation = _format_annotation(arg.annotation)
        default_offset = len(args.args) - len(args.defaults)
        default_str = ""
        if i >= default_offset:
            d = args.defaults[i - default_offset]
            default_str = f"={ast.unparse(d)}"
        params.append(
            f"{arg.arg}: {annotation}{default_str}" if annotation
            else f"{arg.arg}{default_str}"
        )

    # *args
    if args.vararg:
        ann = _format_annotation(args.vararg.annotation)
        params.append(f"*{args.vararg.arg}: {ann}" if ann else f"*{args.vararg.arg}")

    # keyword-only args
    for i, arg in enumerate(args.kwonlyargs):
        annotation = _format_annotation(arg.annotation)
        d = args.kw_defaults[i]
        default_str = f"={ast.unparse(d)}" if d is not None else ""
        params.append(
            f"{arg.arg}: {annotation}{default_str}" if annotation
            else f"{arg.arg}{default_str}"
        )

    # **kwargs
    if args.kwarg:
        ann = _format_annotation(args.kwarg.annotation)
        params.append(f"**{args.kwarg.arg}: {ann}" if ann else f"**{args.kwarg.arg}")

    ret = ""
    if func_node.returns:
        ret = f" -> {_format_annotation(func_node.returns)}"

    prefix = "async def " if isinstance(func_node, ast.AsyncFunctionDef) else "def "
    return f"{prefix}{func_node.name}({', '.join(params)}){ret}"


def _extract_inline_comments(source_lines: list[str]) -> str:
    """Pull out all inline # comments from a list of source lines."""
    comments: list[str] = []
    for line in source_lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            comments.append(stripped.lstrip("# ").strip())
        elif "#" in line:
            # Inline comment after code
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
    """
    Build a rich, natural-language search document for a function.

    The document is designed so that plain-language customer complaints
    (e.g. "VIP25 not applied on sale items", "charged 99 shipping on 1000 order",
    "magic link expired after 30 seconds", "subscription upgrade overcharged")
    return the exact buggy function at Rank #1 when queried against ChromaDB.
    """
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

    # Lightweight source snippet (strip long comment blocks to save tokens)
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
    """
    Parse a single Python file and return a list of function metadata dicts.

    Args:
        py_file:   Absolute path to the .py file.
        repo_root: Absolute path to the repository root (for relative paths).

    Returns:
        List of dicts, one per FunctionDef / AsyncFunctionDef found.
    """
    source = py_file.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(source, filename=str(py_file))
    except SyntaxError as exc:
        print(f"[WARN] Syntax error in {py_file}: {exc}")
        return []

    source_lines = source.splitlines()

    # Relative path inside repo (forward slashes)
    try:
        rel_path = py_file.relative_to(repo_root).as_posix()
    except ValueError:
        rel_path = py_file.as_posix()

    module_name = py_file.stem   # e.g. "pricing"
    results: list[dict] = []

    # Iterate only top-level functions and methods inside top-level ClassDefs
    # (skipping nested closure functions)
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

        # Extract source lines (1-indexed -> 0-indexed)
        func_lines = source_lines[start_line - 1: end_line]
        raw_source = "\n".join(func_lines)

        # Dedent for readability
        func_source = textwrap.dedent(raw_source)

        docstring = ast.get_docstring(node) or ""

        # Param names (excluding self/cls)
        param_names = [
            a.arg for a in node.args.args
            if a.arg not in ("self", "cls")
        ]

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


# ---------------------------------------------------------------------------
# ChromaDB client factory
# ---------------------------------------------------------------------------

def _get_chroma_collection(persist_dir: str) -> Any:
    """Return (or create) the ChromaDB collection for TriageLens functions."""
    client = chromadb.PersistentClient(
        path=persist_dir,
        settings=Settings(anonymized_telemetry=False),
    )
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )
    return collection, client


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def index_repository(
    repo_path: str = "demo_target_repo",
    persist_dir: str = "./chroma_db",
) -> list[dict]:
    """
    Walk repo_path/shopflow/, parse every .py file (skip __init__.py),
    embed all functions, and upsert into ChromaDB.

    Args:
        repo_path:   Path to the target git repository root.
        persist_dir: Directory where ChromaDB persists its data.

    Returns:
        List of extracted function metadata dicts (one per function indexed).
    """
    repo_root = Path(repo_path).resolve()
    shopflow_dir = repo_root / "shopflow"

    if not shopflow_dir.exists():
        raise FileNotFoundError(
            f"shopflow/ directory not found inside {repo_root}. "
            "Run scripts/setup_demo_repo.py first."
        )

    print(f"[INFO] Indexing Python files in: {shopflow_dir}")

    all_functions: list[dict] = []
    for py_file in sorted(shopflow_dir.glob("*.py")):
        if py_file.name == "__init__.py":
            continue
        funcs = extract_functions_from_file(py_file, repo_root)
        print(f"[INFO]   {py_file.name}: {len(funcs)} function(s) extracted")
        all_functions.extend(funcs)

    if not all_functions:
        print("[WARN] No functions found to index.")
        return []

    print(f"[INFO] Total functions extracted: {len(all_functions)}")

    # ----- Embed search documents -----
    print("[INFO] Embedding search documents ...")
    docs = [f["search_document"] for f in all_functions]
    embeddings = embed_texts(docs)  # shape: (N, 384), normalised

    # ----- Upsert into ChromaDB -----
    collection, _ = _get_chroma_collection(persist_dir)

    ids        = [f["function_id"]   for f in all_functions]
    metadatas  = [
        {
            "function_name": f["function_name"],
            "file_path":     f["file_path"],
            "module_name":   f["module_name"],
            "start_line":    f["start_line"],
            "end_line":      f["end_line"],
            "signature":     f["signature"],
            "docstring":     f["docstring"][:500],  # cap for Chroma metadata
        }
        for f in all_functions
    ]

    # ChromaDB expects plain Python lists, not numpy arrays
    collection.upsert(
        ids=ids,
        embeddings=embeddings.tolist(),
        documents=[f["search_document"] for f in all_functions],
        metadatas=metadatas,
    )

    print(f"[OK]   Indexed {len(all_functions)} functions into ChromaDB "
          f"collection '{COLLECTION_NAME}' at '{persist_dir}'")

    return all_functions


def search_suspect_functions(
    query_text: str,
    top_k: int = 3,
    persist_dir: str = "./chroma_db",
) -> list[dict]:
    """
    Embed query_text and retrieve the top-K most semantically similar
    functions from ChromaDB.

    Args:
        query_text:  Plain-language complaint / cluster query text.
        top_k:       Number of results to return.
        persist_dir: ChromaDB persist directory (must already be indexed).

    Returns:
        List of result dicts, sorted by descending similarity, each with:
          - function_id, function_name, file_path, start_line, end_line,
            signature, docstring, similarity_score, match_reason.
    """
    collection, _ = _get_chroma_collection(persist_dir)

    # Return empty list immediately if invalid top_k or empty collection
    if top_k <= 0 or collection.count() == 0:
        return []

    n_results = min(top_k, collection.count())
    query_embedding = embed_texts([query_text])  # shape: (1, 384)

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
            "function_id":     func_id,
            "function_name":   meta.get("function_name", ""),
            "file_path":       meta.get("file_path", ""),
            "module_name":     meta.get("module_name", ""),
            "start_line":      meta.get("start_line", 0),
            "end_line":        meta.get("end_line", 0),
            "signature":       meta.get("signature", ""),
            "docstring":       meta.get("docstring", ""),
            "similarity_score": round(similarity, 4),
            # Heuristic match reason -- Phase 4 will enrich this with LLM reasoning
            "match_reason": (
                f"Semantic similarity {similarity:.2%} between cluster query and "
                f"'{meta.get('function_name', '')}' in "
                f"'{meta.get('file_path', '')}'. "
                f"Docstring: {meta.get('docstring', '')[:120]}..."
                if meta.get("docstring") else
                f"Semantic similarity {similarity:.2%} between cluster query and "
                f"'{meta.get('function_name', '')}' in '{meta.get('file_path', '')}'."
            ),
        })

    return results
