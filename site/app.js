/* US Measles Risk Tracker — renders data/latest/site.json. No build step; d3 v7 + topojson-client. */
(async function () {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  let site, us;
  try {
    [site, us] = await Promise.all([d3.json("data/latest/site.json"), d3.json("site/counties-albers-10m.json")]);
  } catch (e) {
    $("#app").innerHTML = `<div class="err"><h2>The forecast data could not be loaded.</h2>
      <p>If you opened this file straight from disk, serve the folder instead:<br><code>python3 -m http.server 8000</code> and open <code>http://localhost:8000</code>.</p></div>`;
    $("#stamp").textContent = "";
    return;
  }

  // ------------------------------------------------------------------ data
  const M = site.meta;
  const cols = site.county_columns;
  const counties = site.counties.map((r) => Object.fromEntries(cols.map((c, i) => [c, r[i]])));
  const byFips = new Map(counties.map((c) => [c.fips, c]));
  const stateOf = new Map(site.states.map((s) => [s.state_abbr, s.state]));
  const fipsState = (f) => f.slice(0, 2);
  const cuts = site.tier_cuts;               // [50, 150, 500]
  const tiers = site.tiers;                  // ["Top 50", "51–150", "151–500", "501+"]
  const K = 50;
  const quiet = counties.filter((c) => c.status === "quiet");
  const meanP = { "1wk": d3.mean(quiet, (c) => c.p_1wk), "4wk": d3.mean(quiet, (c) => c.p_4wk) };
  const hasProb = meanP["1wk"] != null && !isNaN(meanP["1wk"]);

  const S = { h: "1wk", state: "", sel: null, n: 50 };

  // ------------------------------------------------------------------ formatting
  const pd = (s) => new Date(s + "T12:00:00");
  const fDay = d3.timeFormat("%b %-d");
  const fDayY = d3.timeFormat("%b %-d, %Y");
  const fWk = d3.timeFormat("%a %b %-d, %Y");
  const range = (a, b) => {
    const A = pd(a), B = pd(b);
    return A.getMonth() === B.getMonth() ? `${fDay(A)}–${B.getDate()}, ${B.getFullYear()}` : `${fDay(A)} – ${fDayY(B)}`;
  };
  const pct = (p) => (p == null || isNaN(p) ? "–" : p < 0.001 ? "<0.1%" : (p * 100).toFixed(p < 0.1 ? 1 : 0) + "%");
  const rr = (p, h) => (p == null || !hasProb ? "–" : (p / meanP[h] >= 10 ? Math.round(p / meanP[h]) : (p / meanP[h]).toFixed(1)) + "×");
  const lastCase = (w) => (w == null || w < 0 ? "never" : w === 0 ? "this week" : w === 1 ? "last week" : w >= 99 ? "99+ wk ago" : `${w} wk ago`);
  const mmrTxt = (m) => (m == null ? "–" : Math.round(m * 1000) / 10 + "%");
  const n0 = d3.format(",");
  const tierIdx = (r) => (r == null ? -1 : r <= cuts[0] ? 0 : r <= cuts[1] ? 1 : r <= cuts[2] ? 2 : 3);
  const hLabel = (h) => (h === "1wk" ? "next week" : "the next 4 weeks");
  const target = { "1wk": range(M.target_week_start, M.target_week_end), "4wk": range(M.target_week_start, M.target_4wk_end) };

  $("#stamp").innerHTML = `Forecast for <b>${range(M.target_week_start, M.target_week_end)}</b> · data reported through ${fWk(pd(M.as_of))} · <a href="#data">Download data</a> · <a href="#about">How it works</a>`;

  // ------------------------------------------------------------------ tiles
  const perf = site.performance || {};
  const nat = site.national;
  const lastWeek = nat[nat.length - 1] || { cases: 0 };
  const sc1 = (site.scorecard || []).filter((r) => r.horizon === "1wk");
  const lastScore = sc1[sc1.length - 1];
  const p1 = perf["1wk"] && perf["1wk"].model;
  const tile = (label, value, note) => `<div class="card tile"><div class="label">${label}</div><div class="value">${value}</div><div class="note">${note}</div></div>`;
  $("#tiles").innerHTML = [
    tile("Counties reporting cases this week", n0(M.counties_active), `${n0(lastWeek.cases)} cases reported ${fDay(pd(M.origin_week))}–${fDay(pd(M.as_of))}`),
    tile("Expected newly reporting counties", hasProb ? "≈ " + Math.round(M.expected_onsets_1wk) : "–",
      hasProb ? `next week; about ${Math.round(M.expected_onsets_4wk)} over the next 4 weeks` : "calibration not available"),
    tile("Top-50 list, recent weeks", p1 ? `${Math.round((100 * p1["caught@50"]) / p1.onsets_per_week)}%` : "–",
      p1 ? `of newly reporting counties were on the list (${p1["caught@50"].toFixed(1)} of ${p1.onsets_per_week.toFixed(1)} a week over ${p1.weeks} weeks, backtest)` : "backtest not run"),
    tile("Last scored forecast", lastScore ? `${lastScore.caught_top50} of ${lastScore.onsets}` : "–",
      lastScore ? `newly reporting counties in the week of ${fDay(pd(lastScore.target_week_start))} were on that week's top-50 list` : "the first live forecast is scored after next Friday's update"),
  ].join("");

  // ------------------------------------------------------------------ controls
  const stSel = $("#state");
  site.states.slice().sort((a, b) => d3.ascending(a.state, b.state)).forEach((s) => {
    stSel.insertAdjacentHTML("beforeend", `<option value="${esc(s.state_abbr)}">${esc(s.state)}</option>`);
  });
  const dl = $("#county-list");
  const labelOf = (c) => `${c.county}, ${c.state_abbr}`;
  const byLabel = new Map(counties.map((c) => [labelOf(c).toLowerCase(), c]));
  dl.innerHTML = counties.map((c) => `<option value="${esc(labelOf(c))}"></option>`).join("");
  const onSearch = (e) => {
    const c = byLabel.get(e.target.value.trim().toLowerCase());
    if (c && c.fips !== S.sel) { setState(c.state_abbr, false); select(c.fips, true); }
  };
  $("#search").addEventListener("change", onSearch);
  $("#search").addEventListener("input", onSearch);
  document.querySelectorAll(".seg button").forEach((b) => b.addEventListener("click", () => {
    S.h = b.dataset.h; S.n = 50;
    document.querySelectorAll(".seg button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    render();
  }));
  stSel.addEventListener("change", () => setState(stSel.value, true));
  $("#reset").addEventListener("click", () => setState("", true));
  $("#more").addEventListener("click", () => { S.n += 50; renderList(); });

  function setState(abbr, clearSel) {
    S.state = abbr; S.n = 50; stSel.value = abbr;
    if (clearSel) S.sel = null;
    zoomTo(abbr); render();
  }

  // ------------------------------------------------------------------ map
  const svg = d3.select("#map").attr("viewBox", "0 0 975 610");
  const gRoot = svg.append("g");
  const path = d3.geoPath();
  const feats = topojson.feature(us, us.objects.counties).features;
  const stateFeats = topojson.feature(us, us.objects.states).features;
  const abbrOfStateFips = new Map(feats.map((f) => [fipsState(f.id), byFips.get(f.id) && byFips.get(f.id).state_abbr]));
  const cPaths = gRoot.append("g").selectAll("path").data(feats).join("path").attr("class", "county").attr("d", path);
  gRoot.append("path").datum(topojson.mesh(us, us.objects.states, (a, b) => a !== b)).attr("class", "states").attr("d", path);
  gRoot.append("path").datum(topojson.feature(us, us.objects.nation)).attr("class", "nation").attr("d", path);
  const selPath = gRoot.append("path").attr("class", "sel");
  const tip = $("#tip");
  const mapWrap = $(".map-wrap");

  function cls(f) {
    const c = byFips.get(f.id);
    if (!c) return "county nodata";
    const t = tierIdx(c["rank_" + S.h]);
    let k = c.status === "active" ? "active" : t < 0 ? "nodata" : "t" + t;
    if (S.state && c.state_abbr !== S.state) k += " dim";
    return "county " + k;
  }
  function tipHtml(c) {
    if (!c) return "No forecast for this county.";
    const h = S.h;
    let s = `<b>${esc(c.county)}, ${esc(c.state_abbr)}</b><br>`;
    if (c.status === "active") {
      s += `<span class="k">Reporting cases this week:</span> ${c.cases_this_week}<br><span class="k">Not scored — the model forecasts counties with no cases this week.</span>`;
    } else {
      s += `<span class="k">Chance of a case ${hLabel(h)}:</span> ${pct(c["p_" + h])}<br>`;
      s += `<span class="k">National rank:</span> ${n0(c["rank_" + h])} of ${n0(quiet.length)} · ${tiers[tierIdx(c["rank_" + h])]}<br>`;
      s += `<span class="k">Last reported case:</span> ${lastCase(c.weeks_since_last_case)}`;
    }
    return s;
  }
  cPaths
    .on("pointermove", (ev, f) => {
      const c = byFips.get(f.id);
      tip.innerHTML = tipHtml(c); tip.style.display = "block";
      const r = mapWrap.getBoundingClientRect();
      let x = ev.clientX - r.left + 14, y = ev.clientY - r.top + 14;
      const w = tip.offsetWidth, hgt = tip.offsetHeight;
      if (x + w > r.width) x = ev.clientX - r.left - w - 14;
      if (y + hgt > r.height) y = Math.max(0, ev.clientY - r.top - hgt - 14);
      tip.style.left = x + "px"; tip.style.top = y + "px";
    })
    .on("pointerleave", () => { tip.style.display = "none"; })
    .on("click", (ev, f) => { if (byFips.get(f.id)) select(f.id, false); });

  let zoomK = 1;
  function zoomTo(abbr) {
    let t = d3.zoomIdentity;
    if (abbr) {
      const sf = stateFeats.find((s) => abbrOfStateFips.get(s.id) === abbr);
      if (sf) {
        const [[x0, y0], [x1, y1]] = path.bounds(sf);
        const k = Math.min(8, 0.9 / Math.max((x1 - x0) / 975, (y1 - y0) / 610));
        t = d3.zoomIdentity.translate(975 / 2, 610 / 2).scale(k).translate(-(x0 + x1) / 2, -(y0 + y1) / 2);
      }
    }
    zoomK = t.k;
    gRoot.transition().duration(600).attr("transform", t.toString());
    $("#reset").hidden = !abbr;
  }

  function renderMap() {
    cPaths.attr("class", cls);
    const f = S.sel && feats.find((x) => x.id === S.sel);
    selPath.attr("d", f ? path(f) : null);
    $("#map-title").textContent = `Chance of a reported case ${hLabel(S.h)}`;
    $("#map-sub").textContent = S.h === "1wk"
      ? `Week of ${target["1wk"]}. Counties are shaded by national rank among the ${n0(quiet.length)} counties with no cases this week.`
      : `${target["4wk"]}. Counties are shaded by national rank among the ${n0(quiet.length)} counties with no cases this week.`;
    const sw = (v) => `<span class="sw" style="background:var(${v})"></span>`;
    $("#legend").innerHTML = `<span class="ttl">Rank</span>` +
      tiers.map((t, i) => `<span>${sw("--tier-" + i)}${esc(t)}</span>`).join("") +
      `<span>${sw("--active")}Reporting cases this week (not ranked)</span>`;
    const reg = site.regions || [];
    $("#regions-note").textContent = reg.length
      ? "Also reported in the last 4 weeks without a county, so not shown on the map: " +
        reg.map((r) => `${r.name === "Unknown County" ? "county not given" : r.name}, ${r.state} (${r.value})`).join("; ") + "."
      : "";
  }

  // ------------------------------------------------------------------ detail + list
  function select(fips, scroll) {
    S.sel = fips; renderMap(); renderDetail(); renderList();
    if (scroll) $("#detail").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
  function renderDetail() {
    const c = S.sel && byFips.get(S.sel);
    if (!c) { $("#detail").innerHTML = `<div class="placeholder">Select a county on the map or in the list to see its details.</div>`; return; }
    const st = stateOf.get(c.state_abbr) || c.state_abbr;
    const badge = c.status === "active"
      ? `<span class="badge"><span class="sw" style="background:var(--active)"></span>reporting cases</span>`
      : `<span class="badge"><span class="sw" style="background:var(--tier-${tierIdx(c["rank_" + S.h])})"></span>${esc(tiers[tierIdx(c["rank_" + S.h])])}</span>`;
    let rows = "";
    if (c.status === "active") {
      rows += `<div class="k">Status</div><div>Reporting cases this week, so not ranked</div>`;
    } else {
      rows += `<div class="k">Next week</div><div>${pct(c.p_1wk)} · rank ${n0(c.rank_1wk)} nationally, ${n0(c.state_rank_1wk)} in ${esc(st)}</div>`;
      rows += `<div class="k">Next 4 weeks</div><div>${pct(c.p_4wk)} · rank ${n0(c.rank_4wk)} nationally, ${n0(c.state_rank_4wk)} in ${esc(st)}</div>`;
    }
    rows += `<div class="k">Cases</div><div>${c.cases_this_week} this week · ${c.cases_last_4_weeks} in 4 weeks · ${n0(c.cumulative_cases)} since Jan 2025</div>`;
    rows += `<div class="k">Last case</div><div>${lastCase(c.weeks_since_last_case)}</div>`;
    rows += `<div class="k">Kindergarten MMR</div><div>${c.mmr_coverage == null ? "not published at county level" : mmrTxt(c.mmr_coverage)}</div>`;
    $("#detail").innerHTML = `<div class="name">${esc(c.county)}, ${esc(st)} ${badge}</div><div class="kv">${rows}</div>`;
  }
  function listRows() {
    const h = S.h, rk = S.state ? "state_rank_" + h : "rank_" + h;
    return quiet.filter((c) => !S.state || c.state_abbr === S.state).sort((a, b) => a[rk] - b[rk]);
  }
  function renderList() {
    const h = S.h, rows = listRows();
    const rk = S.state ? "state_rank_" + h : "rank_" + h;
    const where = S.state ? (stateOf.get(S.state) || S.state) : "the US";
    $("#list-title").textContent = S.state ? `Watchlist: ${where}` : "Watchlist: top 50 nationally";
    $("#list-sub").textContent = `Counties with no cases this week, ranked by their chance of reporting one ${hLabel(h)} (${target[h]})${S.state ? ", by rank within the state" : ""}. "vs. avg" compares with the average such county.`;
    $("#watch thead").innerHTML = `<tr><th class="num" title="${S.state ? "Rank within the state" : "National rank"}">#</th><th>County</th><th class="num">Chance</th><th class="num hide-sm">vs. avg</th><th>Last case</th></tr>`;
    $("#watch tbody").innerHTML = rows.slice(0, S.n).map((c) =>
      `<tr tabindex="0" data-f="${c.fips}" class="${c.fips === S.sel ? "is-sel" : ""}"><td class="num">${c[rk]}</td><td class="cty">${esc(c.county)}${S.state ? "" : ", " + esc(c.state_abbr)}</td>` +
      `<td class="num">${pct(c["p_" + h])}</td><td class="num hide-sm">${rr(c["p_" + h], h)}</td><td>${lastCase(c.weeks_since_last_case)}</td></tr>`).join("");
    $("#more").hidden = rows.length <= S.n;
    document.querySelectorAll("#watch tbody tr").forEach((tr) => {
      tr.addEventListener("click", () => select(tr.dataset.f, false));
      tr.addEventListener("keydown", (e) => { if (e.key === "Enter") select(tr.dataset.f, false); });
    });
  }

  // ------------------------------------------------------------------ charts
  function barChart(el, data, opt) {
    const W = 560, H = 220, m = { t: 8, r: 8, b: 26, l: 40 };
    const x = d3.scaleBand().domain(data.map((d) => d.x)).range([m.l, W - m.r]).paddingInner(0.2);
    const ymax = d3.max(data, (d) => d.total) || 1;
    const y = d3.scaleLinear().domain([0, ymax]).nice().range([H - m.b, m.t]);
    const s = d3.select(el).html("").append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("role", "img").attr("aria-label", opt.aria);
    s.append("g").attr("class", "grid").attr("transform", `translate(${m.l},0)`)
      .call(d3.axisLeft(y).ticks(4).tickSize(-(W - m.l - m.r)).tickFormat(""));
    s.append("g").attr("class", "axis").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(4).tickSizeOuter(0));
    const ticks = data.filter((d, i) => opt.tick(d, i)).map((d) => d.x);
    s.append("g").attr("class", "axis").attr("transform", `translate(0,${H - m.b})`)
      .call(d3.axisBottom(x).tickValues(ticks).tickFormat(opt.fmtX).tickSizeOuter(0));
    const bw = x.bandwidth(), r = Math.min(4, bw / 2);
    const col = s.append("g").selectAll("g").data(data).join("g").attr("class", "col");
    // stacked segments: bottom segment "b", optional top segment "rest"; 2px surface gap between them
    col.append("path").attr("class", "b").style("fill", "var(--bar)")
      .attr("d", (d) => roundTop(x(d.x), y(d.a), bw, y(0) - y(d.a), d.b > 0 ? 0 : r));
    if (opt.stack) col.filter((d) => d.b > 0).append("path").style("fill", "var(--bar-rest)")
      .attr("d", (d) => roundTop(x(d.x), y(d.a + d.b), bw, Math.max(0, y(d.a) - y(d.a + d.b) - (d.a > 0 ? 2 : 0)), r));
    col.append("rect").attr("class", "hit").attr("x", (d) => x(d.x) - (x.step() - bw) / 2).attr("width", x.step())
      .attr("y", m.t).attr("height", H - m.t - m.b)
      .on("pointermove", (ev, d) => {
        ctip.innerHTML = opt.tip(d); ctip.style.display = "block";
        const rr_ = el.getBoundingClientRect();
        let px = ev.clientX - rr_.left + 12; if (px + ctip.offsetWidth > rr_.width) px = ev.clientX - rr_.left - ctip.offsetWidth - 12;
        ctip.style.left = px + "px"; ctip.style.top = Math.max(0, ev.clientY - rr_.top - ctip.offsetHeight - 8) + "px";
      })
      .on("pointerleave", () => { ctip.style.display = "none"; });
    el.style.position = "relative";
    const ctip = document.createElement("div"); ctip.className = "tooltip"; el.appendChild(ctip);
  }
  function roundTop(x, y, w, h, r) {
    if (h <= 0) return "";
    r = Math.min(r, h, w / 2);
    return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
  }

  function renderCases() {
    const data = nat.map((d) => ({ x: d.week, a: d.cases, b: 0, total: d.cases, onsets: d.onsets }));
    barChart($("#cases-chart"), data, {
      aria: "Weekly reported measles cases in the US",
      tick: (d, i) => i === 0 || pd(d.x).getMonth() !== pd(data[i - 1].x).getMonth() && [0, 3, 6, 9].includes(pd(d.x).getMonth()),
      fmtX: (v) => d3.timeFormat(pd(v).getMonth() === 0 ? "%b %Y" : "%b")(pd(v)),
      tip: (d) => `<b>Week of ${fDayY(pd(d.x))}</b><br>${n0(d.a)} cases<br><span class="k">${d.onsets} counties reported a case after a week without one</span>`,
    });
    $("#cases-tbl").innerHTML = `<thead><tr><th>Week of</th><th class="num">Cases</th><th class="num">Newly reporting counties</th></tr></thead><tbody>` +
      nat.slice().reverse().map((d) => `<tr><td>${d.week}</td><td class="num">${d.cases}</td><td class="num">${d.onsets}</td></tr>`).join("") + "</tbody>";
  }
  function renderTrack() {
    const h = S.h, bw = (site.backtest_weekly || []).filter((d) => d.horizon === h);
    const p = perf[h] || {}, mo = p.model, ru = p.rule_recency_population;
    $("#track-title").textContent = `How the top-50 list has done (${h === "1wk" ? "next-week" : "4-week"} forecast)`;
    $("#track-sub").textContent = mo
      ? `Backtest over the last ${mo.weeks} weeks: the model was refit each week using only earlier weeks. Each bar is one forecast week's newly reporting counties; the dark part were on that week's top-50 list. On average ${mo["caught@50"].toFixed(1)} of ${mo.onsets_per_week.toFixed(1)} a week (${Math.round((100 * mo["caught@50"]) / mo.onsets_per_week)}%)` +
        (ru ? `; a simple rule (recent cases plus population) caught ${ru["caught@50"].toFixed(1)}.` : ".")
      : "The backtest has not been run yet.";
    $("#track-legend").innerHTML = `<span><span class="sw" style="background:var(--bar)"></span>On the top-50 list</span><span><span class="sw" style="background:var(--bar-rest)"></span>Not on the list</span>`;
    const tgt = (w) => d3.timeFormat("%Y-%m-%d")(d3.timeDay.offset(pd(w), 7));
    const data = bw.map((d) => ({ x: tgt(d.week), a: d["caught@50"], b: d.onsets - d["caught@50"], total: d.onsets, rule: d.caught_rule }));
    barChart($("#track-chart"), data, {
      aria: "Weekly newly reporting counties and how many were on the top-50 list", stack: true,
      tick: (d, i) => i % 4 === 0,
      fmtX: (v) => fDay(pd(v)),
      tip: (d) => `<b>${h === "1wk" ? "Week of" : "4 weeks from"} ${fDayY(pd(d.x))}</b><br>${d.total} newly reporting counties<br>${d.a} on the top-50 list<br><span class="k">simple rule (recent cases + population): ${d.rule}</span>`,
    });
    $("#track-tbl").innerHTML = `<thead><tr><th>${h === "1wk" ? "Week of" : "4 weeks from"}</th><th class="num">Newly reporting</th><th class="num">On top-50 list</th><th class="num">Simple rule</th></tr></thead><tbody>` +
      data.slice().reverse().map((d) => `<tr><td>${d.x}</td><td class="num">${d.total}</td><td class="num">${d.a}</td><td class="num">${d.rule}</td></tr>`).join("") + "</tbody>";
  }

  // ------------------------------------------------------------------ live record, states
  function renderLive() {
    const h = S.h, rows = (site.scorecard || []).filter((r) => r.horizon === h).slice().reverse();
    if (!rows.length) {
      $("#live-body").innerHTML = `<p class="sub">No archived forecast has been scored yet. The first ${h === "1wk" ? "next-week" : "4-week"} forecast is scored ${h === "1wk" ? "after the next Friday update" : "four weeks after it is made"}.</p>`;
      return;
    }
    const on = (site.scorecard_onsets || []).filter((r) => r.horizon === h && r.target_week_start === rows[0].target_week_start)
      .sort((a, b) => a.rank - b.rank);
    const lbl = h === "1wk" ? "Forecast for week of" : "4 weeks from";
    let html = `<div class="tbl-wrap" style="max-height:300px"><table><thead><tr><th>${lbl}</th><th class="num">Newly reporting counties</th><th class="num">On top-50 list</th><th class="num hide-sm">Expected</th><th class="num hide-sm">AUROC</th></tr></thead><tbody>` +
      rows.map((r) => `<tr><td>${r.target_week_start}</td><td class="num">${r.onsets}</td><td class="num">${r.caught_top50}</td><td class="num hide-sm">${r.expected_onsets == null ? "–" : r.expected_onsets.toFixed(1)}</td><td class="num hide-sm">${r.AUROC == null ? "–" : r.AUROC.toFixed(2)}</td></tr>`).join("") +
      `</tbody></table></div>`;
    const tot = d3.sum(rows, (r) => r.onsets), cau = d3.sum(rows, (r) => r.caught_top50);
    html += `<p class="note-line">Across ${rows.length} scored forecasts, ${cau} of ${tot} newly reporting counties (${tot ? Math.round((100 * cau) / tot) : 0}%) were on the top-50 list.` +
      (on.length ? ` Latest (${lbl.toLowerCase()} ${fDay(pd(rows[0].target_week_start))}): ` + on.map((o) => `${esc(o.county)}, ${esc(o.state_abbr)} (rank ${o.rank})`).join("; ") + "." : "") + `</p>`;
    $("#live-body").innerHTML = html;
  }
  function renderStates() {
    const h = S.h;
    const rows = site.states.slice().sort((a, b) => d3.descending(a["expected_new_counties_" + h], b["expected_new_counties_" + h]));
    $("#states thead").innerHTML = `<tr><th>State</th><th class="num">Counties reporting this week</th><th class="num">Cases, last 4 weeks</th><th class="num">Expected newly reporting counties, ${hLabel(h)}</th><th class="num hide-sm">Counties in top 50</th></tr>`;
    $("#states tbody").innerHTML = rows.map((s) =>
      `<tr tabindex="0" data-s="${esc(s.state_abbr)}" class="${s.state_abbr === S.state ? "is-sel" : ""}"><td>${esc(s.state)}</td><td class="num">${s.active_counties}</td><td class="num">${n0(s.cases_last_4_weeks)}</td>` +
      `<td class="num">${s["expected_new_counties_" + h] == null ? "–" : s["expected_new_counties_" + h].toFixed(1)}</td><td class="num hide-sm">${s.counties_in_top50}</td></tr>`).join("");
    document.querySelectorAll("#states tbody tr").forEach((tr) => {
      const go = () => { setState(tr.dataset.s === S.state ? "" : tr.dataset.s, true); $("#map").scrollIntoView({ behavior: "smooth", block: "center" }); };
      tr.addEventListener("click", go);
      tr.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
    });
  }

  // ------------------------------------------------------------------ about, data, footer
  const repo = (() => {
    const h = location.hostname, p = location.pathname.split("/").filter(Boolean);
    return h.endsWith("github.io") && p.length ? `https://github.com/${h.split(".")[0]}/${p[0]}` : null;
  })();
  const src = site.sources;
  const srcLink = (k) => `<a href="${src[k].repo}">${src[k].repo.replace("https://github.com/", "")}</a>`;
  const b1 = perf["1wk"] && perf["1wk"].model, b4 = perf["4wk"] && perf["4wk"].model;
  $("#about-body").innerHTML = `
    <p><b>What is forecast.</b> For every US county that reported no measles cases this week, the chance that it reports at least one confirmed case next week, and at least one over the next four weeks. Counties already reporting cases are shown in orange and are not ranked; forecasting how an active outbreak grows is a different problem.</p>
    <p><b>Data.</b> Confirmed cases by county and report date from the JHU Measles Tracking Team (${srcLink("cases")}), updated on Fridays; kindergarten MMR coverage by county (${srcLink("mmr")}). Static county inputs: population and the CDC Social Vulnerability Index (2022), ACS 2016–2020 county-to-county commuting flows, 2024 presidential vote share and COVID-19 vaccine uptake.</p>
    <p><b>Model.</b> A gradient-boosted tree classifier (XGBoost) with 37 inputs: each county's recent and cumulative cases and weeks since its last case; cases elsewhere in the state and the country; cases in counties linked to it by commuting, including two- and three-step links; season; and the static county inputs. It is retrained every week on all county-weeks reported so far. The four-week model never sees the future: it learns from four-week windows that have closed, from cases already seen, and from still-open windows counted as partial non-cases. Scores are converted to probabilities with a calibration (Platt scaling) fitted on the last 8 weeks of out-of-sample forecasts; the ranking does not depend on it.</p>
    <p><b>How well it works.</b> In the 81-week evaluation in the working paper, the top 50 counties each week included on average 37% of the counties that went on to report a case the next week (2.9 of 8.2 a week) and 7.4 of 26 over four weeks, with AUROC 0.84 at both horizons.${b1 ? ` Over the last ${b1.weeks} weeks: ${b1["caught@50"].toFixed(1)} of ${b1.onsets_per_week.toFixed(1)} a week for next week (AUROC ${b1.AUROC.toFixed(2)})` : ""}${b4 ? ` and ${b4["caught@50"].toFixed(1)} of ${b4.onsets_per_week.toFixed(1)} for four weeks (AUROC ${b4.AUROC.toFixed(2)}).` : "."}</p>
    <p><b>How to read it.</b> The ranking is more reliable than the exact percentage. A case in a county that had none the week before is rare (about 0.3% of such counties a week), so even the top county usually has well under a 50% chance. Probabilities are calibrated to recent weeks, so they lag when activity changes quickly: they tend to run low while outbreaks spread and high as they fade (the live record below compares expected and observed counts). Use the list to prioritize surveillance and outreach, not as an alarm. Most correctly flagged counties had recent cases themselves or nearby; first introductions far from any activity are hard to foresee.</p>
    <p><b>Limitations.</b> The forecast inherits the reporting of the underlying data: cases appear when health departments report them, some jurisdictions report only by health region or without a county (those cases count toward state activity but cannot be placed on the map), reports dated on a weekend are added the following Friday, and past weeks are sometimes revised. Cases reported under Connecticut's former county codes cannot be matched to its 2022 planning regions. MMR coverage is not published at county level in every state; the state average is used there.</p>
    <p class="muted">This is a research forecast, not a public-health advisory. Method: F. Ahmadi, L. Gardner et al., county-level early warning for measles from granular surveillance data (working paper, 2026). Model version ${esc(M.model_version)}.</p>`;
  const base = repo ? repo + "/blob/main/" : "";
  $("#data-body").innerHTML = `
    <p>All forecasts are free to use under <a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>. Please cite the tracker and the JHU Measles Tracking Team data.</p>
    <ul>
      <li><a href="data/latest/forecast.csv" download>data/latest/forecast.csv</a> — this week's forecast, one row per county (3,144 rows).</li>
      <li><a href="data/archive/index.csv">data/archive/index.csv</a> — list of every published forecast; each is in <code>data/archive/forecasts/&lt;week&gt;.csv</code>, named by the first day of the forecast week.</li>
      <li><a href="data/evaluation/scorecard.csv">data/evaluation/scorecard.csv</a> and <a href="data/evaluation/scorecard_onsets.csv">scorecard_onsets.csv</a> — how each archived forecast did.</li>
      <li><a href="data/evaluation/backtest_weekly.csv">data/evaluation/backtest_weekly.csv</a> — this week's 26-week backtest.</li>
      <li><a href="data/latest/metadata.json">data/latest/metadata.json</a> — run details, including the exact upstream commits used.</li>
    </ul>
    <p>Main columns in <code>forecast.csv</code>:</p>
    <dl class="cols">
      <dt>prob_case_next_week</dt><dd>calibrated chance of at least one reported case in the forecast week (empty for counties reporting cases this week)</dd>
      <dt>national_rank_next_week</dt><dd>rank among counties with no cases this week (1 = highest risk); <code>state_rank_next_week</code> within the state</dd>
      <dt>tier_next_week</dt><dd>Top 50, 51–150, 151–500 or 501+</dd>
      <dt>relative_risk_next_week</dt><dd>chance divided by the average for counties with no cases this week</dd>
      <dt>…_next_4_weeks</dt><dd>the same for the four weeks starting with the forecast week</dd>
      <dt>model_score_…</dt><dd>uncalibrated model score, used for ranking</dd>
      <dt>status</dt><dd><code>quiet</code> (no cases this week, ranked) or <code>active</code> (reporting cases, not ranked)</dd>
    </dl>
    ${repo ? `<p>Code and full history: <a href="${repo}">${repo.replace("https://github.com/", "")}</a>.</p>` : ""}`;
  $("#footer").innerHTML = `Case data: JHU Measles Tracking Team Data (CC BY 4.0). MMR coverage: Dong E, et al. JAMA 2025. Map: U.S. Census Bureau cartographic boundaries (2023) via us-atlas.
    Built with d3. Updated ${fWk(pd(M.as_of))} from measles_data@${esc(String(M.cases_commit).slice(0, 7))}.`;

  // ------------------------------------------------------------------ go
  function render() { renderMap(); renderDetail(); renderList(); renderTrack(); renderLive(); renderStates(); }
  renderCases();
  render();
})();
