#!/usr/bin/env python3
"""Bundle Codex and its command-execution companion for a disposable guest."""
from __future__ import annotations

import argparse
import gzip
from pathlib import Path
import subprocess
import tarfile


def bundle(binary: Path, output: Path) -> None:
    binary=binary.resolve(strict=True)
    sources={'codex':binary,'codex-code-mode-host':binary.with_name('codex-code-mode-host')}
    for name,path in sources.items():
        if not path.is_file() or not path.stat().st_mode & 0o111:
            raise ValueError('Missing executable: '+str(path))
    # Exclusive creation preserves existing evidence/payloads. Stable headers
    # make repeated bundles of the same executable bytes hash identically.
    try:
        raw=output.open('xb')
    except FileExistsError as error:
        raise ValueError('Output already exists: '+str(output)) from error
    with raw:
        with gzip.GzipFile(fileobj=raw,mode='wb',filename='',mtime=0) as compressed:
            with tarfile.open(fileobj=compressed,mode='w',format=tarfile.USTAR_FORMAT) as archive:
                for name,path in sources.items():
                    info=tarfile.TarInfo(name)
                    info.size=path.stat().st_size
                    info.mode=0o755
                    with path.open('rb') as stream:archive.addfile(info,stream)


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path)
    parser.add_argument('--binary',type=Path)
    args=parser.parse_args()
    binary=args.binary
    if binary is None:
        binary=Path(subprocess.check_output([str(Path(__file__).with_name('find-codex-binary.sh'))],text=True).strip())
    try:bundle(binary,args.output)
    except (OSError,ValueError) as error:parser.error(str(error))
    print(args.output.resolve())
    return 0


if __name__=='__main__':raise SystemExit(main())
