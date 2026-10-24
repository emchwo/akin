/* Akin frontend: one screen at a time, built with DOM nodes (never innerHTML with player text). */

const state = {
  gameId: null,
  names: { A: "", B: "" },
  setting: "first_date",
  minRounds: 6,
  maxRounds: 10,
  roundsDone: 0,
  scenario: null,
  picks: {},
  done: false,
};

const stage = document.getElementById("stage");
const progressEl = document.getElementById("progress");

/* ---------- tiny DOM helper ---------- */

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

/* Fade out the current screen, then show the next one. Pass WIDE first for a wider layout. */
const WIDE = Symbol("wide");
let showing = Promise.resolve();
function show(...nodes) {
  const wide = nodes[0] === WIDE;
  if (wide) nodes.shift();
  showing = showing.then(async () => {
    const old = stage.firstElementChild;
    if (old) {
      old.classList.add("leaving");
      await new Promise((r) => setTimeout(r, 220));
    }
    stage.classList.toggle("wide", wide);
    stage.replaceChildren(h("section", { class: "screen" }, ...nodes));
    window.scrollTo({ top: 0, behavior: "instant" });
    const focusable = stage.querySelector("[autofocus]") || stage.querySelector("button");
    if (focusable && !("ontouchstart" in window)) focusable.focus({ preventScroll: true });
  });
  return showing;
}

async function api(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ game_id: state.gameId, ...body }),
  });
  const data = await res.json().catch(() => ({ error: "The server sent an unreadable reply." }));
  if (data.game_id) state.gameId = data.game_id;
  refreshStats();
  if (!res.ok && !data.errors) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

/* ---------- Stats panel (tokens per agent for this game) ---------- */

const statsPanel = document.getElementById("stats-panel");
const statsToggle = document.getElementById("stats-toggle");
const statsBody = document.getElementById("stats-body");
const AGENT_LABELS = { scenario_writer: "Scenario Writer", checker: "Checker", prompt_writer: "Prompt Writer", reveal_writer: "Reveal Writer" };
let statsTimer = null;

function setStatsOpen(open) {
  statsPanel.hidden = !open;
  statsToggle.setAttribute("aria-expanded", String(open));
  clearInterval(statsTimer);
  if (open) {
    refreshStats();
    statsTimer = setInterval(refreshStats, 3000); // background agents keep working between clicks
  }
}
statsToggle.addEventListener("click", () => setStatsOpen(statsPanel.hidden));
document.getElementById("stats-close").addEventListener("click", () => setStatsOpen(false));

const fmt = (n) => Number(n).toLocaleString();
const usd = (n) => (n < 0.01 ? `$${n.toFixed(5)}` : `$${n.toFixed(4)}`);

async function refreshStats() {
  if (statsPanel.hidden) return;
  if (!state.gameId) {
    statsBody.replaceChildren(h("p", { class: "stats-empty" }, "Start a game to see token use."));
    return;
  }
  let s;
  try {
    s = await (await fetch(`/api/stats?game_id=${encodeURIComponent(state.gameId)}`)).json();
  } catch { return; }
  if (!s.agents) return;
  const row = (label, r, cls) => h("tr", { class: cls },
    h("th", { scope: "row" }, label), h("td", {}, fmt(r.calls)), h("td", {}, fmt(r.input)),
    h("td", {}, fmt(r.cached_input)), h("td", {}, fmt(r.output)), h("td", {}, usd(r.cost_usd)));
  statsBody.replaceChildren(
    h("table", { class: "stats-table" },
      h("thead", {}, h("tr", {}, ["Agent", "Calls", "Input", "Cached", "Output", "Cost"].map((t) => h("th", { scope: "col" }, t)))),
      h("tbody", {}, Object.entries(s.agents).map(([k, r]) => row(AGENT_LABELS[k] || k, r))),
      h("tfoot", {}, row("Total", s.total, "total")),
    ),
    h("p", { class: "stats-note" },
      `${s.llm_calls} of ${s.max_calls} calls used this game · ${s.rounds_played} rounds · ` +
      `${s.checker_rejections} Checker rejection${s.checker_rejections === 1 ? "" : "s"}. ` +
      `Cached input is billed at $${s.prices_per_1m.cached_input}/1M vs $${s.prices_per_1m.input}/1M.`),
  );
}

