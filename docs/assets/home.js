// The home page's board: the split-flap remarks play the example run, the clock keeps local
// time, and the gate lines copy their command. Without script the board shows its final state.
(() => {
  const DRUM = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:·-";
  const FLIP_MS = 55; // one leaf, as --flip in home.css
  const STAGGER_MS = 700; // between rows
  const CHECKING_MS = 900; // how long a row stays CHECKING before its result
  const COPIED_MS = 1600;
  const still = matchMedia("(prefers-reduced-motion: reduce)").matches;

  const wait = (ms) => new Promise((done) => setTimeout(done, ms));

  // One cell to one character, through the drum, a leaf at a time
  async function turn(cell, target) {
    let at = DRUM.indexOf(cell.textContent || " ");
    const to = Math.max(0, DRUM.indexOf(target));
    while (at !== to) {
      const from = DRUM[at];
      at = (at + 1) % DRUM.length;
      const next = DRUM[at];
      const top = leaf("top", from);
      const bottom = leaf("bottom", next);
      cell.replaceChildren(document.createTextNode(next === " " ? "" : next), top, bottom);
      top.classList.add("turning");
      await wait(FLIP_MS / 2);
      bottom.classList.add("turning");
      await wait(FLIP_MS / 2);
    }
    cell.replaceChildren(document.createTextNode(target === " " ? "" : target));
  }

  function leaf(side, ch) {
    const half = document.createElement("span");
    half.className = `leaf ${side}`;
    const face = document.createElement("span");
    face.textContent = ch === " " ? "" : ch;
    half.append(face);
    return half;
  }

  function show(flaps, text) {
    const cells = [...flaps.querySelectorAll(".flap")];
    const padded = text.padEnd(cells.length);
    flaps.querySelector("[data-flaps-text]").textContent = text;
    return Promise.all(cells.map((cell, i) => turn(cell, padded[i])));
  }

  async function play(row) {
    const flaps = row.querySelector("[data-flaps]");
    const result = flaps.dataset.flaps;
    const kind = row.dataset.remark;
    row.dataset.remark = "checking";
    await show(flaps, "CHECKING");
    await wait(CHECKING_MS);
    row.dataset.remark = kind;
    await show(flaps, result);
  }

  async function run(board) {
    const rows = [...board.querySelectorAll("tbody tr")];
    for (const row of rows) {
      for (const cell of row.querySelectorAll(".flap")) cell.textContent = "";
    }
    rows.forEach((row, i) => wait(i * STAGGER_MS).then(() => play(row)));
  }

  function clock(el) {
    const tick = () => {
      el.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
    };
    tick();
    setInterval(tick, 10_000);
  }

  function copying(button) {
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(button.dataset.copy);
        button.textContent = "Copied";
      } catch {
        button.textContent = "Select it";
      }
      setTimeout(() => (button.textContent = "Copy"), COPIED_MS);
    });
  }

  document.querySelectorAll("[data-clock]").forEach(clock);
  document.querySelectorAll("[data-copy]").forEach(copying);
  const board = document.querySelector("[data-board]");
  if (board && !still) {
    const seen = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) {
        seen.disconnect();
        run(board);
      }
    });
    seen.observe(board);
  }
})();
