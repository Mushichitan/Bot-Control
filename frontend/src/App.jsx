import React, { useCallback, useEffect, useState } from "react";

const NAV = ["Dashboard", "Positions", "Signals", "Activity", "Reports", "Settings"];

function badgeClass(status) {
  const s = String(status || "").toUpperCase();
  if (["RUNNING", "CONNECTED", "VALID", "READY", "HEALTHY", "OPEN", "EXECUTED"].includes(s)) return "bg-ok";
  if (["STARTING", "STOPPING", "PAUSED", "UNKNOWN", "UPDATING", "PAPER", "TESTNET"].includes(s)) return "bg-warn";
  if (["CRASHED", "ERROR", "DISCONNECTED", "INVALID", "LIVE", "FAILED"].includes(s)) return "bg-err";
  return "bg-info";
}

function fmt(n, d = 2) {
  if (n === null || n === undefined || n === "") return "—";
  const x = Number(n);
  if (Number.isNaN(x)) return String(n);
  return x.toFixed(d);
}

function fmtPrice(n) {
  if (n === null || n === undefined || n === "") return "—";
  const x = Number(n);
  if (Number.isNaN(x)) return String(n);
  const abs = Math.abs(x);
  if (abs === 0) return "0";
  const digits = abs >= 1000 ? 2 : abs >= 1 ? 4 : abs >= 0.01 ? 6 : 8;
  return x.toFixed(digits);
}

function pnlClass(n) {
  const x = Number(n || 0);
  return x > 0 ? "ok" : x < 0 ? "err" : "";
}

