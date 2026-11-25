"""Writing files so that a reader never finds half of one."""

from __future__ import annotations

import fcntl
import os
import shutil
from pathlib import Path


def replace_files(folder: Path, texts: dict[str, str]) -> dict[str, int]:
    """Put a set of files in `folder` as one change. Every file is staged beside its place first, so a failure while
    staging changes nothing; a failure while putting them in place puts back the files already replaced (or removes
    the new ones), so the folder keeps its previous set; and the whole runs under a lock on the folder, so two writers
    never leave one's file beside the other's. Returns the size in bytes of each file written."""
    staged: dict[str, tuple[Path, int]] = {}
    kept: dict[str, Path | None] = {}            # name -> a copy of the file it replaces, None when there was none
    placed: list[str] = []
    lock = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for name, text in texts.items():
            data = text.encode("utf-8")
            scratch = folder / f".{name}.{os.getpid()}.tmp"
            staged[name] = (scratch, len(data))
            scratch.write_bytes(data)
        for name in texts:
            final, copy = folder / name, folder / f".{name}.{os.getpid()}.old"
            kept[name] = None
            if final.is_file():
                try:
                    os.link(final, copy)
                except OSError:
                    shutil.copy2(final, copy)
                kept[name] = copy
        try:
            for name, (scratch, _) in staged.items():
                os.replace(scratch, folder / name)
                placed.append(name)
        except BaseException:
            for name in placed:
                if kept[name] is not None:
                    os.replace(kept[name], folder / name)
                else:
                    (folder / name).unlink(missing_ok=True)
            raise
    finally:
        for scratch, _ in staged.values():
            scratch.unlink(missing_ok=True)
        for copy in kept.values():
            if copy is not None:
                copy.unlink(missing_ok=True)
        os.close(lock)
    return {name: size for name, (_, size) in staged.items()}