/* ---------- progress ---------- */

function renderProgress() {
  progressEl.replaceChildren();
  if (!state.gameId) return;
  for (let i = 1; i <= state.maxRounds; i++) {
    let cls = "seg";
    if (i > state.minRounds) cls += " optional";
    if (i <= state.roundsDone) cls += " done";
    else if (i === state.roundsDone + 1 && !state.done) cls += " now";
    else if (state.done) cls += " unused";
    const color = state.roundColors[i - 1];
    progressEl.append(h("span", {
      class: cls,
      style: color ? `--seg:var(--${color})` : null,
      title: i > state.minRounds ? `Round ${i} (if needed)` : `Round ${i}`,
    }));
  }
}

/* ---------- screens ---------- */

function loadingScreen(text) {
  return show(h("div", { class: "loading" }, h("p", {}, text), h("div", { class: "line" })));
}

/* While a scenario is being written: two circles take turns hopping, like passing the device. */
const SVG_NS = "http://www.w3.org/2000/svg";
function svgEl(tag, attrs) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}

function writingScreen(text) {
  const svg = svgEl("svg", { class: "turns", viewBox: "0 0 120 60", fill: "none", "aria-hidden": "true" });
  svg.append(
    svgEl("line", { class: "turns-floor", x1: "14", y1: "52", x2: "106", y2: "52" }),
    svgEl("circle", { class: "turn turn-a", cx: "42", cy: "38", r: "12" }),
    svgEl("circle", { class: "turn turn-b", cx: "78", cy: "38", r: "12" }),
  );
  return show(h("div", { class: "loading" }, svg, h("p", {}, text)));
}

/* While both answers are revealed: the logo's two rings drift together and apart. */
function revealingScreen(text) {
  const svg = svgEl("svg", { class: "rings", viewBox: "0 0 120 60", fill: "none", "aria-hidden": "true" });
  svg.append(
    svgEl("circle", { class: "rings-l", cx: "60", cy: "30", r: "18" }),
    svgEl("circle", { class: "rings-r", cx: "60", cy: "30", r: "18" }),
  );
  return show(h("div", { class: "loading" }, svg, h("p", {}, text)));
}

function errorScreen(msg) {
  return show(
    h("div", { class: "message" },
      h("p", { class: "eyebrow" }, "Something went wrong"),
      h("h2", {}, "Let's try that again."),
      h("p", { class: "lede" }, msg),
      h("div", { class: "actions" }, h("button", { class: "btn", onclick: startScreen }, "Back to start")),
    ),
  );
}

function budgetScreen(data) {
  return show(
    h("div", { class: "message" },
      h("p", { class: "eyebrow" }, "Game over"),
      h("h2", {}, "That's a wrap for this game."),
      h("p", { class: "lede" }, data.message),
      h("div", { class: "actions" },
        data.rounds_played > 0 ? h("button", { class: "btn", onclick: finishGame }, "See what we learned") : null,
        h("button", { class: "btn quiet", onclick: startScreen }, "Start over"),
      ),
    ),
  );
}

/* The start screen shows the header logo (one source: templates/_logo.html) large and animated. */
function startLogo() {
  const logo = document.querySelector(".topbar .logo").cloneNode(true);
  logo.classList.add("animated");
  return logo;
}

