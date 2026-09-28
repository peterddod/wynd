// The toasts store rendered in an aria-live="polite" region (`$DRAFTS/07 §4.6`).
import type { ReactElement } from "react";
import { dismissToast, useToasts } from "../../state/toasts";
import { Icon } from "../common/Icon";

export function Toasts(): ReactElement {
  const toasts = useToasts();
  return (
    <div className="wy-toasts" aria-live="polite" aria-label="Notifications">
      {toasts.map((t) => (
        <div key={t.id} className={`wy-toast wy-toast-${t.level}`}>
          <Icon name={t.level === "info" ? "info" : "warning"} />
          <span className="wy-toast-text">{t.text}</span>
          {t.action !== null && (
            <button
              type="button"
              onClick={() => {
                t.action?.run();
                dismissToast(t.id);
              }}
            >
              {t.action.label}
            </button>
          )}
          <button type="button" className="wy-icon-btn" aria-label="Dismiss" onClick={() => dismissToast(t.id)}>
            <Icon name="close" />
          </button>
        </div>
      ))}
    </div>
  );
}
