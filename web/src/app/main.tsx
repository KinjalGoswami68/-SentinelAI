import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "../styles.css";
import App from "./App";
import { trackPointer, trackSurfaces } from "../theme";

trackPointer();
trackSurfaces();

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
