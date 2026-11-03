"""Copy Tesseract and the libraries it links into the engine bundle.

Called by bundle-engine.sh with the bundle folder and the language data folder
that fetch-tessdata.sh prepared. Takes the Homebrew build of Tesseract, copies
every non-system library it needs into <bundle>/lib, points each reference at
the copy and adds the English and French language data to <bundle>/tessdata.
Signing happens in bundle-engine.sh.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

PREFIX = Path(os.environ.get("HOMEBREW_PREFIX", "/opt/homebrew"))


def references(path: Path) -> list[str]:
    out = subprocess.run(["otool", "-L", str(path)], capture_output=True, text=True, check=True).stdout
    return [line.strip().split(" (")[0] for line in out.splitlines()[1:]]


def resolve(ref: str, owner: Path) -> Path | None:
    if ref.startswith(str(PREFIX)):
        return Path(ref).resolve()
    if ref.startswith(("@rpath/", "@loader_path/")):
        name = ref.split("/", 1)[1]
        for folder in (owner.resolve().parent, PREFIX / "lib"):
            if (folder / name).exists():
                return (folder / name).resolve()
        raise SystemExit(f"cannot resolve {ref} for {owner}")
    return None  # a system library


def main(bundle: Path, languages: Path) -> None:
    tesseract = (PREFIX / "bin" / "tesseract").resolve()
    if not tesseract.exists():
        raise SystemExit("Tesseract is not installed: brew install tesseract")
    bin_dir, lib_dir, data_dir = bundle / "bin", bundle / "lib", bundle / "tessdata"
    for d in (bin_dir, lib_dir, data_dir):
        d.mkdir(parents=True, exist_ok=True)
    copies: dict[Path, Path] = {tesseract: bin_dir / "tesseract"}
    todo = [tesseract]
    while todo:
        owner = todo.pop()
        for ref in references(owner):
            real = resolve(ref, owner)
            if real is not None and real not in copies:
                copies[real] = lib_dir / Path(ref).name
                todo.append(real)
    for real, copy in copies.items():
        shutil.copyfile(real, copy)
        os.chmod(copy, 0o755)
    for real, copy in copies.items():
        args = []
        for ref in references(copy):
            target = resolve(ref, real)
            if target is not None:
                base = copies[target].name
                args += ["-change", ref, f"@executable_path/../lib/{base}" if copy.parent == bin_dir else f"@loader_path/{base}"]
        if copy.parent == lib_dir:
            args += ["-id", f"@loader_path/{copy.name}"]
        if args:
            subprocess.run(["install_name_tool", *args, str(copy)], check=True, capture_output=True)
    for lang in ("eng", "fra"):
        source = languages / f"{lang}.traineddata"
        if not source.exists():
            raise SystemExit(f"missing {source}: run scripts/fetch-tessdata.sh")
        shutil.copyfile(source, data_dir / source.name)
    print(f"tesseract: {len(copies) - 1} libraries, languages eng+fra")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
