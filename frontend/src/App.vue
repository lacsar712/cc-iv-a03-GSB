<template>
  <div>
    <header class="topbar">
      <span class="brand">光伏组串IV扫描台</span>
      <nav v-if="session">
        <button :class="{ active: view === 'queue' }" @click="view = 'queue'">扫描入队</button>
        <button :class="{ active: view === 'seats' }" @click="goSeats">端子座位</button>
      </nav>
      <span v-if="session" class="who">{{ session.username }}（{{ isWriter ? "扫描员" : "观察员" }}）</span>
    </header>

    <main>
      <!-- 未登录 -->
      <div v-if="!session">
        <p class="sub">扫描员提交开路电压、短路电流与填充因子；通知通道叫醒工人出结论。登录框已预填可写账号 scanner / scan123456。</p>
        <section>
          <label>用户名</label><input v-model="loginUser" autocomplete="off" />
          <label>密码</label><input type="password" v-model="loginPass" autocomplete="off" />
          <button :disabled="loading" @click="login">登录</button>
          <p v-if="error" class="err">{{ error }}</p>
        </section>
      </div>

      <!-- 已登录 -->
      <template v-else>
        <section class="toolbar">
          <button class="secondary" @click="refreshAll">刷新</button>
          <button class="secondary" @click="logout">退出</button>
          <span v-if="view === 'seats'" class="hint">观察员可翻阅占位与挡回痕迹；座位上限仅扫描员可改。</span>
        </section>

        <!-- 扫描入队页 -->
        <div v-if="view === 'queue'">
          <section v-if="isWriter">
            <h2>交扫描（先点端子座位）</h2>
            <label>汇流箱端子排</label>
            <select v-model="boxId">
              <option :value="null" disabled>— 请点选汇流箱座位 —</option>
              <option v-for="b in boxes" :key="b.id" :value="b.id">
                {{ b.name }}（已占 {{ b.occupied }}/{{ b.seat_limit }}）{{ b.occupied >= b.seat_limit ? " · 已满" : "" }}
              </option>
            </select>
            <p v-if="selectedBox && selectedBox.occupied >= selectedBox.seat_limit" class="warn">
              {{ selectedBox.name }} 座位已满，此单提交将被整笔退回并留挡回痕迹。
            </p>
            <label>组串编号</label><input v-model="stringCode" placeholder="例如 阵列C-串05" />
            <label>开路电压 V</label><input type="number" step="0.1" v-model="voc" />
            <label>短路电流 A</label><input type="number" step="0.1" v-model="isc" />
            <label>填充因子</label><input type="number" step="0.01" v-model="ff" />
            <button :disabled="loading || boxId === null" @click="submit">提交扫描并占座</button>
            <p v-if="error" class="err">{{ error }}</p>
            <p v-if="okMsg" class="okmsg">{{ okMsg }}</p>
          </section>
          <p v-else class="sub">观察员只读，可到「端子座位」专页翻阅占位与挡回痕迹，不能改上限也不能入队。</p>

          <section>
            <h2>扫描单</h2>
            <table>
              <thead>
                <tr><th>编号</th><th>汇流箱</th><th>座号</th><th>组串</th><th>Voc</th><th>Isc</th><th>FF</th><th>状态</th><th>结论</th></tr>
              </thead>
              <tbody>
                <tr v-for="row in logs" :key="row.id">
                  <td>{{ row.id }}</td>
                  <td>{{ row.box_name || "—" }}</td>
                  <td>{{ seatOf(row) }}</td>
                  <td>{{ row.string_code }}</td>
                  <td>{{ row.voc_v }}</td>
                  <td>{{ row.isc_a }}</td>
                  <td>{{ row.fill_factor }}</td>
                  <td><span class="tag" :class="row.status === 'pending' ? 'pending' : 'ok'">{{ row.status === 'pending' ? '待处理' : '已完成' }}</span></td>
                  <td><span v-if="row.verdict" class="tag" :class="row.verdict === '合格' ? 'ok' : 'bad'">{{ row.verdict }}</span><span v-else>—</span></td>
                </tr>
                <tr v-if="!logs.length"><td colspan="9" class="empty">还没有扫描单</td></tr>
              </tbody>
            </table>
          </section>
        </div>

        <!-- 端子座位专页：左已占 / 中上限 / 右挡回痕迹 -->
        <div v-if="view === 'seats'" class="grid3">
          <!-- 左格：各箱已占 -->
          <section>
            <h2>各箱已占</h2>
            <div v-for="b in boxes" :key="b.id" class="boxcard">
              <div class="boxhead">
                <span class="boxname">{{ b.name }}</span>
                <span class="count">{{ b.occupied }}/{{ b.seat_limit }}</span>
              </div>
              <ul v-if="b.holds.length" class="seats">
                <li v-for="h in b.holds" :key="h.hold_id">
                  <span class="seatno">{{ h.seat_no }}号座</span>
                  <span class="seatstr">{{ h.string_code }}</span>
                  <span class="tag" :class="h.status === 'pending' ? 'pending' : 'ok'">{{ h.status === 'pending' ? '待处理' : '已完成' }}</span>
                </li>
              </ul>
              <p v-else class="empty">还没有占位</p>
            </div>
          </section>

          <!-- 中格：座位上限 -->
          <section>
            <h2>座位上限</h2>
            <div v-for="b in boxes" :key="b.id" class="limitrow">
              <span class="boxname">{{ b.name }}</span>
              <template v-if="isWriter">
                <input type="number" min="0" step="1" v-model.number="limitDraft[b.id]" />
                <button class="mini" :disabled="limitDraft[b.id] === b.seat_limit" @click="saveLimit(b)">保存</button>
              </template>
              <span v-else class="readonly">{{ b.seat_limit }} 座（只读）</span>
            </div>
            <p class="hint">改上限只管新单：旧单占位随单冻住，不受影响。</p>
            <p v-if="limitError" class="err">{{ limitError }}</p>
          </section>

          <!-- 右格：挡回痕迹 -->
          <section>
            <h2>挡回痕迹</h2>
            <table v-if="rejections.length">
              <thead><tr><th>时间</th><th>箱</th><th>组串</th><th>说明</th></tr></thead>
              <tbody>
                <tr v-for="r in rejections" :key="r.id">
                  <td class="nowrap">{{ fmt(r.rejected_at) }}</td>
                  <td>{{ r.box_name }}</td>
                  <td>{{ r.string_code }}</td>
                  <td>{{ r.reason }}</td>
                </tr>
              </tbody>
            </table>
            <p v-else class="empty">还没有挡回痕迹</p>
          </section>
        </div>
      </template>
    </main>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from "vue";
