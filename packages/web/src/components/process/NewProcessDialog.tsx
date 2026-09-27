// id, process root, goal -> POST /api/processes (the controller scaffolds and commits), then open it
// (`$DRAFTS/07 §6.1`). Ids are process-id segments joined by "/" (PLAN §3.1); the server checks the rest.
import { useState, type ReactElement } from "react";
import { useApi, useMeta } from "../../api/context";
import { useOpenProcess } from "../../state/openProcess";
import { invalidate } from "../../state/query";
import { Dialog } from "../common/Dialog";
import { errorMessage } from "../runs/format";

const PROCESS_ID = /^[A-Za-z0-9_][A-Za-z0-9_-]*(\/[A-Za-z0-9_][A-Za-z0-9_-]*)*$/;

export interface NewProcessDialogProps {
  open: boolean;
  onClose(): void;
}

export function NewProcessDialog({ open, onClose }: NewProcessDialogProps): ReactElement | null {
  return open ? <NewProcessForm onClose={onClose} /> : null;
}

function NewProcessForm({ onClose }: { onClose(): void }): ReactElement {
  const api = useApi();
  const meta = useMeta();
  const openProcess = useOpenProcess();
  const roots = meta?.workspace.process_roots ?? [];
  const [id, setId] = useState("");
  const [root, setRoot] = useState(roots[0] ?? "");
  const [goal, setGoal] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const idOk = PROCESS_ID.test(id);

  async function create(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      const created = await api.processes.create({
        id,
        goal: goal.trim() === "" ? null : goal.trim(),
        root: roots.length > 1 ? root : null,
      });
      invalidate("processes");
      onClose();
      await openProcess(created.id);
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <Dialog
      open
      title="New process"
      onClose={onClose}
      actions={
        <>
          <button type="button" onClick={onClose}>Cancel</button>
          <button type="button" disabled={!idOk || busy} onClick={() => void create()}>Create</button>
        </>
      }
    >
      <div className="wo-form">
        <label>
          Process id
          <input
            value={id}
            placeholder="finance/invoices"
            aria-invalid={id !== "" && !idOk}
            aria-describedby="wo-new-process-id-hint"
            onChange={(e) => setId(e.target.value.trim())}
          />
        </label>
        <p id="wo-new-process-id-hint" className="wo-muted">
          Segments of letters, digits, "_" and "-", joined by "/". The last segment is the process name.
        </p>
        {roots.length > 1 && (
          <label>
            Process root
            <select value={root} onChange={(e) => setRoot(e.target.value)}>
              {roots.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          </label>
        )}
        <label>
          Goal
          <textarea value={goal} rows={3} onChange={(e) => setGoal(e.target.value)} />
        </label>
        {error !== null && <p role="alert" className="wo-error">{error}</p>}
      </div>
    </Dialog>
  );
}
