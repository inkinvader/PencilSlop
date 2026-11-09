// PENCILSLOP Worker: game API. Static files in public/ (page, scripts, and the
// artwork images in public/img/) are served by Cloudflare's asset handler; only
// /api/* requests reach this code.
//
//   POST /api/round   -> { roundId, left, right, seconds }   (no answer included)
//   POST /api/answer  { roundId, choice } -> truth, credits, and prior crowd votes
//   POST /api/report  { roundId, side }   -> flag an image from an answered round

const ROUND_SECONDS = 10;
const GRACE_MS = 3000;                 // allowance for network latency on the timer
const AI_CHANCE_MIN = 0.2;             // each round picks its AI chance in this range
const AI_CHANCE_MAX = 0.6;
const REPORTS_TO_HIDE = 3;             // an image with this many reports stops being served
const CHOICES = ["left", "right", "both", "neither", "timeout"];

export default {
  async fetch(request, env) {
    const { pathname } = new URL(request.url);
    try {
      if (request.method === "POST" && pathname === "/api/round") return await newRound(env);
      if (request.method === "POST" && pathname === "/api/answer") return await answer(request, env);
      if (request.method === "POST" && pathname === "/api/report") return await report(request, env);
      return json({ error: "Not found" }, 404);
    } catch (err) {
      console.error(err);
      return json({ error: "Server error" }, 500);
    }
  },
};

// Serve a new pair. Each slot is independently AI with this round's chance.
async function newRound(env) {
  const chance = AI_CHANCE_MIN + Math.random() * (AI_CHANCE_MAX - AI_CHANCE_MIN);
  const leftAI = Math.random() < chance;
  const rightAI = Math.random() < chance;

  const left = await randomImage(env, leftAI, null);
  const right = await randomImage(env, rightAI, left);
  if (!left || !right) return json({ error: "Catalog too small" }, 500);

  const roundId = crypto.randomUUID();
  await env.DB.prepare(
    "INSERT INTO rounds (id, left_id, right_id, created_at) VALUES (?, ?, ?, ?)"
  ).bind(roundId, left, right, Date.now()).run();

  return json({ roundId, left: `/img/${left}.jpg`, right: `/img/${right}.jpg`, seconds: ROUND_SECONDS });
}

async function randomImage(env, isAI, excludeId) {
  const row = await env.DB.prepare(
    "SELECT id FROM images WHERE active = 1 AND is_ai = ? AND id != ? ORDER BY RANDOM() LIMIT 1"
  ).bind(isAI ? 1 : 0, excludeId ?? "").first();
  return row?.id ?? null;
}

// Check an answer, record votes, and reveal. Crowd numbers are the counts
// from before this vote ("what earlier players thought").
async function answer(request, env) {
  const body = await request.json().catch(() => ({}));
  const { roundId } = body;
  let { choice } = body;
  if (typeof roundId !== "string" || !CHOICES.includes(choice)) {
    return json({ error: "Bad request" }, 400);
  }

  const round = await env.DB.prepare("SELECT * FROM rounds WHERE id = ?").bind(roundId).first();
  if (!round) return json({ error: "Unknown round" }, 404);
  if (round.choice !== null) return json({ error: "Round already answered" }, 409);

  // The server, not the browser, decides whether time ran out.
  const now = Date.now();
  if (now - round.created_at > ROUND_SECONDS * 1000 + GRACE_MS) choice = "timeout";

  // Mark answered only if still unanswered, so two simultaneous submissions can't both count.
  const claim = await env.DB.prepare(
    "UPDATE rounds SET choice = ?, answered_at = ? WHERE id = ? AND choice IS NULL"
  ).bind(choice, now, roundId).run();
  if (claim.meta.changes === 0) return json({ error: "Round already answered" }, 409);

  const { results } = await env.DB.prepare(
    "SELECT id, is_ai, title, artist, source_url, license, ai_evidence, votes_ai, votes_real FROM images WHERE id IN (?, ?)"
  ).bind(round.left_id, round.right_id).all();
  const byId = Object.fromEntries(results.map((r) => [r.id, r]));
  const left = byId[round.left_id];
  const right = byId[round.right_id];

  const correctChoice =
    left.is_ai && right.is_ai ? "both" : left.is_ai ? "left" : right.is_ai ? "right" : "neither";

  // A timeout casts no votes: the player didn't judge either image.
  if (choice !== "timeout") {
    const leftJudgedAI = choice === "left" || choice === "both";
    const rightJudgedAI = choice === "right" || choice === "both";
    await env.DB.batch([voteStatement(env, left.id, leftJudgedAI), voteStatement(env, right.id, rightJudgedAI)]);
  }

  return json({
    correct: choice === correctChoice,
    choice,
    correctChoice,
    left: reveal(left),
    right: reveal(right),
  });
}

// A player flags an image as unfair (watermark, text, mislabeled...). Only images
// from a round that has been answered can be reported, once per image per round.
// At REPORTS_TO_HIDE reports the image stops being served.
async function report(request, env) {
  const { roundId, side } = await request.json().catch(() => ({}));
  if (typeof roundId !== "string" || (side !== "left" && side !== "right")) {
    return json({ error: "Bad request" }, 400);
  }
  const round = await env.DB.prepare("SELECT left_id, right_id, choice FROM rounds WHERE id = ?").bind(roundId).first();
  if (!round) return json({ error: "Unknown round" }, 404);
  if (round.choice === null) return json({ error: "Answer the round first" }, 409);

  const imageId = side === "left" ? round.left_id : round.right_id;
  const inserted = await env.DB.prepare(
    "INSERT OR IGNORE INTO reports (round_id, image_id, created_at) VALUES (?, ?, ?)"
  ).bind(roundId, imageId, Date.now()).run();
  if (inserted.meta.changes === 0) return json({ ok: true, duplicate: true });

  await env.DB.prepare(
    `UPDATE images SET reports = reports + 1,
       active = CASE WHEN reports + 1 >= ? THEN 0 ELSE active END
     WHERE id = ?`
  ).bind(REPORTS_TO_HIDE, imageId).run();
  return json({ ok: true });
}

function voteStatement(env, id, judgedAI) {
  const column = judgedAI ? "votes_ai" : "votes_real";
  return env.DB.prepare(`UPDATE images SET ${column} = ${column} + 1 WHERE id = ?`).bind(id);
}

function reveal(img) {
  return {
    isAI: img.is_ai === 1,
    title: img.title,
    artist: img.artist,
    sourceUrl: img.source_url,
    license: img.license,
    aiEvidence: img.ai_evidence,
    votesAI: img.votes_ai,
    votesReal: img.votes_real,
  };
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });
}
