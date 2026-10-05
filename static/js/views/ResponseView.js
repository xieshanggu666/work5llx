/* 视图：联合防汛处置协同 —— 发起→审核→执行→完成 四态闭环，方案回写 */
window.ResponseView = {
  name: "ResponseView",
  data() {
    return {
      tasks: [], runs: [], events: [],
      selectedId: null,
      runId: "", dispatcher: "",
      dutyOfficer: "", reviewNote: "", evacLead: "",
      busy: false,
    };
  },
  computed: {
    selected() { return this.tasks.find(t => t.id === this.selectedId) || null; },
    runOptions() {
      return this.runs.map(r => {
        const ev = this.events.find(e => e.id === r.event_id);
        return { id: r.id, label: `#${r.id} ${ev ? ev.name : "情景" + r.event_id} · ${this.modeName(r.mode)} · ${r.status}` };
      });
    },
    steps() {
      return [
        { id: "initiated", t: "发起", role: "调度员", who: this.selected ? this.selected.dispatcher : "", at: this.selected ? this.selected.created_at : "" },
        { id: "approved", t: "审核", role: "预警值守", who: this.selected ? this.selected.duty_officer : "", at: this.selected ? this.selected.reviewed_at : "" },
        { id: "executed", t: "执行", role: "转移负责人", who: this.selected ? this.selected.evac_lead : "", at: this.selected ? this.selected.executed_at : "" },
        { id: "completed", t: "完成", role: "闭环销号", who: "", at: this.selected ? this.selected.completed_at : "" },
      ];
    },
    stepIdx() {
      if (!this.selected) return -1;
      return ["initiated", "approved", "executed", "completed"].indexOf(this.selected.status);
    },
    wb() { return this.selected ? (this.selected.writeback || {}) : {}; },
    hasWb() { const w = this.wb; return (w.reservoirs && w.reservoirs.length) || w.evacuations_moving != null || w.evacuations_safe != null; },
  },
  methods: {
    async load(keepSelection = true) {
      const [tasks, runs, events] = await Promise.all([API.responseTasks(), API.forecastRuns(), API.rainEvents()]);
      this.tasks = tasks; this.runs = runs; this.events = events;
      if (!keepSelection || !this.tasks.find(t => t.id === this.selectedId)) {
        this.selectedId = this.tasks.length ? this.tasks[0].id : null;
      }
      if (!this.runId && this.runs.length) {
        const done = this.runs.find(r => r.status === "done");
        this.runId = done ? done.id : this.runs[0].id;
      }
    },
    modeName(m) { return { natural: "天然工况", rule: "规则调度", optimized: "联合优化" }[m] || m; },
    stName(s) { return { initiated: "已发起", approved: "已审核", executed: "已执行", completed: "已完成" }[s] || s; },
    stColor(s) { return { initiated: "blue", approved: "yellow", executed: "orange", completed: "green" }[s] || "gray"; },
    async act(fn, okMsg) {
      this.busy = true;
      try {
        const t = await fn();
        await this.load();
        this.selectedId = t.id;
        window.app.showToast(okMsg);
      } catch (e) {
        window.app.showToast("操作失败：" + e.message);
      } finally { this.busy = false; }
    },
    initiate() {
      if (!this.runId) return;
      this.act(() => API.responseInitiate(Number(this.runId), this.dispatcher), "处置协同已发起，待预警值守审核");
    },
    review(approve) {
      this.act(() => API.responseReview(this.selectedId, this.dutyOfficer, approve, this.reviewNote),
               approve ? "审核通过，方案待执行" : "已退回调度员修订");
    },
    execute() {
      this.act(() => API.responseExecute(this.selectedId, this.evacLead), "方案已回写：水库工况更新，转移启动");
    },
    complete() {
      this.act(() => API.responseComplete(this.selectedId, ""), "处置闭环完成：转移安置确认，预警销号");
    },
  },
  mounted() { this.load(); },
  template: `
  <div class="page">
    <div class="page-title">联合防汛处置协同
      <span class="sub">调度员 · 预警值守 · 转移负责人 ｜ 发起 → 审核 → 执行 → 完成 四态闭环</span>
      <button class="btn sm" style="margin-left:auto" @click="load()">刷新</button>
    </div>

    <div class="row">
      <!-- 左列：发起 + 协同单列表 -->
      <div class="col col-1">
        <div class="panel">
          <div class="panel-head">发起处置协同</div>
          <div class="panel-body" style="display:flex;flex-direction:column;gap:10px">
            <div class="field">
              <label>预报运行（同一运行只建一张协同单，重复发起自动归并）</label>
              <select v-model="runId">
                <option v-for="o in runOptions" :key="o.id" :value="o.id">{{ o.label }}</option>
              </select>
            </div>
            <div class="field">
              <label>调度员</label>
              <input type="text" v-model="dispatcher" placeholder="发起调度员姓名">
            </div>
            <button class="btn primary" :disabled="busy || !runId" @click="initiate">＋ 发起处置协同</button>
          </div>
        </div>

        <div class="panel">
          <div class="panel-head">协同单列表 <span class="tag">{{ tasks.length }} 张</span></div>
          <div class="panel-body nopad" style="max-height:430px;overflow-y:auto">
            <table class="grid">
              <thead><tr><th>协同单</th><th>工况</th><th>状态</th><th>发起时间</th></tr></thead>
              <tbody>
                <tr v-for="t in tasks" :key="t.id" @click="selectedId = t.id"
                    :style="{cursor:'pointer', background: t.id===selectedId ? 'rgba(55,182,255,.08)' : ''}">
                  <td style="font-size:12.5px">{{ t.title }}<br><span style="color:#7d95b4">{{ t.run ? t.run.event_name : '' }}</span></td>
                  <td style="font-size:12px">{{ t.run ? modeName(t.run.mode) : '—' }}</td>
                  <td><span class="badge" :class="stColor(t.status)">{{ stName(t.status) }}</span></td>
                  <td style="font-size:11.5px;color:#7d95b4">{{ fmt.time(t.created_at) }}</td>
                </tr>
                <tr v-if="!tasks.length"><td colspan="4" style="text-align:center;color:#7d95b4;padding:26px">暂无协同单，选择预报运行发起</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <!-- 右列：闭环详情 -->
      <div class="col col-2">
        <div v-if="!selected" class="panel" style="padding:60px;text-align:center;color:#7d95b4">
          <div style="font-size:38px;margin-bottom:12px">🤝</div>
          <div style="font-size:15px">选择左侧协同单查看处置闭环，或基于预报运行发起新协同</div>
        </div>

        <template v-else>
          <!-- 四态进度 -->
          <div class="panel">
            <div class="panel-head">{{ selected.title }}
              <span class="tag">{{ selected.run ? selected.run.event_name + ' · ' + modeName(selected.run.mode) : '' }}</span>
            </div>
            <div class="panel-body">
              <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px">
                <div v-for="(s, i) in steps" :key="s.id" class="scene-card" :class="{active: i <= stepIdx}">
                  <div class="t">{{ i + 1 }}. {{ s.t }} <span class="badge" :class="i <= stepIdx ? stColor(s.id) : 'gray'">{{ i <= stepIdx ? '已达成' : '待办' }}</span></div>
                  <div class="d">{{ s.role }}<span v-if="s.who"> · {{ s.who }}</span></div>
                  <div class="o" style="font-size:11.5px;color:#7d95b4">{{ s.at ? fmt.time(s.at) : '—' }}</div>
                </div>
              </div>
              <div v-if="selected.review_note" style="margin-top:10px;font-size:12px;color:#f5b83d">审核意见：{{ selected.review_note }}</div>
            </div>
          </div>

          <!-- 方案快照 + 操作 -->
          <div class="row">
            <div class="col col-1">
              <div class="panel">
                <div class="panel-head">审核后调度方案快照 <span class="tag">执行时按此回写</span></div>
                <div class="panel-body nopad">
                  <table class="grid">
                    <thead><tr><th>水库</th><th>峰值出流</th><th>末态水位</th><th>末态库容</th></tr></thead>
                    <tbody>
                      <tr v-for="r in (selected.plan.reservoirs || [])" :key="r.reservoir_id">
                        <td>{{ r.name }} <span class="badge gray" v-if="r.level_source==='balance'" title="历史运行缺少水位过程线，按水量平衡推算">推算</span></td>
                        <td class="num mono">{{ fmt.num(r.peak_outflow, 0) }} m³/s</td>
                        <td class="num mono">{{ fmt.num(r.end_level, 2) }} m</td>
                        <td class="num mono">{{ fmt.num(r.end_storage, 0) }} 万m³</td>
                      </tr>
                      <tr v-if="!(selected.plan.reservoirs || []).length"><td colspan="4" style="text-align:center;color:#7d95b4;padding:18px">本运行无水库调度内容</td></tr>
                    </tbody>
                  </table>
                  <div style="padding:10px 14px;font-size:12px;color:#7d95b4;border-top:1px solid var(--line-soft)">
                    关联预警 {{ selected.plan.warning_count || 0 }} 条（最高{{ fmt.lvName(selected.plan.max_warning_level) || '—' }}）·
                    转移 {{ selected.plan.evacuation_count || 0 }} 处 / {{ selected.plan.evacuation_people || 0 }} 人
                  </div>
                </div>
              </div>
            </div>

            <div class="col col-1">
              <div class="panel">
                <div class="panel-head">闭环操作</div>
                <div class="panel-body" style="display:flex;flex-direction:column;gap:10px">
                  <template v-if="selected.status === 'initiated'">
                    <div class="field"><label>预警值守（审核人）</label>
                      <input type="text" v-model="dutyOfficer" placeholder="审核人姓名"></div>
                    <div class="field"><label>审核意见</label>
                      <input type="text" v-model="reviewNote" placeholder="可填审核意见"></div>
                    <div style="display:flex;gap:8px">
                      <button class="btn primary" :disabled="busy" @click="review(true)">✔ 审核通过</button>
                      <button class="btn danger" :disabled="busy" @click="review(false)">✖ 退回修订</button>
                    </div>
                  </template>
                  <template v-else-if="selected.status === 'approved'">
                    <div class="field"><label>转移负责人（执行人）</label>
                      <input type="text" v-model="evacLead" placeholder="执行人姓名"></div>
                    <button class="btn primary" :disabled="busy" @click="execute">⚙ 执行方案回写</button>
                    <div style="font-size:11.5px;color:#7d95b4">回写内容：水库末态水位/库容 → 水库工况；本运行转移台账 pending → 转移中</div>
                  </template>
                  <template v-else-if="selected.status === 'executed'">
                    <div style="font-size:12.5px;color:#7d95b4">方案已回写，转移进行中。确认群众安置到位后完成闭环：</div>
                    <button class="btn primary" :disabled="busy" @click="complete">✔ 完成闭环（安置确认 + 预警销号）</button>
                  </template>
                  <template v-else>
                    <div style="font-size:13px;color:#2fd07f">✔ 处置闭环已完成，本运行预警已全部销号。</div>
                  </template>
                </div>
              </div>

              <!-- 回写结果 -->
              <div class="panel" v-if="hasWb">
                <div class="panel-head">方案回写结果</div>
                <div class="panel-body" style="display:flex;flex-direction:column;gap:8px;font-size:12.5px">
                  <div v-for="r in (wb.reservoirs || [])" :key="r.reservoir_id">
                    <b>{{ r.name }}</b>：水位 {{ fmt.num(r.old_level,2) }} → <span style="color:#2fd07f">{{ fmt.num(r.new_level,2) }} m</span>，
                    库容 {{ fmt.num(r.old_storage,0) }} → <span style="color:#2fd07f">{{ fmt.num(r.new_storage,0) }} 万m³</span>
                  </div>
                  <div v-if="wb.evacuations_moving != null">转移台账：{{ wb.evacuations_moving }} 处启动转移</div>
                  <div v-if="wb.evacuations_safe != null">安置确认：{{ wb.evacuations_safe }} 处已安全 · 预警销号 {{ wb.warnings_cleared }} 条</div>
                </div>
              </div>
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>`,
};
