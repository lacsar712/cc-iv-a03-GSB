<template>
  <main>
    <header class="topbar">
      <h1>光伏组串IV扫描台</h1>
      <nav v-if="session" class="tabs">
        <button :class="{ active: tab === 'submit' }" @click="tab = 'submit'">扫描提交</button>
        <button :class="{ active: tab === 'seats' }" @click="tab = 'seats'">端子座位</button>
      </nav>
    </header>

    <div v-if="!session">
      <p class="sub">扫描员提交开路电压、短路电流与填充因子；通知通道叫醒工人出结论。登录框已预填可写账号 scanner / scan123456。</p>
      <section>
        <label>用户名</label><input v-model="loginUser" autocomplete="off" />
        <label>密码</label><input type="password" v-model="loginPass" autocomplete="off" />
        <button :disabled="loading" @click="login">登录</button>
        <p v-if="error" class="err">{{ error }}</p>
      </section>
    </div>

    <div v-else>
      <p class="sub">已登录：{{ session.username }}（{{ isWriter ? "可提交" : "观察员：可翻占位与痕迹，只读" }}）</p>
      <section class="line">
        <button class="secondary" @click="logout">退出</button>
        <button class="secondary" @click="refreshAll">刷新</button>
      </section>

      <!-- ============ 扫描提交页 ============ -->
      <template v-if="tab === 'submit'">
        <section v-if="isWriter">
          <label>组串编号</label><input v-model="stringCode" placeholder="例如 阵列C-串05" />
          <div class="formrow">
            <div class="grow">
              <label>开路电压 V</label><input type="number" step="0.1" v-model="voc" />
            </div>
            <div class="grow">
              <label>短路电流 A</label><input type="number" step="0.1" v-model="isc" />
            </div>
            <div class="grow">
              <label>填充因子</label><input type="number" step="0.01" v-model="ff" />
            </div>
          </div>

          <label>汇流箱</label>
          <select v-model="boxCode" @change="seatNo = null">
            <option value="" disabled>请选择汇流箱</option>
            <option v-for="b in boxes" :key="b.box_code" :value="b.box_code">
              {{ b.box_code }}（已占 {{ b.occupied }}/{{ b.seat_capacity }}）
            </option>
          </select>

          <label>端子座位（必须点一个空席）</label>
          <div v-if="selectedBox" class="seats">
            <button
              v-for="n in selectedBox.seat_capacity"
              :key="n"
              type="button"
              class="seat"
              :class="{ taken: seatOwner(n), picked: seatNo === n }"
              :disabled="!!seatOwner(n)"
              :title="seatOwner(n) ? '已占：' + seatOwner(n) : '第 ' + n + ' 席'"
              @click="seatNo = n"
            >
              <span class="seatno">{{ n }}</span>
              <span class="seatstate">{{ seatOwner(n) ? "占" : "空" }}</span>
            </button>
          </div>
          <p v-else class="hint">还没有可投的汇流箱，先到「端子座位」页设置座位上限。</p>
          <p v-if="selectedBox && selectedFull" class="err">
            {{ selectedBox.box_code }}箱已满员（{{ selectedBox.seat_capacity }} 席全占），不许再塞新扫描。
          </p>

          <button :disabled="loading" @click="submit">占座并提交扫描</button>
          <p v-if="error" class="err">{{ error }}</p>
        </section>

        <section>
          <table>
            <thead>
              <tr><th>编号</th><th>组串</th><th>汇流箱/座位</th><th>Voc</th><th>Isc</th><th>FF</th><th>状态</th><th>结论</th></tr>
            </thead>
            <tbody>
              <tr v-for="row in logs" :key="row.id">
                <td>{{ row.id }}</td>
                <td>{{ row.string_code }}</td>
                <td>{{ row.box_code ? row.box_code + " / 第" + row.seat_no + "席" : "—" }}</td>
                <td>{{ row.voc_v }}</td>
                <td>{{ row.isc_a }}</td>
                <td>{{ row.fill_factor }}</td>
                <td><span class="tag" :class="row.status === 'pending' ? 'pending' : 'ok'">{{ row.status === 'pending' ? '待处理' : '已完成' }}</span></td>
                <td><span v-if="row.verdict" class="tag" :class="row.verdict === '合格' ? 'ok' : 'bad'">{{ row.verdict }}</span><span v-else>—</span></td>
              </tr>
            </tbody>
          </table>
        </section>
      </template>

      <!-- ============ 端子座位专页：三栏 ============ -->
      <template v-else>
        <div class="panes">
          <!-- 左格：各箱已占 -->
          <section class="pane">
            <h2>各箱已占</h2>
            <div v-if="!boxes.length || !totalOccupied">
              <p class="empty">还没有占位</p>
            </div>
            <div v-for="b in boxes" :key="b.box_code" class="boxcard">
              <div class="boxhead">
                <strong>{{ b.box_code }}</strong>
                <span :class="isFull(b) ? 'full' : 'free'">
                  {{ b.occupied }}/{{ b.seat_capacity }} 席{{ isFull(b) ? "（满员）" : "" }}
                </span>
              </div>
              <p v-if="!b.occupations.length" class="empty small">还没有占位</p>
              <ul class="occlist">
                <li v-for="o in b.occupations" :key="o.scan_id">
                  第{{ o.seat_no }}席 · {{ o.string_code }}
                  <span class="by">（{{ o.created_by }}）</span>
                </li>
              </ul>
            </div>
          </section>

          <!-- 中格：座位上限 -->
          <section class="pane">
            <h2>座位上限</h2>
            <p class="hint small">上限随单冻结：事后改上限动不了旧单已占的座位。</p>
            <table v-if="boxes.length" class="cap-table">
              <thead><tr><th>汇流箱</th><th>上限</th><th v-if="isWriter"></th></tr></thead>
              <tbody>
                <tr v-for="b in boxes" :key="b.box_code">
                  <td>{{ b.box_code }}</td>
                  <td v-if="isWriter">
                    <input type="number" min="1" class="capinput" v-model.number="capDrafts[b.box_code]" />
                  </td>
                  <td v-else>{{ b.seat_capacity }}</td>
                  <td v-if="isWriter">
                    <button class="mini" :disabled="capDrafts[b.box_code] === b.seat_capacity || capDrafts[b.box_code] < 1"
                            @click="saveCapacity(b.box_code, capDrafts[b.box_code])">改上限</button>
                  </td>
                </tr>
              </tbody>
            </table>
            <p v-else class="empty small">还没有汇流箱</p>

            <div v-if="isWriter" class="addbox">
              <h3>新增汇流箱</h3>
              <label>箱号</label><input v-model="newBoxCode" placeholder="例如 甲箱" />
              <label>座位上限</label><input type="number" min="1" v-model.number="newCapacity" />
              <button class="mini" @click="addBox">设上限</button>
              <p v-if="boxError" class="err">{{ boxError }}</p>
            </div>
            <p v-else class="hint small">观察员不能修改上限。</p>
          </section>

          <!-- 右格：挡回痕迹 -->
          <section class="pane">
            <h2>挡回痕迹</h2>
            <p v-if="!rejections.length" class="empty">
              {{ totalOccupied === 0 ? "还没有占位" : "暂无挡回记录" }}
            </p>
            <ul v-else class="rejlist">
              <li v-for="r in rejections" :key="r.id">
                <div class="rejhead">
                  <strong>{{ r.box_code || "未选箱" }}{{ r.seat_no ? " · 第" + r.seat_no + "席" : "" }}</strong>
                  <span class="by">{{ r.created_by }} · {{ fmt(r.created_at) }}</span>
                </div>
                <div>{{ r.string_code }}（Voc {{ r.voc_v }} / Isc {{ r.isc_a }} / FF {{ r.fill_factor }}）</div>
                <div class="rejreason">{{ r.reason }}</div>
              </li>
            </ul>
          </section>
        </div>
      </template>
    </div>
  </main>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from "vue";
