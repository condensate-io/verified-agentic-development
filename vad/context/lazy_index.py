"""Lazy Indexing Pattern: Skeletal AST symbol indexing with on-demand expansion for context token economics."""

import ast
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, Field


class SymbolKind(str, Enum):
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    IMPORT = "import"
    VARIABLE = "variable"


class LazySymbol(BaseModel):
    name: str
    kind: SymbolKind
    line_start: int
    line_end: int
    signature: Optional[str] = None
    docstring: Optional[str] = None
    file_path: str = ""


class LazyFileIndex(BaseModel):
    file_path: str
    relative_path: str
    file_hash: str
    size_bytes: int
    language: str = "python"
    symbols: List[LazySymbol] = Field(default_factory=list)

    def read_content(self) -> str:
        """Loads and returns the full file content on-demand."""
        try:
            with open(self.file_path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception as e:
            return f"# Error reading file {self.file_path}: {e}"

    def get_symbol_source(self, symbol_name: str) -> Optional[str]:
        """Extracts the slice of source code corresponding to a specific symbol on-demand."""
        target = next((s for s in self.symbols if s.name == symbol_name), None)
        if not target:
            return None

        lines = self.read_content().splitlines()
        start = max(0, target.line_start - 1)
        end = min(len(lines), target.line_end)
        return "\n".join(lines[start:end])

    def get_skeleton(self) -> str:
        """Renders a token-efficient skeletal outline without function bodies."""
        out = [f"File: {self.relative_path}"]
        for sym in self.symbols:
            sig = f"({sym.signature})" if sym.signature else ""
            doc = f' - "{sym.docstring[:60]}..."' if sym.docstring else ""
            out.append(f"  [{sym.kind.value}] {sym.name}{sig}{doc} (L{sym.line_start}-L{sym.line_end})")
        return "\n".join(out)


class PythonAstVisitor(ast.NodeVisitor):
    """Parses AST nodes to extract lightweight symbol definitions."""

    def __init__(self, file_path: str):
        self.file_path = file_path
        self.symbols: List[LazySymbol] = []
        self._current_class: Optional[str] = None

    def visit_ClassDef(self, node: ast.ClassDef):
        doc = ast.get_docstring(node)
        self.symbols.append(
            LazySymbol(
                name=node.name,
                kind=SymbolKind.CLASS,
                line_start=node.lineno,
                line_end=getattr(node, "end_lineno", node.lineno),
                docstring=doc.split("\n")[0] if doc else None,
                file_path=self.file_path,
            )
        )
        old_class = self._current_class
        self._current_class = node.name
        self.generic_visit(node)
        self._current_class = old_class

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._process_func(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._process_func(node)

    def _process_func(self, node):
        doc = ast.get_docstring(node)
        kind = SymbolKind.METHOD if self._current_class else SymbolKind.FUNCTION
        full_name = f"{self._current_class}.{node.name}" if self._current_class else node.name

        args = [a.arg for a in node.args.args]
        sig = ", ".join(args)

        self.symbols.append(
            LazySymbol(
                name=full_name,
                kind=kind,
                line_start=node.lineno,
                line_end=getattr(node, "end_lineno", node.lineno),
                signature=sig,
                docstring=doc.split("\n")[0] if doc else None,
                file_path=self.file_path,
            )
        )


class LazyRepositoryIndex:
    """Repository-wide lazy AST index that avoids loading raw file contents into prompt context."""

    DEFAULT_EXCLUDES: Set[str] = {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        "node_modules",
        ".egg-info",
        "dist",
        "build",
        ".pytest_cache",
    }

    def __init__(self, root_dir: str = "."):
        self.root_dir = Path(root_dir).resolve()
        self.files: Dict[str, LazyFileIndex] = {}

    def build_index(
        self,
        include_exts: Optional[List[str]] = None,
        exclude_dirs: Optional[Set[str]] = None,
    ) -> int:
        """Scans the repository and builds a skeletal index of symbols."""
        if include_exts is None:
            include_exts = [".py"]
        excludes = exclude_dirs or self.DEFAULT_EXCLUDES

        indexed_count = 0
        for current_root, dirs, filenames in os.walk(self.root_dir):
            dirs[:] = [d for d in dirs if d not in excludes and not d.endswith(".egg-info")]

            for fn in filenames:
                ext = Path(fn).suffix.lower()
                if ext not in include_exts:
                    continue

                abs_path = Path(current_root) / fn
                rel_path = abs_path.relative_to(self.root_dir).as_posix()

                try:
                    content_bytes = abs_path.read_bytes()
                    file_hash = hashlib.sha256(content_bytes).hexdigest()
                    size_bytes = len(content_bytes)

                    # Check if cached and unchanged
                    cached = self.files.get(rel_path)
                    if cached and cached.file_hash == file_hash:
                        indexed_count += 1
                        continue

                    symbols: List[LazySymbol] = []
                    if ext == ".py":
                        try:
                            tree = ast.parse(content_bytes.decode("utf-8", errors="replace"))
                            visitor = PythonAstVisitor(abs_path.as_posix())
                            visitor.visit(tree)
                            symbols = visitor.symbols
                        except Exception:
                            symbols = []

                    self.files[rel_path] = LazyFileIndex(
                        file_path=abs_path.as_posix(),
                        relative_path=rel_path,
                        file_hash=file_hash,
                        size_bytes=size_bytes,
                        language="python" if ext == ".py" else ext.lstrip("."),
                        symbols=symbols,
                    )
                    indexed_count += 1
                except Exception:
                    continue

        return indexed_count

    def get_skeleton_tree(self) -> str:
        """Returns a compact textual overview of all indexed files and symbols."""
        blocks = []
        for rel_path in sorted(self.files.keys()):
            f = self.files[rel_path]
            blocks.append(f.get_skeleton())
        return "\n\n".join(blocks)

    def expand_symbol(self, symbol_name: str) -> Optional[str]:
        """Finds and materializes the full source text for the requested symbol on-demand."""
        for file_idx in self.files.values():
            src = file_idx.get_symbol_source(symbol_name)
            if src is not None:
                return src
        return None

    def expand_file(self, relative_path: str) -> Optional[str]:
        """Materializes the full content for a specific file on-demand."""
        file_idx = self.files.get(relative_path)
        if not file_idx:
            return None
        return file_idx.read_content()

    def search_symbols(self, query: str) -> List[LazySymbol]:
        """Searches symbols matching the given query string."""
        q = query.lower()
        results = []
        for file_idx in self.files.values():
            for sym in file_idx.symbols:
                if q in sym.name.lower() or (sym.docstring and q in sym.docstring.lower()):
                    results.append(sym)
        return results

    def estimate_token_economics(self) -> Dict[str, Any]:
        """Calculates token counts and percentage reduction achieved by skeletal lazy indexing."""
        total_raw_chars = sum(f.size_bytes for f in self.files.values())
        skeleton_text = self.get_skeleton_tree()
        skeleton_chars = len(skeleton_text)

        # Approximate 4 chars per token
        raw_tokens = max(1, total_raw_chars // 4)
        skeleton_tokens = max(1, skeleton_chars // 4)
        savings_tokens = max(0, raw_tokens - skeleton_tokens)
        reduction_pct = round((savings_tokens / raw_tokens) * 100, 2)

        # Baseline cost calculation ($3.00 / 1M prompt tokens for Claude 3.5 Sonnet)
        raw_cost_usd = round((raw_tokens / 1_000_000.0) * 3.00, 4)
        skeleton_cost_usd = round((skeleton_tokens / 1_000_000.0) * 3.00, 4)
        savings_cost_usd = round(raw_cost_usd - skeleton_cost_usd, 4)

        return {
            "indexed_files": len(self.files),
            "total_symbols": sum(len(f.symbols) for f in self.files.values()),
            "raw_tokens_estimate": raw_tokens,
            "skeleton_tokens_estimate": skeleton_tokens,
            "tokens_saved": savings_tokens,
            "reduction_percentage": reduction_pct,
            "raw_prompt_cost_usd": raw_cost_usd,
            "skeleton_prompt_cost_usd": skeleton_cost_usd,
            "prompt_cost_savings_usd": savings_cost_usd,
        }

    def save_cache(self, cache_path: str) -> None:
        """Saves the index to disk for rapid subsequent loads."""
        p = Path(cache_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {rel_path: f.model_dump() for rel_path, f in self.files.items()}
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def load_cache(self, cache_path: str) -> bool:
        """Loads index from disk cache if it exists."""
        p = Path(cache_path)
        if not p.exists():
            return False
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.files = {rel_path: LazyFileIndex(**f_data) for rel_path, f_data in data.items()}
            return True
        except Exception:
            return False
