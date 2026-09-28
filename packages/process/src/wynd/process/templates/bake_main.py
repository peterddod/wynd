"""Zipapp bootstrap copied into a baked `.pyz` as `__main__.py` (PLAN §6.1; owner PROC-BAKE, M2; `$DRAFTS/04 §8.9`).

Standard library only. Reads `_wynd_bake/meta.json`, extracts the archive once to `$WYND_HOME/bake/<id>/` (temp dir,
exec bits restored, `.complete` marker, atomic rename), puts `site` first on `sys.path` and calls
`wynd.runtime.bake.main(plan_path, site, argv)`. pydantic-core is a compiled extension, so nothing can be imported
from the zip itself.
"""

import json
import os
import shutil
import sys
import zipfile
from pathlib import Path

META = "_wynd_bake/meta.json"
COMPLETE = ".complete"


def main() -> int:
    archive = Path(sys.argv[0]).resolve()
    with zipfile.ZipFile(archive) as zf:
        meta = json.loads(zf.read(META))
        home = Path(os.environ.get("WYND_HOME") or Path.home() / ".wynd")
        root = home / "bake" / meta["id"]
        if not (root / COMPLETE).exists():
            extract(zf, root)
    site = root / "site"
    sys.path.insert(0, str(site))
    from wynd.runtime.bake import main as run

    return run(root / "_wynd_bake" / "plan.json", site, sys.argv[1:])


def extract(zf: zipfile.ZipFile, root: Path) -> None:
    """Extract into a temp dir next to `root`, restore file modes, mark complete, rename into place. A concurrent
    extractor of the same id simply wins; a leftover incomplete `root` is replaced."""
    root.parent.mkdir(parents=True, exist_ok=True)
    tmp = root.parent / f".tmp-{root.name}-{os.getpid()}"
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        for info in zf.infolist():
            path = Path(zf.extract(info, tmp))
            mode = info.external_attr >> 16
            if mode and not info.is_dir():
                os.chmod(path, mode & 0o7777)
        (tmp / COMPLETE).write_text("")
        if root.exists() and not (root / COMPLETE).exists():
            shutil.rmtree(root)
        os.rename(tmp, root)
    except OSError:
        shutil.rmtree(tmp, ignore_errors=True)
        if not (root / COMPLETE).exists():
            raise


if __name__ == "__main__":
    raise SystemExit(main())