function startScreen() {
  Object.assign(state, { gameId: null, roundsDone: 0, roundColors: [], scenario: null, picks: {}, done: false });
  renderProgress();

  const nameA = h("input", { id: "name-a", maxlength: "20", autocomplete: "off", autocapitalize: "words", spellcheck: "false", autofocus: true });
  const nameB = h("input", { id: "name-b", maxlength: "20", autocomplete: "off", autocapitalize: "words", spellcheck: "false" });
  const errA = h("div", { class: "err" });
  const errB = h("div", { class: "err" });

  const settingBtns = [["first_date", "First date"], ["new_friends", "Friends"]].map(([val, label]) =>
    h("button", {
      type: "button", "aria-pressed": String(state.setting === val),
      onclick: (e) => {
        state.setting = val;
        e.currentTarget.parentElement.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b === e.currentTarget)));
      },
    }, label));

  async function begin() {
    errA.textContent = ""; errB.textContent = "";
    if (!nameA.value.trim()) { errA.textContent = "Please type a first name."; return; }
    if (!nameB.value.trim()) { errB.textContent = "Please type a first name."; return; }
    writingScreen("Writing your first scenario…");
    try {
      const data = await api("/api/start", { name_a: nameA.value, name_b: nameB.value, setting: state.setting });
      if (data.errors) {
        await startScreen();
        document.getElementById("name-a").value = nameA.value;
        document.getElementById("name-b").value = nameB.value;
        stage.querySelectorAll(".err")[0].textContent = data.errors.a || "";
        stage.querySelectorAll(".err")[1].textContent = data.errors.b || "";
        return;
      }
      if (data.ended === "budget") return budgetScreen(data);
      Object.assign(state, { gameId: data.game_id, names: data.names, minRounds: data.min_rounds, maxRounds: data.max_rounds });
      state.scenario = data.scenario;
      scenarioScreen();
    } catch (e) {
      errorScreen(e.message);
    }
  }

  return show(
    h("p", { class: "eyebrow" }, "A conversation game for two"),
    h("h1", { class: "hero-logo" }, startLogo()),
    h("div", { class: "axis-marks", "aria-hidden": "true" }, ["slate", "clay", "sage"].map((c) => h("span", { style: `border-color:var(--${c})` }))),
    h("p", { class: "lede" }, "Read a scenario together, answer in private, then talk about it. Pass the device back and forth."),
    h("div", { class: "names" },
      h("div", { class: "field" }, h("label", { for: "name-a" }, "First player"), nameA, errA),
      h("div", { class: "field" }, h("label", { for: "name-b" }, "Second player"), nameB, errB),
    ),
    h("div", { class: "toggle", role: "group", "aria-label": "Setting" }, settingBtns),
    h("p", { class: "notice" }, "This game reveals each player's relationship type at the end. Your answers aren't saved with your names."),
    h("div", { class: "actions" },
      h("button", { class: "btn", onclick: begin }, "Start"),
    ),
  );
}

function scenarioScreen() {
  state.picks = {};
  renderProgress();
  const s = state.scenario;
  return show(
    h("p", { class: "eyebrow" }, `Round ${s.round}`),
    h("p", { class: "scenario-text" }, s.scenario),
    h("hr", { class: "rule" }),
    h("p", { class: "step" }, "Read it together. Then ", h("strong", {}, state.names.A), " takes the device and ",
      h("strong", {}, `turns the screen away from ${state.names.B}`), "."),
    h("div", { class: "actions" },
      h("button", { class: "btn", onclick: () => answerScreen("A") }, `I'm ${state.names.A}, the screen is turned away`),
    ),
  );
}

const other = (p) => (p === "A" ? "B" : "A");

function backButton(label, onclick) {
  return h("button", { class: "back", type: "button", onclick }, "← ", label);
}

/* Hand-off to the second player. The first player can still go back and change their answer here. */
function coverB() {
  return show(
    h("div", { class: "cover" },
      h("p", { class: "hand" }, "Hand the device to"),
      h("p", { class: "who" }, state.names.B),
      h("div", { class: "who-line" }),
      h("p", { class: "step center" }, h("strong", {}, `${state.names.B}, turn the screen away from ${state.names.A}`), " before you continue."),
      h("div", { class: "actions" },
        h("button", { class: "btn", onclick: () => answerScreen("B") }, `I'm ${state.names.B}, the screen is turned away`)),
      h("p", { class: "hint" }, `${state.names.A}'s answer is hidden.`),
      backButton(`${state.names.A}, change my answer`, () => answerScreen("A")),
    ),
  );
}

