"""Release gate: move exactly the verified bytes into the shareable folder.

The staged file is linked into the output folder in one atomic step, and only
when its hash still equals the hash the verifier checked. Staging must be on
the same volume as the output folder; otherwise the move could not be atomic
and a crash could leave a partial file where it can be shared.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


class ReleaseRefused(Exception):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def same_volume(a: Path, b: Path) -> bool:
    return os.stat(a).st_dev == os.stat(b).st_dev


def release(staged: Path, verified_sha256: str, out_dir: Path, name: str) -> Path:
    staged, out_dir = Path(staged), Path(out_dir)
    if "/" in name or name.startswith("."):
        raise ReleaseRefused(f"invalid output name {name!r}")
    if not out_dir.is_dir():
        raise ReleaseRefused(f"output folder {out_dir} does not exist")
    if not same_volume(staged, out_dir):
        raise ReleaseRefused("staging and output folder are on different volumes")
    if sha256_file(staged) != verified_sha256:
        raise ReleaseRefused("file changed after verification")
    target = out_dir / name
    try:
        os.link(staged, target)  # atomic, and fails instead of overwriting
    except FileExistsError as exc:
        raise ReleaseRefused(f"{name} already exists in the output folder") from exc
    except OSError as exc:  # for example a volume without hard links
        raise ReleaseRefused(f"cannot place the file atomically: {exc.strerror or exc}") from exc
    if sha256_file(target) != verified_sha256:
        target.unlink(missing_ok=True)
        raise ReleaseRefused("released file does not match the verified hash")
    staged.unlink()
    os.chmod(target, 0o644)
    return target
