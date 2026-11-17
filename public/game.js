// PENCILSLOP client. Holds only presentation state; the server owns the answers
// and the crowd votes. Personal best lives in localStorage on this device.
//
// Round sequence: curtains close → next pair loads behind them → curtains open →
// clock starts → answer → reveal (curtains stay open) → Next / Play again → repeat.

const MIN_VOTES_TO_SHOW = 3;   // hide crowd percentages until an image has this many votes
const BEST_KEY = "pencilslop-best";
const VOTED_KEY = "pencilslop-voted";   // image IDs this device has voted on (one vote each)
const MAX_VOTED = 5000;                 // keep the list bounded
const CURTAIN_MS = 900;        // must match --curtain-ms in style.css
const LOAD_ATTEMPTS = 3;       // pairs to try when an image fails to load
const LOW_TIME_S = 3;          // the clock number turns red at this many seconds left
// Speed bonus for a correct answer, by time since the clock started (first match wins).
// Points total = streak + bonus. Best is the best points total.
const SPEED_BONUS = [{ withinMs: 500, points: 2 }, { withinMs: 1000, points: 1 }];
// Must match the phone breakpoint in style.css: there the pieces stack, so the
// sides are called Top and Bottom instead of Left and Right.
const STACKED = window.matchMedia("(max-width: 700px)");
const KEYS = { l: "left", arrowleft: "left", r: "right", arrowright: "right", b: "both", n: "neither" };

const $ = (id) => document.getElementById(id);
const state = {
  streak: 0, best: readBest(), roundId: null, deadline: 0, seconds: 10, frameId: 0,
  runOver: false, busy: false, curtainsOpen: false, seen: [],
  bonus: 0,                   // speed bonus points this run
  bestAtStart: 0,             // best when this run began; "New best!" shows once, on passing it
  pair: [],                   // image IDs of the current round [left, right]
  voted: readVoted(),         // images this device has already voted on
};