function readyToReveal() {
  return show(
    h("div", { class: "cover" },
      h("p", { class: "hand" }, "Both answers are in"),
      h("p", { class: "who" }, "Put the device between you"),
      h("div", { class: "who-line" }),
      h("div", { class: "actions" }, h("button", { class: "btn", onclick: submitAnswers }, "Reveal answers")),
      backButton(`${state.names.B}, change my answer`, () => answerScreen("B")),
    ),
  );
}

function answerScreen(player) {
  const s = state.scenario;
  const choose = (key, btn) => {
    btn.classList.add("picked");
    stage.querySelectorAll(".option").forEach((b) => (b.disabled = true));
    state.picks[player] = key;
    setTimeout(() => (player === "A" ? coverB() : readyToReveal()), 260);
  };
  return show(
    backButton("Back", () => (player === "A" ? scenarioScreen() : coverB())),
    h("p", { class: "eyebrow" }, `${state.names[player]}, your answer`),
    h("p", { class: "privacy" }, `Keep the screen turned away from ${state.names[other(player)]}.`),
    h("p", { class: "scenario-small" }, s.scenario),
    h("div", { class: "options" },
      ["a", "b"].map((k) =>
        h("button", {
          class: "option" + (state.picks[player] === k ? " was" : ""),
          onclick: (e) => choose(k, e.currentTarget),
        }, h("span", { class: "key" }, k.toUpperCase()), h("span", {}, s.options[k]))),
    ),
  );
}

async function submitAnswers() {
  revealingScreen("Revealing…");
  try {
    const data = await api("/api/answers", { a: state.picks.A, b: state.picks.B });
    state.roundsDone = data.round;
    state.roundColors[data.round - 1] = data.color;
    state.done = data.done;
    revealScreen(data);
  } catch (e) {
    errorScreen(e.message);
  }
}

/* Take back the round just revealed (a misclick) and play that scenario again. */
async function undoRound() {
  loadingScreen("Going back…");
  try {
    const data = await api("/api/undo", {});
    state.scenario = data.scenario;
    state.roundsDone = data.rounds_played;
    state.roundColors.length = data.rounds_played;
    state.done = false;
    scenarioScreen();
  } catch (e) {
    errorScreen(e.message);
  }
}

function revealScreen(data) {
  renderProgress();
  let next;
  if (data.done) {
    next = [h("div", { class: "actions" }, h("button", { class: "btn", onclick: finishGame }, "See your types"))];
  } else if (data.can_end) {
    const left = state.maxRounds - data.round;
    const note = data.settled
      ? `Your types are clear. Keep going for more to talk about (up to ${left} more round${left === 1 ? "" : "s"}).`
      : `A few of your answers are still close. ${left === 1 ? "One more round" : `Up to ${left} more rounds`} would sharpen your results.`;
    const end = h("button", { class: data.settled ? "btn" : "btn quiet", onclick: finishGame }, "End now and see your types");
    const keep = h("button", { class: data.settled ? "btn quiet" : "btn", onclick: nextRound }, "Keep going");
    next = [
      h("div", { class: "choice" }, h("p", { class: "eyebrow" }, "End now or keep going?"), h("p", { class: "hint" }, note)),
      h("div", { class: "actions" }, ...(data.settled ? [end, keep] : [keep, end])),
    ];
  } else {
    next = [h("div", { class: "actions" }, h("button", { class: "btn", onclick: nextRound }, "Next scenario"))];
  }
  return show(
    h("div", { class: "reveal" },
      h("p", { class: "eyebrow" }, `Round ${data.round} · The reveal`),
      h("p", { class: "scenario-small" }, state.scenario.scenario),
      h("div", { class: "answers" },
        ["A", "B"].map((p) => h("div", {}, h("div", { class: "name" }, state.names[p]), h("div", { class: "said" }, data.answers[p]))),
      ),
      data.prompt ? h("div", { class: "talk" }, h("p", { class: "eyebrow" }, "Talk about it"), h("p", {}, data.prompt)) : null,
    ),
    ...next,
    backButton("Someone misclicked? Change your answers", undoRound),
  );
}

