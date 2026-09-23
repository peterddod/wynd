"""The build job, split into prepare and finalize for Kubernetes (PLAN §6.5; owner PROC-BUILD, M2).

`prepare_build_job` validates, checks the design phase, computes closure-HEAD `C`, gates on passing tests at `C`,
chooses the base, assigns edge venvs, resolves host-side, builds step wheels, writes the env manifest,
`process.lock.yaml`, the Dockerfile and `image.ref` into the build context (`ctx.scratch/"ctx"` or
`$WYND_BUILD_CONTEXT_DIR`). `run_build_job` = prepare -> `open_image_builder().build(...)` -> `finalize_build_job`
(digest, optional push, `artefacts.put_build`, outcome artefacts).

Job inputs (as sent by `JobService.submit_build`, `$DRAFTS/06 §5.8`): `{process, registry: <image registry name> | None,
push: bool, platform?: str}`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from ..jobs import JobContext, JobOutcome


class Prepared(BaseModel):
    """Result of the prepare phase, stored as `JobRecord.artefacts["prepared"]` between phases (fields: PROC-BUILD)."""


def prepare_build_job(ctx: JobContext) -> Prepared:
    raise NotImplementedError("PLAN §6.5 prepare_build_job")


def finalize_build_job(ctx: JobContext) -> JobOutcome:
    raise NotImplementedError("PLAN §6.5 finalize_build_job")


def run_build_job(ctx: JobContext) -> JobOutcome:
    raise NotImplementedError("PLAN §6.5 run_build_job")
