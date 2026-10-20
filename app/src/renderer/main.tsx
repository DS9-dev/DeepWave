import "@fontsource-variable/inter";
import "./styles.css";
import { createRoot } from "react-dom/client";
import { initBackend } from "./api";
import { App } from "./ui/App";

initBackend().finally(() => createRoot(document.getElementById("root")!).render(<App />));