async function nextRound() {
  writingScreen("Writing the next scenario…");
  try {
    const data = await api("/api/next", {});
    if (data.ended === "budget") return budgetScreen(data);
    if (data.done) return finishGame();
    state.scenario = data.scenario;
    scenarioScreen();
  } catch (e) {
    errorScreen(e.message);
  }
}

async function finishGame() {
  state.done = true;
  renderProgress();
  loadingScreen("Reading your answers…");
  try {
    const data = await api("/api/finish", {});
    endScreen(data);
  } catch (e) {
    errorScreen(e.message);
  }
}

/* ---------- end screen ---------- */

const ACCENT = { slate: "var(--slate)", clay: "var(--clay)", sage: "var(--sage)" };

function roundsNote(rounds) {
  if (!rounds || !rounds.length) return null;
  const label = rounds.length === 1 ? "Round" : "Rounds";
  return h("p", { class: "cites" }, `${label} ${rounds.join(" · ")}`);
}

function meter(axisKey, axis, lean) {
  // lean: +1 = fully the first side (left), -1 = fully the second side (right)
  const pos = ((1 - lean) / 2) * 100;
  const [left, right] = Object.values(axis.sides);
  const dot = h("span", { class: "meter-dot", style: `background:${ACCENT[axis.color]}` });
  const el = h("div", { class: "meter", "data-axis": axisKey },
    h("div", { class: "meter-labels" }, h("span", {}, left), h("span", {}, right)),
    h("div", { class: "meter-track" },
      h("span", { class: "meter-fill", style: `border-color:${ACCENT[axis.color]}` }),
      dot),
  );
  el.dataset.pos = pos;
  return el;
}

function playerBlock(p, data) {
  const t = data.players[p];
  const iconWrap = h("div", { class: "icon-wrap" });
  const svg = AkinIcons.element(t.key);
  iconWrap.append(svg);
  const feel = h("div", { class: "feel" },
    h("p", { class: "feel-q" }, "Does this feel right?"),
    h("div", { class: "toggle", role: "group", "aria-label": `Does this feel right for ${data.names[p]}?` },
      [["yes", "Yes"], ["kind_of", "Kind of"], ["no", "No"]].map(([val, label]) =>
        h("button", {
          type: "button", "aria-pressed": "false",
          onclick: (e) => {
            state.feelsRight[p] = val;
            e.currentTarget.parentElement.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b === e.currentTarget)));
            updateContinue();
          },
        }, label))),
  );
  const block = h("div", { class: "player" },
    iconWrap,
    h("div", { class: "after-draw" },
      h("p", { class: "eyebrow" }, data.names[p]),
      h("h2", { class: "type-name" }, t.name),
      h("p", { class: "tagline" }, t.tagline),
      h("div", { class: "meters" }, Object.entries(data.axes).map(([k, ax]) => meter(k, ax, t.meters[k]))),
      h("ul", { class: "type-summary" },
        t.summary.map((s) => h("li", {},
          h("span", { class: "ts-dot", style: `background:${ACCENT[data.axes[s.axis].color]}` }),
          h("span", {}, h("strong", {}, `${s.label}${s.close ? " (a close call)" : ""}: `), s.text)))),
      h("p", { class: "eyebrow evidence-label" }, "What your answers say"),
      h("p", { class: "evidence" }, t.evidence),
      roundsNote(t.evidence_rounds),
      feel,
    ),
  );
  block._svg = svg;
  return block;
}

let continueBtn = null;
function updateContinue() {
  if (continueBtn) continueBtn.disabled = !(state.feelsRight.A && state.feelsRight.B);
}

