// Counts down the seconds in data-countdown-seconds. At zero it reloads the page when the
// element has data-reload-at-zero (the contest has just started), and otherwise shows the
// element's data-at-zero text. Time is measured as elapsed time on this computer, so a
// clock that is set wrong doesn't matter.
document.addEventListener("DOMContentLoaded", () => {
  const countdown = document.querySelector("[data-countdown-seconds]");
  if (!countdown) {
    return;
  }
  const zeroAt = Date.now() + Number(countdown.dataset.countdownSeconds) * 1000;
  const shown = countdown.querySelector("time");
  const pad = (number) => String(number).padStart(2, "0");
  const tick = () => {
    const left = Math.max(0, Math.ceil((zeroAt - Date.now()) / 1000));
    const hours = Math.floor(left / 3600);
    shown.textContent = `${hours}:${pad(Math.floor(left / 60) % 60)}:${pad(left % 60)}`;
    if (left > 0) {
      setTimeout(tick, 250);
    } else if ("reloadAtZero" in countdown.dataset) {
      location.reload();
    } else {
      countdown.textContent = countdown.dataset.atZero;
    }
  };
  tick();
});
