"""Create a source-only ZIP for the Linux central host; never include local data."""

from __future__ import annotations

import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parent.parent
SINGLE_FILES = (
    "requirements-central.txt",
    "docs/postgres-schema.sql",
    "docs/Caddyfile.example",
    "deploy/linux/mt5-dashboard.service",
    "deploy/linux/central.env.example",
    "deploy/linux/Caddyfile.ip-bootstrap.example",
    "deploy/linux/Caddyfile.ip.example",
    "deploy/linux/certbot-caddy-deploy-hook.sh",
)
CODE_DIRS = ("server", "collector", "shared")


def source_files() -> list[Path]:
    files = [ROOT / name for name in SINGLE_FILES]
    for directory in CODE_DIRS:
        files.extend((ROOT / directory).glob("*.py"))
    missing = [str(path.relative_to(ROOT)) for path in files if not path.is_file()]
    if missing:
        raise ValueError(f"missing deployment files: {', '.join(missing)}")
    return sorted(files)


def package(output: Path) -> int:
    if output.exists():
        raise ValueError(f"refusing to overwrite {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    files = source_files()
    try:
        with ZipFile(output, "x", ZIP_DEFLATED) as archive:
            for path in files:
                archive.write(path, path.relative_to(ROOT).as_posix())
    except Exception:
        output.unlink(missing_ok=True)
        raise
    return len(files)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="new ZIP path, such as dist/central-vps.zip")
    args = parser.parse_args()
    try:
        count = package(args.output)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(f"Created {args.output} with {count} source files")


if __name__ == "__main__":
    main()