function duration(seconds) {
  if (seconds == null) return "—";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${s}s`;
  return `${s}s`;
}

function fmtTps(tps, fallback) {
  if (Array.isArray(tps) && tps.length) return tps.map((n) => fmtPrice(n)).join(" / ");
  return fmtPrice(fallback);
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text };
  }
  if (!res.ok) {
    const detail = data?.detail || res.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

function StatusCard({ title, value, sub }) {
  return (
    <div className="card">
      <h4>{title}</h4>
      <div className="val">
        <span className={`badge ${badgeClass(value)}`}>{value || "UNKNOWN"}</span>
      </div>
      {sub ? <div className="muted" style={{ marginTop: 8 }}>{sub}</div> : null}
    </div>
  );
}

export default function App() {
  const [page, setPage] = useState("Dashboard");
  const [bots, setBots] = useState([]);
  const [botId, setBotId] = useState(null);
  const [bot, setBot] = useState(null);
  const [health, setHealth] = useState({});
  const [signals, setSignals] = useState([]);
  const [positions, setPositions] = useState([]);
  const [events, setEvents] = useState([]);
  const [logs, setLogs] = useState([]);
  const [pnl, setPnl] = useState({});
  const [hourly, setHourly] = useState([]);
  const [fourHour, setFourHour] = useState([]);
  const [envVars, setEnvVars] = useState([]);
  const [versions, setVersions] = useState([]);
  const [changes, setChanges] = useState(null);
  const [commands, setCommands] = useState([]);
  const [strategyFiles, setStrategyFiles] = useState([]);
  const [runtime, setRuntime] = useState(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [showAdd, setShowAdd] = useState(false);
  const [showLive, setShowLive] = useState(false);
  const [busy, setBusy] = useState(false);
  const [busyKind, setBusyKind] = useState("");

  const loadBots = useCallback(async () => {
    const list = await api("/api/bots");
    setBots(list);
    setBotId((id) => id || list[0]?.id || null);
  }, []);

  const refresh = useCallback(async () => {
    if (!botId) {
      setBot(null);
      return;
    }
    try {
      const [b, h, sig, pos, ev, lg, p, hr, fh, env, ver, ch, cmd, files, rt] = await Promise.all([
        api(`/api/bots/${botId}`),
        api(`/api/health?bot_id=${botId}`),
        api(`/api/bots/${botId}/signals`),
        api(`/api/bots/${botId}/positions`),
        api(`/api/bots/${botId}/events`),
        api(`/api/bots/${botId}/logs`),
        api(`/api/bots/${botId}/pnl`),
        api(`/api/bots/${botId}/reports/hourly`),
        api(`/api/bots/${botId}/reports/four-hour`),
        api(`/api/bots/${botId}/env`),
        api(`/api/bots/${botId}/versions`),
        api(`/api/bots/${botId}/changes`),
        api(`/api/bots/${botId}/telegram/commands`),
        api(`/api/bots/${botId}/strategy/files`),
        api(`/api/bots/${botId}/runtime-settings`),
      ]);
      setBot(b);
      setHealth(h);
      setSignals(sig);
      setPositions(pos);
      setEvents(ev);
      setLogs(lg);
      setPnl(p);
      setHourly(hr);
      setFourHour(fh);
      setEnvVars(env);
      setVersions(ver);
      setChanges(ch);
      setCommands(cmd);
      setStrategyFiles(files);
      setRuntime(rt);
      setError("");
    } catch (e) {
      setError(e.message);
    }
  }, [botId]);

  useEffect(() => {
    loadBots().catch((e) => setError(e.message));
  }, [loadBots]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 2500);
    return () => clearInterval(t);
  }, [refresh]);

  const open = positions.filter((p) => p.status === "OPEN");
  const closed = positions.filter((p) => p.status === "CLOSED");
  const today = pnl.TODAY || {};

  async function control(kind) {
    if (!botId) return;
    setBusy(true);
    setBusyKind(kind);
    setError("");
    const labels = { start: "Starting bot…", stop: "Stopping bot…", restart: "Restarting bot…", pause: "Pausing bot…", resume: "Resuming bot…" };
    setNotice(labels[kind] || `${kind}…`);
    try {
      if (kind === "start" && bot?.trading_mode === "LIVE") {
        setShowLive(true);
        setNotice("LIVE confirmation required");
        return;
      }
      const r = await api(`/api/bots/${botId}/${kind}`, { method: "POST", body: "{}" });
      await loadBots();
      await refresh();
      const status = r?.status || "";
      if (kind === "start") setNotice(status === "RUNNING" ? "Bot started." : `Start requested (${status || "unknown"}).`);
      else if (kind === "stop") setNotice(status === "STOPPED" ? "Bot stopped." : `Stop requested (${status || "unknown"}).`);
      else if (kind === "restart") setNotice(status === "RUNNING" ? "Bot restarted." : `Restart requested (${status || "unknown"}).`);
      else setNotice(`${kind} done (${status || "ok"}).`);
    } catch (e) {
      if (String(e.message).includes("LIVE_CONFIRMATION_REQUIRED")) {
        setShowLive(true);
        setNotice("LIVE confirmation required");
      } else {
        setError(e.message);
        setNotice("");
      }
    } finally {
      setBusy(false);
      setBusyKind("");
    }
  }

  async function confirmLive() {
    setBusy(true);
    setBusyKind("start");
    setNotice("Starting LIVE bot…");
    try {
      await api(`/api/bots/${botId}/start`, {
        method: "POST",
        body: JSON.stringify({ live_confirmed: true }),
      });
      setShowLive(false);
      await loadBots();
      await refresh();
      setNotice("LIVE bot started.");
    } catch (e) {
      setError(e.message);
      setNotice("");
    } finally {
      setBusy(false);
      setBusyKind("");
    }
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          Crypto Bot Control
          <small>App v{health.app_version || "1.0.0"}</small>
        </div>
        {NAV.map((n) => (
          <button key={n} className={`nav-btn ${page === n ? "active" : ""}`} onClick={() => setPage(n)}>
            {n}
          </button>
        ))}
        <div className="bot-list">
          <button className="btn ghost" onClick={() => setShowAdd(true)}>Add Bot</button>
          {bots.map((b) => (
            <button
              key={b.id}
              className={`bot-chip ${b.id === botId ? "active" : ""}`}
              onClick={() => setBotId(b.id)}
            >
              {b.name}
              <div className="muted">{b.status}</div>
            </button>
          ))}
        </div>
      </aside>
      <section className="main">
        <header className="topbar">
          <div>
            <strong>{bot?.name || "No bot selected"}</strong>
            <div className="muted">
              Bot {bot?.version_label || "—"} · Mode {bot?.trading_mode || "—"} · Entry {bot?.entry_point || "—"}
            </div>
          </div>
          <div className="controls">
            <button className="btn primary" disabled={busy || !bot} onClick={() => control("start")}>{busyKind === "start" ? "Starting…" : "Start"}</button>
            <button className="btn danger" disabled={busy || !bot} onClick={() => control("stop")}>{busyKind === "stop" ? "Stopping…" : "Stop"}</button>
            <button className="btn ghost" disabled={busy || !bot} onClick={() => control("restart")}>{busyKind === "restart" ? "Restarting…" : "Restart"}</button>
            {bot?.pause_supported ? (
              <>
                <button className="btn ghost" disabled={busy} onClick={() => control("pause")}>Pause</button>
                <button className="btn ghost" disabled={busy} onClick={() => control("resume")}>Resume</button>
              </>
            ) : null}
          </div>
        </header>
        <div className="page">
          {error ? <div className="warn-box" style={{ marginBottom: 12 }}>{error}</div> : null}
          {notice ? <div className="notice-box" style={{ marginBottom: 12 }}>{notice}</div> : null}
          {!bot ? (
            <div className="panel empty">Import a Python trading-bot project to get started. The app never invents trading logic.</div>
          ) : page === "Dashboard" ? (
            <Dashboard bot={bot} health={health} today={today} open={open} signals={signals} events={events} logs={logs} botId={botId} runtime={runtime} onRefresh={refresh} setError={setError} setNotice={setNotice} />
          ) : page === "Positions" ? (
            <Positions open={open} closed={closed} botId={botId} onRefresh={refresh} setError={setError} setNotice={setNotice} />
          ) : page === "Signals" ? (
            <Signals signals={signals} />
          ) : page === "Activity" ? (
            <Activity events={events} logs={logs} botId={botId} />
          ) : page === "Reports" ? (
            <Reports pnl={pnl} hourly={hourly} fourHour={fourHour} />
          ) : (
            <Settings
              bot={bot}
              envVars={envVars}
              versions={versions}
              changes={changes}
              commands={commands}
              strategyFiles={strategyFiles}
              runtime={runtime}
              onRefresh={async () => {
                await loadBots();
                await refresh();
              }}
              setError={setError}
              setNotice={setNotice}
            />
          )}
        </div>
      </section>
      {showAdd ? (
        <AddBotModal
          onClose={() => setShowAdd(false)}
          onCreated={async (created) => {
            setShowAdd(false);
            await loadBots();
            setBotId(created.id);
          }}
        />
      ) : null}
      {showLive ? (
        <LiveModal bot={bot} onCancel={() => setShowLive(false)} onConfirm={confirmLive} />
      ) : null}
    </div>
  );
}

function Dashboard({ bot, health, today, open, signals, events, logs, botId, runtime, onRefresh, setError, setNotice }) {
  const uptime = bot.last_heartbeat ? new Date(bot.last_heartbeat).toLocaleTimeString() : "—";
  const delayEvt = [...events].reverse().find((e) => e.type === "STARTUP_DELAY");
  const gapEvt = [...events].reverse().find((e) => e.type === "TRADE_COOLDOWN");
  const scanEvt = [...events].reverse().find((e) => e.type === "SCAN_UNIVERSE");
  return (
    <>
      <div className="cards">
        <StatusCard title="Bot" value={health.bot || bot.status} sub={`Last heartbeat ${uptime}`} />
        <StatusCard title="Binance" value={health.binance || bot.binance_status} />
        <StatusCard title="Telegram" value={health.telegram || bot.telegram_status} />
        <StatusCard title="Environment" value={health.environment || bot.env_status} sub={`Deps ${health.dependencies || bot.deps_status}`} />
      </div>
      <div className="grid-2">
        <div className="panel">
          <h3>P&L today</h3>
          <div className={`kpi ${pnlClass(today.realized_pnl)}`}>{fmt(today.realized_pnl)} USDT realized</div>
          <div className="muted">Unrealized {fmt(today.unrealized_pnl)} · Total {fmt(today.total_pnl)} · Win rate {fmt(today.win_rate, 1)}%</div>
        </div>
        <div className="panel">
          <h3>Market scan</h3>
          <div>{runtime?.universe_label || runtime?.scan_mode || "ALL"}</div>
          <div className="muted">Symbols {runtime?.all_symbol_count || 0} · Crypto {runtime?.crypto_symbol_count || 0} · TradFi {runtime?.tradfi_symbol_count || 0}</div>
          <div className="muted" style={{ marginTop: 8 }}>Startup delay {runtime?.startup_delay_seconds || 0}s · Trade gap {runtime?.trade_gap_seconds || 0}s</div>
          {scanEvt ? <div className="muted" style={{ marginTop: 8 }}>{scanEvt.message}</div> : null}
          {delayEvt ? <div className="muted">{delayEvt.message}</div> : null}
          {gapEvt ? <div className="muted">{gapEvt.message}</div> : null}
        </div>
      </div>
      <div className="grid-3" style={{ marginTop: 12 }}>
        <div className="panel">
          <h3>Recent signals</h3>
          {signals.slice(0, 6).map((s) => (
            <div key={s.id}>{s.symbol} {s.side} TP {fmtTps(s.tps, s.tp)} {s.execution_status}</div>
          ))}
          {!signals.length ? <div className="muted">No signals yet</div> : null}
        </div>
        <div className="panel">
          <h3>Open positions</h3>
          {open.slice(0, 6).map((p) => (
            <div key={p.id} className="row">
              <div>{p.number} {p.symbol} {p.side} <span className={pnlClass(p.unrealized_pnl)}>{fmt(p.unrealized_pnl)}</span></div>
              <button className="btn danger" onClick={async () => {
                try {
                  setNotice(`Closing ${p.number} ${p.symbol}…`);
                  await api(`/api/bots/${botId}/positions/${p.id}/close`, { method: "POST", body: JSON.stringify({ reason: "MANUAL" }) });
                  setNotice(`Closed ${p.number} ${p.symbol}`);
                  if (onRefresh) await onRefresh();
                } catch (e) { setError(e.message); }
              }}>Close</button>
            </div>
          ))}
          {!open.length ? <div className="muted">No open positions</div> : null}
        </div>
        <div className="panel">
          <h3>Live activity</h3>
          <div className="feed">
            {events.slice(-12).reverse().map((e) => (
              <div key={e.id}><span className="muted">{(e.created_at || "").slice(11, 16)}</span> [{e.category}] {e.message}</div>
            ))}
          </div>
        </div>
      </div>
      <div className="panel" style={{ marginTop: 12 }}>
        <h3>Bot log</h3>
        <div className="feed">
          {logs.slice(-20).map((l) => (
            <div key={l.id} className="log">{l.line}</div>
          ))}
        </div>
      </div>
    </>
  );
}

function Positions({ open, closed, botId, onRefresh, setError, setNotice }) {
  return (
    <div className="grid-2">
      <div className="panel">
        <h3>Open</h3>
        <PosTable rows={open} open botId={botId} onRefresh={onRefresh} setError={setError} setNotice={setNotice} />
      </div>
      <div className="panel">
        <h3>Closed — TP/SL remain visible</h3>
        <PosTable rows={closed} />
      </div>
    </div>
  );
}

function PosTable({ rows, open, botId, onRefresh, setError, setNotice }) {
  const [busyId, setBusyId] = useState(null);
  if (!rows.length) return <div className="empty">None</div>;
  async function closePos(p) {
    if (!botId) return;
    setBusyId(p.id);
    try {
      setNotice(`Closing ${p.number} ${p.symbol}…`);
      await api(`/api/bots/${botId}/positions/${p.id}/close`, {
        method: "POST",
        body: JSON.stringify({ reason: "MANUAL" }),
      });
      setNotice(`Closed ${p.number} ${p.symbol}`);
      if (onRefresh) await onRefresh();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusyId(null);
    }
  }
  return (
    <table>
      <thead>
        <tr>
          <th>#</th><th>Symbol</th><th>Side</th><th>Entry</th>
          <th>{open ? "Current" : "Exit"}</th><th>TP</th><th>SL</th>
          <th>P&L</th><th>Reason</th><th>Dur</th>
          {open ? <th></th> : null}
        </tr>
      </thead>
      <tbody>
        {rows.map((p) => (
          <tr key={p.id}>
            <td>{p.number}</td>
            <td>{p.symbol}</td>
            <td>{p.side}</td>
            <td>{fmtPrice(p.entry)}</td>
            <td>{fmtPrice(open ? p.current_price : p.exit)}</td>
            <td>{fmtTps(p.tps, p.tp)}</td>
            <td>{fmtPrice(p.sl)}</td>
            <td className={pnlClass(open ? p.unrealized_pnl : p.realized_pnl)}>
              {fmt(open ? p.unrealized_pnl : p.realized_pnl)}
            </td>
            <td>{p.close_reason || p.status}</td>
            <td>{duration(p.duration_seconds)}</td>
            {open ? (
              <td>
                <button className="btn danger" disabled={busyId === p.id} onClick={() => closePos(p)}>Close</button>
              </td>
            ) : null}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Signals({ signals }) {
  return (
    <div className="panel">
      <h3>Signals</h3>
      <table>
        <thead>
          <tr>
            <th>ID</th><th>Time</th><th>Symbol</th><th>Side</th><th>Entry</th><th>TPs</th><th>SL</th>
            <th>Conf</th><th>Strategy</th><th>Status</th><th>TG</th><th>EX</th>
          </tr>
        </thead>
        <tbody>
          {signals.map((s) => (
            <tr key={s.id}>
              <td>{s.signal_id}</td>
              <td>{(s.time || "").slice(11, 19)}</td>
              <td>{s.symbol}</td>
              <td>{s.side}</td>
              <td>{fmtPrice(s.entry)}</td>
              <td>{fmtTps(s.tps, s.tp)}</td>
              <td>{fmtPrice(s.sl)}</td>
              <td>{fmt(s.confidence, 2)}</td>
              <td>{s.strategy || "—"}</td>
              <td>{s.execution_status}</td>
              <td>{s.telegram_status}</td>
              <td>{s.binance_status}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {!signals.length ? <div className="empty">No signals from the bot yet. The app never invents them.</div> : null}
    </div>
  );
}

function Activity({ events, logs, botId }) {
  const [cat, setCat] = useState("ALL");
  const [q, setQ] = useState("");
  const filtered = events.filter((e) => (cat === "ALL" || e.category === cat) && (!q || e.message.toLowerCase().includes(q.toLowerCase())));
  return (
    <div className="grid-2">
      <div className="panel">
        <h3>Activity</h3>
        <div className="row">
          <select value={cat} onChange={(e) => setCat(e.target.value)}>
            {["ALL", "SIGNAL", "BINANCE", "TELEGRAM", "POSITION", "TP", "SL", "SYSTEM", "ERROR"].map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
          <input placeholder="Search" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <div className="feed" style={{ maxHeight: 480 }}>
          {filtered.slice().reverse().map((e) => (
            <div key={e.id}>[{e.category}] {e.message} <span className="muted">{e.source}</span></div>
          ))}
        </div>
      </div>
      <div className="panel">
        <h3>Logs</h3>
        <button className="btn ghost" onClick={() => window.open(`/api/bots/${botId}/logs/export`)}>Export (secrets redacted)</button>
        <div className="feed" style={{ maxHeight: 480 }}>
          {logs.map((l) => <div key={l.id} className="log">{l.line}</div>)}
        </div>
      </div>
    </div>
  );
}

function Reports({ pnl, hourly, fourHour }) {
  return (
    <>
      <div className="cards">
        {Object.entries(pnl).map(([k, v]) => (
          <div className="card" key={k}>
            <h4>{k}</h4>
            <div className={`val ${pnlClass(v.realized_pnl)}`}>{fmt(v.realized_pnl)}</div>
            <div className="muted">Unreal {fmt(v.unrealized_pnl)} · {v.trades} trades · {fmt(v.win_rate, 1)}%</div>
          </div>
        ))}
      </div>
      <div className="grid-2">
        <div className="panel">
          <h3>Hourly</h3>
          <table>
            <thead><tr><th>Window</th><th>Trades</th><th>W/L</th><th>Win%</th><th>Realized</th></tr></thead>
            <tbody>
              {hourly.map((r) => (
                <tr key={r.period_start}>
                  <td>{r.label}</td><td>{r.trades}</td><td>{r.wins}/{r.losses}</td><td>{r.win_rate}</td>
                  <td className={pnlClass(r.realized_pnl)}>{fmt(r.realized_pnl)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="panel">
          <h3>4-hour</h3>
          <table>
            <thead><tr><th>Window</th><th>Trades</th><th>Win%</th><th>Realized</th><th>Unrealized</th></tr></thead>
            <tbody>
              {fourHour.map((r) => (
                <tr key={r.period_start}>
                  <td>{r.label}</td><td>{r.trades}</td><td>{r.win_rate}</td>
                  <td className={pnlClass(r.realized_pnl)}>{fmt(r.realized_pnl)}</td>
                  <td>{fmt(r.unrealized_pnl)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}

function Settings({ bot, envVars, versions, changes, commands, strategyFiles, runtime, onRefresh, setError, setNotice }) {
  const [mode, setMode] = useState(bot.trading_mode);
  const [entry, setEntry] = useState(bot.entry_point);
  const [key, setKey] = useState("");
  const [val, setVal] = useState("");
  const [envText, setEnvText] = useState("");
  const entries = bot.analysis?.entry_points || [];

  useEffect(() => {
    setMode(bot.trading_mode);
    setEntry(bot.entry_point);
  }, [bot.id, bot.trading_mode, bot.entry_point]);

  return (
    <div className="grid-2">
      <div className="panel">
        <h3>Bot</h3>
        <label>Entry point</label>
        <select value={entry} onChange={(e) => setEntry(e.target.value)}>
          {(entries.length ? entries : [entry]).map((e) => <option key={e}>{e}</option>)}
        </select>
        <label>Trading mode</label>
        <select value={mode} onChange={(e) => setMode(e.target.value)}>
          <option>PAPER</option><option>TESTNET</option><option>LIVE</option>
        </select>
        <div className="row" style={{ marginTop: 12 }}>
          <button className="btn primary" onClick={async () => {
            try {
              await api(`/api/bots/${bot.id}`, { method: "PATCH", body: JSON.stringify({ entry_point: entry, trading_mode: mode }) });
              onRefresh();
            } catch (e) { setError(e.message); }
          }}>Save</button>
          <button className="btn ghost" onClick={async () => {
            const r = await api(`/api/bots/${bot.id}/validate`);
            setNotice((r.ok ? "Valid. " : "Invalid. ") + [...(r.issues || []), ...(r.warnings || [])].join(" | "));
          }}>Validate</button>
          <button className="btn ghost" onClick={async () => {
            const r = await api(`/api/bots/${bot.id}/dependencies`, { method: "POST" });
            setNotice(r.ok ? "Dependencies ready" : r.error);
          }}>Install dependencies</button>
        </div>
        <label>Keep bot running when app closes</label>
        <select
          value={bot.keep_running_on_app_close ? "yes" : "no"}
          onChange={async (e) => {
            try {
              await api(`/api/bots/${bot.id}`, { method: "PATCH", body: JSON.stringify({ keep_running_on_app_close: e.target.value === "yes" }) });
              onRefresh();
            } catch (err) { setError(err.message); }
          }}
        >
          <option value="no">Stop bot when app closes (default)</option>
          <option value="yes">Keep bot running when app closes</option>
        </select>
        <label>Auto-restart after crash (bounded)</label>
        <div className="row">
          <button
            className={`btn ${bot.auto_restart ? "ghost" : "primary"}`}
            onClick={async () => {
              try {
                await api(`/api/bots/${bot.id}`, { method: "PATCH", body: JSON.stringify({ auto_restart: false }) });
                setNotice("Auto-restart off");
                onRefresh();
              } catch (err) { setError(err.message); }
            }}
          >Off</button>
          <button
            className={`btn ${bot.auto_restart ? "primary" : "ghost"}`}
            onClick={async () => {
              try {
                await api(`/api/bots/${bot.id}`, { method: "PATCH", body: JSON.stringify({ auto_restart: true }) });
                setNotice("Auto-restart on (max 3 / 5 min)");
                onRefresh();
              } catch (err) { setError(err.message); }
            }}
          >On (max 3 / 5 min)</button>
        </div>
        <p className="muted">Imported Python code can execute with your account permissions. Analysis is static.</p>
      </div>
      <div className="panel">
        <h3>Environment</h3>
        {envVars.map((v) => (
          <div className="row" key={v.key} style={{ marginBottom: 6 }}>
            <div style={{ flex: 1 }}>{v.key} {v.is_secret ? <span className="muted">secret</span> : null}</div>
            <div>{v.value || (v.has_value ? "••••" : "empty")}</div>
            <button className="btn ghost" onClick={async () => {
              await api(`/api/bots/${bot.id}/env/${encodeURIComponent(v.key)}`, { method: "DELETE" });
              onRefresh();
            }}>Delete</button>
          </div>
        ))}
        <label>Add / update variable</label>
        <div className="row">
          <input placeholder="KEY" value={key} onChange={(e) => setKey(e.target.value)} />
          <input placeholder="value" value={val} onChange={(e) => setVal(e.target.value)} />
          <button className="btn primary" onClick={async () => {
            await api(`/api/bots/${bot.id}/env`, { method: "POST", body: JSON.stringify({ key, value: val }) });
            setKey(""); setVal(""); onRefresh();
          }}>Save</button>
        </div>
        <label>Import .env</label>
        <textarea rows={4} value={envText} onChange={(e) => setEnvText(e.target.value)} />
        <button className="btn ghost" onClick={async () => {
          await api(`/api/bots/${bot.id}/env/import`, { method: "POST", body: JSON.stringify({ content: envText }) });
          setEnvText(""); onRefresh();
        }}>Import into vault</button>
      </div>
      <div className="panel">
        <h3>Versions</h3>
        {changes?.changed ? (
          <div className="warn-box">
            New changes detected vs {changes.current_version}. Added {changes.diff.added.length}, modified {changes.diff.modified.length}, removed {changes.diff.removed.length}.
            <div className="row" style={{ marginTop: 8 }}>
              <button className="btn primary" onClick={async () => {
                try {
                  await api(`/api/bots/${bot.id}/versions/activate`, { method: "POST", body: JSON.stringify({ note: "user activated" }) });
                  onRefresh();
                } catch (e) { setError(e.message); }
              }}>Activate new version</button>
            </div>
            <div className="muted">{[...changes.diff.added, ...changes.diff.modified, ...changes.diff.removed].join(", ")}</div>
          </div>
        ) : <div className="muted">No pending file changes.</div>}
        {versions.map((v) => (
          <div key={v.id} className="row" style={{ marginTop: 8 }}>
            <div>{v.version_label} {v.is_active ? "(active)" : ""} {v.is_known_good ? "known-good" : ""}</div>
            {!v.is_active ? (
              <button className="btn ghost" onClick={async () => {
                await api(`/api/bots/${bot.id}/versions/rollback`, { method: "POST", body: JSON.stringify({ version_id: v.id }) });
                onRefresh();
              }}>Rollback</button>
            ) : null}
          </div>
        ))}
      </div>
      <div className="panel">
        <h3>Telegram / Binance / Security</h3>
        <div>Detected commands: {commands.length ? commands.join(" ") : "Unknown / Not Detected"}</div>
        <p className="muted">Telegram continues to work inside the bot. The app only mirrors activity.</p>
        <p className="muted">Withdrawal permission is never required. LIVE start always asks for confirmation.</p>
        <button className="btn ghost" onClick={async () => {
          const r = await api(`/api/bots/${bot.id}/backup`, { method: "POST" });
          setNotice(`${r.warning} Saved to ${r.path}`);
        }}>Backup (secrets excluded)</button>
      </div>
      <RuntimePanel bot={bot} runtime={runtime} onRefresh={onRefresh} setError={setError} setNotice={setNotice} />
      <StrategyPanel bot={bot} files={strategyFiles || []} onRefresh={onRefresh} setError={setError} setNotice={setNotice} />
    </div>
  );
}

function percentsForCount(count, current) {
  const parts = String(current || "").split(",").map((x) => x.trim()).filter(Boolean);
  const n = Number(count) || 1;
  if (parts.length >= n) return parts.slice(0, n).join(",");
  const base = Number(parts[0] || 0.8) || 0.8;
  const out = [...parts];
  while (out.length < n) out.push(String((base * (out.length + 1)).toFixed(4)));
  return out.join(",");
}

function RuntimePanel({ bot, runtime, onRefresh, setError, setNotice }) {
  const [scanMode, setScanMode] = useState("ALL");
  const [symbols, setSymbols] = useState("");
  const [delay, setDelay] = useState(180);
  const [gap, setGap] = useState(180);
  const [tpCount, setTpCount] = useState(2);
  const [tpPercents, setTpPercents] = useState("0.8,1.6");
  const [sl, setSl] = useState(0.5);
  const [maxOpen, setMaxOpen] = useState(3);
  const [loadedFor, setLoadedFor] = useState(null);

  useEffect(() => {
    if (!runtime) return;
    if (loadedFor === bot.id) return;
    setScanMode(runtime.scan_mode || "ALL");
    setSymbols((runtime.symbols || []).join(","));
    setDelay(runtime.startup_delay_seconds ?? 180);
    setGap(runtime.trade_gap_seconds ?? 180);
    setTpCount(runtime.tp_count ?? 2);
    setTpPercents((runtime.tp_percents || [0.8, 1.6]).join(","));
    setSl(runtime.sl_percent ?? 0.5);
    setMaxOpen(runtime.max_open_positions ?? 3);
    setLoadedFor(bot.id);
  }, [bot.id, runtime, loadedFor]);

  async function persist(patch, notice) {
    const body = {
      scan_mode: scanMode,
      symbols,
      startup_delay_seconds: Number(delay),
      trade_gap_seconds: Number(gap),
      tp_count: Number(tpCount),
      tp_percents: tpPercents,
      sl_percent: Number(sl),
      max_open_positions: Number(maxOpen),
      ...patch,
    };
    try {
      const saved = await api(`/api/bots/${bot.id}/runtime-settings`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      setScanMode(saved.scan_mode);
      setSymbols((saved.symbols || []).join(","));
      setDelay(saved.startup_delay_seconds);
      setGap(saved.trade_gap_seconds);
      setTpCount(saved.tp_count);
      setTpPercents((saved.tp_percents || []).join(","));
      setSl(saved.sl_percent);
      setMaxOpen(saved.max_open_positions ?? 3);
      const extra = saved.all_symbol_count ? ` Universe ${saved.all_symbol_count} (${saved.universe_label || saved.scan_mode}).` : "";
      setNotice((notice || "Settings saved. Restart the bot to apply.") + extra);
      onRefresh();
    } catch (e) {
      setError(e.message);
    }
  }

  return (
    <div className="panel">
      <h3>Scan / Delay / Take profits</h3>
      <label>Market scan</label>
      <p className="help">Every listed Binance USD-M futures + TradFi contract is scanned. Prices come from the Binance public ticker, never random. Restart the bot after changing this.</p>
      <div className="row">
        <button className={`btn ${scanMode === "ALL" ? "primary" : "ghost"}`} onClick={() => persist({ scan_mode: "ALL", refresh_universe: true }, "Scan mode: Binance Futures + US TradFi")}>ALL SUPPORTED</button>
        <button className={`btn ${scanMode === "BINANCE" ? "primary" : "ghost"}`} onClick={() => persist({ scan_mode: "BINANCE", refresh_universe: true }, "Scan mode: Binance Futures")}>Binance Futures</button>
        <button className={`btn ${scanMode === "TRADFI" ? "primary" : "ghost"}`} onClick={() => persist({ scan_mode: "TRADFI", refresh_universe: true }, "Scan mode: US TradFi")}>US TradFi</button>
      </div>
      <p className="muted">{runtime?.universe_label || scanMode} · {runtime?.all_symbol_count || 0} symbols · crypto {runtime?.crypto_symbol_count || 0} · tradfi {runtime?.tradfi_symbol_count || 0}{runtime?.all_symbols?.length ? ` · sample ${runtime.all_symbols.slice(0, 8).join(", ")}` : ""}{runtime?.scan_error ? ` · ${runtime.scan_error}` : ""}</p>
      <label>Open position limit</label>
      <p className="help">Maximum concurrent open positions. New signals are rejected after this limit until a position closes.</p>
      <div className="row">
        {[1, 2, 3, 5, 10].map((n) => (
          <button key={n} className={`btn ${Number(maxOpen) === n ? "primary" : "ghost"}`} onClick={() => persist({ max_open_positions: n }, `Position limit ${n}`)}>{n}</button>
        ))}
        <input type="number" min="1" max="20" value={maxOpen} onChange={(e) => setMaxOpen(e.target.value)} onBlur={() => persist({ max_open_positions: Number(maxOpen) }, `Position limit ${maxOpen}`)} />
      </div>
      <label>Startup delay</label>
      <p className="help">Wait this long after Start before the first new trade. Open positions stay visible and can still be closed.</p>
      <div className="row">
        {[180, 300, 600].map((n) => (
          <button key={n} className={`btn ${Number(delay) === n ? "primary" : "ghost"}`} onClick={() => persist({ startup_delay_seconds: n }, `Startup delay ${n / 60} min`)}>{n / 60} min</button>
        ))}
        <input type="number" min="0" value={delay} onChange={(e) => setDelay(e.target.value)} onBlur={() => persist({ startup_delay_seconds: Number(delay) }, `Startup delay ${delay}s`)} />
      </div>
      <label>Trade gap after close</label>
      <p className="help">After a position closes, wait this long before opening a new one. Does not freeze or close an already open trade.</p>
      <div className="row">
        {[180, 300, 600].map((n) => (
          <button key={n} className={`btn ${Number(gap) === n ? "primary" : "ghost"}`} onClick={() => persist({ trade_gap_seconds: n }, `Trade gap ${n / 60} min`)}>{n / 60} min</button>
        ))}
        <input type="number" min="0" value={gap} onChange={(e) => setGap(e.target.value)} onBlur={() => persist({ trade_gap_seconds: Number(gap) }, `Trade gap ${gap}s`)} />
      </div>
      <label>Take-profit count (1-5)</label>
      <p className="help">How many take-profit levels the next signal should carry. Partial TPs keep the remaining position open.</p>
      <div className="row">
        {[1, 2, 3, 4, 5].map((n) => (
          <button
            key={n}
            className={`btn ${Number(tpCount) === n ? "primary" : "ghost"}`}
            onClick={() => persist({ tp_count: n, tp_percents: percentsForCount(n, tpPercents) }, `Take-profit count ${n}`)}
          >{n}</button>
        ))}
      </div>
      <label>TP percents</label>
      <p className="help">Percent distance from entry for each TP, comma-separated. Example: 0.8,1.6 for two levels.</p>
      <input value={tpPercents} onChange={(e) => setTpPercents(e.target.value)} onBlur={() => persist({ tp_percents: tpPercents }, "TP percents saved")} placeholder="0.8,1.6" />
      <label>Stop-loss percent</label>
      <p className="help">Percent distance from entry for the stop-loss on the next signal.</p>
      <div className="row">
        {[0.3, 0.5, 0.8, 1, 1.5].map((n) => (
          <button key={n} className={`btn ${Number(sl) === n ? "primary" : "ghost"}`} onClick={() => persist({ sl_percent: n }, `Stop-loss ${n}%`)}>{n}%</button>
        ))}
        <input type="number" min="0" step="0.1" value={sl} onChange={(e) => setSl(e.target.value)} onBlur={() => persist({ sl_percent: Number(sl) }, `Stop-loss ${sl}%`)} />
      </div>
      <div className="row" style={{ marginTop: 12 }}>
        <button className="btn primary" onClick={() => persist({ refresh_universe: true }, "Runtime settings saved. Restart the bot to apply.")}>Save runtime settings</button>
        <button className="btn danger" onClick={async () => {
          if (!window.confirm("Reset all signals, positions, trades, events, and logs for this bot?")) return;
          try {
            await api(`/api/bots/${bot.id}/reset-data`, { method: "POST", body: "{}" });
            setNotice("Trading data reset.");
            onRefresh();
          } catch (e) {
            setError(e.message);
          }
        }}>Reset all data</button>
      </div>
      <p className="muted">Delay and trade gap never close or block management of an already open position. Reset does not delete strategy files or secrets.</p>
    </div>
  );
}

function StrategyPanel({ bot, files, onRefresh, setError, setNotice }) {
  const [path, setPath] = useState(files.find((f) => f.path === "strategy.py")?.path || files[0]?.path || "");
  const [content, setContent] = useState("");
  const [activate, setActivate] = useState(false);
  const [backups, setBackups] = useState([]);
  const [backup, setBackup] = useState("");
  const [uploadName, setUploadName] = useState("strategy.py");

  useEffect(() => {
    const next = files.find((f) => f.path === path) ? path : (files.find((f) => f.path === "strategy.py")?.path || files[0]?.path || "");
    setPath(next);
  }, [bot.id, files]);

  useEffect(() => {
    if (!path) {
      setContent("");
      return;
    }
    api(`/api/bots/${bot.id}/strategy/file?path=${encodeURIComponent(path)}`)
      .then((r) => setContent(r.content || ""))
      .catch((e) => setError(e.message));
  }, [bot.id, path]);

  async function loadBackups() {
    try {
      const rows = await api(`/api/bots/${bot.id}/strategy/backups`);
      setBackups(rows || []);
      setBackup((rows && rows[0]?.backup) || "");
    } catch (e) {
      setError(e.message);
    }
  }

  useEffect(() => {
    loadBackups();
  }, [bot.id, files]);

  async function uploadFile(file) {
    if (!file) return;
    const dest = uploadName || file.name || "strategy.py";
    const body = new FormData();
    body.append("file", file);
    body.append("path", dest);
    body.append("activate", activate ? "true" : "false");
    try {
      const res = await fetch(`/api/bots/${bot.id}/strategy/upload`, { method: "POST", body });
      const text = await res.text();
      let data = null;
      try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
      if (!res.ok) throw new Error(typeof data?.detail === "string" ? data.detail : JSON.stringify(data?.detail || res.statusText));
      setPath(data.path || dest);
      setContent(data.content || "");
      setNotice(data.activated ? `Uploaded and activated ${data.path}` : `Uploaded ${data.path}. Activate the new version when ready.`);
      onRefresh();
      loadBackups();
    } catch (e) { setError(e.message); }
  }

  return (
    <div className="panel">
      <h3>Strategy files</h3>
      <p className="help">Edit, upload, or restore strategy files. Delete keeps a backup you can restore here. LIVE bots are never auto-activated while running.</p>
      <label>File</label>
      <select value={path} onChange={(e) => setPath(e.target.value)}>
        {(files || []).length ? files.map((f) => <option key={f.path} value={f.path}>{f.path}</option>) : <option value="">No strategy file — upload one</option>}
      </select>
      <textarea rows={12} value={content} onChange={(e) => setContent(e.target.value)} className="code-edit" />
      <label className="row">
        <input type="checkbox" checked={activate} onChange={(e) => setActivate(e.target.checked)} />
        Activate after save (blocked while LIVE is running)
      </label>
      <div className="row" style={{ marginTop: 8 }}>
        <button className="btn primary" disabled={!path} onClick={async () => {
          try {
            const r = await api(`/api/bots/${bot.id}/strategy/file`, {
              method: "POST",
              body: JSON.stringify({ path: path || "strategy.py", content, activate }),
            });
            setNotice(r.activated ? `Saved and activated ${r.path}` : `Saved ${r.path}. Activate the new version when ready.`);
            onRefresh();
            loadBackups();
          } catch (e) { setError(e.message); }
        }}>Save</button>
        <button className="btn ghost" disabled={!path} onClick={async () => {
          try {
            const r = await api(`/api/bots/${bot.id}/strategy/clean`, { method: "POST", body: JSON.stringify({ path }) });
            setContent(r.content || "");
            setNotice(`Cleaned ${path}. Backup ${r.backup || "created"}.`);
            onRefresh();
            loadBackups();
          } catch (e) { setError(e.message); }
        }}>Clean comments</button>
        <button className="btn danger" disabled={!path} onClick={async () => {
          try {
            const r = await api(`/api/bots/${bot.id}/strategy/file?path=${encodeURIComponent(path)}`, { method: "DELETE" });
            setNotice(`Deleted ${path}. Backup ${r.backup || "created"}. Upload or restore to put it back.`);
            setContent("");
            onRefresh();
            loadBackups();
          } catch (e) { setError(e.message); }
        }}>Delete</button>
      </div>
      <label>Upload strategy file</label>
      <div className="row">
        <input value={uploadName} onChange={(e) => setUploadName(e.target.value)} placeholder="strategy.py" />
        <input type="file" onChange={(e) => uploadFile(e.target.files?.[0])} />
      </div>
      <label>Restore backup</label>
      <div className="row">
        <select value={backup} onChange={(e) => setBackup(e.target.value)}>
          {(backups || []).length ? backups.map((b) => <option key={b.backup} value={b.backup}>{b.path} · {b.stamp}</option>) : <option value="">No backups</option>}
        </select>
        <button className="btn ghost" disabled={!backup} onClick={async () => {
          try {
            const r = await api(`/api/bots/${bot.id}/strategy/restore`, {
              method: "POST",
              body: JSON.stringify({ backup, activate }),
            });
            setPath(r.path || path);
            setContent(r.content || "");
            setNotice(`Restored ${r.path} from backup.`);
            onRefresh();
            loadBackups();
          } catch (e) { setError(e.message); }
        }}>Restore</button>
      </div>
      <p className="muted">Saves create a local backup. LIVE bots are never auto-activated while running.</p>
    </div>
  );
}

function AddBotModal({ onClose, onCreated }) {
  const [name, setName] = useState("My Bot");
  const [path, setPath] = useState("");
  const [analysis, setAnalysis] = useState(null);
  const [entry, setEntry] = useState("");
  const [err, setErr] = useState("");

  async function analyze() {
    try {
      const a = await api("/api/projects/analyze", { method: "POST", body: JSON.stringify({ source_path: path }) });
      setAnalysis(a);
      setEntry(a.entry_points?.[0] || "");
      setErr("");
    } catch (e) {
      setErr(e.message);
    }
  }

  async function create() {
    try {
      const bot = await api("/api/bots", { method: "POST", body: JSON.stringify({ name, source_path: path, entry_point: entry || null }) });
      onCreated(bot);
    } catch (e) {
      setErr(e.message);
    }
  }

  return (
    <div className="modal-back">
      <div className="panel modal">
        <h3>Add bot</h3>
        <p className="muted">Select a complete project folder. The original structure is preserved. Code is not executed during analysis.</p>
        <label>Bot name</label>
        <input value={name} onChange={(e) => setName(e.target.value)} />
        <label>Project folder path</label>
        <input value={path} onChange={(e) => setPath(e.target.value)} placeholder="/path/to/your/python/bot" />
        <div className="row" style={{ marginTop: 10 }}>
          <button className="btn ghost" onClick={analyze}>Analyze</button>
          <button className="btn primary" onClick={create} disabled={!path}>Save bot</button>
          <button className="btn ghost" onClick={onClose}>Cancel</button>
        </div>
        {err ? <div className="err">{err}</div> : null}
        {analysis ? (
          <div style={{ marginTop: 12 }}>
            <div>Python files: {analysis.file_count}</div>
            <label>Entry point</label>
            <select value={entry} onChange={(e) => setEntry(e.target.value)}>
              {(analysis.entry_points || []).map((e) => <option key={e}>{e}</option>)}
            </select>
            {analysis.uncertain?.map((u) => <div key={u} className="muted">{u}</div>)}
            <div className="muted">{analysis.untrusted_code_warning}</div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function LiveModal({ bot, onCancel, onConfirm }) {
  return (
    <div className="modal-back">
      <div className="panel modal">
        <h3>LIVE TRADING</h3>
        <div className="warn-box">
          This bot may place real orders using configured exchange credentials. Real funds may be affected.
        </div>
        <p>Bot: {bot?.name}</p>
        <p>Version: {bot?.version_label}</p>
        <p>Mode: LIVE</p>
        <div className="row">
          <button className="btn ghost" onClick={onCancel}>Cancel</button>
          <button className="btn danger" onClick={onConfirm}>I Understand - Start Live Bot</button>
        </div>
      </div>
    </div>
  );
}