buildClockTicks();
nameSides();
STACKED.addEventListener("change", nameSides);
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
  state.bonus = 0;
  state.runOver = false;
  state.seen = [];
  state.bestAtStart = state.best;
  $("streak").textContent = 0;
  $("bonus").hidden = true;          // appears only once a bonus is earned
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
    $(`${side}-img`).alt = `${sideName(side)} artwork`;
  }

  // Load the pair behind the curtains. If an image fails to load, tell the server
  // (it hides the image if the file is really gone) and try a different pair.
  let round = null;
  for (let attempt = 0; attempt < LOAD_ATTEMPTS && !round; attempt++) {
    const next = await api("/api/round", { streak: state.streak, seen: state.seen });
    if (!next) { state.busy = false; return; }
    state.seen.push(imageId(next.left), imageId(next.right));
    const loaded = await Promise.all([loadImage($("left-img"), next.left), loadImage($("right-img"), next.right)]);
    if (loaded[0] && loaded[1]) round = next;
    ["left", "right"].forEach((side, i) => {
      if (!loaded[i]) api("/api/missing", { roundId: next.roundId, side }, { quiet: true });
    });
  }
  if (!round) {
    state.busy = false;
    showError("The artwork couldn't be loaded. Check your connection and try again.");
    $("answers").hidden = true;
    $("result").hidden = false;
    $("verdict").textContent = "";
    $("next-btn").textContent = "Try again";
    return;
  }
  state.roundId = round.roundId;
  state.seconds = round.seconds;
  state.pair = [imageId(round.left), imageId(round.right)];

  // Both images are in; open the curtains, then start the clock.
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
  const answeredMs = performance.now() - (state.deadline - state.seconds * 1000);
  state.roundId = null;               // one submission per round
  cancelAnimationFrame(state.frameId);
  setAnswersEnabled(false);

  // Only this device's first vote on each image counts toward the crowd stats.
  const [leftId, rightId] = state.pair;
  const res = await api("/api/answer", {
    roundId, choice, countLeft: !state.voted.has(leftId), countRight: !state.voted.has(rightId),
  });
  if (!res) return;
  if (choice !== "timeout") rememberVotes(leftId, rightId);

  showReveal("left", res.left, roundId);
  showReveal("right", res.right, roundId);

  let verdict;
  if (res.correct) {
    const before = points();
    const earned = SPEED_BONUS.find((b) => answeredMs <= b.withinMs)?.points ?? 0;
    state.streak += 1;
    if (earned) showBonus(earned);
    if (points() > state.best) {
      state.best = points();
      writeBest(state.best);
    }
    // Only the round that first passes the old record (and only if there was one).
    const passedRecord = state.bestAtStart > 0 && before <= state.bestAtStart && points() > state.bestAtStart;
    verdict = (earned ? `Correct! Speed bonus +${earned}.` : "Correct!") + (passedRecord ? " New best!" : "");
  } else {
    state.runOver = true;
    const why = res.choice === "timeout" ? "Time's up." : "Wrong.";
    const total = state.bonus
      ? `Streak ${state.streak} + speed bonus ${state.bonus} = ${points()} points.`
      : `${points()} points.`;
    verdict = `${why} The answer was "${label(res.correctChoice)}". ${total}`;
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

function points() {
  return state.streak + state.bonus;
}

// The +N badge next to the streak; replays its pop each time it grows.
function showBonus(earned) {
  state.bonus += earned;
  const badge = $("bonus");
  badge.textContent = `+${state.bonus}`;
  badge.hidden = false;
  badge.classList.remove("pop");
  void badge.offsetWidth;            // restart the animation
  badge.classList.add("pop");
}

// Museum wall label under each frame. Human works: verdict, title, artist, crowd
// result, source link. AI images: verdict and crowd result only, plus a License
// link when the server sends one (licenses that require attribution).
function showReveal(side, img, roundId) {
  $(`${side}-piece`).classList.add(img.isAI ? "is-ai" : "is-human");
  $(`${side}-img`).alt = img.isAI ? `AI-generated image (${sideName(side).toLowerCase()})` : `${img.title}, ${img.artist}`;
  const cap = $(`${side}-cap`);
  cap.replaceChildren();

  cap.append(el("span", "verdict-tag", img.isAI ? "AI-generated" : "Human-made"));
  if (!img.isAI) {
    cap.append(el("span", "art-title", img.title));
    cap.append(el("span", "art-artist", img.artist));
  }

  // Share of earlier players who judged this image correctly.
  const total = img.votesAI + img.votesReal;
  const right = img.isAI ? img.votesAI : img.votesReal;
  cap.append(el("span", "art-votes",
    total >= MIN_VOTES_TO_SHOW
      ? `${Math.round((right / total) * 100)}% of ${total} players got it right`
      : "Not enough votes yet"));

  const row = el("span", "placard-row");
  if (img.sourceUrl) {
    const link = el("a", "", img.isAI ? "License" : "Source");
    link.href = img.sourceUrl;
    link.target = "_blank";
    link.rel = "noopener";
    link.setAttribute("aria-label", img.isAI ? `Image source and license (${img.license})` : `Source and license for ${img.title}`);
    row.append(link);
  }

  // Players flag unfair images (watermark, text, wrong label); enough reports hide it.
  const flag = el("button", "flag", "Report image");
  flag.addEventListener("click", async () => {
    flag.disabled = true;
    const ok = await api("/api/report", { roundId, side }, { quiet: true });
    flag.textContent = ok ? "Reported, thanks" : "Couldn't report";
  });
  row.append(flag);
  cap.append(row);
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function label(choice) {
  if (choice === "left" || choice === "right") return `${sideName(choice)} is AI`;
  return { both: "Both", neither: "Neither" }[choice];
}

// "Left"/"Right" side by side; "Top"/"Bottom" when the pieces are stacked (phones).
function sideName(side) {
  if (STACKED.matches) return side === "left" ? "Top" : "Bottom";
  return side === "left" ? "Left" : "Right";
}

// Label the answer plaques and the artwork descriptions for the current layout.
function nameSides() {
  for (const el of document.querySelectorAll("[data-side]")) el.textContent = sideName(el.dataset.side);
  for (const side of ["left", "right"]) {
    if (!$(`${side}-piece`).matches(".is-ai, .is-human")) $(`${side}-img`).alt = `${sideName(side)} artwork`;
  }
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

// Resolves true when the image loaded, false when it failed.
function loadImage(img, src) {
  return new Promise((resolve) => {
    img.onload = () => resolve(true);
    img.onerror = () => resolve(false);
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

// localStorage can throw in private windows; the game still works without it
// (repeat votes then count, as before).
function readVoted() {
  try { return new Set(JSON.parse(localStorage.getItem(VOTED_KEY) || "[]")); } catch { return new Set(); }
}
function rememberVotes(...ids) {
  for (const id of ids) state.voted.add(id);
  try { localStorage.setItem(VOTED_KEY, JSON.stringify([...state.voted].slice(-MAX_VOTED))); } catch { /* ignore */ }
}

function readBest() {
  try { return Number(localStorage.getItem(BEST_KEY)) || 0; } catch { return 0; }
}
function writeBest(n) {
  try { localStorage.setItem(BEST_KEY, String(n)); } catch { /* ignore */ }
}
