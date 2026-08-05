/**
 * `team:<id>` websocket frame router for the team chat (Phase 27, D-27-04).
 *
 * One function, five frame types, no DOM: everything visual goes through the
 * injected renderer, so the extension popup and the PWA route identical frames
 * through identical logic and only differ in what they hand in.
 *
 * Frames handled (memory-api team_chat publisher):
 *   message              — a persisted chat message, rendered as a bubble
 *   message_deleted      — somebody removed a message; the row leaves this screen
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
 *              writeStreamText: Function, clearStreaming: Function,
 *              scrollToBottom: Function},
 *   streamBuffer: {start: Function, append: Function, get: Function, finalize: Function},
 *   onNonEmpty?: () => void,
 *   onMessageDeleted?: (messageId: string) => void
 * }} opts
 *   onNonEmpty — called when a frame proves the thread is not empty. The surface
 *   owns its "no messages yet" panel (different id, different markup), so the
 *   router only reports the fact.
 *   onMessageDeleted — called with the id of a message somebody removed. The
 *   surface takes the row out and reconciles its own layout; the router does not
 *   reach into the renderer for this, which is what keeps the removal path
 *   independent of how a bubble is drawn.
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
  const onMessageDeleted =
    typeof cfg.onMessageDeleted === "function" ? cfg.onMessageDeleted : null;

  return function handlePublication(data) {
    if (!data || !data.type) return;
    if (data.type === "message") {
      renderer.renderMessage(data.message, { prepend: false });
      renderer.syncDaySeparators();
      renderer.scrollToBottom();
      onNonEmpty();
      return;
    }
    if (data.type === "message_deleted") {
      // A message that vanishes for the person who removed it and stays for
      // everyone else is worse than no feature — this is the half that makes the
      // deletion real on the other screens. A surface that ships no handler is
      // left alone rather than being reached into: an older client simply keeps
      // showing the row until it reloads, which is the honest degradation.
      if (onMessageDeleted && data.message_id) onMessageDeleted(data.message_id);
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
      // The FULL buffer, not the delta: the agent answers in markdown, and
      // `**Excalibur**` arrives as three chunks of which only the last one
      // makes a word bold. The renderer re-parses the whole answer and swaps
      // the body in one synchronous pass, so nothing is ever painted half-built.
      //
      // `partial` says the last character is not the last character. It buys one
      // thing: a bare URL at the end of the buffer stays text until the answer
      // is done, so a reader never gets a clickable
      // `https://pitch.com/v/excalibur-prop` that resolves to nothing.
      if (
        renderer.writeStreamText(data.message_id, streamBuffer.get(data.message_id), {
          partial: true,
        })
      ) {
        renderer.scrollToBottom();
      }
      return;
    }
    if (data.type === "agent_stream_end") {
      streamBuffer.finalize(data.message_id);
      // THE FINISHING PASS, and it is not cosmetic. Every chunk so far was
      // rendered as an answer that might still grow; this is the only frame that
      // knows it will not. Without it an answer ending in a URL — which is how
      // half the agent's answers end — keeps that URL as dead text until the
      // page is reloaded.
      renderer.writeStreamText(data.message_id, streamBuffer.get(data.message_id));
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
