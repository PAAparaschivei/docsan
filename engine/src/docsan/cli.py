"""Command line: ``docsan run``, ``docsan verify`` and ``docsan classify``.

``run`` takes Word files, PDFs, PNG and JPEG images, or folders of them. A
PDF's pages and an image's masks need the owner's confirmation, which the
Mac app collects; on the command line such files stop at "needs review"."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .profile import LABELS, ProfileError, add_classifications
from .review import Lexicon

DEFAULT_PRIVATE = Path.home() / "Library" / "Application Support" / "DocSan"


def _run(args: argparse.Namespace) -> int:
    from .pipeline import run_batch

    lexicon_dir = Path(args.lexicon) if args.lexicon else Path(args.private) / "lexicon"
    try:
        lexicon = Lexicon.load(lexicon_dir)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        batch = run_batch(
            [Path(p) for p in args.inputs],
            Path(args.profile),
            Path(args.out),
            Path(args.private),
            lexicon=lexicon,
            remove_images=args.remove_images,
            label_unreadable=args.label_unreadable,
        )
    except ProfileError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    worst = 0
    for f in batch.files:
        if f.status == "ready":
            print(f"ready         {f.source} -> {f.output}")
        else:
            worst = 1
            print(f"{f.status.replace('_', ' '):<13} {f.source}")
            for reason in f.reasons:
                print(f"    {reason}")
            for item in f.review:
                print(f"    review {item.word!r}: {item.context}")
    print(f"private report: {batch.report_path}")
    if batch.summary_path:
        print(f"shareable summary: {batch.summary_path}")
    return worst


def _verify(args: argparse.Namespace) -> int:
    from .verify import verify_docx, verify_image, verify_pdf

    path = Path(args.file)
    suffix = path.suffix.lower()
    check = verify_pdf if suffix == ".pdf" else verify_image if suffix in (".png", ".jpg", ".jpeg") else verify_docx
    result = check(path, Path(args.profile))
    print(f"{result.status}  sha256 {result.sha256}")
    for line in result.findings + result.problems:
        print(f"    {line}")
    return 0 if result.passed else 1


def _classify(args: argparse.Namespace) -> int:
    company: dict[str, str] = {}
    safe: list[str] = []
    for item in args.decisions:
        word, sep, label = item.rpartition("=")
        if not sep or not word:
            print(f"error: expected WORD=LABEL or WORD=safe, got {item!r}", file=sys.stderr)
            return 2
        if label.lower() == "safe":
            safe.append(word)
        elif label.upper() in LABELS:
            company[word] = label.upper()
        else:
            print(f"error: {label!r} must be 'safe' or one of {sorted(LABELS)}", file=sys.stderr)
            return 2
    try:
        add_classifications(Path(args.profile), company, safe)
    except ProfileError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"recorded {len(company)} company and {len(safe)} safe decisions")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="docsan", description="Remove company references from documents, offline.")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="sanitize Word files, PDFs and images, or folders of them")
    run.add_argument("inputs", nargs="+")
    run.add_argument("--profile", required=True, help="private company profile (JSON)")
    run.add_argument("--out", required=True, help="shareable output folder")
    run.add_argument("--private", default=str(DEFAULT_PRIVATE), help="private staging and reports")
    run.add_argument("--lexicon", help="folder of word lists (default: <private>/lexicon)")
    run.add_argument("--remove-images", action="store_true", help="replace every picture and diagram with a label such as [IMAGE-REMOVED]")
    run.add_argument("--label-unreadable", action="store_true", help="keep every visual DocSan can read; label only the others")
    run.set_defaults(func=_run)

    ver = sub.add_parser("verify", help="check a sanitized Word file, PDF or image independently")
    ver.add_argument("file")
    ver.add_argument("--profile", required=True)
    ver.set_defaults(func=_verify)

    cls = sub.add_parser("classify", help="record review decisions in the profile")
    cls.add_argument("decisions", nargs="+", metavar="WORD=LABEL|safe")
    cls.add_argument("--profile", required=True)
    cls.set_defaults(func=_classify)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
