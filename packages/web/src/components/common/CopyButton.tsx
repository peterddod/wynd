// Copies text to the clipboard; "Copied" / "Copy failed" is announced for COPIED_MS.
import { useEffect, useState, type ReactElement } from "react";
import { Icon } from "./Icon";

export interface CopyButtonProps {
  text: string;
  label?: string;                    // accessible label, default "Copy"
}

export const COPIED_MS = 1500;

export function CopyButton({ text, label = "Copy" }: CopyButtonProps): ReactElement {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");

  useEffect(() => {
    if (state === "idle") return;
    const timer = setTimeout(() => setState("idle"), COPIED_MS);
    return () => clearTimeout(timer);
  }, [state]);

  const copy = async (): Promise<void> => {
    try {
      await navigator.clipboard.writeText(text);
      setState("copied");
    } catch {
      setState("failed");           // no clipboard (insecure context, permission, jsdom)
    }
  };

  return (
    <span className="wy-copy">
      <button type="button" className="wy-icon-btn" aria-label={label} title={label} onClick={() => void copy()}>
        <Icon name={state === "copied" ? "check" : "copy"} />
      </button>
      <span className="wy-copy-status" role="status">
        {state === "copied" ? "Copied" : state === "failed" ? "Copy failed" : ""}
      </span>
    </span>
  );
}
