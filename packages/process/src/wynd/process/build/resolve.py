"""Host-side dependency resolution (PLAN §6.5; owner PROC-BUILD, M2; `$DRAFTS/04 §8.2`).

`UvResolver` runs `uv pip compile` (`--universal` for venv pins; `--python-platform … --only-binary :all:` for the
musl check) and `uv build --wheel` (`WYND_UV` overrides the executable). Failures raise `errors.ResolutionError`.
Both run with `--no-config` from a neutral directory, so no project or workspace settings leak into a resolution.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from .. import _proc
from ..errors import ResolutionError


class Resolver(Protocol):
    def compile(
        self,
        requirements: Sequence[str],
        *,
        universal: bool,
        python_version: str = "3.12",
        python_platform: str | None = None,
        only_binary: bool = False,
        find_links: Sequence[Path] = (),
    ) -> list[str]: ...                                  # full pinned set, one requirement per entry

    def build_wheel(self, src: Path, out_dir: Path) -> Path: ...


class UvResolver:
    def compile(
        self,
        requirements: Sequence[str],
        *,
        universal: bool,
        python_version: str = "3.12",
        python_platform: str | None = None,
        only_binary: bool = False,
        find_links: Sequence[Path] = (),
    ) -> list[str]:
        args = [_proc.require_tool("uv"), "pip", "compile", "-", "--no-config", "--no-header", "--no-annotate",
                "--python-version", python_version]
        if universal:
            args.append("--universal")
        if python_platform is not None:
            args += ["--python-platform", python_platform]
        if only_binary:
            args += ["--only-binary", ":all:"]
        for link in find_links:
            args += ["--find-links", str(link)]
        try:
            out = _proc.run(args, cwd=tempfile.gettempdir(), input="".join(f"{req}\n" for req in requirements))
        except subprocess.CalledProcessError as err:
            raise ResolutionError((err.stderr or "").strip() or f"uv pip compile exited {err.returncode}") from None
        return [line.strip() for line in out.splitlines() if line.strip() and not line.lstrip().startswith("#")]

    def build_wheel(self, src: Path, out_dir: Path) -> Path:
        """Build one wheel of the project at `src` into `out_dir` (replacing a wheel of the same name)."""
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix=".wheel-", dir=out_dir))
        try:
            try:
                _proc.run([_proc.require_tool("uv"), "build", "--wheel", "--no-config", "--out-dir", str(tmp),
                           str(src)], cwd=tmp)
            except subprocess.CalledProcessError as err:
                raise ResolutionError(f"cannot build a wheel of {src}: {(err.stderr or '').strip()}") from None
            built = sorted(tmp.glob("*.whl"))
            if len(built) != 1:
                raise ResolutionError(f"uv build of {src} produced {len(built)} wheels, expected one")
            dest = out_dir / built[0].name
            dest.unlink(missing_ok=True)
            shutil.move(built[0], dest)
            return dest
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
