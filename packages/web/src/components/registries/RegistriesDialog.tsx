// Settings: MCP servers (add url + auth env, OAuth start -> window.location.assign(authorize_url), remove) and
// providers (PLAN §10 amendment 4, §3.14). Opened from the header "Settings" button. Secrets never pass through
// here: an auth env var is referenced as `${env:NAME}`, and OAuth stores its tokens in the user registry.
import { useState, type ReactElement } from "react";
import { useApi } from "../../api/context";
import type { McpServerEntry, RemoveResult } from "../../api/types";
import { invalidate, useQuery } from "../../state/query";
import { Dialog } from "../common/Dialog";
import { ErrorBox } from "../common/ErrorBox";
import { errorMessage, formatTime } from "../runs/format";

export interface RegistriesDialogProps {
  open: boolean;
  onClose(): void;
}

/** Leaving the app for the authorization server; a seam so tests need not navigate jsdom. */
export const browser = {
  assign(url: string): void {
    window.location.assign(url);
  },
};

const SERVER_NAME = /^[a-z0-9][a-z0-9_-]*$/;
const ENV_NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;

export function RegistriesDialog({ open, onClose }: RegistriesDialogProps): ReactElement | null {
  return open ? <Registries onClose={onClose} /> : null;
}

function Registries({ onClose }: { onClose(): void }): ReactElement {
  const api = useApi();
  const mcp = useQuery("registries:mcp", () => api.registries.mcp.list());
  const providers = useQuery("providers", () => api.providers.list());
  const [removed, setRemoved] = useState<{ name: string; result: RemoveResult } | null>(null);
  const servers = mcp.data?.items;

  return (
    <Dialog open title="Settings" className="wo-settings" onClose={onClose} actions={<button type="button" onClick={onClose}>Done</button>}>
      <section className="wo-section" aria-labelledby="wo-mcp-title">
        <h3 id="wo-mcp-title">MCP servers</h3>
        <ErrorBox error={mcp.error} onRetry={() => void mcp.reload()} />
        {removed !== null && (
          <p role="status" className={removed.result.referenced_by.length > 0 ? "wo-warn" : "wo-muted"}>
            {removed.result.removed ? `Removed ${removed.name}.` : `${removed.name} was not registered.`}
            {removed.result.referenced_by.length > 0 &&
              ` Still used by ${removed.result.referenced_by.join(", ")}: their steps fail with a config error until it is added again.`}
          </p>
        )}
        {servers !== undefined && servers.length === 0 && <p className="wo-muted">No MCP servers registered.</p>}
        {servers !== undefined && servers.length > 0 && (
          <ul className="wo-rows">
            {servers.map((entry) => (
              <McpRow key={entry.name} entry={entry} onRemoved={(result) => setRemoved({ name: entry.name, result })} />
            ))}
          </ul>
        )}
        <AddMcpServer />
      </section>
      <section className="wo-section" aria-labelledby="wo-providers-title">
        <h3 id="wo-providers-title">Providers</h3>
        <ErrorBox error={providers.error} onRetry={() => void providers.reload()} />
        {providers.data !== undefined && (
          <ul className="wo-rows">
            {providers.data.providers.map((p) => (
              <li key={p.name} className="wo-registry-row">
                <strong>{p.name}</strong> <span className="wo-muted">{p.kind}</span>{" "}
                <span className={`wo-pill ${p.ready ? "wo-ok" : "wo-bad"}`}>{p.ready ? "ready" : "not ready"}</span>
                <div className="wo-muted">{Object.entries(p.tiers).map(([tier, model]) => `${tier} → ${model}`).join(" · ")}</div>
                <div className="wo-muted">{p.message}</div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </Dialog>
  );
}

function McpRow({ entry, onRemoved }: { entry: McpServerEntry; onRemoved(result: RemoveResult): void }): ReactElement {
  const api = useApi();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const expires = typeof entry.oauth?.expires_at === "string" ? entry.oauth.expires_at : null;

  async function connect(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      const { authorize_url } = await api.registries.mcp.oauthStart(entry.name, { return_to: window.location.href });
      browser.assign(authorize_url);
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  async function remove(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      const result = await api.registries.mcp.remove(entry.name);
      invalidate("registries:mcp");
      onRemoved(result);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  }

  return (
    <li className="wo-registry-row" aria-label={`MCP server ${entry.name}`}>
      <strong>{entry.name}</strong> <span className="wo-muted">{entry.transport}</span>{" "}
      <code>{entry.transport === "http" ? entry.url : (entry.command ?? []).join(" ")}</code>
      {entry.description !== "" && <div className="wo-muted">{entry.description}</div>}
      {entry.auth_env.length > 0 && <div className="wo-muted">auth env: {entry.auth_env.join(", ")}</div>}
      {entry.oauth !== null && <div className="wo-muted">OAuth connected{expires !== null && `, token expires ${formatTime(expires)}`}</div>}
      <div className="wo-card-actions">
        {entry.transport === "http" && (
          <button type="button" disabled={busy} onClick={() => void connect()}>
            {entry.oauth === null ? "Connect with OAuth" : "Reconnect with OAuth"}
          </button>
        )}
        {confirming ? (
          <>
            <button type="button" disabled={busy} onClick={() => void remove()}>Confirm remove</button>
            <button type="button" onClick={() => setConfirming(false)}>Keep</button>
          </>
        ) : (
          <button type="button" onClick={() => setConfirming(true)}>Remove</button>
        )}
      </div>
      {error !== null && <p role="alert" className="wo-error">{error}</p>}
    </li>
  );
}

function AddMcpServer(): ReactElement {
  const api = useApi();
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [authEnv, setAuthEnv] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const nameOk = SERVER_NAME.test(name);
  const urlOk = /^https?:\/\/\S+$/.test(url);
  const envOk = authEnv === "" || ENV_NAME.test(authEnv);

  async function add(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      await api.registries.mcp.add({
        name,
        transport: "http",
        url,
        headers: authEnv === "" ? {} : { Authorization: `Bearer \${env:${authEnv}}` },
        auth_env: authEnv === "" ? [] : [authEnv],
      });
      invalidate("registries:mcp");
      setName("");
      setUrl("");
      setAuthEnv("");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="wo-form wo-add-mcp">
      <h4>Add an MCP server</h4>
      <label>
        Name
        <input value={name} placeholder="github" aria-invalid={name !== "" && !nameOk} onChange={(e) => setName(e.target.value.trim())} />
      </label>
      <label>
        URL
        <input value={url} placeholder="https://api.githubcopilot.com/mcp/" aria-invalid={url !== "" && !urlOk} onChange={(e) => setUrl(e.target.value.trim())} />
      </label>
      <label>
        Auth env var
        <input value={authEnv} placeholder="GITHUB_TOKEN (optional)" aria-invalid={!envOk} onChange={(e) => setAuthEnv(e.target.value.trim())} />
      </label>
      <p className="wo-muted">
        The server is sent <code>Authorization: Bearer $&#123;env:NAME&#125;</code>; set the variable in the workspace
        .env. Leave it empty to connect with OAuth after adding.
      </p>
      {error !== null && <p role="alert" className="wo-error">{error}</p>}
      <button type="button" disabled={busy || !nameOk || !urlOk || !envOk} onClick={() => void add()}>Add server</button>
    </div>
  );
}
