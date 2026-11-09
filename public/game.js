// PENCILSLOP client. Holds only presentation state; the server owns the answers
// and the crowd votes. Personal best lives in localStorage on this device.

const MIN_VOTES_TO_SHOW = 3;   // hide crowd percentages until an image has this many votes
const BEST_KEY = "pencilslop-best";

const $ = (id) => document.getElementById(id);
const state = { streak: 0, best: readBest(), roundId: null, timerId: null, deadline: 0, runOver: false };

$("best").textContent = state.best;
$("start-btn").addEventListener("click", startRun);
$("next-btn").addEventListener("click", () => (state.runOver ? startRun() : nextRound()));
$("answers").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-choice]");
  if (btn) submit(btn.dataset.choice);
});

function startRun() {
  state.streak = 0;
  state.runOver = false;
  $("streak").textContent = 0;
  $("start").hidden = true;
  $("round").hidden = false;
  nextRound();
}

async function nextRound() {
  showError("");
  $("result").hidden = true;
  setAnswersEnabled(false);
  for (const side of ["left", "right"]) {
    $(`${side}-cap`).textContent = "";
    $(`${side}-fig`).className = "art";
  }

  const round = await api("/api/round");
  if (!round) return;
  state.roundId = round.roundId;

  // Wait for both images before starting the clock so slow loading doesn't eat the timer.
  await Promise.all([loadImage($("left-img"), round.left), loadImage($("right-img"), round.right)]);
  setAnswersEnabled(true);
  startTimer(round.seconds);
}

function startTimer(seconds) {
  state.deadline = Date.now() + seconds * 1000;
  const tick = () => {
    const left = Math.max(0, Math.ceil((state.deadline - Date.now()) / 1000));
    $("timer").textContent = left;
    if (left === 0) submit("timeout");
  };
  tick();
  state.timerId = setInterval(tick, 200);
}

async function submit(choice) {
  if (!state.roundId) return;
  const roundId = state.roundId;
  state.roundId = null;               // one submission per round
  clearInterval(state.timerId);
  setAnswersEnabled(false);

  const res = await api("/api/answer", { roundId, choice });
  if (!res) return;

  showReveal("left", res.left, roundId);
  showReveal("right", res.right, roundId);

  if (res.correct) {
    state.streak += 1;
    if (state.streak > state.best) {
      state.best = state.streak;
      writeBest(state.best);
    }
    $("verdict").textContent = "Correct!";
  } else {
    state.runOver = true;
    const why = res.choice === "timeout" ? "Time's up." : "Wrong.";
    $("verdict").textContent = `${why} The answer was "${label(res.correctChoice)}". Final streak: ${state.streak}.`;
  }
  $("streak").textContent = state.streak;
  $("best").textContent = state.best;
  $("next-btn").textContent = state.runOver ? "Play again" : "Next";
  $("result").hidden = false;
}

// Caption under each artwork: truth, credit/link, prior crowd votes, and a report button.
function showReveal(side, img, roundId) {
  $(`${side}-fig`).className = `art ${img.isAI ? "is-ai" : "is-human"}`;
  const cap = $(`${side}-cap`);
  cap.replaceChildren();

  const tag = document.createElement("strong");
  tag.textContent = img.isAI ? "AI" : "Human";
  cap.append(tag, " ");

  const link = document.createElement("a");
  link.href = img.sourceUrl;
  link.target = "_blank";
  link.rel = "noopener";
  link.textContent = `${img.title} (${img.artist})`;
  cap.append(link, document.createElement("br"));

  const total = img.votesAI + img.votesReal;
  cap.append(
    total >= MIN_VOTES_TO_SHOW
      ? `${Math.round((img.votesAI / total) * 100)}% of ${total} earlier players said AI`
      : "Not enough votes yet"
  );

  // Players flag unfair images (watermark, text, wrong label); enough reports hide it.
  const flag = document.createElement("button");
  flag.className = "flag";
  flag.textContent = "Report image";
  flag.addEventListener("click", async () => {
    flag.disabled = true;
    const ok = await api("/api/report", { roundId, side }, { quiet: true });
    flag.textContent = ok ? "Reported, thanks" : "Couldn't report";
  });
  cap.append(document.createElement("br"), flag);
}

function label(choice) {
  return { left: "Left is AI", right: "Right is AI", both: "Both", neither: "Neither" }[choice];
}

// quiet: a failure doesn't interrupt the game (used for optional actions like reporting).
async function api(path, body = {}, { quiet = false } = {}) {
  try {
    const res = await fetch(path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
    return await res.json();
  } catch (err) {
    if (quiet) return null;
    showError(`Something went wrong: ${err.message}`);
    $("result").hidden = false;
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
