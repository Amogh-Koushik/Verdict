// The scoreboard's container names the contest's SSE stream in data-live-url. An event only
// says "the contest changed"; htmx then re-fetches the table. Every open scoreboard hears the
// same event, so each waits a random moment before fetching, which spreads the requests out,
// and further events during that wait are covered by the same fetch.
const MAX_REFRESH_DELAY_MS = 1000;

document.addEventListener("DOMContentLoaded", () => {
  const board = document.querySelector("[data-live-url]");
  if (!board) {
    return;
  }
  let refreshScheduled = false;
  const refresh = () => {
    if (refreshScheduled) {
      return;
    }
    refreshScheduled = true;
    setTimeout(() => {
      refreshScheduled = false;
      htmx.trigger(board, "contest-changed");
    }, Math.random() * MAX_REFRESH_DELAY_MS);
  };
  const source = new EventSource(board.dataset.liveUrl);
  // Also re-fetch whenever the stream (re)opens, so changes made while it was down still show.
  source.onopen = refresh;
  source.onmessage = refresh;
});
