const API_BASE = "http://localhost:8000";
const URL_ESTADO = `${API_BASE}/api/estado`;
const URL_DETECTAR = `${API_BASE}/api/detectar`;
const URL_SIMULAR = `${API_BASE}/api/simular`;

const COLOR_CAJA = "#00b4d8";

let mediaStream = null;
let camaraAbierta = false;

let yoloActivo = false;
let captureTimer = null;
let drawRAF = null;
let captureCanvas = null;
let currentDetecciones = {};
let targetDetecciones = {};
const FADE_FRAMES = 12;

let lineaYRatio = 0.6;
let mostrarLinea = true;
let arrastrandoLinea = false;

let autoActivo = false;

const NOMBRES_AUTO = ["Ana", "Luis", "Maria", "Juan", "Sofia", "Carlos", "Elena", "Pedro", "Laura", "Diego"];

// =====================
// POLLING
// =====================

async function consultarServidor() {
  try {
    const [resEstado, resAlertas] = await Promise.all([
      fetch(URL_ESTADO + "?_=" + Date.now()),
      fetch(`http://localhost:8000/api/alertas-fraude?_=${Date.now()}`),
    ]);
    if (!resEstado.ok) throw new Error();
    const d = await resEstado.json();

    document.getElementById("pasajeros-cuenta").textContent = d.pasajeros_a_bordo;
    document.getElementById("caja-total").textContent = `C$ ${d.total_caja_colectada.toFixed(2)}`;
    document.getElementById("parada-actual").textContent = d.parada_actual;

    const cargaEl = document.getElementById("carga-cuenta");
    const cargaIngresosEl = document.getElementById("carga-ingresos");
    if (cargaEl) cargaEl.textContent = d.carga_abordo ? d.carga_abordo.length : 0;
    if (cargaIngresosEl) cargaIngresosEl.textContent = `C$ ${(d.total_carga_colectada || 0).toFixed(2)}`;

    const pesoEl = document.getElementById("peso-estimado");
    if (pesoEl) pesoEl.textContent = (d.peso_estimado_kg || 0) + " kg";

    const txtEstado = document.getElementById("estado-bus");
    txtEstado.textContent = d.estado_bus || "---";
    txtEstado.style.color = d.estado_bus === "EN_RUTA" ? "var(--warning)" : "var(--success)";

    actualizarBotonParada(d);

    if (resAlertas.ok) {
      const alertas = await resAlertas.json();
      renderAlertasExpandibles(alertas);
    }
  } catch (err) {
    console.error("NAVO error:", err);
  }
}

function renderAlertasExpandibles(alertas) {
  const container = document.getElementById("main-alertas-lista");
  if (!container) return;
  if (!alertas || alertas.length === 0) {
    container.innerHTML = '<p style="color:var(--success);font-size:0.85rem;">No hay alertas.</p>';
    return;
  }
  const activas = alertas.filter(a => a.tipo !== "tripulacion_sospechosa");
  const html = activas.slice(-5).reverse().map(a => {
    const ts = a.ultimo_timestamp ? new Date(a.ultimo_timestamp).toLocaleString("es-NI") : "";
    const tipo = (a.tipo || "evento").replace(/_/g, " ");
    const conteo = a.conteo || 1;
    const badge = conteo > 1 ? `<span class="alerta-badge">x${conteo}</span>` : "";
    const detalle = (a.detalle && a.detalle.length > 0)
      ? a.detalle.map(d =>
          `<div class="alerta-detalle-item">
            <span class="alerta-detalle-time">${new Date(d.fecha).toLocaleString("es-NI")}</span>
            <span class="alerta-detalle-motivo">${d.motivo || ""}</span>
            <span class="alerta-detalle-recom">${d.recomendacion || ""}</span>
          </div>`
        ).join("")
      : "";
    return `<div class="alerta-card critica" onclick="this.classList.toggle('expanded')">
      <div class="alerta-card-header">
        <strong>${tipo}</strong> ${badge}
        <span class="alerta-time">${ts}</span>
      </div>
      <p>${a.mensaje || ""}</p>
      ${detalle ? `<div class="alerta-detalle">${detalle}</div>` : ""}
    </div>`;
  }).join("");
  container.innerHTML = html;
}

