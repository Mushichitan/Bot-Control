import React, { useCallback, useEffect, useMemo, useState } from "react";

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
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [showAdd, setShowAdd] = useState(false);
  const [showLive, setShowLive] = useState(false);
  const [busy, setBusy] = useState(false);

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
      const [b, h, sig, pos, ev, lg, p, hr, fh, env, ver, ch, cmd] = await Promise.all([
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
    try {
      if (kind === "start" && bot?.trading_mode === "LIVE") {
        setShowLive(true);
        return;
      }
      await api(`/api/bots/${botId}/${kind}`, { method: "POST", body: "{}" });
      await refresh();
    } catch (e) {
      if (String(e.message).includes("LIVE_CONFIRMATION_REQUIRED")) setShowLive(true);
      else setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function confirmLive() {
    setBusy(true);
    try {
      await api(`/api/bots/${botId}/start`, {
        method: "POST",
        body: JSON.stringify({ live_confirmed: true }),
      });
      setShowLive(false);
      await refresh();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
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
            <button className="btn primary" disabled={busy || !bot} onClick={() => control("start")}>Start</button>
            <button className="btn danger" disabled={busy || !bot} onClick={() => control("stop")}>Stop</button>
            <button className="btn ghost" disabled={busy || !bot} onClick={() => control("restart")}>Restart</button>
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
          {notice ? <div className="panel" style={{ marginBottom: 12 }}>{notice}</div> : null}
          {!bot ? (
            <div className="panel empty">Import a Python trading-bot project to get started. The app never invents trading logic.</div>
          ) : page === "Dashboard" ? (
            <Dashboard bot={bot} health={health} today={today} open={open} signals={signals} events={events} logs={logs} />
          ) : page === "Positions" ? (
            <Positions open={open} closed={closed} />
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

function Dashboard({ bot, health, today, open, signals, events, logs }) {
  const uptime = bot.last_heartbeat ? new Date(bot.last_heartbeat).toLocaleTimeString() : "—";
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
          <h3>Health</h3>
          <div>Database <span className={`badge ${badgeClass(health.database)}`}>{health.database}</span></div>
          <div style={{ marginTop: 8 }}>Mode <span className={`badge ${badgeClass(bot.trading_mode)}`}>{bot.trading_mode}</span></div>
          {bot.last_error ? <div className="err" style={{ marginTop: 8 }}>{bot.last_error}</div> : null}
        </div>
      </div>
      <div className="grid-3" style={{ marginTop: 12 }}>
        <div className="panel">
          <h3>Recent signals</h3>
          {signals.slice(0, 6).map((s) => (
            <div key={s.id}>{s.symbol} {s.side} {s.execution_status}</div>
          ))}
          {!signals.length ? <div className="muted">No signals yet</div> : null}
        </div>
        <div className="panel">
          <h3>Open positions</h3>
          {open.slice(0, 6).map((p) => (
            <div key={p.id}>{p.number} {p.symbol} {p.side} <span className={pnlClass(p.unrealized_pnl)}>{fmt(p.unrealized_pnl)}</span></div>
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

function Positions({ open, closed }) {
  return (
    <div className="grid-2">
      <div className="panel">
        <h3>Open</h3>
        <PosTable rows={open} open />
      </div>
      <div className="panel">
        <h3>Closed — TP/SL remain visible</h3>
        <PosTable rows={closed} />
      </div>
    </div>
  );
}

function PosTable({ rows, open }) {
  if (!rows.length) return <div className="empty">None</div>;
  return (
    <table>
      <thead>
        <tr>
          <th>#</th><th>Symbol</th><th>Side</th><th>Entry</th>
          <th>{open ? "Current" : "Exit"}</th><th>TP</th><th>SL</th>
          <th>P&L</th><th>Reason</th><th>Dur</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((p) => (
          <tr key={p.id}>
            <td>{p.number}</td>
            <td>{p.symbol}</td>
            <td>{p.side}</td>
            <td>{fmt(p.entry)}</td>
            <td>{fmt(open ? p.current_price : p.exit)}</td>
            <td>{fmt(p.tp)}</td>
            <td>{fmt(p.sl)}</td>
            <td className={pnlClass(open ? p.unrealized_pnl : p.realized_pnl)}>
              {fmt(open ? p.unrealized_pnl : p.realized_pnl)}
            </td>
            <td>{p.close_reason || p.status}</td>
            <td>{duration(p.duration_seconds)}</td>
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
            <th>ID</th><th>Time</th><th>Symbol</th><th>Side</th><th>Entry</th><th>TP</th><th>SL</th>
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
              <td>{fmt(s.entry)}</td>
              <td>{fmt(s.tp)}</td>
              <td>{fmt(s.sl)}</td>
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

function Settings({ bot, envVars, versions, changes, commands, onRefresh, setError, setNotice }) {
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
            await api(`/api/bots/${bot.id}`, { method: "PATCH", body: JSON.stringify({ keep_running_on_app_close: e.target.value === "yes" }) });
            onRefresh();
          }}
        >
          <option value="no">Stop bot when app closes (default)</option>
          <option value="yes">Keep bot running when app closes</option>
        </select>
        <label>Auto-restart after crash (bounded)</label>
        <select
          value={bot.auto_restart ? "yes" : "no"}
          onChange={async (e) => {
            await api(`/api/bots/${bot.id}`, { method: "PATCH", body: JSON.stringify({ auto_restart: e.target.value === "yes" }) });
            onRefresh();
          }}
        >
          <option value="no">Off</option>
          <option value="yes">On (max 3 / 5 min)</option>
        </select>
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
