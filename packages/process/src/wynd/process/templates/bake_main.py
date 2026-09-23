"""Zipapp bootstrap copied into a baked `.pyz` as `__main__.py` (PLAN §6.1; owner PROC-BAKE, M2; `$DRAFTS/04 §8.9`).

Standard library only. Reads `_wynd_bake/meta.json`, extracts the archive once to `$WYND_HOME/bake/<id>/` (temp dir,
exec bits restored, `.complete` marker, atomic rename), puts `site` first on `sys.path` and calls
`wynd.runtime.bake.main(plan_path, site, argv)`.
"""


def main() -> int:
    raise NotImplementedError("PLAN §6.1 bake_main")


if __name__ == "__main__":
    raise SystemExit(main())