const session = ref(null);
const logs = ref([]);
const boxes = ref([]);
const rejections = ref([]);
const loginUser = ref("scanner");
const loginPass = ref("scan123456");
const stringCode = ref("");
const voc = ref("");
const isc = ref("");
const ff = ref("");
const boxCode = ref("");
const seatNo = ref(null);
const newBoxCode = ref("");
const newCapacity = ref(1);
const capDrafts = reactive({});
const error = ref("");
const boxError = ref("");
const loading = ref(false);
const tab = ref("submit");
let timer;
const isWriter = computed(() => session.value?.role === "writer");
const selectedBox = computed(() => boxes.value.find((b) => b.box_code === boxCode.value) || null);
const totalOccupied = computed(() => boxes.value.reduce((n, b) => n + b.occupied, 0));

// 满员 = 1..上限 每一席都已占；被上限冻在高位的旧单不计入空位判断
function isFull(b) {
  const seats = new Set(b.occupations.map((o) => o.seat_no));
  for (let n = 1; n <= b.seat_capacity; n++) {
    if (!seats.has(n)) return false;
  }
  return true;
}
const selectedFull = computed(() => (selectedBox.value ? isFull(selectedBox.value) : false));

function headers() {
  return session.value ? { Authorization: "Bearer " + session.value.token } : {};
}
function fmt(ts) {
  return ts ? new Date(ts).toLocaleString("zh-CN", { hour12: false }) : "";
}
function seatOwner(n) {
  const occ = selectedBox.value?.occupations.find((o) => o.seat_no === n);
  return occ ? occ.string_code : null;
}

