/* 轻量 API 封装 + 全局加载态 */
const API = {
  async get(path) {
    const r = await fetch(path);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  },
  async post(path, body) {
    const opt = { method: "POST" };
    if (body !== undefined) {
      opt.headers = { "Content-Type": "application/json" };
      opt.body = JSON.stringify(body);
    }
    const r = await fetch(path, opt);
    if (!r.ok) {
      let msg = `HTTP ${r.status}`;
      try { const j = await r.json(); if (j && j.detail) msg = j.detail; } catch (e) { /* 保留默认 */ }
      throw new Error(msg);
    }
    return r.json();
  },
  overview: () => API.get("/api/overview"),
  map: () => API.get("/api/map"),
  rainEvents: () => API.get("/api/rain-events"),
  rainEvent: (id) => API.get(`/api/rain-events/${id}`),
  reservoirs: () => API.get("/api/reservoirs"),
  warnings: () => API.get("/api/warnings"),
  evacuations: () => API.get("/api/evacuations"),
  forecast: (eid, mode) => API.post(`/api/forecast/${eid}/${mode}`),
  forecastRuns: () => API.get("/api/forecast/runs"),
  forecastSeries: (runId) => API.get(`/api/forecast/series/${runId}`),
  responseTasks: () => API.get("/api/response-tasks"),
  responseInitiate: (runId, dispatcher, title) =>
    API.post("/api/response-tasks", { run_id: runId, dispatcher, title }),
  responseReview: (id, dutyOfficer, approve, note) =>
    API.post(`/api/response-tasks/${id}/review`, { duty_officer: dutyOfficer, approve, note }),
  responseExecute: (id, evacLead) =>
    API.post(`/api/response-tasks/${id}/execute`, { evac_lead: evacLead }),
  responseComplete: (id, actor) =>
    API.post(`/api/response-tasks/${id}/complete`, { actor }),
};

/* 全局运行状态：跨视图共享最近一次预报结果 / 运行记录 */
const store = {
  lastForecast: null,        // {run_id, ...完整预报结果}
  runs: [],                  // [{id, event_id, mode, created_at}]
  mapData: null,
  overview: null,
};

const fmt = {
  num(v, d = 1) { return (v == null ? "—" : Number(v).toFixed(d)); },
  lvName(lv) { return { red: "红色预警", orange: "橙色预警", yellow: "黄色预警", blue: "蓝色预警", "": "" }[lv] || lv; },
  lvClass(lv) { return { red: "lv-4", orange: "lv-3", yellow: "lv-2", blue: "lv-1" }[lv] || ""; },
  lvBadge(lv) { return { red: "red", orange: "orange", yellow: "yellow", blue: "blue" }[lv] || "gray"; },
  hour(h) { return `${String(h).padStart(2, "0")}:00`; },
  time(t) { if (!t) return "—"; const d = new Date(t); return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`; },
};

// 普通 script 的顶层 const 不会挂到 window，显式导出供视图/全局配置使用
window.store = store;
window.fmt = fmt;
window.API = API;
