// Read-only YAML of process.yaml and each proto from the last save/load (`$DRAFTS/07 §7.8`).
import type { ReactElement } from "react";
import { anyDirty } from "../../state/design";
import { useDesignState } from "../graph/designHooks";

export function YamlView(): ReactElement | null {
  const s = useDesignState();
  if (s === null) return null;
  const files = [s.process, ...Object.values(s.protos).filter((f) => f.deleted !== true && f.yaml !== null)];
  return (
    <div className="wg-yaml">
      {anyDirty(s) && <p className="wg-muted" role="status">Showing last saved version</p>}
      {files.map((f) => (
        <section key={f.path} aria-label={f.path}>
          <h3 className="wg-mono">{f.path}</h3>
          <pre className="wg-mono">{f.yaml ?? ""}</pre>
        </section>
      ))}
    </div>
  );
}