function actualizarBotonParada(datos) {
  const container = document.getElementById("parada-buttons");
  if (!container) return;
  const paradas = datos.paradas || ["Chinandega", "Posoltega", "Leon"];
  const actual = datos.parada_actual;
  container.innerHTML = paradas.map(p => `
    <button class="btn-parada ${p === actual ? 'active' : ''}"
      data-parada="${p}" onclick="cambiarParada('${p}')">${p}</button>
  `).join("");
}

// =====================
// PANEL DE CONTROL
// =====================

function togglePanelControl() {
  const expand = document.getElementById("panel-expand");
  const btn = document.getElementById("btn-panel-control");
  const isOpen = expand.classList.contains("open");
  if (isOpen) {
    expand.classList.remove("open");
    btn.classList.remove("active");
  } else {
    expand.classList.add("open");
    btn.classList.add("active");
  }
}

function toggleLinea() {
  const toggle = document.getElementById("toggle-linea");
  mostrarLinea = !mostrarLinea;
  toggle.classList.toggle("active");
}

// =====================
// CAMARA
// =====================

async function abrirCamaraLocal() {
  try {
    const video = document.getElementById("cam-video");
    const overlay = document.getElementById("cam-overlay");
    const placeholder = document.getElementById("cam-placeholder");
    const btnAbrir = document.getElementById("btn-abrir-cam");
    const btnCerrar = document.getElementById("btn-cerrar-cam");
    const camDot = document.getElementById("cam-dot");
    const camStatusText = document.getElementById("cam-status-text");

    const stream = await navigator.mediaDevices.getUserMedia({
      video: { width: { ideal: 640 }, height: { ideal: 480 } },
      audio: false,
    });

    mediaStream = stream;
    camaraAbierta = true;
    video.srcObject = stream;

    video.onloadedmetadata = () => {
      video.play();
      overlay.width = video.videoWidth;
      overlay.height = video.videoHeight;
    };

    video.style.display = "block";
    placeholder.style.display = "none";
    btnAbrir.style.display = "none";
    btnCerrar.style.display = "block";
    camDot.className = "panel-dot on";
    camStatusText.textContent = "Activa";
    setStatus("Camara local activa", "ok");
  } catch (err) {
    setStatus("Error: " + err.message, "error");
  }
}

function cerrarCamaraLocal() {
  if (yoloActivo) detenerYOLO();
  if (mediaStream) {
    mediaStream.getTracks().forEach(t => t.stop());
    mediaStream = null;
  }
  camaraAbierta = false;

  const video = document.getElementById("cam-video");
  const overlay = document.getElementById("cam-overlay");
  const placeholder = document.getElementById("cam-placeholder");
  const btnAbrir = document.getElementById("btn-abrir-cam");
  const btnCerrar = document.getElementById("btn-cerrar-cam");
  const camDot = document.getElementById("cam-dot");
  const camStatusText = document.getElementById("cam-status-text");
  const camInfo = document.getElementById("cam-info");

  video.srcObject = null;
  video.style.display = "none";
  overlay.style.display = "none";
  const ctx = overlay.getContext("2d");
  if (ctx) ctx.clearRect(0, 0, overlay.width, overlay.height);
  placeholder.style.display = "flex";
  btnAbrir.style.display = "block";
  btnCerrar.style.display = "none";
  camDot.className = "panel-dot off";
  camStatusText.textContent = "Inactiva";
  camInfo.style.display = "none";
  setStatus("Camara cerrada", "ok");
}

// =====================
// CONTROL MANUAL
// =====================

function getTrackId() {
  return parseInt(document.getElementById("track-id-input").value) || 1;
}

async function eventoSubir() {
  const tid = getTrackId();
  try {
    const res = await fetch(`${API_BASE}/api/pasajero-subio`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ track_id: tid }),
    });
    const data = await res.json();
    document.getElementById("track-id-input").value = tid + 1;
    setStatus(data.mensaje, "ok");
  } catch { setStatus("Error de conexion", "error"); }
}

