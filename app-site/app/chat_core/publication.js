/**
 * `team:<id>` websocket frame router for the team chat (Phase 27, D-27-04).
 *
 * One function, five frame types, no DOM: everything visual goes through the
 * injected renderer, so the extension popup and the PWA route identical frames
 * through identical logic and only differ in what they hand in.
 *
 * Frames handled (memory-api team_chat publisher):
 *   message              — a persisted chat message, rendered as a bubble
 *   agent_stream_start   — an agent answer begins; a streaming bubble is created
 *   agent_stream_chunk   — a delta appended to the buffer and written to the DOM
 *   agent_stream_end     — the answer is complete; the streaming class is dropped
 *   agent_stream_error   — the answer failed; the bubble becomes a failure state
 *
 * Anything else is ignored, exactly as the popup did — an unknown frame from a
 * newer server must never throw in an older client.
 *
 * Used by:
 *   - chrome-extension/popup.js (via chrome-extension/chat_core/)
 *   - app-site/app/ — the PWA (via app-site/app/chat_core/)
 *   - chrome-extension/tests/test_chat_core_render.mjs
 */

/**
 * @param {{
 *   renderer: {renderMessage: Function, renderAgentBubble: Function,
 *              renderAgentFailure: Function, syncDaySeparators: Function,
 *              streamTextTarget: Function, clearStreaming: Function,
 *              scrollToBottom: Function},
 *   streamBuffer: {start: Function, append: Function, get: Function, finalize: Function},
 *   onNonEmpty?: () => void
 * }} opts
 *   onNonEmpty — called when a frame proves the thread is not empty. The surface
 *   owns its "no messages yet" panel (different id, different markup), so the
 *   router only reports the fact.
 * @returns {(data: any) => void}
 */
export function createPublicationRouter(opts) {
  const cfg = opts || {};
  const renderer = cfg.renderer;
  const streamBuffer = cfg.streamBuffer;
  if (!renderer) throw new TypeError("createPublicationRouter requires opts.renderer");
  if (!streamBuffer) {
    throw new TypeError("createPublicationRouter requires opts.streamBuffer");
  }
  const onNonEmpty =
    typeof cfg.onNonEmpty === "function" ? cfg.onNonEmpty : () => {};

  return function handlePublication(data) {
    if (!data || !data.type) return;
    if (data.type === "message") {
      renderer.renderMessage(data.message, { prepend: false });
      renderer.syncDaySeparators();
      renderer.scrollToBottom();
      onNonEmpty();
      return;
    }
    if (data.type === "agent_stream_start") {
      streamBuffer.start(data.message_id);
      renderer.renderAgentBubble({
        id: data.message_id,
        agent_name: data.agent_name,
        routed_via: data.routed_via,
        streaming: true,
      });
      renderer.scrollToBottom();
      onNonEmpty();
      return;
    }
    if (data.type === "agent_stream_chunk") {
      streamBuffer.append(data.message_id, data.delta);
      const textEl = renderer.streamTextTarget(data.message_id);
      if (textEl) {
        textEl.textContent = streamBuffer.get(data.message_id);
        renderer.scrollToBottom();
      }
      return;
    }
    if (data.type === "agent_stream_end") {
      streamBuffer.finalize(data.message_id);
      renderer.clearStreaming(data.message_id);
      return;
    }
    if (data.type === "agent_stream_error") {
      // The frame is handed over WHOLE and the renderer decides what to print,
      // from the failure code alone. Two things used to be wrong here in one
      // line: the frame's text was appended to the answer, so a provider's error
      // read as the agent's last sentence; and it was that provider's text, so a
      // billing message reached every member of the team.
      renderer.renderAgentFailure(data.message_id, data);
      renderer.scrollToBottom();
      return;
    }
  };
}
