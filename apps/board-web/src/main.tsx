// main.tsx — client-side entry. Excalidraw does not support SSR, so there is no
// hydration path here; the whole app mounts in the browser with createRoot.
//
// The session is read ONCE at module scope. Reading it here strips the token
// fragment from the address bar before React renders, and therefore before any
// network call runs (see session.ts). The resulting session is passed down.

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import Board from "./Board";
import { readSession } from "./session";
import "./styles.css";

const session = readSession();

const rootEl = document.getElementById("root");
if (!rootEl) {
  throw new Error("Root element #root not found");
}

// StrictMode is intentional: it double-invokes effects in development, which is
// exactly the case Board.tsx guards against (provider kept in a ref, destroyed in
// cleanup). Production builds do not double-invoke.
createRoot(rootEl).render(
  <StrictMode>
    <Board session={session} />
  </StrictMode>,
);
