// "Can't reach wynd serve-api. Retrying…" while offline (`$DRAFTS/07 §4.6`).
import type { ReactElement } from "react";
import { useConnection } from "../../state/connection";
import { Icon } from "../common/Icon";

export function ConnectionBanner(): ReactElement | null {
  const connection = useConnection();
  if (connection.online) return null;
  return (
    <div className="wy-connection" role="alert">
      <Icon name="warning" />
      Can't reach wynd serve-api. Retrying…
    </div>
  );
}