async function endScreen(data) {
  state.feelsRight = { A: null, B: null };
  state.endData = data;
  const blocks = ["A", "B"].map((p) => playerBlock(p, data));
  continueBtn = h("button", { class: "btn", disabled: true, onclick: () => feedbackScreen() }, "Continue");
  const card = h("div", { class: "clickclash after-draw" },
    h("div", {}, h("p", { class: "eyebrow" }, "Where you'd click"), h("p", { class: "cc-text" }, data.click.text), roundsNote(data.click.rounds)),
    h("div", {}, h("p", { class: "eyebrow" }, "Where you'd clash"), h("p", { class: "cc-text" }, data.clash.text), roundsNote(data.clash.rounds)),
  );
  await show(
    WIDE,
    h("p", { class: "eyebrow center" }, `After ${data.rounds_played} rounds`),
    h("div", { class: "players" }, blocks),
    h("hr", { class: "rule" }),
    card,
    h("div", { class: "actions center after-draw" }, continueBtn),
    h("p", { class: "hint center after-draw" }, "Each of you answer “Does this feel right?” to continue."),
  );
  // Signature moment: each icon draws itself, then the words fade in and the meters slide to place.
  await Promise.all(blocks.map((b) => AkinIcons.draw(b._svg, 1500)));
  stage.querySelectorAll(".after-draw").forEach((el, i) => setTimeout(() => el.classList.add("in"), i < 2 ? 0 : 500));
  setTimeout(() => {
    stage.querySelectorAll(".meter").forEach((m) => {
      const pos = parseFloat(m.dataset.pos);
      m.querySelector(".meter-dot").style.left = `${pos}%`;
      const fill = m.querySelector(".meter-fill");
      fill.style.left = `${Math.min(pos, 50)}%`;
      fill.style.width = `${Math.abs(pos - 50)}%`;
    });
  }, 350);
}

/* ---------- feedback ---------- */

function choiceRow(options, onPick, label, cls = "toggle") {
  return h("div", { class: cls, role: "group", "aria-label": label },
    options.map(([val, text]) =>
      h("button", {
        type: "button", "aria-pressed": "false",
        onclick: (e) => {
          e.currentTarget.parentElement.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b === e.currentTarget)));
          onPick(val);
        },
      }, text)));
}

function feedbackScreen() {
  const answers = { learned: null, again: null };
  const submit = h("button", { class: "btn", disabled: true, onclick: send }, "Send feedback");
  const ready = () => (submit.disabled = !(answers.learned && answers.again));
  async function send() {
    submit.disabled = true;
    try {
      await api("/api/feedback", { feels_right: state.feelsRight, learned: answers.learned, play_again: answers.again });
      thanksScreen();
    } catch (e) {
      errorScreen(e.message);
    }
  }
  return show(
    h("p", { class: "eyebrow" }, "Two quick questions"),
    h("h2", {}, "Did you learn something new about the other person?"),
    h("div", { class: "spacer" }),
    choiceRow([["yes", "Yes"], ["a_little", "A little"], ["no", "No"]], (v) => { answers.learned = v; ready(); }, "Did you learn something new"),
    h("hr", { class: "rule" }),
    h("h2", {}, "Would you play again with someone else?"),
    h("div", { class: "spacer" }),
    choiceRow([["yes", "Yes"], ["no", "No"]], (v) => { answers.again = v; ready(); }, "Would you play again"),
    h("p", { class: "notice" }, "Saved without your names: just the setting, your ratings, and whether your type felt right."),
    h("div", { class: "actions" }, submit),
  );
}

function thanksScreen() {
  return show(
    h("div", { class: "cover" },
      h("p", { class: "hand" }, "Thank you"),
      h("p", { class: "who" }, "Keep talking."),
      h("div", { class: "who-line" }),
      h("p", { class: "lede", style: "margin-left:auto;margin-right:auto" }, "The best part of the game is the conversation after it."),
      h("div", { class: "actions" },
        h("button", { class: "btn", onclick: startScreen }, "Play again"),
        h("a", { class: "btn link", href: "/results" }, "See everyone's results")),
    ),
  );
}

startScreen();