async function eventoBajar() {
  const tid = getTrackId();
  try {
    const res = await fetch(`${API_BASE}/api/pasajero-bajo`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ track_id: tid }),
    });
    const data = await res.json();
    setStatus(data.mensaje, data.alerta ? "alerta" : "ok");
  } catch { setStatus("Error de conexion", "error"); }
}

async function eventoAgregarCarga() {
  try {
    const res = await fetch(`${API_BASE}/api/simular`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ accion: "carga" }),
    });
    const data = await res.json();
    setStatus(data.mensaje, "ok");
  } catch { setStatus("Error de conexion", "error"); }
}

async function cambiarParada(parada) {
  try {
    await fetch(`${API_BASE}/api/cambiar-estado-bus`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ estado: "DETENIDO", parada }),
    });
    document.querySelectorAll(".btn-parada").forEach(b => b.classList.remove("active"));
    const btn = document.querySelector(`.btn-parada[data-parada="${parada}"]`);
    if (btn) btn.classList.add("active");
    setStatus("Parada: " + parada, "ok");
  } catch { setStatus("Error de conexion", "error"); }
}

async function ponerEnRuta() {
  try {
    await fetch(`${API_BASE}/api/cambiar-estado-bus`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ estado: "EN_RUTA" }),
    });
    document.querySelectorAll(".btn-parada").forEach(b => b.classList.remove("active"));
    setStatus("Bus en ruta", "ok");
  } catch { setStatus("Error de conexion", "error"); }
}

// =====================
// MODO FERIA (AUTO) — manejado por el servidor
// =====================

async function toggleAutoMode() {
  const toggle = document.getElementById("toggle-auto");
  const status = document.getElementById("auto-status");
  const btn = document.getElementById("btn-panel-control");
  const expand = document.getElementById("panel-expand");

  if (!autoActivo) {
    // Encender: llamar al servidor
    try {
      const res = await fetch(`${API_BASE}/api/auto/start`, { method: "POST" });
      const data = await res.json();
      if (data.status === "ok") {
        autoActivo = true;
        toggle.classList.add("active");
        status.textContent = "Servidor esta simulando...";
        status.style.color = "var(--success)";
        setStatus(data.mensaje, "ok");
        // Abrir panel de control y mostrar feed
        if (expand && !expand.classList.contains("open")) {
          expand.classList.add("open");
          if (btn) btn.classList.add("active");
        }
      } else {
        setStatus("Error: " + data.mensaje, "error");
      }
    } catch {
      setStatus("Error de conexion al iniciar auto", "error");
    }
  } else {
    // Apagar
    try {
      const res = await fetch(`${API_BASE}/api/auto/stop`, { method: "POST" });
      const data = await res.json();
      autoActivo = false;
      toggle.classList.remove("active");
      status.textContent = "Desactivado — use botones manuales";
      status.style.color = "var(--text-muted)";
      setStatus(data.mensaje, "ok");
    } catch {
      setStatus("Error de conexion al detener auto", "error");
    }
  }
}

// =====================
// YOLO
// =====================

async function iniciarYOLO() {
  const video = document.getElementById("cam-video");
  if (!video.srcObject) { setStatus("Abra la camara primero", "error"); return; }

  yoloActivo = true;
  currentDetecciones = {};
  targetDetecciones = {};

  const overlay = document.getElementById("cam-overlay");
  const ctx = overlay.getContext("2d");
  overlay.style.display = "block";

  document.getElementById("cam-info").style.display = "flex";
  document.getElementById("btn-yolo-start").style.display = "none";
  document.getElementById("btn-yolo-stop").style.display = "block";
  document.getElementById("yolo-dot").className = "panel-dot on";
  document.getElementById("yolo-status-text").textContent = "Procesando...";
  setStatus("YOLO iniciado", "ok");

  captureCanvas = document.createElement("canvas");
  captureLoop();
  drawRAF = requestAnimationFrame(drawLoop);
}

