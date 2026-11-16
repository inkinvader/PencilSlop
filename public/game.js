// PENCILSLOP client. Holds only presentation state; the server owns the answers
// and the crowd votes. Personal best lives in localStorage on this device.
//
// Round sequence: curtains close → next pair loads behind them → curtains open →
// clock starts → answer → reveal (curtains stay open) → Next / Play again → repeat.

const MIN_VOTES_TO_SHOW = 3;   // hide crowd percentages until an image has this many votes
const BEST_KEY = "pencilslop-best";
const CURTAIN_MS = 900;        // must match --curtain-ms in style.css
const LOW_TIME_S = 3;          // the clock number turns red at this many seconds left
const KEYS = { l: "left", arrowleft: "left", r: "right", arrowright: "right", b: "both", n: "neither" };

const $ = (id) => document.getElementById(id);
const state = {
  streak: 0, best: readBest(), roundId: null, deadline: 0, seconds: 10, frameId: 0,
  runOver: false, busy: false, curtainsOpen: false, seen: [],
  bestAtStart: 0,             // best when this run began; "New best!" shows once, on passing it
};

buildClockTicks();
$("best").textContent = state.best;
$("start-btn").addEventListener("click", startRun);
$("next-btn").addEventListener("click", () => (state.runOver ? startRun() : nextRound()));
$("answers").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-choice]");
  if (btn) submit(btn.dataset.choice);
});
// Keyboard answers: L / ← left, R / → right, B both, N neither (Enter/Space work on focused buttons).
document.addEventListener("keydown", (e) => {
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const choice = KEYS[e.key.toLowerCase()];
  if (choice && state.roundId && !$("answers").hidden) {
    e.preventDefault();
    submit(choice);
  }
});

function startRun() {
  state.streak = 0;
  state.runOver = false;
  state.seen = [];
  state.bestAtStart = state.best;
  $("streak").textContent = 0;
  $("start").hidden = true;
  nextRound();
}

async function nextRound() {
  if (state.busy) return;            // ignore double clicks while curtains move
  state.busy = true;
  showError("");
  $("result").hidden = true;
  $("answers").hidden = false;
  setAnswersEnabled(false);
  resetClock();

  await setCurtains(false);
  // Behind closed curtains: clear the old labels and load the next pair.
  for (const side of ["left", "right"]) {
    $(`${side}-cap`).replaceChildren();
    $(`${side}-piece`).classList.remove("is-ai", "is-human");
    $(`${side}-img`).alt = `${side === "left" ? "Left" : "Right"} artwork`;
  }

  const round = await api("/api/round", { streak: state.streak, seen: state.seen });
  if (!round) { state.busy = false; return; }
  state.roundId = round.roundId;
  state.seconds = round.seconds;
  state.seen.push(imageId(round.left), imageId(round.right));

  // Wait for both images, then the curtains, before starting the clock.
  await Promise.all([loadImage($("left-img"), round.left), loadImage($("right-img"), round.right)]);
  await setCurtains(true);
  state.busy = false;
  setAnswersEnabled(true);
  announce(`New round. ${round.seconds} seconds.`);
  startClock(round.seconds);
}

// --------------------------------------------------------------------------
// Curtains: both pieces open and close together. Resolves when the slide ends.

function setCurtains(open) {
  if (state.curtainsOpen === open) return Promise.resolve();
  state.curtainsOpen = open;
  document.body.classList.toggle("curtains-open", open);
  return new Promise((resolve) => setTimeout(resolve, CURTAIN_MS + 50));
}

// --------------------------------------------------------------------------
// Clock: the hand sweeps once over the round; the number counts down.

function buildClockTicks() {
  const ns = "http://www.w3.org/2000/svg";
  for (let i = 0; i < 60; i++) {
    const major = i % 5 === 0;
    const tick = document.createElementNS(ns, "line");
    tick.setAttribute("x1", 60); tick.setAttribute("x2", 60);
    tick.setAttribute("y1", 12); tick.setAttribute("y2", major ? 20 : 15);
    tick.setAttribute("class", major ? "tick tick-major" : "tick");
    tick.setAttribute("transform", `rotate(${i * 6} 60 60)`);
    $("clock-ticks").append(tick);
  }
}

function resetClock() {
  cancelAnimationFrame(state.frameId);
  drawClock(state.seconds, 0);
}

function drawClock(secondsLeft, fraction) {
  $("clock-hand").setAttribute("transform", `rotate(${fraction * 360} 60 60)`);
  $("timer").textContent = secondsLeft;
  $("timer").classList.toggle("low", secondsLeft <= LOW_TIME_S && fraction > 0);
}

