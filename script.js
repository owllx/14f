const hearts = ["❤️", "🧡", "💛", "💚", "💙", "🩷", "🤍"];

let current = 0;
let timerId;

function rotateTitle() {
  document.title = hearts[current];
  current = (current + 1) % hearts.length;
}

function startRotation() {
  clearInterval(timerId);
  rotateTitle();
  timerId = setInterval(rotateTitle, 850);
}

function stopRotation() {
  clearInterval(timerId);
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    stopRotation();
    document.title = "🤍";
    return;
  }

  startRotation();
});

startRotation();
