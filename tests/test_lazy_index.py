import os
from pathlib import Path
import tempfile
import pytest

from vad.context.lazy_index import (
    LazyRepositoryIndex,
    LazyFileIndex,
    LazySymbol,
    SymbolKind,
)


def test_lazy_index_ast_extraction():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        sample_code = '''"""Sample module docstring."""

class Calculator:
    """A simple math calculator."""

    def add(self, a, b):
        """Adds two numbers."""
        return a + b

    def subtract(self, a, b):
        return a - b

def standalone_helper(x, y):
    """A helper function."""
    return x * y
'''
        file_path = tmp_path / "calc.py"
        file_path.write_text(sample_code, encoding="utf-8")

        index = LazyRepositoryIndex(root_dir=tmpdir)
        count = index.build_index()

        assert count == 1
        assert "calc.py" in index.files

        file_idx = index.files["calc.py"]
        symbols = file_idx.symbols

        # Check symbols: Calculator, Calculator.add, Calculator.subtract, standalone_helper
        sym_names = [s.name for s in symbols]
        assert "Calculator" in sym_names
        assert "Calculator.add" in sym_names
        assert "Calculator.subtract" in sym_names
        assert "standalone_helper" in sym_names

        # Check classes and kinds
        calc_class = next(s for s in symbols if s.name == "Calculator")
        assert calc_class.kind == SymbolKind.CLASS
        assert calc_class.docstring == "A simple math calculator."

        add_method = next(s for s in symbols if s.name == "Calculator.add")
        assert add_method.kind == SymbolKind.METHOD
        assert "self, a, b" in add_method.signature

        # Test expand symbol
        add_source = index.expand_symbol("Calculator.add")
        assert add_source is not None
        assert "def add(self, a, b):" in add_source
        assert "return a + b" in add_source

        # Test skeleton output
        skeleton = index.get_skeleton_tree()
        assert "File: calc.py" in skeleton
        assert "[class] Calculator" in skeleton
        assert "[method] Calculator.add" in skeleton

        # Test token economics estimate
        economics = index.estimate_token_economics()
        assert economics["indexed_files"] == 1
        assert economics["total_symbols"] == 4
        assert economics["tokens_saved"] >= 0
        assert "raw_prompt_cost_usd" in economics


def test_lazy_index_search_and_cache():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        (tmp_path / "foo.py").write_text("def find_me_secret():\n    pass\n", encoding="utf-8")
        (tmp_path / "bar.py").write_text("class TargetService:\n    def execute(self):\n        pass\n", encoding="utf-8")

        index = LazyRepositoryIndex(root_dir=tmpdir)
        index.build_index()

        # Search symbols
        found = index.search_symbols("secret")
        assert len(found) == 1
        assert found[0].name == "find_me_secret"

        found_svc = index.search_symbols("TargetService")
        assert len(found_svc) >= 1
        assert found_svc[0].name == "TargetService"

        # Cache persistence
        cache_file = tmp_path / "cache.json"
        index.save_cache(str(cache_file))
        assert cache_file.exists()

        loaded_index = LazyRepositoryIndex(root_dir=tmpdir)
        success = loaded_index.load_cache(str(cache_file))
        assert success is True
        assert "foo.py" in loaded_index.files
        assert "bar.py" in loaded_index.files
