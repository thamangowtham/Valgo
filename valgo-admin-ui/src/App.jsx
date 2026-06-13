import { useState, useEffect, useMemo, createContext, useContext } from "react";
import {
  LayoutDashboard, GitBranch, Server, Webhook, Shield, Key,
  ScrollText, Plus, Pencil, Trash2, AlertTriangle,
  CheckCircle2, X, Activity, Wifi, Lock, RefreshCw,
  ArrowUpRight, ArrowDownRight, Radio, Zap, Layers, RotateCw,
  Sun, Moon
} from "lucide-react";

const THEME_KEY = "valgo-theme";
const API_TOKEN = "local-dev-token";

function apiFetch(path, { method = "GET", body } = {}) {
  return fetch(path, {
    method,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${API_TOKEN}`,
    },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
}

const API_PATH = {
  strategies: "/api/strategies",
  dataSources: "/api/data-sources",
  accounts: "/api/accounts",
  nodes: "/api/nodes",
  signals: "/api/signals",
};
const API_KEY = {
  strategies: "strategies",
  dataSources: "sources",
  accounts: "accounts",
  nodes: "nodes",
  signals: "signals",
};

function riskFromApi(d) {
  return {
    killSwitch:        d.kill_switch         ?? false,
    maxOrdersPerSec:   d.max_orders_per_sec  ?? 10,
    maxDailyLoss:      Number(d.max_daily_loss     ?? 50000),
    maxPositionValue:  Number(d.max_position_value ?? 1000000),
    maxOpenPositions:  d.max_open_positions  ?? 5,
  };
}
function riskToApi(r) {
  return {
    kill_switch:        r.killSwitch,
    max_orders_per_sec: r.maxOrdersPerSec,
    max_daily_loss:     r.maxDailyLoss,
    max_position_value: r.maxPositionValue,
    max_open_positions: r.maxOpenPositions,
  };
}

function normalizeStrategyFromApi(s) {
  return {
    ...s,
    class_name:  s.class_name  ?? "st_psar_confluence",
    instruments: Array.isArray(s.instruments) ? s.instruments.join(", ") : (s.instruments || ""),
    qty:         s.qty         ?? s.quantity        ?? 1,
    type:        s.type        ?? "CE",
    strikeLogic: s.strikeLogic ?? s.strike_logic    ?? "",
    entry:       s.entry       ?? s.entry_condition ?? s.class_name ?? "",
    target:      s.target      ?? (s.target_pct     != null ? Number(s.target_pct)     : 0),
    stopLoss:    s.stopLoss    ?? (s.stop_loss_pct   != null ? Number(s.stop_loss_pct)  : 0),
    accountId:   s.accountId   ?? s.account_id      ?? "",
    lastFired:   s.lastFired   ?? (s.last_fired ? new Date(s.last_fired).toLocaleString("en-IN") : "—"),
    active:      s.active      ?? true,
  };
}
function normalizeStrategyForApi(s) {
  const insts = parseInstruments(s.instruments || "");
  return {
    ...s,
    class_name:      s.class_name     ?? "st_psar_confluence",
    instruments:     insts,
    quantity:        s.qty ?? s.quantity ?? 1,
    strike_logic:    s.strikeLogic ?? s.strike_logic    ?? "",
    entry_condition: s.entry       ?? s.entry_condition ?? s.class_name ?? "",
    target_pct:      s.target      ?? s.target_pct      ?? 0,
    stop_loss_pct:   s.stopLoss    ?? s.stop_loss_pct   ?? 0,
    account_id:      s.accountId   ?? s.account_id      ?? "",
  };
}
function normalizeAuditFromApi(e) {
  const p = e.payload || {};
  return {
    id:       e.event_id ?? e.id ?? String(Math.random()),
    ts:       e.timestamp ? new Date(e.timestamp).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—",
    strategy: e.actor || "—",
    account:  p.account_id || "—",
    symbol:   p.tradingsymbol || p.symbol || "—",
    side:     p.side  || "—",
    qty:      p.quantity ?? p.qty ?? "—",
    price:    p.price ?? p.average_price ?? "—",
    status:   e.event_type === "order_filled" ? "FILLED" : e.event_type === "order_rejected" ? "REJECTED" : p.status || "PENDING",
  };
}

const SECTIONS = [
  { id: "dashboard", label: "Dashboard", icon: LayoutDashboard },
  { id: "strategies", label: "Strategies", icon: GitBranch },
  { id: "data", label: "Market data", icon: Radio },
  { id: "signals", label: "Signal sources", icon: Webhook },
  { id: "accounts", label: "Broker accounts", icon: Key },
  { id: "nodes", label: "Execution nodes", icon: Server },
  { id: "risk", label: "Risk limits", icon: Shield },
  { id: "audit", label: "Audit log", icon: ScrollText },
];

// =======================================================================
// THEME TOKENS
// =======================================================================
const THEMES = {
  dark: {
    // surfaces
    bg: "bg-zinc-950",
    surface: "bg-zinc-900/40",
    surfaceSolid: "bg-zinc-900",
    surfaceDeep: "bg-zinc-950",
    surfaceHover: "hover:bg-zinc-900/60",
    rowHover: "hover:bg-zinc-900/60",
    btnHover: "hover:bg-zinc-800",
    inputBg: "bg-zinc-950",
    overlay: "bg-zinc-950/40",
    // borders
    border: "border-zinc-800",
    borderHover: "hover:border-zinc-700",
    borderStrong: "border-zinc-700",
    rowBorder: "border-zinc-900",
    divider: "divide-zinc-800",
    // text
    text: "text-zinc-100",
    textBody: "text-zinc-200",
    textMuted: "text-zinc-300",
    textDim: "text-zinc-400",
    textFaint: "text-zinc-500",
    textGhost: "text-zinc-600",
    placeholder: "placeholder:text-zinc-600",
    // accent (teal)
    accent: "text-teal-300",
    accentStrong: "text-teal-400",
    accentBg: "bg-teal-500",
    accentBgHover: "hover:bg-teal-400",
    accentBgText: "text-zinc-950",
    accentBorder: "border-teal-500",
    accentFocus: "focus:border-teal-500",
    accentSurface: "bg-teal-950/30",
    accentSurfaceText: "text-teal-200",
    accentSurfaceBorder: "border-teal-700",
    accentDot: "bg-teal-400",
    // pills
    pillNeutral: "bg-zinc-800 text-zinc-300 border-zinc-700",
    pillSuccess: "bg-emerald-950 text-emerald-300 border-emerald-900",
    pillWarning: "bg-amber-950 text-amber-300 border-amber-900",
    pillDanger: "bg-red-950 text-red-300 border-red-900",
    pillInfo: "bg-teal-950 text-teal-300 border-teal-900",
    pillPrimary: "bg-teal-900 text-teal-200 border-teal-700",
    // semantic
    successText: "text-emerald-300",
    successTextStrong: "text-emerald-400",
    warningText: "text-amber-300",
    warningTextStrong: "text-amber-400",
    dangerText: "text-red-300",
    dangerTextStrong: "text-red-400",
    // status dots
    dotOk: "bg-emerald-400",
    dotPending: "bg-amber-400",
    dotIdle: "bg-zinc-500",
    dotError: "bg-red-400",
    // active sidebar
    activeBg: "bg-zinc-900",
    activeText: "text-teal-300",
    activeBorder: "border-teal-500",
    inactiveText: "text-zinc-400",
    inactiveTextHover: "hover:text-zinc-100",
    // kill switch
    killOnBg: "bg-red-950",
    killOnText: "text-red-300",
    killOnBorder: "border-red-700",
    killOnBgHover: "hover:bg-red-900",
    killOffText: "text-zinc-500",
    killOffTextHover: "hover:text-red-400",
    killOffBorderHover: "hover:border-red-900",
    // accent buttons (ghost danger)
    ghostDanger: "text-red-400 hover:bg-red-950 hover:border-red-900",
    // toast
    toastBg: "bg-zinc-900",
    toastBorder: "border-teal-700",
    toastText: "text-teal-300",
    // checkbox accents (Tailwind accent utility)
    checkboxTeal: "accent-teal-500",
    checkboxRed: "accent-red-500",
  },
  light: {
    bg: "bg-stone-50",
    surface: "bg-white",
    surfaceSolid: "bg-white",
    surfaceDeep: "bg-stone-50",
    surfaceHover: "hover:bg-stone-50",
    rowHover: "hover:bg-stone-50",
    btnHover: "hover:bg-stone-100",
    inputBg: "bg-white",
    overlay: "bg-white/40",
    border: "border-stone-200",
    borderHover: "hover:border-stone-300",
    borderStrong: "border-stone-300",
    rowBorder: "border-stone-100",
    divider: "divide-stone-200",
    text: "text-stone-900",
    textBody: "text-stone-800",
    textMuted: "text-stone-700",
    textDim: "text-stone-600",
    textFaint: "text-stone-500",
    textGhost: "text-stone-400",
    placeholder: "placeholder:text-stone-400",
    accent: "text-teal-700",
    accentStrong: "text-teal-800",
    accentBg: "bg-teal-600",
    accentBgHover: "hover:bg-teal-700",
    accentBgText: "text-white",
    accentBorder: "border-teal-600",
    accentFocus: "focus:border-teal-600",
    accentSurface: "bg-teal-50",
    accentSurfaceText: "text-teal-800",
    accentSurfaceBorder: "border-teal-400",
    accentDot: "bg-teal-600",
    pillNeutral: "bg-stone-100 text-stone-700 border-stone-300",
    pillSuccess: "bg-emerald-50 text-emerald-800 border-emerald-300",
    pillWarning: "bg-amber-50 text-amber-800 border-amber-300",
    pillDanger: "bg-red-50 text-red-800 border-red-300",
    pillInfo: "bg-teal-50 text-teal-800 border-teal-300",
    pillPrimary: "bg-teal-100 text-teal-900 border-teal-400",
    successText: "text-emerald-700",
    successTextStrong: "text-emerald-800",
    warningText: "text-amber-700",
    warningTextStrong: "text-amber-800",
    dangerText: "text-red-700",
    dangerTextStrong: "text-red-800",
    dotOk: "bg-emerald-500",
    dotPending: "bg-amber-500",
    dotIdle: "bg-stone-400",
    dotError: "bg-red-500",
    activeBg: "bg-stone-100",
    activeText: "text-teal-700",
    activeBorder: "border-teal-600",
    inactiveText: "text-stone-600",
    inactiveTextHover: "hover:text-stone-900",
    killOnBg: "bg-red-100",
    killOnText: "text-red-800",
    killOnBorder: "border-red-400",
    killOnBgHover: "hover:bg-red-200",
    killOffText: "text-stone-500",
    killOffTextHover: "hover:text-red-700",
    killOffBorderHover: "hover:border-red-400",
    ghostDanger: "text-red-700 hover:bg-red-50 hover:border-red-300",
    toastBg: "bg-white",
    toastBorder: "border-teal-500",
    toastText: "text-teal-700",
    checkboxTeal: "accent-teal-600",
    checkboxRed: "accent-red-600",
  },
};

const ThemeContext = createContext(THEMES.dark);
const useT = () => useContext(ThemeContext);

// =======================================================================
// EMPTY STATE (data comes from the API at load time)
// =======================================================================
const EMPTY_STATE = {
  strategies:      [],
  dataSources:     [],
  baseInstruments: [],
  resilience: {
    autoReconnect: true, maxReconnectAttempts: 5, backoffStrategy: "exponential",
    backoffInitialMs: 1000, backoffMaxMs: 32000,
    autoFailover: true, failoverThresholdMs: 10000, failoverNotify: true,
  },
  accounts: [],
  nodes:    [],
  signals:  [],
  risk: { maxOrdersPerSec: 10, maxDailyLoss: 50000, maxPositionValue: 1000000, maxOpenPositions: 5, killSwitch: false },
  audit:    [],
};

const PROVIDERS = [
  "Shoonya (Finvasia)", "Zerodha Kite", "Fyers", "Upstox", "ICICI Direct (Breeze)", "Angel One (SmartAPI)",
  "5paisa", "Dhan", "TrueData", "GlobalDataFeed", "NSE direct", "Custom"
];

function genId(prefix) { return `${prefix}_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 5)}`; }
function parseInstruments(str) {
  if (!str) return [];
  return str.split(/[,\n]/).map((s) => s.trim()).filter(Boolean);
}

// =======================================================================
// THEMED PRIMITIVES
// =======================================================================
function StatusDot({ status }) {
  const t = useT();
  const map = {
    running: t.dotOk, connected: t.dotOk, active: t.dotOk, configured: t.dotOk,
    standby: t.dotPending, pending: t.dotPending, reconnecting: t.dotPending,
    stopped: t.dotIdle, inactive: t.dotIdle, disconnected: t.dotIdle, disabled: t.dotIdle,
    error: t.dotError, failed: t.dotError,
    FILLED: t.dotOk, REJECTED: t.dotError, PENDING: t.dotPending,
  };
  const pulse = status === "connected" || status === "running" || status === "reconnecting";
  return <span className={`inline-block w-1.5 h-1.5 rounded-full ${map[status] || t.dotIdle} ${pulse ? "animate-pulse" : ""}`} />;
}

function Pill({ children, tone = "neutral" }) {
  const t = useT();
  const tones = {
    neutral: t.pillNeutral, success: t.pillSuccess, warning: t.pillWarning,
    danger: t.pillDanger, info: t.pillInfo, primary: t.pillPrimary,
  };
  return (
    <span className={`inline-flex items-center gap-1.5 px-1.5 py-0.5 text-[10px] uppercase tracking-wider border ${tones[tone]} font-mono`}>
      {children}
    </span>
  );
}

function Btn({ children, onClick, variant = "ghost", size = "sm", title, type = "button" }) {
  const t = useT();
  const variants = {
    primary: `${t.accentBg} ${t.accentBgText} ${t.accentBgHover} border ${t.accentBorder}`,
    ghost: `bg-transparent ${t.textMuted} ${t.btnHover} border ${t.border} ${t.borderHover}`,
    danger: `bg-transparent border ${t.border} ${t.ghostDanger}`,
  };
  const sizes = { sm: "px-2 py-1 text-xs", md: "px-3 py-1.5 text-xs" };
  return (
    <button type={type} onClick={onClick} title={title} className={`${variants[variant]} ${sizes[size]} font-mono uppercase tracking-wider transition-colors inline-flex items-center gap-1.5`}>
      {children}
    </button>
  );
}

function Field({ label, children, hint }) {
  const t = useT();
  return (
    <label className="block">
      <div className={`text-[10px] uppercase tracking-wider ${t.textFaint} mb-1 font-mono`}>{label}</div>
      {children}
      {hint && <div className={`text-[10px] ${t.textGhost} mt-1 font-mono`}>{hint}</div>}
    </label>
  );
}

function Input({ value, onChange, type = "text", placeholder }) {
  const t = useT();
  return (
    <input
      type={type}
      value={value ?? ""}
      onChange={(e) => onChange(type === "number" ? parseFloat(e.target.value) || 0 : e.target.value)}
      placeholder={placeholder}
      className={`w-full ${t.inputBg} border ${t.border} px-2.5 py-1.5 text-sm font-mono ${t.text} ${t.placeholder} focus:outline-none ${t.accentFocus}`}
    />
  );
}

function Textarea({ value, onChange, placeholder, rows = 3 }) {
  const t = useT();
  return (
    <textarea
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      rows={rows}
      className={`w-full ${t.inputBg} border ${t.border} px-2.5 py-1.5 text-xs font-mono ${t.text} ${t.placeholder} focus:outline-none ${t.accentFocus} resize-none`}
    />
  );
}

function Select({ value, onChange, options }) {
  const t = useT();
  return (
    <select
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value)}
      className={`w-full ${t.inputBg} border ${t.border} px-2.5 py-1.5 text-sm font-mono ${t.text} focus:outline-none ${t.accentFocus}`}
    >
      <option value="">— select —</option>
      {options.map((o) => (
        <option key={o.value} value={o.value}>{o.label}</option>
      ))}
    </select>
  );
}

function Th({ children, className = "" }) {
  const t = useT();
  return <th className={`text-left text-[10px] uppercase tracking-wider ${t.textFaint} font-mono font-normal py-2 px-3 border-b ${t.border} ${className}`}>{children}</th>;
}

function Td({ children, className = "", mono = false }) {
  const t = useT();
  return <td className={`py-2.5 px-3 text-sm ${t.textBody} border-b ${t.rowBorder} ${mono ? "font-mono" : ""} ${className}`}>{children}</td>;
}

// =======================================================================
// ROOT
// =======================================================================
export default function ValgoAdmin() {
  const [active, setActive] = useState("dashboard");
  const [state, setState] = useState(EMPTY_STATE);
  const [themeMode, setThemeMode] = useState("dark");
  const [loading, setLoading] = useState(true);
  const [drawer, setDrawer] = useState(null);
  const [toast, setToast] = useState(null);

  const theme = THEMES[themeMode];

  useEffect(() => { load(); }, []);

  async function load() {
    try {
      const stored = localStorage.getItem(THEME_KEY);
      if (stored === "dark" || stored === "light") setThemeMode(stored);
    } catch {}

    const [strats, srcs, accts, nds, sigs, rsk, aud] = await Promise.allSettled([
      apiFetch("/api/strategies").then((r) => r.json()),
      apiFetch("/api/data-sources").then((r) => r.json()),
      apiFetch("/api/accounts").then((r) => r.json()),
      apiFetch("/api/nodes").then((r) => r.json()),
      apiFetch("/api/signals").then((r) => r.json()),
      apiFetch("/api/risk").then((r) => r.json()),
      apiFetch("/api/audit").then((r) => r.json()),
    ]);

    setState({
      strategies:      strats.status === "fulfilled" ? (strats.value.strategies  || []).map(normalizeStrategyFromApi) : [],
      dataSources:     srcs.status   === "fulfilled" ? (srcs.value.sources        || []) : [],
      accounts:        accts.status  === "fulfilled" ? (accts.value.accounts       || []) : [],
      nodes:           nds.status    === "fulfilled" ? (nds.value.nodes            || []) : [],
      signals:         sigs.status   === "fulfilled" ? (sigs.value.signals         || []) : [],
      risk:            rsk.status    === "fulfilled" ? riskFromApi(rsk.value)              : EMPTY_STATE.risk,
      audit:           aud.status    === "fulfilled" ? (aud.value.events           || []).map(normalizeAuditFromApi) : [],
      baseInstruments: [],
      resilience:      EMPTY_STATE.resilience,
    });
    setLoading(false);
  }

  async function saveCollection(collection, list) {
    const path = API_PATH[collection];
    const key  = API_KEY[collection];
    if (!path) return;
    const items = collection === "strategies" ? list.map(normalizeStrategyForApi) : list;
    try { await apiFetch(path, { method: "PUT", body: { [key]: items } }); }
    catch (e) { console.error("save failed:", collection, e); }
  }

  async function toggleTheme() {
    const next = themeMode === "dark" ? "light" : "dark";
    setThemeMode(next);
    try { localStorage.setItem(THEME_KEY, next); } catch {}
  }

  function showToast(msg) { setToast(msg); setTimeout(() => setToast(null), 2200); }

  async function upsert(collection, item) {
    const list = state[collection];
    const exists = list.some((x) => x.id === item.id);
    const newList = exists ? list.map((x) => (x.id === item.id ? item : x)) : [...list, item];
    setState({ ...state, [collection]: newList });
    await saveCollection(collection, newList);
    showToast(exists ? "Updated" : "Created");
  }

  async function remove(collection, id) {
    const newList = state[collection].filter((x) => x.id !== id);
    setState({ ...state, [collection]: newList });
    await saveCollection(collection, newList);
    showToast("Deleted");
  }

  async function toggleActive(collection, id) {
    const newList = state[collection].map((x) => (x.id === id ? { ...x, active: !x.active } : x));
    setState({ ...state, [collection]: newList });
    await saveCollection(collection, newList);
  }

  async function updateRisk(patch) {
    const newRisk = { ...state.risk, ...patch };
    setState({ ...state, risk: newRisk });
    try { await apiFetch("/api/risk", { method: "PUT", body: riskToApi(newRisk) }); }
    catch (e) { console.error("risk save failed:", e); }
    showToast("Risk limits saved");
  }

  function updateResilience(patch) {
    setState({ ...state, resilience: { ...state.resilience, ...patch } });
    showToast("Resilience config saved");
  }

  function updateBaseInstruments(list) {
    setState({ ...state, baseInstruments: list });
    showToast("Base instruments updated");
  }

  function resetAll() {
    if (confirm("Reload all data from the API?")) {
      setLoading(true);
      load();
      showToast("Reloaded");
    }
  }

  if (loading) {
    return (
      <div className={`${theme.bg} ${theme.textFaint} min-h-screen flex items-center justify-center font-mono text-sm`}>
        <RefreshCw className="w-4 h-4 mr-2 animate-spin" /> loading config…
      </div>
    );
  }

  const collMap = { strategy: "strategies", account: "accounts", node: "nodes", signal: "signals", data: "dataSources" };

  return (
    <ThemeContext.Provider value={theme}>
      <div className={`${theme.bg} ${theme.text} min-h-screen flex relative transition-colors duration-200`} style={{ fontFamily: "ui-sans-serif, system-ui, sans-serif" }}>
        <aside className={`w-56 border-r ${theme.border} ${theme.bg} flex flex-col`}>
          <div className={`px-4 py-5 border-b ${theme.border}`}>
            <div className="flex items-center gap-2">
              <div className={`w-2 h-2 ${theme.accentDot} animate-pulse`} />
              <span className="text-base font-medium tracking-wide">VALGO</span>
            </div>
            <div className={`text-[10px] ${theme.textGhost} font-mono uppercase tracking-widest mt-1`}>admin // v3.0</div>
          </div>
          <nav className="flex-1 py-3">
            {SECTIONS.map((s) => {
              const Icon = s.icon;
              const isActive = active === s.id;
              return (
                <button
                  key={s.id}
                  onClick={() => setActive(s.id)}
                  className={`w-full text-left px-4 py-2 text-sm flex items-center gap-3 border-l-2 transition-colors ${
                    isActive ? `${theme.activeBg} ${theme.activeText} ${theme.activeBorder}` : `${theme.inactiveText} ${theme.inactiveTextHover} ${theme.btnHover} border-transparent`
                  }`}
                >
                  <Icon className="w-4 h-4" />
                  <span>{s.label}</span>
                </button>
              );
            })}
          </nav>

          {/* Theme switcher + reset */}
          <div className={`p-4 border-t ${theme.border} space-y-3`}>
            <button
              onClick={toggleTheme}
              className={`w-full flex items-center justify-between gap-2 px-2 py-1.5 border ${theme.border} ${theme.borderHover} ${theme.btnHover} transition-colors`}
              title={`Switch to ${themeMode === "dark" ? "light" : "dark"} theme`}
            >
              <span className="flex items-center gap-2">
                {themeMode === "dark" ? <Moon className="w-3.5 h-3.5" /> : <Sun className="w-3.5 h-3.5" />}
                <span className={`text-[10px] uppercase tracking-wider ${theme.textDim} font-mono`}>{themeMode}</span>
              </span>
              <span className={`text-[10px] ${theme.textGhost} font-mono`}>
                ⇄ {themeMode === "dark" ? "light" : "dark"}
              </span>
            </button>
            <button onClick={resetAll} className={`text-[10px] ${theme.textGhost} ${theme.inactiveTextHover} font-mono uppercase tracking-wider`}>
              reset config
            </button>
          </div>
        </aside>

        <main className="flex-1 min-w-0 flex flex-col">
          <TopBar state={state} onKill={() => updateRisk({ killSwitch: !state.risk.killSwitch })} />
          <div className="flex-1 p-6 overflow-auto">
            {active === "dashboard" && <DashboardView state={state} onJump={setActive} />}
            {active === "strategies" && <StrategiesView state={state} onAdd={() => setDrawer({ type: "strategy", item: null })} onEdit={(item) => setDrawer({ type: "strategy", item })} onDelete={(id) => remove("strategies", id)} onToggle={(id) => toggleActive("strategies", id)} />}
            {active === "data" && <MarketDataView state={state} onAdd={() => setDrawer({ type: "data", item: null })} onEdit={(item) => setDrawer({ type: "data", item })} onDelete={(id) => remove("dataSources", id)} onToggle={(id) => toggleActive("dataSources", id)} onUpdateBase={updateBaseInstruments} onUpdateResilience={updateResilience} />}
            {active === "signals" && <SignalsView state={state} onAdd={() => setDrawer({ type: "signal", item: null })} onEdit={(item) => setDrawer({ type: "signal", item })} onDelete={(id) => remove("signals", id)} onToggle={(id) => toggleActive("signals", id)} />}
            {active === "accounts" && <AccountsView state={state} onAdd={() => setDrawer({ type: "account", item: null })} onEdit={(item) => setDrawer({ type: "account", item })} onDelete={(id) => remove("accounts", id)} />}
            {active === "nodes" && <NodesView state={state} onAdd={() => setDrawer({ type: "node", item: null })} onEdit={(item) => setDrawer({ type: "node", item })} onDelete={(id) => remove("nodes", id)} />}
            {active === "risk" && <RiskView risk={state.risk} onSave={updateRisk} />}
            {active === "audit" && <AuditView audit={state.audit} />}
          </div>
        </main>

        {drawer && (
          <EditDrawer
            drawer={drawer}
            state={state}
            onSave={(item) => { upsert(collMap[drawer.type], item); setDrawer(null); }}
            onClose={() => setDrawer(null)}
          />
        )}

        {toast && (
          <div className={`absolute bottom-6 right-6 ${theme.toastBg} border ${theme.toastBorder} ${theme.toastText} px-3 py-2 text-xs font-mono uppercase tracking-wider z-40`}>
            {toast}
          </div>
        )}
      </div>
    </ThemeContext.Provider>
  );
}

// =======================================================================
// VIEWS
// =======================================================================
function TopBar({ state, onKill }) {
  const t = useT();
  const runningNodes = state.nodes.filter((n) => n.status === "running").length;
  const activeStrats = state.strategies.filter((s) => s.active).length;
  const primaryFeed = state.dataSources.find((d) => d.priority === "primary" && d.active);
  const feedConnected = primaryFeed?.status === "connected";
  const todayOrders = state.audit.length;
  const filledRate = todayOrders ? Math.round((state.audit.filter((a) => a.status === "FILLED").length / todayOrders) * 100) : 0;
  const killOn = state.risk.killSwitch;

  return (
    <header className={`${t.bg} border-b ${t.border} px-6 py-3 flex items-center justify-between`}>
      <div className="flex items-center gap-5 text-xs font-mono">
        <div className="flex items-center gap-2">
          <Activity className={`w-3.5 h-3.5 ${t.accentStrong}`} />
          <span className={`${t.textFaint} uppercase tracking-wider`}>market</span>
          <span className={t.successText}>open</span>
        </div>
        <div className={t.textGhost}>|</div>
        <div className="flex items-center gap-2">
          <Radio className={`w-3.5 h-3.5 ${t.textFaint}`} />
          <span className={`${t.textFaint} uppercase tracking-wider`}>feed</span>
          <span className={feedConnected ? t.successText : t.warningText}>kite</span>
          <span className={t.textGhost}>·</span>
          <span className={t.accent}>{primaryFeed?.subscriptionMode || "—"}</span>
          <StatusDot status={primaryFeed?.status || "disconnected"} />
        </div>
        <div className={t.textGhost}>|</div>
        <div className="flex items-center gap-2">
          <span className={`${t.textFaint} uppercase tracking-wider`}>strategies</span>
          <span className={t.text}>{activeStrats}<span className={t.textGhost}>/{state.strategies.length}</span></span>
        </div>
        <div className={t.textGhost}>|</div>
        <div className="flex items-center gap-2">
          <span className={`${t.textFaint} uppercase tracking-wider`}>nodes</span>
          <span className={t.text}>{runningNodes}<span className={t.textGhost}>/{state.nodes.length}</span></span>
        </div>
        <div className={t.textGhost}>|</div>
        <div className="flex items-center gap-2">
          <span className={`${t.textFaint} uppercase tracking-wider`}>orders</span>
          <span className={t.text}>{todayOrders}</span>
          <span className={t.textGhost}>({filledRate}% fill)</span>
        </div>
      </div>
      <button
        onClick={onKill}
        className={`px-3 py-1.5 text-[10px] uppercase tracking-widest font-mono border transition-colors ${
          killOn ? `${t.killOnBg} ${t.killOnText} ${t.killOnBorder} ${t.killOnBgHover}` : `bg-transparent ${t.killOffText} ${t.border} ${t.killOffTextHover} ${t.killOffBorderHover}`
        }`}
      >
        {killOn ? "● kill switch ON — click to release" : "kill switch"}
      </button>
    </header>
  );
}

function StatCard({ label, value, sub, accent = "default" }) {
  const t = useT();
  const accents = {
    default: t.text, teal: t.accent, emerald: t.successText,
    amber: t.warningText, red: t.dangerText,
  };
  return (
    <div className={`border ${t.border} ${t.surface} p-4`}>
      <div className={`text-[10px] uppercase tracking-widest ${t.textFaint} font-mono`}>{label}</div>
      <div className={`text-2xl font-mono mt-2 ${accents[accent]}`}>{value}</div>
      {sub && <div className={`text-xs ${t.textFaint} mt-1 font-mono`}>{sub}</div>}
    </div>
  );
}

function useEffectiveSubscription(state) {
  return useMemo(() => {
    const base = state.baseInstruments || [];
    const fromStrategies = new Map();
    state.strategies.forEach((s) => {
      if (!s.active) return;
      parseInstruments(s.instruments).forEach((inst) => {
        if (!fromStrategies.has(inst)) fromStrategies.set(inst, []);
        fromStrategies.get(inst).push(s.name);
      });
    });
    const merged = new Set([...base, ...fromStrategies.keys()]);
    return {
      total: merged.size, baseCount: base.length, strategyCount: fromStrategies.size,
      activeStrategiesContributing: new Set([...fromStrategies.values()].flat()).size,
      list: [...merged], contributions: fromStrategies,
    };
  }, [state.strategies, state.baseInstruments]);
}

function DashboardView({ state, onJump }) {
  const t = useT();
  const runningNodes = state.nodes.filter((n) => n.status === "running").length;
  const activeStrats = state.strategies.filter((s) => s.active).length;
  const filled = state.audit.filter((a) => a.status === "FILLED").length;
  const rejected = state.audit.filter((a) => a.status === "REJECTED").length;
  const acct = state.accounts[0];
  const primaryFeed = state.dataSources.find((d) => d.priority === "primary" && d.active);
  const backupFeed = state.dataSources.find((d) => d.priority === "backup-1" && d.active);
  const sub = useEffectiveSubscription(state);

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-medium tracking-tight">Overview</h2>
        <div className={`text-xs ${t.textFaint} font-mono mt-1`}>Real-time system state · ap-south-1</div>
      </div>

      <div className="grid grid-cols-4 gap-3">
        <StatCard label="active strategies" value={activeStrats} sub={`of ${state.strategies.length} configured`} accent="teal" />
        <StatCard label="instruments subscribed" value={sub.total} sub={`${sub.baseCount} base · ${sub.strategyCount} from strategies`} accent="emerald" />
        <StatCard label="nodes online" value={runningNodes} sub={`of ${state.nodes.length} provisioned`} accent="emerald" />
        <StatCard label="today's orders" value={state.audit.length} sub={`${filled} filled · ${rejected} rejected`} />
      </div>

      <div className="grid grid-cols-3 gap-3">
        <div className={`col-span-2 border ${t.border} ${t.surface}`}>
          <div className={`px-4 py-3 border-b ${t.border} flex items-center justify-between`}>
            <div className={`text-[10px] uppercase tracking-widest ${t.textFaint} font-mono`}>recent activity</div>
            <button onClick={() => onJump("audit")} className={`text-[10px] ${t.accent} hover:opacity-80 font-mono uppercase tracking-wider`}>view all →</button>
          </div>
          <div>
            {state.audit.slice(0, 6).map((a) => (
              <div key={a.id} className={`px-4 py-2.5 border-b ${t.rowBorder} last:border-0 flex items-center gap-4 text-sm font-mono`}>
                <span className={`${t.textFaint} text-xs w-16`}>{a.ts}</span>
                <span className={a.side === "BUY" ? t.successTextStrong : t.dangerTextStrong}>
                  {a.side === "BUY" ? <ArrowUpRight className="w-3.5 h-3.5 inline" /> : <ArrowDownRight className="w-3.5 h-3.5 inline" />}
                  {" "}{a.side}
                </span>
                <span className={`${t.text} flex-1`}>{a.symbol}</span>
                <span className={t.textDim}>{a.qty} @ {a.price}</span>
                <Pill tone={a.status === "FILLED" ? "success" : a.status === "REJECTED" ? "danger" : "warning"}>{a.status}</Pill>
              </div>
            ))}
          </div>
        </div>

        <div className={`border ${t.border} ${t.surface}`}>
          <div className={`px-4 py-3 border-b ${t.border}`}>
            <div className={`text-[10px] uppercase tracking-widest ${t.textFaint} font-mono`}>system health</div>
          </div>
          <div className="p-4 space-y-3 text-sm">
            <HealthRow icon={Radio} label="Kite WebSocket" value={primaryFeed?.status === "connected" ? `${primaryFeed.subscriptionMode} · ${primaryFeed.latency}` : "down"} ok={primaryFeed?.status === "connected"} mono />
            <HealthRow icon={RotateCw} label="Backup feed" value={backupFeed ? `${backupFeed.provider.split(" ")[0]} ${backupFeed.status}` : "—"} ok={backupFeed?.active} />
            <HealthRow icon={Lock} label="Daily auth (2FA)" value={acct?.lastAuth || "—"} ok={!!acct?.lastAuth} />
            <HealthRow icon={Wifi} label="Whitelisted IP" value={acct?.staticIp || "—"} ok mono />
            <HealthRow icon={Server} label="NAT Gateway" value="13.234.x.x" ok mono />
            <HealthRow icon={Shield} label="Kill switch" value={state.risk.killSwitch ? "ENGAGED" : "armed"} ok={!state.risk.killSwitch} />
          </div>
        </div>
      </div>
    </div>
  );
}

function HealthRow({ icon: Icon, label, value, ok, mono }) {
  const t = useT();
  return (
    <div className="flex items-center justify-between">
      <span className={`flex items-center gap-2 ${t.textDim}`}>
        <Icon className="w-3.5 h-3.5" />
        <span>{label}</span>
      </span>
      <span className="flex items-center gap-2">
        <span className={`${mono ? "font-mono" : ""} ${t.textBody} text-xs`}>{value}</span>
        <StatusDot status={ok ? "running" : "error"} />
      </span>
    </div>
  );
}

function SectionHeader({ title, subtitle, onAdd, addLabel, banner }) {
  const t = useT();
  return (
    <>
      <div className="flex items-end justify-between mb-3">
        <div>
          <h2 className="text-2xl font-medium tracking-tight">{title}</h2>
          {subtitle && <div className={`text-xs ${t.textFaint} font-mono mt-1`}>{subtitle}</div>}
        </div>
        {onAdd && (
          <Btn variant="primary" size="md" onClick={onAdd}>
            <Plus className="w-3.5 h-3.5" /> {addLabel || "new"}
          </Btn>
        )}
      </div>
      {banner && (
        <div className={`mb-5 border-l-2 ${t.accentSurfaceBorder} ${t.accentSurface} px-3 py-2 text-xs ${t.accentSurfaceText} font-mono`}>
          {banner}
        </div>
      )}
    </>
  );
}

function MarketDataView({ state, onAdd, onEdit, onDelete, onToggle, onUpdateBase, onUpdateResilience }) {
  const t = useT();
  const [tab, setTab] = useState("sources");
  const sub = useEffectiveSubscription(state);
  const primary = state.dataSources.find((d) => d.priority === "primary" && d.active);
  const backup = state.dataSources.find((d) => d.priority === "backup-1" && d.active);

  return (
    <div>
      <SectionHeader
        title="Market data"
        subtitle="Kite WebSocket · provider-agnostic ingestion → unified Redis cache"
        banner="Strategies declare instruments they need; the data layer also subscribes to a base list of indices/benchmarks. Effective subscription = base ∪ active strategy instruments. Auto-reconnect with exponential backoff; on failure, auto-failover to backup provider."
      />

      <div className={`border ${t.border} ${t.surface} mb-5`}>
        <div className={`px-4 py-3 border-b ${t.border} flex items-center justify-between`}>
          <div className={`text-[10px] uppercase tracking-widest ${t.textFaint} font-mono flex items-center gap-2`}>
            <Layers className="w-3 h-3" /> active subscription
          </div>
          <Pill tone={primary?.status === "connected" ? "success" : "warning"}>
            <StatusDot status={primary?.status} /> {primary?.status || "no primary"}
          </Pill>
        </div>
        <div className={`grid grid-cols-4 ${t.divider} divide-x`}>
          <div className="p-4">
            <div className={`text-[10px] uppercase tracking-wider ${t.textFaint} font-mono`}>primary</div>
            <div className={`text-sm ${t.accent} font-mono mt-1.5`}>{primary?.provider || "—"}</div>
            <div className={`text-[10px] ${t.textFaint} font-mono mt-0.5`}>{primary?.endpoint}</div>
          </div>
          <div className="p-4">
            <div className={`text-[10px] uppercase tracking-wider ${t.textFaint} font-mono`}>mode</div>
            <div className={`text-sm ${t.text} font-mono mt-1.5`}>{primary?.subscriptionMode || "—"}</div>
            <div className={`text-[10px] ${t.textFaint} font-mono mt-0.5`}>{primary?.tickTypes}</div>
          </div>
          <div className="p-4">
            <div className={`text-[10px] uppercase tracking-wider ${t.textFaint} font-mono`}>throughput</div>
            <div className={`text-sm ${t.successText} font-mono mt-1.5`}>{primary?.throughput || "—"}</div>
            <div className={`text-[10px] ${t.textFaint} font-mono mt-0.5`}>latency {primary?.latency}</div>
          </div>
          <div className="p-4">
            <div className={`text-[10px] uppercase tracking-wider ${t.textFaint} font-mono`}>failover target</div>
            <div className={`text-sm ${t.text} font-mono mt-1.5`}>{backup?.provider.split(" ")[0] || "none"}</div>
            <div className={`text-[10px] ${t.textFaint} font-mono mt-0.5`}>{backup ? `${backup.status} · last: ${primary?.lastFailover}` : "configure backup-1"}</div>
          </div>
        </div>
        <div className={`px-4 py-3 border-t ${t.border} ${t.overlay} text-xs font-mono ${t.textDim} flex items-center gap-4`}>
          <span>
            <span className={t.textFaint}>subscription:</span>{" "}
            <span className={t.accent}>{sub.total} instruments</span>
            <span className={t.textGhost}>{" = "}</span>
            <span className={t.textMuted}>{sub.baseCount} base</span>
            <span className={t.textGhost}>{" + "}</span>
            <span className={t.textMuted}>{sub.strategyCount} from {sub.activeStrategiesContributing} active strategies</span>
          </span>
        </div>
      </div>

      <div className={`flex gap-1 mb-3 border-b ${t.border}`}>
        {[
          { id: "sources", label: "Sources", icon: Radio },
          { id: "instruments", label: "Effective subscription", icon: Layers },
          { id: "base", label: "Base instruments", icon: Plus },
          { id: "resilience", label: "Resilience", icon: RotateCw },
        ].map((tabItem) => {
          const Icon = tabItem.icon;
          const isActive = tab === tabItem.id;
          return (
            <button key={tabItem.id} onClick={() => setTab(tabItem.id)} className={`px-3 py-2 text-xs font-mono uppercase tracking-wider flex items-center gap-1.5 border-b-2 -mb-px transition-colors ${
              isActive ? `${t.accent} ${t.accentBorder}` : `${t.textFaint} border-transparent ${t.inactiveTextHover}`
            }`}>
              <Icon className="w-3 h-3" /> {tabItem.label}
            </button>
          );
        })}
        <div className="flex-1" />
        {tab === "sources" && (
          <Btn variant="primary" size="md" onClick={onAdd}>
            <Plus className="w-3.5 h-3.5" /> new source
          </Btn>
        )}
      </div>

      {tab === "sources" && <SourcesTable state={state} onEdit={onEdit} onDelete={onDelete} onToggle={onToggle} />}
      {tab === "instruments" && <EffectiveSubscriptionView sub={sub} state={state} />}
      {tab === "base" && <BaseInstrumentsView list={state.baseInstruments} onChange={onUpdateBase} />}
      {tab === "resilience" && <ResilienceView config={state.resilience} onSave={onUpdateResilience} />}
    </div>
  );
}

function SourcesTable({ state, onEdit, onDelete, onToggle }) {
  const t = useT();
  const sorted = [...state.dataSources].sort((a, b) => {
    const order = { primary: 0, "backup-1": 1, "backup-2": 2, disabled: 3 };
    return (order[a.priority] ?? 9) - (order[b.priority] ?? 9);
  });

  return (
    <div className={`border ${t.border} ${t.surface} overflow-x-auto`}>
      <table className="w-full">
        <thead><tr>
          <Th>Name</Th><Th>Provider</Th><Th>Mode</Th><Th>Endpoint</Th><Th>Priority</Th><Th>Reconnect</Th><Th className="text-right">Throughput</Th><Th className="text-right">Latency</Th><Th>Status</Th><Th></Th>
        </tr></thead>
        <tbody>
          {sorted.map((d) => (
            <tr key={d.id} className={`${t.rowHover} group ${!d.active ? "opacity-50" : ""}`}>
              <Td>
                <div className="flex items-center gap-2">
                  {d.priority === "primary" && <span className={`${t.accentStrong} text-[10px]`}>●</span>}
                  {d.name}
                </div>
              </Td>
              <Td className={`${t.textMuted} text-xs`}>{d.provider}</Td>
              <Td><Pill tone={d.subscriptionMode === "FULL" ? "primary" : "info"}>{d.subscriptionMode}</Pill></Td>
              <Td mono className={`${t.textFaint} text-xs max-w-[180px] truncate`} title={d.endpoint}>{d.endpoint}</Td>
              <Td>
                <Pill tone={d.priority === "primary" ? "primary" : d.priority === "disabled" ? "neutral" : "info"}>
                  {d.priority}
                </Pill>
              </Td>
              <Td mono className={`${t.textDim} text-xs`}>
                {d.autoReconnect ? <span className={t.successTextStrong}>auto · {d.maxReconnectAttempts}x</span> : <span className={t.textGhost}>manual</span>}
              </Td>
              <Td mono className={`text-right ${t.successText} text-xs`}>{d.throughput}</Td>
              <Td mono className={`text-right ${t.textDim} text-xs`}>{d.latency}</Td>
              <Td>
                <button onClick={() => onToggle(d.id)} className="flex items-center gap-1.5">
                  <StatusDot status={d.active ? d.status : "disabled"} />
                  <span className={`text-[10px] font-mono uppercase tracking-wider ${t.textDim}`}>{d.active ? d.status : "disabled"}</span>
                </button>
              </Td>
              <Td>
                <div className="opacity-0 group-hover:opacity-100 flex gap-1">
                  <button onClick={() => onEdit(d)} className={`p-1 ${t.btnHover} ${t.textDim} ${t.inactiveTextHover}`}><Pencil className="w-3 h-3" /></button>
                  <button onClick={() => onDelete(d.id)} className={`p-1 ${t.btnHover} ${t.textDim} hover:${t.dangerTextStrong}`}><Trash2 className="w-3 h-3" /></button>
                </div>
              </Td>
            </tr>
          ))}
          {state.dataSources.length === 0 && (
            <tr><td colSpan={10} className={`text-center py-12 ${t.textGhost} font-mono text-sm`}>no data sources — add one to start receiving ticks</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function EffectiveSubscriptionView({ sub, state }) {
  const t = useT();
  return (
    <div className={`border ${t.border} ${t.surface}`}>
      <div className={`px-4 py-3 border-b ${t.border} text-[10px] uppercase tracking-widest ${t.textFaint} font-mono`}>
        merged subscription · {sub.total} instruments sent to Kite WebSocket
      </div>
      <div className={`${t.divider} divide-y`}>
        {sub.list.length === 0 && (
          <div className={`text-center py-12 ${t.textGhost} font-mono text-sm`}>no instruments subscribed — add base instruments or activate strategies</div>
        )}
        {sub.list.map((inst, i) => {
          const isBase = state.baseInstruments.includes(inst);
          const contributors = sub.contributions.get(inst) || [];
          return (
            <div key={i} className={`px-4 py-2 flex items-center gap-3 ${t.rowHover}`}>
              <span className={`font-mono text-sm ${t.accent} flex-1`}>{inst}</span>
              {isBase && <Pill tone="primary">base</Pill>}
              {contributors.map((c, j) => (<Pill key={j} tone="info">{c}</Pill>))}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function BaseInstrumentsView({ list, onChange }) {
  const t = useT();
  const [draft, setDraft] = useState((list || []).join("\n"));

  function save() {
    const parsed = draft.split("\n").map((s) => s.trim()).filter(Boolean);
    onChange(parsed);
  }

  return (
    <div className="max-w-2xl">
      <div className={`border ${t.border} ${t.surface} p-5 space-y-4`}>
        <div>
          <div className={`text-sm ${t.text} font-medium mb-1`}>Always-subscribed instruments</div>
          <div className={`text-xs ${t.textFaint} font-mono`}>
            These instruments are subscribed regardless of which strategies are active. Useful for indices, benchmarks, and any data your decision engine needs continuously (e.g., for context, regime detection, or hedging logic).
          </div>
        </div>
        <Field label="Instruments (one per line)" hint="Use the symbol format your provider expects (e.g., NIFTY 50, NSE:NIFTY50-INDEX)">
          <Textarea value={draft} onChange={setDraft} rows={10} placeholder="NIFTY 50&#10;NIFTY BANK&#10;INDIA VIX" />
        </Field>
        <div className="flex gap-2">
          <Btn variant="primary" size="md" onClick={save}>save base list</Btn>
          <Btn size="md" onClick={() => setDraft((list || []).join("\n"))}>revert</Btn>
        </div>
      </div>
    </div>
  );
}

function ResilienceView({ config, onSave }) {
  const t = useT();
  const [draft, setDraft] = useState(config);
  const dirty = JSON.stringify(draft) !== JSON.stringify(config);

  return (
    <div className="max-w-2xl">
      <div className={`border ${t.border} ${t.surface} p-6 space-y-5`}>
        <div>
          <div className={`text-sm ${t.text} font-medium mb-1`}>Connection resilience</div>
          <div className={`text-xs ${t.textFaint} font-mono`}>
            How the data layer responds to disconnects, slow ticks, or feed-side errors. Auto-failover promotes the next-priority source when the primary is unreachable past the threshold.
          </div>
        </div>

        <div className={`border-t ${t.border} pt-4`}>
          <div className={`text-[10px] uppercase tracking-widest ${t.textFaint} font-mono mb-3`}>reconnect</div>
          <div className="space-y-4">
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={draft.autoReconnect} onChange={(e) => setDraft({ ...draft, autoReconnect: e.target.checked })} className={t.checkboxTeal} />
              <span className={`text-sm ${t.textMuted}`}>Auto-reconnect on disconnect</span>
            </label>
            <Field label="Max reconnect attempts" hint="After this many failures, trigger failover (if enabled)">
              <Input type="number" value={draft.maxReconnectAttempts} onChange={(v) => setDraft({ ...draft, maxReconnectAttempts: v })} />
            </Field>
            <Field label="Backoff strategy">
              <Select value={draft.backoffStrategy} onChange={(v) => setDraft({ ...draft, backoffStrategy: v })} options={[
                { value: "exponential", label: "Exponential (1s, 2s, 4s, 8s, …)" },
                { value: "linear", label: "Linear (constant interval)" },
                { value: "fixed", label: "Fixed delay" },
              ]} />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Initial delay (ms)"><Input type="number" value={draft.backoffInitialMs} onChange={(v) => setDraft({ ...draft, backoffInitialMs: v })} /></Field>
              <Field label="Max delay (ms)"><Input type="number" value={draft.backoffMaxMs} onChange={(v) => setDraft({ ...draft, backoffMaxMs: v })} /></Field>
            </div>
          </div>
        </div>

        <div className={`border-t ${t.border} pt-4`}>
          <div className={`text-[10px] uppercase tracking-widest ${t.textFaint} font-mono mb-3`}>failover</div>
          <div className="space-y-4">
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={draft.autoFailover} onChange={(e) => setDraft({ ...draft, autoFailover: e.target.checked })} className={t.checkboxTeal} />
              <span className={`text-sm ${t.textMuted}`}>Auto-failover to backup provider</span>
            </label>
            <Field label="Failover threshold (ms)" hint="Switch to backup if primary is unresponsive longer than this">
              <Input type="number" value={draft.failoverThresholdMs} onChange={(v) => setDraft({ ...draft, failoverThresholdMs: v })} />
            </Field>
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={draft.failoverNotify} onChange={(e) => setDraft({ ...draft, failoverNotify: e.target.checked })} className={t.checkboxTeal} />
              <span className={`text-sm ${t.textMuted}`}>Send notification on failover (CloudWatch alarm + email)</span>
            </label>
          </div>
        </div>

        <div className={`flex gap-2 pt-2 border-t ${t.border}`}>
          <Btn variant="primary" size="md" onClick={() => onSave(draft)}>save resilience config</Btn>
          <Btn size="md" onClick={() => setDraft(config)}>revert</Btn>
          {dirty && <span className={`${t.warningTextStrong} text-xs font-mono uppercase tracking-wider self-center`}>● unsaved</span>}
        </div>
      </div>
    </div>
  );
}

const STRATEGY_CLASSES = [
  {
    value: "st_psar_confluence",
    label: "SuperTrend + PSAR Confluence",
    badge: "RECOMMENDED",
    badgeTone: "success",
    exchange: "NSE · NFO",
    timeframe: "5-min bars",
    description: "Tick-driven. Preloads 250 historical candles on startup then builds a 251-row DataFrame on every tick (250 closed + 1 live). Entry needs SuperTrend, EMA21, PSAR and RSI all aligned. On signal, scans ITM1→OTM3 option strikes filtered by Volume/OI > 15% and confirmed by the same indicators on the option's own OHLC.",
    symbols: ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"],
  },
  {
    value: "ema_crossover",
    label: "EMA Crossover",
    badge: "BASIC",
    badgeTone: "neutral",
    exchange: "NSE",
    timeframe: "Every tick",
    description: "Fast EMA crosses above slow EMA → BUY. No trend filter, no RSI, no PSAR. Very noisy in sideways markets. Use only for testing the pipeline.",
    symbols: ["NIFTY", "BANKNIFTY", "FINNIFTY"],
  },
  {
    value: "mcx_multi_tick",
    label: "MCX Multi-Commodity",
    badge: "MCX",
    badgeTone: "warning",
    exchange: "MCX",
    timeframe: "5-min bars",
    description: "Same SuperTrend + PSAR indicator stack but for MCX commodity futures. Market hours 9:00 AM – 11:30 PM IST. Symbols are near-month FUT contracts that change on expiry.",
    symbols: ["CRUDEOIL", "GOLD", "SILVER", "NATURALGAS", "COPPER"],
  },
  {
    value: "mcx_multi",
    label: "MCX Multi-Commodity (Polling)",
    badge: "DEPRECATED",
    badgeTone: "danger",
    exchange: "MCX",
    timeframe: "Bar polling",
    description: "Old version of mcx_multi_tick. Uses a timer to poll bar data — up to 1 second behind the market. Use mcx_multi_tick instead.",
    symbols: ["CRUDEOIL", "GOLD"],
  },
];

function StrategiesView({ state, onAdd, onEdit, onDelete, onToggle }) {
  const t = useT();
  const clsLabel = (cn) => STRATEGY_CLASSES.find(c => c.value === cn)?.label || cn || "—";
  const clsExchange = (cn) => STRATEGY_CLASSES.find(c => c.value === cn)?.exchange || "";

  return (
    <div className="space-y-4">
      <SectionHeader
        title="Strategies"
        subtitle={`${state.strategies.length} configured · select logic, instruments, quantity, then activate`}
        onAdd={onAdd}
        addLabel="new strategy"
        banner="Select a strategy class, pick which symbols to watch, set quantity and activate. The decision engine loads active strategies on startup and preloads historical data automatically."
      />

      {/* Strategy cards */}
      <div className="space-y-2">
        {state.strategies.map((s) => {
          const syms = parseInstruments(s.instruments);
          const cls = STRATEGY_CLASSES.find(c => c.value === s.class_name);
          return (
            <div key={s.id} className={`border ${t.border} ${t.surface} p-4`}>
              <div className="flex items-start justify-between gap-4">
                {/* Left: name + class + symbols */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className={`font-medium ${t.text}`}>{s.name}</span>
                    {cls && (
                      <Pill tone={cls.badgeTone}>{cls.badge}</Pill>
                    )}
                  </div>
                  <div className={`text-xs font-mono ${t.textDim} mb-2`}>
                    {clsLabel(s.class_name)}
                    {cls && <span className={`ml-2 ${t.textGhost}`}>· {cls.exchange} · {cls.timeframe}</span>}
                  </div>
                  {/* Symbol pills */}
                  <div className="flex flex-wrap gap-1">
                    {syms.map(sym => (
                      <span key={sym} className={`px-2 py-0.5 text-[10px] font-mono border rounded ${t.accentSurface} ${t.accentSurfaceBorder} ${t.accentStrong}`}>
                        {sym}
                      </span>
                    ))}
                    {syms.length === 0 && <span className={`text-xs ${t.textGhost}`}>no symbols</span>}
                  </div>
                </div>

                {/* Right: qty + last fired + status + actions */}
                <div className="flex items-center gap-4 shrink-0">
                  <div className="text-right">
                    <div className={`text-xs ${t.textGhost}`}>qty</div>
                    <div className={`text-sm font-mono ${t.text}`}>{s.qty}</div>
                  </div>
                  <div className="text-right">
                    <div className={`text-xs ${t.textGhost}`}>last signal</div>
                    <div className={`text-xs font-mono ${t.textFaint}`}>{s.lastFired}</div>
                  </div>

                  {/* Run / Pause button */}
                  <button
                    onClick={() => onToggle(s.id)}
                    className={`flex items-center gap-2 px-3 py-1.5 border text-xs font-mono uppercase tracking-wider transition-all ${
                      s.active
                        ? `${t.pillSuccess} ${t.border}`
                        : `${t.border} ${t.textDim} ${t.btnHover}`
                    }`}
                  >
                    {s.active ? (
                      <><Activity className="w-3 h-3" /> Running</>
                    ) : (
                      <><Zap className="w-3 h-3" /> Run</>
                    )}
                  </button>

                  {/* Edit / Delete */}
                  <div className="flex gap-1">
                    <button onClick={() => onEdit(s)} className={`p-1.5 ${t.btnHover} ${t.textDim} border ${t.border}`} title="Edit">
                      <Pencil className="w-3.5 h-3.5" />
                    </button>
                    <button onClick={() => onDelete(s.id)} className={`p-1.5 ${t.btnHover} ${t.textDim} border ${t.border}`} title="Delete">
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
              </div>
            </div>
          );
        })}

        {state.strategies.length === 0 && (
          <div className={`border ${t.border} ${t.surface} py-16 text-center`}>
            <Zap className={`w-8 h-8 mx-auto mb-3 ${t.textGhost}`} />
            <p className={`${t.textGhost} font-mono text-sm`}>no strategies yet</p>
            <p className={`${t.textFaint} text-xs mt-1`}>click NEW STRATEGY to configure one</p>
          </div>
        )}
      </div>

      {/* How to apply changes notice */}
      {state.strategies.some(s => s.active) && (
        <div className={`border ${t.accentSurfaceBorder} ${t.accentSurface} p-3 flex items-start gap-3`}>
          <RefreshCw className={`w-4 h-4 mt-0.5 shrink-0 ${t.accentStrong}`} />
          <div>
            <p className={`text-xs font-mono ${t.accentStrong}`}>Apply changes</p>
            <p className={`text-xs ${t.textDim} mt-0.5`}>
              After saving, restart the decision engine to pick up the new configuration:
            </p>
            <code className={`text-xs font-mono ${t.accent} mt-1 block`}>
              docker-compose restart decision
            </code>
          </div>
        </div>
      )}
    </div>
  );
}

function AccountsView({ state, onAdd, onEdit, onDelete }) {
  const t = useT();
  return (
    <div>
      <SectionHeader title="Broker accounts" subtitle="One static IP, one API key per SEBI rules · whitelist IP with broker before activating" onAdd={onAdd} addLabel="new account" />
      <div className={`border ${t.border} ${t.surface}`}>
        <table className="w-full">
          <thead><tr>
            <Th>Name</Th><Th>Broker</Th><Th>Static IP</Th><Th>API key</Th><Th>TOTP</Th><Th>Last auth</Th><Th>Status</Th><Th></Th>
          </tr></thead>
          <tbody>
            {state.accounts.map((a) => (
              <tr key={a.id} className={`${t.rowHover} group`}>
                <Td>{a.name}</Td>
                <Td className={`${t.textDim} text-xs`}>{a.broker}</Td>
                <Td mono className={t.accent}>{a.staticIp}</Td>
                <Td mono className={`${t.textFaint} text-xs`}>{a.apiKey}</Td>
                <Td>
                  <Pill tone={a.totpStatus === "configured" ? "success" : "warning"}>
                    {a.totpStatus === "configured" ? <CheckCircle2 className="w-3 h-3" /> : <AlertTriangle className="w-3 h-3" />}
                    {a.totpStatus}
                  </Pill>
                </Td>
                <Td className={`${t.textFaint} text-xs font-mono`}>{a.lastAuth}</Td>
                <Td><Pill tone={a.active ? "success" : "neutral"}>{a.active ? "active" : "inactive"}</Pill></Td>
                <Td>
                  <div className="opacity-0 group-hover:opacity-100 flex gap-1">
                    <button onClick={() => onEdit(a)} className={`p-1 ${t.btnHover} ${t.textDim} ${t.inactiveTextHover}`}><Pencil className="w-3 h-3" /></button>
                    <button onClick={() => onDelete(a.id)} className={`p-1 ${t.btnHover} ${t.textDim}`}><Trash2 className="w-3 h-3" /></button>
                  </div>
                </Td>
              </tr>
            ))}
            {state.accounts.length === 0 && (
              <tr><td colSpan={8} className={`text-center py-12 ${t.textGhost} font-mono text-sm`}>no accounts yet — add a broker account to start trading</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function NodesView({ state, onAdd, onEdit, onDelete }) {
  const t = useT();
  const acctName = (id) => state.accounts.find((a) => a.id === id)?.name || "—";
  return (
    <div>
      <SectionHeader title="Execution nodes" subtitle={`${state.nodes.length} provisioned · EC2 in private subnet · cluster placement group`} onAdd={onAdd} addLabel="new node" />
      <div className={`border ${t.border} ${t.surface}`}>
        <table className="w-full">
          <thead><tr>
            <Th>Name</Th><Th>Instance ID</Th><Th>Private IP</Th><Th>Region</Th><Th>Account</Th><Th>Heartbeat</Th><Th>Status</Th><Th></Th>
          </tr></thead>
          <tbody>
            {state.nodes.map((n) => (
              <tr key={n.id} className={`${t.rowHover} group`}>
                <Td>{n.name}</Td>
                <Td mono className={`${t.textDim} text-xs`}>{n.instanceId}</Td>
                <Td mono className={t.accent}>{n.privateIp}</Td>
                <Td mono className={`${t.textDim} text-xs`}>{n.region}</Td>
                <Td className={`${t.textDim} text-xs`}>{acctName(n.accountId)}</Td>
                <Td mono className={`text-xs ${n.status === "running" ? t.successTextStrong : t.textGhost}`}>{n.heartbeat}</Td>
                <Td>
                  <span className="flex items-center gap-2">
                    <StatusDot status={n.status} />
                    <span className={`text-[10px] font-mono uppercase tracking-wider ${t.textDim}`}>{n.status}</span>
                  </span>
                </Td>
                <Td>
                  <div className="opacity-0 group-hover:opacity-100 flex gap-1">
                    <button onClick={() => onEdit(n)} className={`p-1 ${t.btnHover} ${t.textDim} ${t.inactiveTextHover}`}><Pencil className="w-3 h-3" /></button>
                    <button onClick={() => onDelete(n.id)} className={`p-1 ${t.btnHover} ${t.textDim}`}><Trash2 className="w-3 h-3" /></button>
                  </div>
                </Td>
              </tr>
            ))}
            {state.nodes.length === 0 && (
              <tr><td colSpan={8} className={`text-center py-12 ${t.textGhost} font-mono text-sm`}>no nodes provisioned</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function SignalsView({ state, onAdd, onEdit, onDelete, onToggle }) {
  const t = useT();
  const stratName = (id) => state.strategies.find((s) => s.id === id)?.name || "—";
  return (
    <div>
      <SectionHeader title="Signal sources" subtitle="Inbound webhooks (TradingView etc) mapped to strategies" onAdd={onAdd} addLabel="new source" />
      <div className={`border ${t.border} ${t.surface}`}>
        <table className="w-full">
          <thead><tr>
            <Th>Name</Th><Th>Type</Th><Th>Webhook URL</Th><Th>Secret</Th><Th>Maps to</Th><Th>Last signal</Th><Th>Status</Th><Th></Th>
          </tr></thead>
          <tbody>
            {state.signals.map((sg) => (
              <tr key={sg.id} className={`${t.rowHover} group`}>
                <Td>{sg.name}</Td>
                <Td><Pill tone="info">{sg.type}</Pill></Td>
                <Td mono className={`${t.accent} text-xs`}>{sg.url}</Td>
                <Td mono className={`${t.textFaint} text-xs`}>{sg.secret}</Td>
                <Td className={`${t.textMuted} text-xs`}>{stratName(sg.strategyId)}</Td>
                <Td className={`${t.textFaint} text-xs font-mono`}>{sg.lastSignal}</Td>
                <Td>
                  <button onClick={() => onToggle(sg.id)} className="flex items-center gap-1.5">
                    <StatusDot status={sg.active ? "active" : "inactive"} />
                    <span className={`text-[10px] font-mono uppercase tracking-wider ${t.textDim}`}>{sg.active ? "active" : "paused"}</span>
                  </button>
                </Td>
                <Td>
                  <div className="opacity-0 group-hover:opacity-100 flex gap-1">
                    <button onClick={() => onEdit(sg)} className={`p-1 ${t.btnHover} ${t.textDim} ${t.inactiveTextHover}`}><Pencil className="w-3 h-3" /></button>
                    <button onClick={() => onDelete(sg.id)} className={`p-1 ${t.btnHover} ${t.textDim}`}><Trash2 className="w-3 h-3" /></button>
                  </div>
                </Td>
              </tr>
            ))}
            {state.signals.length === 0 && (
              <tr><td colSpan={8} className={`text-center py-12 ${t.textGhost} font-mono text-sm`}>no signal sources configured</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function RiskView({ risk, onSave }) {
  const t = useT();
  const [draft, setDraft] = useState(risk);
  const dirty = JSON.stringify(draft) !== JSON.stringify(risk);
  return (
    <div className="max-w-2xl">
      <SectionHeader title="Risk limits" subtitle="Pre-trade gates enforced by execution router · changes apply immediately" />
      <div className={`border ${t.border} ${t.surface} p-6 space-y-5`}>
        <Field label="Max orders per second" hint="SEBI cap: 10/s per API key unless exchange-approved">
          <Input type="number" value={draft.maxOrdersPerSec} onChange={(v) => setDraft({ ...draft, maxOrdersPerSec: v })} />
        </Field>
        <Field label="Max daily loss (₹)" hint="Kill switch engages automatically when breached">
          <Input type="number" value={draft.maxDailyLoss} onChange={(v) => setDraft({ ...draft, maxDailyLoss: v })} />
        </Field>
        <Field label="Max position value (₹)" hint="Per-strategy notional limit">
          <Input type="number" value={draft.maxPositionValue} onChange={(v) => setDraft({ ...draft, maxPositionValue: v })} />
        </Field>
        <Field label="Max open positions" hint="Across all strategies in this account">
          <Input type="number" value={draft.maxOpenPositions} onChange={(v) => setDraft({ ...draft, maxOpenPositions: v })} />
        </Field>
        <div className={`pt-4 border-t ${t.border} flex items-center justify-between`}>
          <label className="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={draft.killSwitch} onChange={(e) => setDraft({ ...draft, killSwitch: e.target.checked })} className={t.checkboxRed} />
            <span className={`text-sm ${t.textMuted}`}>Engage kill switch <span className={`${t.textGhost} font-mono text-xs`}>(blocks all outbound orders)</span></span>
          </label>
        </div>
        <div className="flex gap-2 pt-2">
          <Btn variant="primary" size="md" onClick={() => onSave(draft)}>save changes</Btn>
          <Btn size="md" onClick={() => setDraft(risk)}>revert</Btn>
          {dirty && <span className={`${t.warningTextStrong} text-xs font-mono uppercase tracking-wider self-center`}>● unsaved</span>}
        </div>
      </div>
    </div>
  );
}

function AuditView({ audit }) {
  const t = useT();
  const [filter, setFilter] = useState("all");
  const filtered = filter === "all" ? audit : audit.filter((a) => a.status === filter);
  return (
    <div>
      <SectionHeader title="Audit log" subtitle={`${audit.length} orders today · DynamoDB-backed`} />
      <div className="flex gap-1 mb-3">
        {["all", "FILLED", "REJECTED", "PENDING"].map((f) => (
          <button key={f} onClick={() => setFilter(f)} className={`px-2.5 py-1 text-[10px] uppercase tracking-wider font-mono border ${
            filter === f ? `${t.surfaceSolid} ${t.text} ${t.borderStrong}` : `bg-transparent ${t.textFaint} ${t.border} ${t.inactiveTextHover}`
          }`}>
            {f}
          </button>
        ))}
      </div>
      <div className={`border ${t.border} ${t.surface}`}>
        <table className="w-full">
          <thead><tr>
            <Th>Time</Th><Th>Strategy</Th><Th>Account</Th><Th>Symbol</Th><Th>Side</Th><Th className="text-right">Qty</Th><Th className="text-right">Price</Th><Th>Status</Th>
          </tr></thead>
          <tbody>
            {filtered.map((a) => (
              <tr key={a.id} className={t.rowHover}>
                <Td mono className={`${t.textFaint} text-xs`}>{a.ts}</Td>
                <Td className={`${t.text} text-xs`}>{a.strategy}</Td>
                <Td className={`${t.textDim} text-xs`}>{a.account}</Td>
                <Td mono className={t.accent}>{a.symbol}</Td>
                <Td><span className={`font-mono text-xs ${a.side === "BUY" ? t.successTextStrong : t.dangerTextStrong}`}>{a.side}</span></Td>
                <Td mono className="text-right">{a.qty}</Td>
                <Td mono className="text-right">{a.price}</Td>
                <Td><Pill tone={a.status === "FILLED" ? "success" : a.status === "REJECTED" ? "danger" : "warning"}>{a.status}</Pill></Td>
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr><td colSpan={8} className={`text-center py-12 ${t.textGhost} font-mono text-sm`}>no orders match this filter</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function EditDrawer({ drawer, state, onSave, onClose }) {
  const t = useT();
  const { type, item } = drawer;
  const [form, setForm] = useState(item || initFormFor(type));

  function commit() {
    const finalItem = { ...form };
    if (!finalItem.id) finalItem.id = genId(type[0]);
    onSave(finalItem);
  }

  return (
    <div className={`absolute inset-y-0 right-0 w-[420px] ${t.bg} border-l ${t.border} shadow-2xl z-30 flex flex-col`}>
      <div className={`px-5 py-4 border-b ${t.border} flex items-center justify-between`}>
        <div>
          <div className={`text-[10px] uppercase tracking-widest ${t.textFaint} font-mono`}>{item ? "edit" : "new"}</div>
          <div className="text-base font-medium capitalize">{type === "data" ? "data source" : type}</div>
        </div>
        <button onClick={onClose} className={`${t.textFaint} ${t.inactiveTextHover}`}><X className="w-4 h-4" /></button>
      </div>
      <div className="flex-1 overflow-auto p-5 space-y-4">
        {type === "strategy" && <StrategyForm form={form} setForm={setForm} accounts={state.accounts} />}
        {type === "data" && <DataSourceForm form={form} setForm={setForm} />}
        {type === "account" && <AccountForm form={form} setForm={setForm} />}
        {type === "node" && <NodeForm form={form} setForm={setForm} accounts={state.accounts} />}
        {type === "signal" && <SignalForm form={form} setForm={setForm} strategies={state.strategies} />}
      </div>
      <div className={`px-5 py-4 border-t ${t.border} flex gap-2`}>
        <Btn variant="primary" size="md" onClick={commit}>save</Btn>
        <Btn size="md" onClick={onClose}>cancel</Btn>
      </div>
    </div>
  );
}

function initFormFor(type) {
  if (type === "strategy") return { active: true, type: "CE", target: 0.5, stopLoss: 0.3, qty: 50, instruments: "" };
  if (type === "data") return { active: true, connectionType: "WebSocket", subscriptionMode: "FULL", priority: "backup-1", status: "disconnected", autoReconnect: true, maxReconnectAttempts: 5, reconnectBackoff: "exponential 1s→32s", throughput: "0 t/s", latency: "—", lastTick: "—" };
  if (type === "account") return { active: true, totpStatus: "pending", broker: "Zerodha Kite" };
  if (type === "node") return { status: "stopped", region: "ap-south-1" };
  if (type === "signal") return { active: true, type: "TradingView" };
  return {};
}

function DataSourceForm({ form, setForm }) {
  const t = useT();
  return (
    <>
      <Field label="Source name" hint="A friendly label, e.g. 'Kite WebSocket primary'">
        <Input value={form.name} onChange={(v) => setForm({ ...form, name: v })} placeholder="Kite WebSocket primary" />
      </Field>
      <Field label="Provider">
        <Select value={form.provider} onChange={(v) => setForm({ ...form, provider: v })} options={PROVIDERS.map((p) => ({ value: p, label: p }))} />
      </Field>
      <Field label="Connection type">
        <Select value={form.connectionType} onChange={(v) => setForm({ ...form, connectionType: v })} options={[
          { value: "WebSocket", label: "WebSocket (recommended)" },
          { value: "REST polling", label: "REST polling" },
          { value: "FIX", label: "FIX protocol" },
        ]} />
      </Field>
      <Field label="WebSocket endpoint">
        <Input value={form.endpoint} onChange={(v) => setForm({ ...form, endpoint: v })} placeholder="wss://ws.kite.trade" />
      </Field>
      <Field label="Subscription mode" hint="Kite: LTP (8 bytes), QUOTE (44 bytes), FULL (184 bytes incl. 5-level depth)">
        <Select value={form.subscriptionMode} onChange={(v) => setForm({ ...form, subscriptionMode: v })} options={[
          { value: "LTP", label: "LTP — last traded price only" },
          { value: "QUOTE", label: "QUOTE — LTP + OHLC + volume" },
          { value: "FULL", label: "FULL — adds 5-level market depth" },
        ]} />
      </Field>
      <Field label="API key" hint="Provider API key (stored encrypted in Secrets Manager)">
        <Input value={form.apiKey} onChange={(v) => setForm({ ...form, apiKey: v })} placeholder="kite_md_xxx_***" />
      </Field>
      <Field label="API secret">
        <Input value={form.apiSecret} onChange={(v) => setForm({ ...form, apiSecret: v })} placeholder="****" />
      </Field>
      <Field label="Failover priority" hint="Primary feeds Redis; backups take over on primary failure">
        <Select value={form.priority} onChange={(v) => setForm({ ...form, priority: v })} options={[
          { value: "primary", label: "Primary (active source)" },
          { value: "backup-1", label: "Backup 1 (first failover)" },
          { value: "backup-2", label: "Backup 2 (second failover)" },
          { value: "disabled", label: "Disabled (configured, not used)" },
        ]} />
      </Field>
      <div className={`pt-3 border-t ${t.border}`}>
        <div className={`text-[10px] uppercase tracking-wider ${t.textFaint} font-mono mb-3`}>reconnect (per-source)</div>
        <label className="flex items-center gap-2 cursor-pointer mb-3">
          <input type="checkbox" checked={form.autoReconnect || false} onChange={(e) => setForm({ ...form, autoReconnect: e.target.checked })} className={t.checkboxTeal} />
          <span className={`text-sm ${t.textMuted}`}>Auto-reconnect on this source</span>
        </label>
        <Field label="Max reconnect attempts before failover">
          <Input type="number" value={form.maxReconnectAttempts} onChange={(v) => setForm({ ...form, maxReconnectAttempts: v })} />
        </Field>
      </div>
      <label className="flex items-center gap-2 cursor-pointer pt-2">
        <input type="checkbox" checked={form.active || false} onChange={(e) => setForm({ ...form, active: e.target.checked })} className={t.checkboxTeal} />
        <span className={`text-sm ${t.textMuted}`}>Active</span>
      </label>
    </>
  );
}

function StrategyForm({ form, setForm, accounts }) {
  const t = useT();

  const currentClass = form.class_name || "st_psar_confluence";
  const clsMeta = STRATEGY_CLASSES.find(c => c.value === currentClass) || STRATEGY_CLASSES[0];
  const selectedSymbols = parseInstruments(form.instruments || "");

  function handleClassChange(cls_name) {
    const meta = STRATEGY_CLASSES.find(c => c.value === cls_name);
    setForm({
      ...form,
      class_name: cls_name,
      entry: cls_name,
      // auto-fill default symbols only when instruments is empty
      instruments: form.instruments ? form.instruments : (meta?.symbols.slice(0, 2).join(", ") || ""),
    });
  }

  function toggleSymbol(sym) {
    const next = selectedSymbols.includes(sym)
      ? selectedSymbols.filter(s => s !== sym)
      : [...selectedSymbols, sym];
    setForm({ ...form, instruments: next.join(", ") });
  }

  return (
    <>
      {/* ── Step 1: Strategy class ─────────────────────────── */}
      <Field label="Strategy logic" hint="Which decision algorithm to run">
        <Select
          value={currentClass}
          onChange={handleClassChange}
          options={STRATEGY_CLASSES.map(c => ({ value: c.value, label: c.label }))}
        />
      </Field>

      {/* Description card */}
      <div className={`rounded border p-3 space-y-1.5 ${t.accentSurface} ${t.accentSurfaceBorder}`}>
        <div className="flex items-center gap-2">
          <Pill tone={clsMeta.badgeTone}>{clsMeta.badge}</Pill>
          <span className={`text-xs font-mono ${t.textDim}`}>{clsMeta.exchange}</span>
          <span className={t.textGhost}>·</span>
          <span className={`text-xs ${t.textDim}`}>{clsMeta.timeframe}</span>
        </div>
        <p className={`text-xs leading-relaxed ${t.textMuted}`}>{clsMeta.description}</p>
      </div>

      {/* ── Step 2: Name ───────────────────────────────────── */}
      <Field label="Strategy name">
        <Input
          value={form.name || ""}
          onChange={v => setForm({ ...form, name: v })}
          placeholder={`${clsMeta.label} — NIFTY`}
        />
      </Field>

      {/* ── Step 3: Symbols (toggle buttons) ──────────────── */}
      <Field
        label="Watch symbols"
        hint="Strategy will receive a tick for each selected symbol via Kite WebSocket"
      >
        <div className="flex flex-wrap gap-2 pt-1">
          {clsMeta.symbols.map(sym => {
            const active = selectedSymbols.includes(sym);
            return (
              <button
                key={sym}
                type="button"
                onClick={() => toggleSymbol(sym)}
                className={`px-3 py-1.5 text-xs font-mono border rounded-sm transition-all ${
                  active
                    ? `${t.accentBg} ${t.accentBgText} border-transparent font-semibold`
                    : `${t.border} ${t.textDim} ${t.btnHover}`
                }`}
              >
                {sym}
              </button>
            );
          })}
        </div>
        {selectedSymbols.length === 0 && (
          <p className={`text-xs mt-1 ${t.dangerText}`}>Select at least one symbol</p>
        )}
      </Field>

      {/* ── Step 4: Quantity ───────────────────────────────── */}
      <Field label="Quantity (lots)" hint="NIFTY lot size = 75  ·  BANKNIFTY = 30  ·  FINNIFTY = 40">
        <Input
          type="number"
          value={form.qty ?? 1}
          onChange={v => setForm({ ...form, qty: Number(v) })}
        />
      </Field>

      {/* ── Step 5: Account ────────────────────────────────── */}
      {accounts.length > 0 && (
        <Field label="Broker account">
          <Select
            value={form.accountId || ""}
            onChange={v => setForm({ ...form, accountId: v })}
            options={[
              { value: "", label: "— select account —" },
              ...accounts.map(a => ({ value: a.id, label: a.name })),
            ]}
          />
        </Field>
      )}

      {/* ── Step 6: Active toggle ──────────────────────────── */}
      <div className={`border ${t.border} rounded p-3 flex items-center justify-between`}>
        <div>
          <p className={`text-sm font-medium ${t.text}`}>Activate strategy</p>
          <p className={`text-xs ${t.textDim} mt-0.5`}>
            When active, the decision engine loads and runs this strategy on startup
          </p>
        </div>
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={form.active ?? true}
            onChange={e => setForm({ ...form, active: e.target.checked })}
            className={t.checkboxTeal}
          />
          <span className={`text-sm font-mono ${form.active ? t.successTextStrong : t.textGhost}`}>
            {form.active ? "ON" : "OFF"}
          </span>
        </label>
      </div>
    </>
  );
}

const BROKER_OPTIONS = [
  { value: "Shoonya (Finvasia)", label: "Shoonya (Finvasia)" },
  { value: "Zerodha Kite",       label: "Zerodha Kite" },
  { value: "Fyers",              label: "Fyers" },
  { value: "Upstox",             label: "Upstox" },
  { value: "ICICI Direct",       label: "ICICI Direct (Breeze)" },
  { value: "Angel One",          label: "Angel One (SmartAPI)" },
  { value: "Dhan",               label: "Dhan" },
  { value: "Other",              label: "Other" },
];

function AccountForm({ form, setForm }) {
  const t = useT();
  const f = (k, v) => setForm({ ...form, [k]: v });
  const broker = form.broker || "";
  const isShoonya = broker === "Shoonya (Finvasia)";
  const isKite    = broker === "Zerodha Kite";
  const isFyers   = broker === "Fyers";

  return (
    <>
      {/* ── Common ─────────────────────────────────────────── */}
      <Field label="Account name">
        <Input value={form.name} onChange={(v) => f("name", v)} placeholder="Shoonya Main" />
      </Field>

      <Field label="Broker">
        <Select value={form.broker} onChange={(v) => f("broker", v)} options={BROKER_OPTIONS} />
      </Field>

      <Field label="User ID / Client ID" hint="Your login ID with the broker (e.g. FA12345)">
        <Input value={form.userId} onChange={(v) => f("userId", v)} placeholder="FA12345" />
      </Field>

      {/* ── Shoonya (Finvasia) ──────────────────────────────── */}
      {isShoonya && (
        <>
          <Field label="Password" hint="Your Shoonya login password — hashed before use">
            <Input type="password" value={form.password} onChange={(v) => f("password", v)} placeholder="••••••••" />
          </Field>
          <Field label="TOTP seed (base32)" hint="The text secret shown under the QR code when you set up 2FA — enables auto-login without manual OTP">
            <Input type="password" value={form.totpSeed} onChange={(v) => f("totpSeed", v)} placeholder="JBSWY3DPEHPK3PXP…" />
          </Field>
          <Field label="Vendor code" hint="From Shoonya API console → your app → Vendor Code">
            <Input value={form.vendorCode} onChange={(v) => f("vendorCode", v)} placeholder="FA12345_U" />
          </Field>
          <Field label="App key / API secret" hint="From Shoonya API console → your app → App Key — hashed before use">
            <Input type="password" value={form.apiSecret} onChange={(v) => f("apiSecret", v)} placeholder="••••••••••••" />
          </Field>
          <Field label="IMEI / Device tag" hint="Any unique string that identifies this machine">
            <Input value={form.imei} onChange={(v) => f("imei", v)} placeholder="valgo-trader-01" />
          </Field>
        </>
      )}

      {/* ── Zerodha Kite ───────────────────────────────────── */}
      {isKite && (
        <>
          <Field label="API key" hint="From developers.kite.trade → your app">
            <Input value={form.apiKey} onChange={(v) => f("apiKey", v)} placeholder="jr877i7sigl4bnhd" />
          </Field>
          <Field label="API secret">
            <Input type="password" value={form.apiSecret} onChange={(v) => f("apiSecret", v)} placeholder="••••••••••••" />
          </Field>
          <Field label="Access token" hint="Generated daily — paste here for local dev (or use auth-refresh service)">
            <Input type="password" value={form.accessToken} onChange={(v) => f("accessToken", v)} placeholder="3L8vISP4DBD…" />
          </Field>
        </>
      )}

      {/* ── Fyers ──────────────────────────────────────────── */}
      {isFyers && (
        <>
          <Field label="App ID" hint="From Fyers API dashboard">
            <Input value={form.apiKey} onChange={(v) => f("apiKey", v)} placeholder="FYERS_APP_ID" />
          </Field>
          <Field label="App secret">
            <Input type="password" value={form.apiSecret} onChange={(v) => f("apiSecret", v)} placeholder="••••••••••••" />
          </Field>
          <Field label="PIN">
            <Input type="password" value={form.pin} onChange={(v) => f("pin", v)} placeholder="••••" />
          </Field>
          <Field label="TOTP seed (base32)" hint="Base32 secret from your authenticator">
            <Input type="password" value={form.totpSeed} onChange={(v) => f("totpSeed", v)} placeholder="JBSWY3DPEHPK3PXP…" />
          </Field>
        </>
      )}

      {/* ── Generic (Upstox / Dhan / Other) ────────────────── */}
      {!isShoonya && !isKite && !isFyers && (
        <>
          <Field label="API key">
            <Input value={form.apiKey} onChange={(v) => f("apiKey", v)} placeholder="api_key_***" />
          </Field>
          <Field label="API secret">
            <Input type="password" value={form.apiSecret} onChange={(v) => f("apiSecret", v)} placeholder="••••••••••••" />
          </Field>
        </>
      )}

      {/* ── Common tail ─────────────────────────────────────── */}
      <Field label="Static IP (whitelisted)" hint="The public IP you registered with the broker — leave blank if broker has no IP restriction">
        <Input value={form.staticIp} onChange={(v) => f("staticIp", v)} placeholder="223.178.x.x" />
      </Field>

      <Field label="TOTP status">
        <Select value={form.totpStatus} onChange={(v) => f("totpStatus", v)} options={[
          { value: "configured", label: "Configured — auto 2FA active" },
          { value: "pending",    label: "Pending setup" },
          { value: "error",      label: "Error" },
        ]} />
      </Field>

      <Field label="Last auth" hint="Filled automatically after each successful login">
        <Input value={form.lastAuth} onChange={(v) => f("lastAuth", v)} placeholder="—" />
      </Field>

      <label className="flex items-center gap-2 cursor-pointer pt-2">
        <input type="checkbox" checked={form.active || false} onChange={(e) => f("active", e.target.checked)} className={t.checkboxTeal} />
        <span className={`text-sm ${t.textMuted}`}>Active</span>
      </label>
    </>
  );
}

function NodeForm({ form, setForm, accounts }) {
  return (
    <>
      <Field label="Node name"><Input value={form.name} onChange={(v) => setForm({ ...form, name: v })} placeholder="exec-node-05" /></Field>
      <Field label="EC2 instance ID"><Input value={form.instanceId} onChange={(v) => setForm({ ...form, instanceId: v })} placeholder="i-0xxxxxxxxxxxxxxxx" /></Field>
      <Field label="Private IP"><Input value={form.privateIp} onChange={(v) => setForm({ ...form, privateIp: v })} placeholder="10.0.2.x" /></Field>
      <Field label="Region"><Input value={form.region} onChange={(v) => setForm({ ...form, region: v })} placeholder="ap-south-1" /></Field>
      <Field label="Bound account">
        <Select value={form.accountId} onChange={(v) => setForm({ ...form, accountId: v })} options={accounts.map((a) => ({ value: a.id, label: a.name }))} />
      </Field>
      <Field label="Status">
        <Select value={form.status} onChange={(v) => setForm({ ...form, status: v })} options={[
          { value: "stopped", label: "Stopped" },
          { value: "running", label: "Running" },
          { value: "pending", label: "Pending" },
          { value: "error", label: "Error" },
        ]} />
      </Field>
      <Field label="Heartbeat"><Input value={form.heartbeat} onChange={(v) => setForm({ ...form, heartbeat: v })} placeholder="5s ago" /></Field>
    </>
  );
}

function SignalForm({ form, setForm, strategies }) {
  const t = useT();
  return (
    <>
      <Field label="Source name"><Input value={form.name} onChange={(v) => setForm({ ...form, name: v })} placeholder="TradingView NIFTY breakout" /></Field>
      <Field label="Type">
        <Select value={form.type} onChange={(v) => setForm({ ...form, type: v })} options={[
          { value: "TradingView", label: "TradingView webhook" },
          { value: "Custom", label: "Custom webhook" },
          { value: "Internal", label: "Internal signal" },
        ]} />
      </Field>
      <Field label="Webhook path" hint="Appended to your ALB hostname"><Input value={form.url} onChange={(v) => setForm({ ...form, url: v })} placeholder="/webhook/tv/nifty-bo" /></Field>
      <Field label="Shared secret" hint="Used to verify inbound payload signature"><Input value={form.secret} onChange={(v) => setForm({ ...form, secret: v })} placeholder="****" /></Field>
      <Field label="Maps to strategy">
        <Select value={form.strategyId} onChange={(v) => setForm({ ...form, strategyId: v })} options={strategies.map((s) => ({ value: s.id, label: s.name }))} />
      </Field>
      <label className="flex items-center gap-2 cursor-pointer pt-2">
        <input type="checkbox" checked={form.active || false} onChange={(e) => setForm({ ...form, active: e.target.checked })} className={t.checkboxTeal} />
        <span className={`text-sm ${t.textMuted}`}>Active</span>
      </label>
    </>
  );
}