function startClock(seconds) {
  state.deadline = performance.now() + seconds * 1000;
  const frame = (now) => {
    const msLeft = Math.max(0, state.deadline - now);
    drawClock(Math.ceil(msLeft / 1000), 1 - msLeft / (seconds * 1000));
    if (msLeft === 0) submit("timeout");
    else state.frameId = requestAnimationFrame(frame);
  };
  state.frameId = requestAnimationFrame(frame);
}

// --------------------------------------------------------------------------
// Answer and reveal

async function submit(choice) {
  if (!state.roundId) return;
  const roundId = state.roundId;
  state.roundId = null;               // one submission per round
  cancelAnimationFrame(state.frameId);
  setAnswersEnabled(false);

  const res = await api("/api/answer", { roundId, choice });
  if (!res) return;

  showReveal("left", res.left, roundId);
  showReveal("right", res.right, roundId);

  let verdict;
  if (res.correct) {
    state.streak += 1;
    if (state.streak > state.best) {
      state.best = state.streak;
      writeBest(state.best);
    }
    // Only the round that first passes the old record (and only if there was one).
    const passedRecord = state.bestAtStart > 0 && state.streak === state.bestAtStart + 1;
    verdict = passedRecord ? "Correct! New best!" : "Correct!";
  } else {
    state.runOver = true;
    const why = res.choice === "timeout" ? "Time's up." : "Wrong.";
    verdict = `${why} The answer was "${label(res.correctChoice)}". Final streak: ${state.streak}.`;
  }
  $("verdict").textContent = verdict;
  $("streak").textContent = state.streak;
  $("best").textContent = state.best;
  $("next-btn").textContent = state.runOver ? "Play again" : "Next";
  $("answers").hidden = true;
  $("result").hidden = false;
  $("next-btn").focus({ preventScroll: true });
  announce(verdict);
}

// Museum wall label under each frame: verdict, title, artist, crowd votes, credit, report.
function showReveal(side, img, roundId) {
  $(`${side}-piece`).classList.add(img.isAI ? "is-ai" : "is-human");
  $(`${side}-img`).alt = `${img.title}, ${img.artist}`;
  const cap = $(`${side}-cap`);
  cap.replaceChildren();

  cap.append(el("span", "verdict-tag", img.isAI ? "AI-generated" : "Human-made"));
  cap.append(el("span", "art-title", img.title));
  cap.append(el("span", "art-artist", img.artist));

  const total = img.votesAI + img.votesReal;
  cap.append(el("span", "art-votes",
    total >= MIN_VOTES_TO_SHOW
      ? `${Math.round((img.votesAI / total) * 100)}% of ${total} earlier players said AI`
      : "Not enough votes yet"));

  const row = el("span", "placard-row");
  const link = el("a", "", "Source");
  link.href = img.sourceUrl;
  link.target = "_blank";
  link.rel = "noopener";
  link.setAttribute("aria-label", `Source and license for ${img.title}`);

  // Players flag unfair images (watermark, text, wrong label); enough reports hide it.
  const flag = el("button", "flag", "Report image");
  flag.addEventListener("click", async () => {
    flag.disabled = true;
    const ok = await api("/api/report", { roundId, side }, { quiet: true });
    flag.textContent = ok ? "Reported, thanks" : "Couldn't report";
  });
  row.append(link, flag);
  cap.append(row);
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function label(choice) {
  return { left: "Left is AI", right: "Right is AI", both: "Both", neither: "Neither" }[choice];
}

// "/img/0123456789abcdef.jpg" -> "0123456789abcdef"
function imageId(path) {
  return path.split("/").pop().split(".")[0];
}

// --------------------------------------------------------------------------
// Helpers

// quiet: a failure doesn't interrupt the game (used for optional actions like reporting).
async function api(path, body = {}, { quiet = false } = {}) {
  try {
    const res = await fetch(path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
    return await res.json();
  } catch (err) {
    if (quiet) return null;
    showError(`Something went wrong: ${err.message}`);
    $("answers").hidden = true;
    $("result").hidden = false;
    $("verdict").textContent = "";
    $("next-btn").textContent = "Try again";
    return null;
  }
}

function loadImage(img, src) {
  return new Promise((resolve) => {
    img.onload = img.onerror = resolve;
    img.src = src;
  });
}

function setAnswersEnabled(on) {
  for (const b of $("answers").querySelectorAll("button")) b.disabled = !on;
}

function announce(msg) {
  $("announce").textContent = msg;
}

function showError(msg) {
  $("error").textContent = msg;
  $("error").hidden = !msg;
}

// localStorage can throw in private windows; the game still works without it.
function readBest() {
  try { return Number(localStorage.getItem(BEST_KEY)) || 0; } catch { return 0; }
}
function writeBest(n) {
  try { localStorage.setItem(BEST_KEY, String(n)); } catch { /* ignore */ }
}
