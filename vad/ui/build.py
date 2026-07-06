from __future__ import annotations

import argparse
import shutil
from pathlib import Path


STATIC_ROOT = Path(__file__).parent / "static"
REQUIRED_FILES = ("index.html", "styles.css", "app.js")


def build_ui(out_dir: Path) -> list[Path]:
    missing = [name for name in REQUIRED_FILES if not (STATIC_ROOT / name).is_file()]
    if missing:
        raise FileNotFoundError(f"missing UI assets: {', '.join(missing)}")

    out_dir.mkdir(parents=True, exist_ok=True)
    copied = []
    for name in REQUIRED_FILES:
        source = STATIC_ROOT / name
        target = out_dir / name
        shutil.copyfile(source, target)
        copied.append(target)

    _validate_build(out_dir)
    return copied


def _validate_build(out_dir: Path) -> None:
    index = (out_dir / "index.html").read_text(encoding="utf-8")
    for name in ("styles.css", "app.js"):
        if name not in index:
            raise ValueError(f"index.html does not reference {name}")
        if not (out_dir / name).is_file():
            raise FileNotFoundError(name)
    required_mounts = ("run-list", "evidence-detail", "approval-panel")
    for mount in required_mounts:
        if f'id="{mount}"' not in index:
            raise ValueError(f"index.html is missing {mount}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m vad.ui.build")
    parser.add_argument("--out", required=True, help="Directory to write built UI assets")
    args = parser.parse_args()
    copied = build_ui(Path(args.out))
    for path in copied:
        print(path)


if __name__ == "__main__":
    main()

