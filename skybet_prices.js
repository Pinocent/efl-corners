// Sky Bet's price for each of the board's upcoming calls, read from oddschecker.
// Run by the nightly task in the browser, on any oddschecker.com page (oddschecker
// refuses requests that don't come from a browser). It runs in the background
// and leaves a JSON string in window.skybetPrices (null while working, or
// "ERROR: ..."): read that with a second call and save it as skybet_prices.json;
// run.py attaches the prices to the calls.
// Calls are made first, from statistics alone; the price is only a check on them.
window.skybetPrices = null;
(async () => {
  const DAYS = 3;                                       // calls for matches this many days ahead
  const LEAGUES = { "Championship": "championship", "League 1": "league-1", "League 2": "league-2" };
  const MARKETS = { "Corners total": "Total Corners", "Cards total": "Total Cards", "Corner dominance": "Most Corners",
                    "BTTS": "Both Teams To Score", "Goals 2.5": "Total Goals Over/Under" };
  const board = await fetch("https://pinocent.github.io/efl-corners/data.json?x=" + Date.now()).then(r => r.json());
  const now = Date.now(), until = now + DAYS * 864e5;
  const kickoff = x => {                                // board times are UK time
    const d = new Date(`${x.date}T${x.time || "15:00"}:00Z`);
    const at = tz => new Date(d.toLocaleString("en-US", { timeZone: tz }));
    return d - (at("Europe/London") - at("UTC"));
  };
  const todo = board.fixtures.filter(x => x.model === "v3" && !x.off && x.hc == null && (x.calls || []).length
                                     && kickoff(x) > now && kickoff(x) < until);

  // club names: the board's spelling against oddschecker's address for the match
  const FILL = new Set(["fc", "afc", "utd", "united", "city", "town", "county", "rovers", "athletic", "wanderers", "albion"]);
  const words = s => s.toLowerCase().replace(/[^a-z0-9 ]/g, " ").split(/\s+/).filter(Boolean)
    .map(w => ({ wed: "wednesday", qpr: "qpr", mk: "mk", wolves: "wolverhampton" }[w] || w));
  const same = (board, oc) => {
    const b = words(board), o = words(oc.replace(/-/g, " "));
    const core = b.filter(w => !FILL.has(w));
    return (core.length ? core : b).every(w => o.some(x => x.startsWith(w) || w.startsWith(x)))
        && b.filter(w => FILL.has(w) && !["utd", "united"].includes(w)).every(w => o.includes(w));
  };

  const pages = {};
  for (const lg of new Set(todo.map(x => x.league))) {
    const html = await fetch(`/football/english/${LEAGUES[lg]}`).then(r => r.text());
    // links are written with and without a leading slash
    pages[lg] = [...new Set([...html.matchAll(new RegExp(`football/english/${LEAGUES[lg]}/([a-z0-9-]+)-v-([a-z0-9-]+)/winner`, "g"))]
      .map(m => "/" + m[0] + "|" + m[1] + "|" + m[2]))].map(s => s.split("|"));
  }

  const rows = [], missing = [];
  const one = async x => {
    const page = (pages[x.league] || []).find(([, h, a]) => same(x.home, h) && same(x.away, a));
    if (!page) { missing.push([x.date, x.home, x.away, "match not on oddschecker"]); return; }
    const html = await fetch(page[0]).then(r => r.text());
    const ids = [...new Set([...html.matchAll(/\b(3\d{9})\b/g)].map(m => m[1]))];
    const mk = {}, batches = [];
    for (let i = 0; i < ids.length; i += 25) batches.push(ids.slice(i, i + 25));
    await Promise.all(batches.map(async b => {
      const r = await fetch(`/api/markets/v2/all-odds?market-ids=${b.join(",")}&repub=OC`);
      if (r.ok) for (const m of await r.json()) if (Object.values(MARKETS).includes(m.marketTypeName)) mk[m.marketTypeName] = m;
    }));
    for (const c of x.calls) {
      const m = mk[MARKETS[c.market]];
      if (!m) { missing.push([x.date, x.home, x.away, c.market + ": no market"]); continue; }
      const [pick, line] = c.market === "Goals 2.5" ? [c.pick, "2.5"] : c.pick.split(" ");
      const [ocHome, ocAway] = m.subeventName.split(" v ");
      const want = b => c.market === "Corner dominance" ? b.betName === (pick === "Home" ? ocHome : ocAway)
                      : b.betName === pick && (!line || parseFloat(b.line) === parseFloat(line));
      const bet = (m.bets || []).find(want);
      const odd = bet && m.odds.find(o => o.betId === bet.betId && o.bookmakerCode === "SK" && o.status === "ACTIVE");
      if (!odd) { missing.push([x.date, x.home, x.away, `${c.market} ${c.pick}: no Sky Bet price`]); continue; }
      rows.push([x.date, x.league, x.home, x.away, c.market, c.pick, m.subeventName, odd.oddsFractional, odd.oddsDecimal]);
    }
  };
  for (let i = 0; i < todo.length; i += 6) await Promise.all(todo.slice(i, i + 6).map(one));   // six matches at a time
  window.skybetPrices = JSON.stringify({ fetched: new Date().toISOString(), calls: todo.length,
    fields: ["date", "league", "home", "away", "market", "pick", "match", "odds", "decimal"], rows, missing });
})().catch(e => { window.skybetPrices = "ERROR: " + e; });
"started: read window.skybetPrices in a few seconds";