const session = ref(null);
const view = ref("queue");
const logs = ref([]);
const boxes = ref([]);
const rejections = ref([]);
const loginUser = ref("scanner");
const loginPass = ref("scan123456");
const boxId = ref(null);
const stringCode = ref("");
const voc = ref("");
const isc = ref("");
const ff = ref("");
const error = ref("");
const okMsg = ref("");
const limitError = ref("");
const loading = ref(false);
const limitDraft = reactive({});
let timer;
const isWriter = computed(() => session.value?.role === "writer");
const selectedBox = computed(() => boxes.value.find((b) => b.id === boxId.value) || null);

function headers() {
  return session.value ? { Authorization: "Bearer " + session.value.token } : {};
}
function fmt(ts) {
  return ts ? new Date(ts).toLocaleString() : "—";
}
function seatOf(row) {
  const b = boxes.value.find((x) => x.id === row.box_id);
  const h = b && b.holds.find((x) => x.scan_id === row.id);
  return h ? h.seat_no + "号" : "—";
}

async function getJSON(path) {
  const res = await fetch(path, { headers: headers() });
  if (res.status === 401) { logout(); throw new Error("401"); }
  if (!res.ok) throw new Error(String(res.status));
  return res.json();
}
async function refreshAll() {
  if (!session.value) return;
  try {
    const [l, b, r] = await Promise.all([
      getJSON("/api/logs"), getJSON("/api/boxes"), getJSON("/api/rejections"),
    ]);
    logs.value = l;
    boxes.value = b;
    rejections.value = r;
    for (const box of b) if (!(box.id in limitDraft)) limitDraft[box.id] = box.seat_limit;
  } catch { /* 401 已处理，其余等下一拍 */ }
}
function goSeats() {
  view.value = "seats";
  refreshAll();
}

async function login() {
  error.value = "";
  loading.value = true;
  try {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: loginUser.value, password: loginPass.value }),
    });
    const data = await res.json();
    if (!res.ok) { error.value = data.detail || "登录失败"; return; }
    session.value = { token: data.access_token, username: data.username, role: data.role };
    localStorage.setItem("pv_session", JSON.stringify(session.value));
    await refreshAll();
    timer = setInterval(refreshAll, 2000);
  } catch { error.value = "无法连接接口"; }
  finally { loading.value = false; }
}
function logout() {
  if (timer) clearInterval(timer);
  session.value = null;
  logs.value = []; boxes.value = []; rejections.value = [];
  localStorage.removeItem("pv_session");
}