async function api(path, options = {}) {
  return fetch("/api" + path, { headers: { ...(options.body ? { "Content-Type": "application/json" } : {}), ...headers() }, ...options });
}

async function refreshAll() {
  if (!session.value) return;
  const [lr, br, rr] = await Promise.all([
    fetch("/api/logs", { headers: headers() }),
    fetch("/api/boxes", { headers: headers() }),
    fetch("/api/rejections", { headers: headers() }),
  ]);
  if (lr.status === 401 || br.status === 401 || rr.status === 401) { logout(); return; }
  if (lr.ok) logs.value = await lr.json();
  if (br.ok) {
    const fresh = await br.json();
    for (const b of fresh) {
      if (!(b.box_code in capDrafts)) capDrafts[b.box_code] = b.seat_capacity;
    }
    boxes.value = fresh;
  }
  if (rr.ok) rejections.value = await rr.json();
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
  logs.value = [];
  boxes.value = [];
  rejections.value = [];
  localStorage.removeItem("pv_session");
}

async function submit() {
  error.value = "";
  if (!boxCode.value) { error.value = "请选择汇流箱"; return; }
  if (seatNo.value === null) { error.value = "必须点选一个端子座位"; return; }
  if (selectedFull.value) { error.value = `${boxCode.value}箱已满员，整笔无法提交`; return; }
  loading.value = true;
  try {
    const res = await api("/logs", {
      method: "POST",
      body: JSON.stringify({
        string_code: stringCode.value,
        box_code: boxCode.value,
        seat_no: seatNo.value,
        voc_v: Number(voc.value),
        isc_a: Number(isc.value),
        fill_factor: Number(ff.value),
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      // 满员、漏点座位、对撞落败：后端整笔退回，这里原样亮出原因
      error.value = data.detail || "提交失败，整笔退回";
      seatNo.value = null;
      await refreshAll();
      return;
    }
    stringCode.value = voc.value = isc.value = ff.value = "";
    seatNo.value = null;
    await refreshAll();
  } catch { error.value = "提交时网络异常"; }
  finally { loading.value = false; }
}

async function saveCapacity(code, cap) {
  boxError.value = "";
  if (!Number.isInteger(cap) || cap < 1) { boxError.value = "座位上限必须是正整数"; return; }
  const res = await api("/boxes", { method: "POST", body: JSON.stringify({ box_code: code, seat_capacity: cap }) });
  const data = await res.json();
  if (!res.ok) { boxError.value = data.detail || "改上限失败"; return; }
  await refreshAll();
}

async function addBox() {
  boxError.value = "";
  const code = newBoxCode.value.trim();
  if (!code) { boxError.value = "箱号不能为空"; return; }
  const res = await api("/boxes", {
    method: "POST",
    body: JSON.stringify({ box_code: code, seat_capacity: Number(newCapacity.value) }),
  });
  const data = await res.json();
  if (!res.ok) { boxError.value = data.detail || "设置失败"; return; }
  newBoxCode.value = "";
  newCapacity.value = 1;
  await refreshAll();
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
main { max-width: 1180px; margin: 0 auto; padding: 1.5rem; }
.topbar { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 0.75rem; }
h1 { color: #86efac; margin: 0; font-size: 1.4rem; }
.tabs button { background: #14532d; border: 1px solid #166534; color: #a7f3d0; border-radius: 6px 6px 0 0; margin-left: 0.35rem; }
.tabs button.active { background: #16a34a; color: #fff; }
.sub { color: #a7f3d0; margin: 0.6rem 0 1.25rem; }
section { background: #14532d; border: 1px solid #166534; border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 1rem; }
.line { display: flex; gap: 0.4rem; }
h2 { margin: 0 0 0.75rem; font-size: 1.05rem; color: #86efac; }
h3 { font-size: 0.95rem; margin: 0.9rem 0 0.4rem; color: #bbf7d0; }
label { display: block; font-size: 0.85rem; margin-bottom: 0.25rem; }
input, select { width: 100%; box-sizing: border-box; padding: 0.5rem 0.65rem; border-radius: 6px; border: 1px solid #4ade80; background: #022c22; color: #ecfdf5; margin-bottom: 0.75rem; }
.formrow { display: flex; gap: 0.75rem; }
.grow { flex: 1; }
button { cursor: pointer; padding: 0.5rem 1rem; border: none; border-radius: 6px; background: #16a34a; color: #fff; font-weight: 600; margin-right: 0.4rem; }
button.secondary { background: #365314; }
button.mini { padding: 0.3rem 0.7rem; font-size: 0.82rem; }
button:disabled { cursor: not-allowed; opacity: 0.55; }
.err { color: #fecaca; }
.hint { color: #a7f3d0; font-size: 0.88rem; }
.small { font-size: 0.8rem; }
.empty { color: #86efac; opacity: 0.75; padding: 0.6rem 0; text-align: center; border: 1px dashed #166534; border-radius: 6px; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
th, td { text-align: left; padding: 0.45rem; border-bottom: 1px solid #166534; }
.tag { padding: 0.1rem 0.4rem; border-radius: 4px; font-size: 0.8rem; }
.ok { background: #14532d; color: #bbf7d0; }
.bad { background: #7f1d1d; color: #fecaca; }
.pending { background: #854d0e; color: #fde68a; }

.seats { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-bottom: 0.9rem; }
.seat { width: 4.2rem; height: 3.1rem; margin: 0; border-radius: 8px; display: flex; flex-direction: column; align-items: center; justify-content: center; background: #022c22; border: 2px solid #4ade80; color: #bbf7d0; }
.seat .seatno { font-weight: 700; }
.seat .seatstate { font-size: 0.72rem; opacity: 0.8; }
.seat.picked { background: #16a34a; color: #fff; box-shadow: 0 0 0 3px rgba(134,239,172,0.45); }
.seat.taken { background: #7f1d1d; border-color: #b91c1c; color: #fecaca; }

.panes { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 1rem; align-items: start; }
.pane { margin-bottom: 0; min-height: 12rem; }
.boxcard { border: 1px solid #166534; border-radius: 6px; padding: 0.6rem 0.75rem; margin-bottom: 0.6rem; background: #052e16; }
.boxhead { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.35rem; }
.full { color: #fecaca; font-weight: 700; }
.free { color: #bbf7d0; }
.occlist, .rejlist { list-style: none; padding: 0; margin: 0; }
.occlist li { font-size: 0.86rem; padding: 0.25rem 0; border-bottom: 1px dotted #166534; }
.by { color: #a7f3d0; font-size: 0.78rem; }
.cap-table input { margin: 0; width: 5rem; padding: 0.3rem 0.4rem; }
.addbox { margin-top: 0.9rem; border-top: 1px solid #166534; padding-top: 0.4rem; }
.rejlist li { border: 1px solid #7f1d1d; border-radius: 6px; padding: 0.55rem 0.7rem; margin-bottom: 0.55rem; background: #450a0a; font-size: 0.83rem; }
.rejhead { display: flex; justify-content: space-between; gap: 0.5rem; margin-bottom: 0.2rem; }
.rejreason { color: #fde68a; margin-top: 0.2rem; }
@media (max-width: 900px) { .panes { grid-template-columns: 1fr; } }
</style>
