// Full-time statistics for EFL matches, read from Flashscore's own pages.
// Run by the nightly task in the browser, on any flashscore.co.uk page.
//
// First set the matches to read (results_needed from collect_todo.py):
//   window.fsTodo = [{"date": "2026-11-14", "league": "League 2", "home": "Fleetwood", "away": "Tranmere"}, ...];
// then run this whole file. It runs in the background and leaves a JSON string
// in window.flashscoreStats (null while working, or "ERROR: ..."): read that
// with a second call and save it, exactly, as flashscore_pending.json.
// espn_check.py then checks the copy is exact (the "check" field), checks every
// row against ESPN, and adds the good ones to manual_results.csv.
//
// Each match page is loaded in a hidden frame, exactly as a visitor would see
// it: the summary for the score, cards and referee, then the Stats tab.
// Nothing is estimated: a figure the page doesn't show comes back null.
window.flashscoreStats = null;
(async () => {
  const todo = window.fsTodo || [];
  const LEAGUES = { "Championship": "championship", "League 1": "league-one", "League 2": "league-two" };
  const PARALLEL = 3;

  // load a same-site page in a hidden frame; resolves once `sel` exists (or null after `ms`)
  const load = (url, sel, ms = 20000) => new Promise(res => {
    const f = document.createElement("iframe");
    f.style.cssText = "position:fixed;left:-4000px;top:0;width:1200px;height:2400px;visibility:hidden";
    f.src = url;
    document.body.appendChild(f);
    const t0 = Date.now();
    const iv = setInterval(() => {
      let d = null;
      try { d = f.contentDocument; } catch (e) { /* not ready */ }
      if (d && d.querySelector(sel)) { clearInterval(iv); res({ f, d }); }
      else if (Date.now() - t0 > ms) { clearInterval(iv); res({ f, d: null }); }
    }, 250);
  });
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const waitFor = async (d, sel, ms) => { for (let t = 0; t < ms && !d.querySelector(sel); t += 250) await sleep(250); return !!d.querySelector(sel); };

  // club names: the board's spelling against Flashscore's
  const FILL = new Set(["fc", "afc", "utd", "united", "city", "town", "county", "rovers", "athletic", "wanderers", "albion"]);
  const ALIAS = { wed: "wednesday", weds: "wednesday", wolves: "wolverhampton", mk: "milton", boro: "middlesbrough" };
  const words = s => s.toLowerCase().replace(/[^a-z0-9 ]/g, " ").split(/\s+/).filter(Boolean).map(w => ALIAS[w] || w);
  const same = (board, fs) => {
    const b = words(board), o = words(fs);
    const core = b.filter(w => !FILL.has(w));
    return (core.length ? core : b).every(w => o.some(x => x.startsWith(w) || w.startsWith(x)));
  };
  const dm = iso => `${iso.slice(8, 10)}.${iso.slice(5, 7)}.`;          // 2026-11-14 -> 14.11.
  const int = s => { const m = String(s ?? "").match(/-?\d+/); return m ? parseInt(m[0], 10) : null; };

  // each league's results list: match links with date and both clubs (the
  // name only: a red-card badge in the same box would add a digit)
  const club = el => (el?.querySelector('[class*="wcl-name"]')?.textContent ?? el?.textContent ?? "").trim().replace(/\d+$/, "").trim();
  const lists = {};
  for (const lg of new Set(todo.map(x => x.league))) {
    if (!LEAGUES[lg]) continue;
    const r = await load(`/football/england/${LEAGUES[lg]}/results/`, ".event__match");
    lists[lg] = r.d ? [...r.d.querySelectorAll(".event__match")].map(m => ({
      href: m.querySelector("a.eventRowLink, a")?.getAttribute("href") || "",
      date: (m.querySelector(".event__stageTime, .event__time")?.textContent || "").trim().slice(0, 6),
      home: club(m.querySelector(".event__homeParticipant")),
      away: club(m.querySelector(".event__awayParticipant")),
      text: m.textContent,
    })) : null;
    r.f.remove();
  }

  const one = async x => {
    const row = { date: x.date, league: x.league, home: x.home, away: x.away, status: null,
                  HomeGoals: null, AwayGoals: null, HomeCorners: null, AwayCorners: null,
                  HomePossession: null, AwayPossession: null, HomeCrosses: null, AwayCrosses: null,
                  HomeYellow: null, AwayYellow: null, HomeRed: null, AwayRed: null,
                  HomeFouls: null, AwayFouls: null, Referee: null,
                  fs_home: null, fs_away: null, url: null, notes: [] };
    const list = lists[x.league];
    if (!list) { row.status = "page_error"; row.notes.push("results list didn't load"); return row; }
    const m = list.find(e => e.date === dm(x.date) && same(x.home, e.home) && same(x.away, e.away));
    if (!m) { row.status = "not_found"; return row; }
    if (/postp|abn|canc|award/i.test(m.text)) { row.status = /abn/i.test(m.text) ? "abandoned" : "postponed"; return row; }
    row.fs_home = m.home; row.fs_away = m.away; row.url = m.href;

    // summary: score, status, cards, referee
    const s = await load(m.href, ".detailScore__wrapper");
    if (!s.d) { s.f.remove(); row.status = "page_error"; return row; }
    await waitFor(s.d, ".smv__participantRow", 6000);                  // a match can have no incidents
    await sleep(300);
    const status = (s.d.querySelector(".fixedHeaderDuel__detailStatus, .detailScore__status")?.textContent || "").trim();
    const score = (s.d.querySelector(".detailScore__wrapper")?.textContent || "").trim();
    if (!/^finished$/i.test(status)) { s.f.remove(); row.status = status ? status.toLowerCase().replace(/\s+/g, "_") : "not_finished"; return row; }
    row.status = "finished";
    const g = score.match(/^(\d+)\s*-\s*(\d+)$/);
    if (g) { row.HomeGoals = +g[1]; row.AwayGoals = +g[2]; }

    // cards, counted the way football-data counts them: a second yellow is a
    // yellow and a red; cards shown to players not on the pitch don't count
    const c = { HomeYellow: 0, AwayYellow: 0, HomeRed: 0, AwayRed: 0 };
    for (const r of s.d.querySelectorAll(".smv__participantRow")) {
      const icon = r.querySelector("svg")?.dataset.testid || "";
      if (!/card/.test(icon)) continue;
      const side = r.classList.contains("smv__homeParticipant") ? "Home" : "Away";
      const txt = r.textContent.replace(/\s+/g, " ").trim();
      if (/not on pitch/i.test(txt)) { row.notes.push(`${side} card not counted (not on pitch): ${txt.slice(0, 50)}`); continue; }
      if (/red-card-second/.test(icon)) { c[side + "Yellow"]++; c[side + "Red"]++; }
      else if (/red-card/.test(icon)) c[side + "Red"]++;
      else if (/yellow-card/.test(icon)) c[side + "Yellow"]++;
    }
    if (s.d.querySelector(".smv__participantRow")) Object.assign(row, c);   // no incident list -> leave cards null

    const info = [...s.d.querySelectorAll("*")].find(e => e.children.length === 0 && /^Referee:?$/.test(e.textContent.trim()));
    // "Referee:" sits in its own wrapper; the name is in the element after it
    const ref = (info?.parentElement?.nextElementSibling?.textContent || "").replace(/\(.*?\)/g, "").trim();
    if (ref) {                                                            // "Howard P." -> "P Howard"
      const w = ref.replace(/\./g, "").split(/\s+/);
      row.Referee = w.length > 1 && w[w.length - 1].length <= 2 ? [w[w.length - 1], ...w.slice(0, -1)].join(" ") : w.join(" ");
    }
    s.f.remove();

    // Stats tab: corners, fouls, possession, crosses
    const statsUrl = m.href.replace(/\/\?mid=/, "/summary/stats/overall/?mid=");
    const t = await load(statsUrl, '[data-testid="wcl-statistics"]');
    if (t.d) {
      await sleep(400);
      const stat = {};
      for (const r of t.d.querySelectorAll('[data-testid="wcl-statistics"]')) {
        const k = r.querySelector('[class*="labelRow"]')?.children;
        if (!k || k.length < 3) continue;
        const label = k[1].textContent.trim();
        if (!(label in stat)) stat[label] = [k[0].textContent.trim(), k[2].textContent.trim()];
      }
      const pair = (label, home, away, f = int) => {
        const v = stat[label];
        if (!v) return;
        row[home] = f(v[0]); row[away] = f(v[1]);
      };
      pair("Corner kicks", "HomeCorners", "AwayCorners");
      pair("Fouls", "HomeFouls", "AwayFouls");
      pair("Ball possession", "HomePossession", "AwayPossession");
      pair("Crosses", "HomeCrosses", "AwayCrosses", v => int(v.includes("/") ? v.split("/")[1] : v));
      if (!stat["Corner kicks"]) row.notes.push("stats tab has no corner kicks row");
    } else row.notes.push("stats tab didn't load");
    t.f.remove();
    return row;
  };

  const rows = [];
  for (let i = 0; i < todo.length; i += PARALLEL) {
    const got = await Promise.all(todo.slice(i, i + PARALLEL).map(x => one(x).catch(e => ({ ...x, status: "page_error", notes: [String(e)] }))));
    rows.push(...got);
  }
  // checksum of the rows, so espn_check.py can tell the saved file is an exact copy
  const body = JSON.stringify(rows);
  let h = 0x811c9dc5;
  for (let i = 0; i < body.length; i++) { h ^= body.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; }
  window.flashscoreStats = JSON.stringify({ fetched: new Date().toISOString(), matches: todo.length,
                                            check: h.toString(16), rows });
})().catch(e => { window.flashscoreStats = "ERROR: " + e; });
"started: read window.flashscoreStats in a few seconds";
