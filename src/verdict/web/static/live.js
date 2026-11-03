// A page marks the element holding its live fragments with data-live-url, an SSE stream.
// Each fragment carries data-live-submission and, until its verdict is in, data-pending.
// An event only says "this submission changed"; htmx then re-fetches that fragment.
document.addEventListener("DOMContentLoaded", () => {
  const container = document.querySelector("[data-live-url]");
  if (!container) {
    return;
  }
  const pending = (selector = "") =>
    container.querySelectorAll(`[data-live-submission${selector}][data-pending]`);
  const refresh = (fragments) =>
    fragments.forEach((fragment) => htmx.trigger(fragment, "submission-changed"));

  const source = new EventSource(container.dataset.liveUrl);
  // Fetch everything still pending whenever the stream (re)opens, so an event sent
  // before the connection existed, or while it was down, is never missed.
  source.onopen = () => refresh(pending());
  source.onmessage = (event) => {
    const { submission_id: id } = JSON.parse(event.data);
    refresh(pending(`="${id}"`));
  };
  container.addEventListener("htmx:afterSettle", () => {
    if (pending().length === 0) {
      source.close();
    }
  });
});