function detenerYOLO() {
  yoloActivo = false;
  if (captureTimer) { clearTimeout(captureTimer); captureTimer = null; }
  if (drawRAF) { cancelAnimationFrame(drawRAF); drawRAF = null; }

  const overlay = document.getElementById("cam-overlay");
  const ctx = overlay.getContext("2d");
  if (ctx) ctx.clearRect(0, 0, overlay.width, overlay.height);
  overlay.style.display = "none";

  document.getElementById("cam-info").style.display = "none";
  document.getElementById("btn-yolo-start").style.display = "block";
  document.getElementById("btn-yolo-stop").style.display = "none";
  document.getElementById("yolo-dot").className = "panel-dot off";
  document.getElementById("yolo-status-text").textContent = "Detenido";
  setStatus("YOLO detenido", "ok");
}

async function captureLoop() {
  if (!yoloActivo) return;
  const video = document.getElementById("cam-video");
  if (!video.srcObject) return;

  const w = video.videoWidth, h = video.videoHeight;
  if (!w || !h) { captureTimer = setTimeout(captureLoop, 50); return; }

  captureCanvas.width = w;
  captureCanvas.height = h;
  captureCanvas.getContext("2d").drawImage(video, 0, 0, w, h);

  captureCanvas.toBlob(async (blob) => {
    if (!blob || !yoloActivo) return;
    try {
      const form = new FormData();
      form.append("file", blob, "frame.jpg");
      const res = await fetch(URL_DETECTAR, { method: "POST", body: form });
      if (!res.ok) {
        setStatus("YOLO: error " + res.status, "error");
        return;
      }
      const data = await res.json();
      if (data.error) {
        setStatus("YOLO: " + data.error, "error");
        return;
      }
      if (data.detecciones) {
        const freshIds = new Set();
        data.detecciones.forEach(d => {
          const tid = d.track_id;
          freshIds.add(tid);
          targetDetecciones[tid] = {
            x1: d.x1, y1: d.y1, x2: d.x2, y2: d.y2,
            cx: d.cx, cy: d.cy,
            conf: d.conf, track_id: tid,
            keypoints: d.keypoints,
          };
        });
        for (const tid in targetDetecciones) {
          if (!freshIds.has(Number(tid))) delete targetDetecciones[tid];
        }
      }
      if (data.eventos) {
        data.eventos.forEach(ev => {
          if (ev.evento === "subio") {
            setStatus(`Persona ${ev.track_id} subio`, "ok");
          } else if (ev.evento === "bajo") {
            setStatus(`Persona ${ev.track_id} bajo`, "ok");
          }
        });
      }
    } catch (err) {
      setStatus("YOLO: " + (err.message || "error de red"), "error");
    }
    captureTimer = setTimeout(captureLoop, 50);
  }, "image/jpeg", 0.6);
}

