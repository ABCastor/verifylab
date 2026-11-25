"""`vl docs [NAME]`: print a document that ships with VerifyLab.

The skill is installed as a copy of its own folder, and it names documents of the repository as `docs/NAME.md`. A
reader who has the command and no checkout reads them here, so each text keeps its one home under `docs/`."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import verifylab

from ..output import emit, fail
from . import JSON_HELP


def docs_root() -> Path:
    """The `docs` folder next to the package: inside it in an installed wheel, at the root of a checkout otherwise."""
    package = Path(verifylab.__file__).resolve().parent
    for folder in (package / "docs", package.parents[1] / "docs"):
        if folder.is_dir() and any(folder.glob("*.md")):
            return folder
    raise FileNotFoundError("bundled documents not found next to the package; the installation is incomplete")


def documents() -> dict[str, Path]:
    """Name -> file of every Markdown document that ships: `GUIDE`, `adr/0009-clean-clone-replay`."""
    root = docs_root()
    return {path.relative_to(root).with_suffix("").as_posix(): path for path in sorted(root.rglob("*.md"))}


def find(name: str, known: dict[str, Path]) -> Path | None:
    """The document meant by `NAME`, `NAME.md` or `docs/NAME.md`, in any case. The name is looked up among the
    documents that ship and is never joined to a path."""
    wanted = name.removeprefix("docs/").removesuffix(".md").casefold()
    return next((path for known_name, path in known.items() if known_name.casefold() == wanted), None)


def title(path: Path) -> str:
    first = path.read_text(encoding="utf-8").split("\n", 1)[0]
    return first.lstrip("# ").strip()


def register(sub):
    parser = sub.add_parser("docs", help="print a document that ships with VerifyLab, or list them",
                            description="Print a document of VerifyLab's docs/ folder, for a reader who has the command "
                                        "and no checkout: the installed skill names them as docs/NAME.md.")
    parser.add_argument("name", nargs="?", metavar="NAME",
                        help="CHEATS, docs/CHEATS.md or adr/0009-clean-clone-replay; without it, the list")
    parser.add_argument("--json", action="store_true", help=JSON_HELP)
    parser.set_defaults(handler=run)
    return parser


def show(args, text: str, data: dict) -> int:
    """A reader may stop early (`vl docs SPEC | head`): a closed pipe ends the output and is no error."""
    try:
        emit(args, text, data)
        sys.stdout.flush()
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())      # nothing more to flush at exit
    return 0


def run(args) -> int:
    try:
        known = documents()
    except FileNotFoundError as exc:
        return fail(str(exc))
    if args.name is None:
        width = max(map(len, known))
        return show(args, "\n".join(f"{name.ljust(width)}  {title(path)}" for name, path in known.items()),
                    {"documents": [{"name": name, "title": title(path)} for name, path in known.items()]})
    path = find(args.name, known)
    if path is None:
        return fail(f"no document '{args.name}': `vl docs` lists the {len(known)} that ship")
    text = path.read_text(encoding="utf-8")
    return show(args, text, {"name": next(name for name, file in known.items() if file == path), "text": text})