async function submit() {
  error.value = ""; okMsg.value = "";
  if (boxId.value === null) { error.value = "必须先点选汇流箱座位"; return; }
  loading.value = true;
  try {
    const res = await fetch("/api/logs", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...headers() },
      body: JSON.stringify({
        box_id: boxId.value,
        string_code: stringCode.value,
        voc_v: Number(voc.value),
        isc_a: Number(isc.value),
        fill_factor: Number(ff.value),
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      // 满员 409：整笔退回，刷新痕迹
      error.value = data.detail || "提交失败";
      await refreshAll();
      return;
    }
    okMsg.value = `已收下：${data.box_name} ${data.seat_no}号座占位成功`;
    stringCode.value = voc.value = isc.value = ff.value = "";
    await refreshAll();
  } catch { error.value = "提交时网络异常"; }
  finally { loading.value = false; }
}

async function saveLimit(b) {
  limitError.value = "";
  const val = limitDraft[b.id];
  if (!Number.isInteger(val) || val < 0) { limitError.value = "座位上限必须是不小于 0 的整数"; return; }
  try {
    const res = await fetch(`/api/boxes/${b.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json", ...headers() },
      body: JSON.stringify({ seat_limit: val }),
    });
    const data = await res.json();
    if (!res.ok) { limitError.value = data.detail || "保存失败"; return; }
    await refreshAll();
  } catch { limitError.value = "保存时网络异常"; }
}

onMounted(() => {
  const raw = localStorage.getItem("pv_session");
  if (raw) {
    try {
      session.value = JSON.parse(raw);
      refreshAll();
      timer = setInterval(refreshAll, 2000);
    } catch { localStorage.removeItem("pv_session"); }
  }
});
onUnmounted(() => { if (timer) clearInterval(timer); });
</script>

<style>
body { margin: 0; font-family: "Segoe UI", system-ui, sans-serif; background: #052e16; color: #ecfdf5; }
.topbar { display: flex; align-items: center; gap: 1rem; background: #022c22; border-bottom: 1px solid #166534; padding: 0.7rem 1.5rem; }
.brand { font-weight: 700; color: #86efac; }
.topbar nav { display: flex; gap: 0.5rem; }
.topbar nav button { margin: 0; background: #14532d; }
.topbar nav button.active { background: #16a34a; }
.who { margin-left: auto; color: #a7f3d0; font-size: 0.9rem; }
main { max-width: 1100px; margin: 0 auto; padding: 1.5rem; }
h1 { color: #86efac; margin: 0 0 0.25rem; }
h2 { margin: 0 0 0.75rem; font-size: 1.05rem; color: #bbf7d0; }
.sub { color: #a7f3d0; margin-bottom: 1.25rem; }
.toolbar { display: flex; align-items: center; gap: 0.5rem; }
.toolbar .hint { color: #a7f3d0; font-size: 0.85rem; }
section { background: #14532d; border: 1px solid #166534; border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 1rem; }
label { display: block; font-size: 0.85rem; margin-bottom: 0.25rem; }
input, select { width: 100%; box-sizing: border-box; padding: 0.5rem 0.65rem; border-radius: 6px; border: 1px solid #4ade80; background: #022c22; color: #ecfdf5; margin-bottom: 0.75rem; }
button { cursor: pointer; padding: 0.5rem 1rem; border: none; border-radius: 6px; background: #16a34a; color: #fff; font-weight: 600; margin-right: 0.4rem; }
button:disabled { opacity: 0.5; cursor: not-allowed; }
button.secondary { background: #365314; }
button.mini { padding: 0.3rem 0.7rem; font-size: 0.85rem; }
.err { color: #fecaca; }
.warn { color: #fde68a; background: #854d0e33; border: 1px solid #854d0e; border-radius: 6px; padding: 0.5rem 0.75rem; }
.okmsg { color: #bbf7d0; }
.hint { color: #a7f3d0; font-size: 0.8rem; }
.empty { color: #86efac99; text-align: center; padding: 0.75rem; font-size: 0.9rem; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
th, td { text-align: left; padding: 0.45rem; border-bottom: 1px solid #166534; vertical-align: top; }
.nowrap { white-space: nowrap; }
.tag { padding: 0.1rem 0.4rem; border-radius: 4px; font-size: 0.8rem; }
.ok { background: #14532d; color: #bbf7d0; }
.bad { background: #7f1d1d; color: #fecaca; }
.pending { background: #854d0e; color: #fde68a; }
.grid3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 1rem; align-items: start; }
@media (max-width: 900px) { .grid3 { grid-template-columns: 1fr; } }
.boxcard { background: #052e16; border: 1px solid #166534; border-radius: 6px; padding: 0.6rem 0.75rem; margin-bottom: 0.75rem; }
.boxhead { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.4rem; }
.boxname { font-weight: 600; color: #d1fae5; }
.count { color: #86efac; font-size: 0.85rem; }
.seats { list-style: none; margin: 0; padding: 0; }
.seats li { display: flex; align-items: center; gap: 0.5rem; padding: 0.3rem 0; border-bottom: 1px dashed #166534; font-size: 0.85rem; }
.seatno { color: #fde68a; min-width: 3rem; }
.seatstr { color: #ecfdf5; }
.limitrow { display: flex; align-items: center; gap: 0.6rem; margin-bottom: 0.7rem; }
.limitrow .boxname { min-width: 3rem; }
.limitrow input { width: 5.5rem; margin: 0; }
.readonly { color: #a7f3d0; }
</style>
