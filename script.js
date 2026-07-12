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

function reportImageError(img) {
  if (!(img instanceof HTMLImageElement) || img.dataset.loadFailed === "true") {
    return;
  }

  console.error(`Failed to load image: ${img.currentSrc || img.src}`);
  img.dataset.loadFailed = "true";

  // Decorative images are aria-hidden; hide the broken-image icon rather than
  // leaving a silent visual glitch. Meaningful images keep their alt text.
  if (img.closest("[aria-hidden='true']")) {
    img.style.display = "none";
  }
}

// `error` events do not bubble, so listen in the capture phase to catch
// failures from every <img> instead of silently ignoring them.
document.addEventListener("error", (event) => reportImageError(event.target), true);

// A deferred script can start after some images have already failed, and the
// error event does not replay. Check any images that finished loading early.
function checkLoadedImages() {
  for (const img of document.images) {
    if (img.complete && img.naturalWidth === 0) {
      reportImageError(img);
    }
  }
}

checkLoadedImages();

startRotation();