function drawLoop() {
  if (!yoloActivo) return;
  const overlay = document.getElementById("cam-overlay");
  const video = document.getElementById("cam-video");
  const w = video.videoWidth, h = video.videoHeight;
  if (!w || !h) { drawRAF = requestAnimationFrame(drawLoop); return; }

  const ctx = overlay.getContext("2d");
  overlay.width = w; overlay.height = h;

  // Snapshot targets → currents (instant)
  const seenIds = new Set(Object.keys(targetDetecciones));

  for (const [tid, tgt] of Object.entries(targetDetecciones)) {
    if (currentDetecciones[tid]) {
      const cur = currentDetecciones[tid];
      cur.x1 = tgt.x1; cur.y1 = tgt.y1;
      cur.x2 = tgt.x2; cur.y2 = tgt.y2;
      cur.cx = tgt.cx; cur.cy = tgt.cy;
      cur.conf = tgt.conf;
      cur.keypoints = tgt.keypoints;
      cur.alpha = 1;
      cur.fadeCount = 0;
    } else {
      currentDetecciones[tid] = { ...tgt, alpha: 1, fadeCount: 0 };
    }
  }

  for (const [tid, cur] of Object.entries(currentDetecciones)) {
    if (!seenIds.has(tid)) {
      cur.fadeCount = (cur.fadeCount || 0) + 1;
      cur.alpha = Math.max(0, 1 - cur.fadeCount / FADE_FRAMES);
      if (cur.fadeCount >= FADE_FRAMES) {
        delete currentDetecciones[tid];
      }
    }
  }

  // Dibujar
  ctx.clearRect(0, 0, w, h);

  const dets = Object.values(currentDetecciones);
  dets.forEach(d => {
    const alpha = d.alpha || 1;
    const tid = d.track_id || "?";
    const pct = (d.conf * 100).toFixed(0);

    ctx.globalAlpha = alpha;

    ctx.strokeStyle = COLOR_CAJA;
    ctx.lineWidth = 2;
    ctx.strokeRect(d.x1, d.y1, d.x2 - d.x1, d.y2 - d.y1);

    const label = `ID:${tid} ${pct}%`;
    ctx.fillStyle = "rgba(0,180,216,0.85)";
    const tw = ctx.measureText(label).width + 10;
    ctx.fillRect(d.x1, d.y1 - 20, tw, 20);
    ctx.fillStyle = "#000";
    ctx.font = "bold 11px monospace";
    ctx.fillText(label, d.x1 + 5, d.y1 - 6);

    ctx.globalAlpha = 1;
  });

  // Linea de puerta
  if (mostrarLinea) {
    const lineY = h * lineaYRatio;
    ctx.strokeStyle = "#fca311";
    ctx.lineWidth = 2;
    ctx.setLineDash([8, 5]);
    ctx.beginPath(); ctx.moveTo(0, lineY); ctx.lineTo(w, lineY); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "#fca311";
    ctx.font = "bold 12px monospace";
    ctx.fillText("PUERTA", 10, lineY - 6);
    // handle visual para drag
    ctx.fillStyle = "rgba(252,163,17,0.25)";
    ctx.fillRect(0, lineY - 8, w, 16);
  }

  drawRAF = requestAnimationFrame(drawLoop);
}

function setStatus(msg, tipo) {
  const el = document.getElementById("prueba-status");
  if (!el) return;
  el.textContent = msg;
  el.className = "status-bar";
  if (tipo) el.classList.add("status-" + tipo);
}

document.addEventListener("DOMContentLoaded", () => {
  consultarServidor();
  setInterval(consultarServidor, 2000);

  // Verificar si el servidor tiene auto mode corriendo
  fetch(`${API_BASE}/api/auto/status`)
    .then(r => r.json())
    .then(data => {
      if (data.corriendo) {
        autoActivo = true;
        const toggle = document.getElementById("toggle-auto");
        const status = document.getElementById("auto-status");
        if (toggle) toggle.classList.add("active");
        if (status) {
          status.textContent = "Servidor esta simulando...";
          status.style.color = "var(--success)";
        }
        setStatus("Auto mode restaurado del servidor", "ok");
      }
    })
    .catch(() => {});

  const slider = document.getElementById("linea-slider");
  const valor = document.getElementById("linea-valor");
  if (slider && valor) {
    slider.addEventListener("input", () => {
      lineaYRatio = parseInt(slider.value) / 100;
      valor.textContent = slider.value + "%";
    });
  }

  const overlay = document.getElementById("cam-overlay");
  if (overlay) {
    overlay.addEventListener("mousedown", (e) => {
      const rect = overlay.getBoundingClientRect();
      const y = (e.clientY - rect.top) / rect.height;
      if (Math.abs(y - lineaYRatio) < 0.04) {
        arrastrandoLinea = true;
        overlay.style.cursor = "grabbing";
      }
    });
  }

  document.addEventListener("mousemove", (e) => {
    if (!arrastrandoLinea) return;
    const overlay = document.getElementById("cam-overlay");
    const rect = overlay.getBoundingClientRect();
    let y = (e.clientY - rect.top) / rect.height;
    y = Math.max(0.1, Math.min(0.9, y));
    lineaYRatio = y;
    const pct = Math.round(y * 100);
    const slider = document.getElementById("linea-slider");
    const valor = document.getElementById("linea-valor");
    if (slider) slider.value = pct;
    if (valor) valor.textContent = pct + "%";
  });

  document.addEventListener("mouseup", () => {
    if (arrastrandoLinea) {
      arrastrandoLinea = false;
      const overlay = document.getElementById("cam-overlay");
      if (overlay) overlay.style.cursor = "";
    }
  });
});
