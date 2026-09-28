// Entry: theme bootstrap before React renders, CSS (React Flow's first so the app's tokens and rules win), createRoot
// (`$DRAFTS/07 §16`).
import "@xyflow/react/dist/style.css";
import "./styles/tokens.css";
import "./styles/app.css";
import "./styles/graph.css";
import "./styles/chat.css";
import "./styles/ops.css";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { applyTheme, readTheme } from "./state/theme";

applyTheme(readTheme());

const root = document.getElementById("root");
if (root === null) throw new Error("index.html has no #root element");
createRoot(root).render(<App />);
