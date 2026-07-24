// Board.tsx — the collaborative canvas the team actually sees.
//
// Excalidraw is a client-only React component (no SSR). It is bound to a Yjs
// document synchronised by a HocuspocusProvider over the same-origin /collab
// WebSocket (session.ts derives that URL). The one-time board token is delivered
// to the provider through its ASYNC token supplier, so it travels inside the
// Hocuspocus Auth message and is never appended to the socket URL (which would
// land it in an nginx access log).
//
// D-26-06: images stay Excalidraw-native (base64 inside the document) for 26a.
// Native paste already satisfies "send photos". Offloading image bytes to object
// storage, an asset map, a signed-fetch cache, and text ingestion into the shared
// memory layer are ALL 26b — do NOT wire any of that here. This component
// deliberately implements none of it, so a later reader does not mistake the
// absence for an omission.

import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Excalidraw } from "@excalidraw/excalidraw";
import "@excalidraw/excalidraw/index.css";
import type { ExcalidrawImperativeAPI } from "@excalidraw/excalidraw/types";
import { HocuspocusProvider } from "@hocuspocus/provider";
import * as Y from "yjs";
import { ExcalidrawBinding } from "./yjs-binding";
import type { Session } from "./session";

type ConnState = "connecting" | "live" | "denied";

// One combined message for BOTH "wrong team" and "expired link" (T-26-25): the
// denied state must not act as an oracle telling the visitor which cause applied.
const ACCESS_DENIED =
  "Access denied. This board belongs to another team, or your board link has " +
  "expired. Reopen the board from the team chat.";

function Centered({ children }: { children: ReactNode }) {
  return <div className="board-message">{children}</div>;
}

export default function Board({ session }: { session: Session }) {
  const { boardId, token, wsUrl } = session;
  const [api, setApi] = useState<ExcalidrawImperativeAPI | null>(null);
  const [conn, setConn] = useState<ConnState>("connecting");
  // The provider is kept in a ref so React 18 StrictMode's double mount/unmount
  // destroys the first provider in cleanup instead of leaking a second live
  // socket. The effect below wires the CRDT exactly once per board.
  const providerRef = useRef<HocuspocusProvider | null>(null);

  useEffect(() => {
    // Wire the CRDT only once the Excalidraw API is ready and we actually have a
    // board id and a token. Excalidraw must stay mounted while "connecting" so
    // this callback fires — hence these are guards, not a blank-screen gate.
    if (!api || !boardId || !token) return;

    const ydoc = new Y.Doc();
    const provider = new HocuspocusProvider({
      url: wsUrl,
      name: boardId, // === documentName; the server asserts claims.board_id === name
      document: ydoc,
      token: async () => token, // async supplier -> the Auth MESSAGE, never the URL
      onAuthenticationFailed: () => {
        // Stop the reconnect loop: a rejected token must not hammer the socket
        // with a dead credential (T-26-26). Disconnect, then show one message.
        provider.disconnect();
        setConn("denied");
      },
      onStatus: ({ status }) => {
        // Never overwrite the terminal "denied" state with a transport status.
        setConn((prev) =>
          prev === "denied"
            ? prev
            : status === "connected"
              ? "live"
              : "connecting",
        );
      },
    });
    providerRef.current = provider;

    // Bind the Y.Doc to Excalidraw. Remote cursors come free from provider
    // awareness — no second presence channel is opened. The vendored binding
    // applies CaptureUpdateAction.NEVER to every remote/init updateScene
    // internally (26-01, verified in yjs-binding/index.ts), so there is no
    // history-control call site here. Element and asset container names must be
    // identical across clients for them to converge; every client is this SPA.
    const yElements = ydoc.getArray<Y.Map<any>>("elements");
    const yAssets = ydoc.getMap<any>("assets");
    const binding = new ExcalidrawBinding(
      yElements,
      yAssets,
      api,
      provider.awareness ?? undefined,
    );

    return () => {
      binding.destroy();
      provider.destroy();
      ydoc.destroy();
      providerRef.current = null;
    };
  }, [api, boardId, token, wsUrl]);

  // States rendered INSTEAD of the canvas (missing preconditions or a refusal).
  if (!boardId) {
    return <Centered>No board specified.</Centered>;
  }
  if (!token) {
    return (
      <Centered>
        This board link is missing its access token. Reopen the board from the
        team chat.
      </Centered>
    );
  }
  if (conn === "denied") {
    return <Centered>{ACCESS_DENIED}</Centered>;
  }

  // Excalidraw is always mounted here so its excalidrawAPI callback fires and the
  // effect can connect. The connection status is a small overlay indicator.
  return (
    <div className="board-root">
      {conn === "connecting" ? (
        <div className="board-status board-status--connecting">
          Connecting to the board…
        </div>
      ) : (
        <div className="board-status board-status--live">Live</div>
      )}
      <div className="board-canvas">
        <Excalidraw excalidrawAPI={(a) => setApi(a)} />
      </div>
    </div>
  );
}
