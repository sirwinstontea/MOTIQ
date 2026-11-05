// Generic component editor: every form is built from the hardware type definitions (S.types).
function fieldInput(cid, key, fd, v) {
  const k = `data-c="${esc(cid)}" data-f="${esc(key)}" data-t="${fd.type}"`;
  const lab = `<label>${esc(key)}${fd.required ? " *" : ""}${fd.unit ? ` [${esc(fd.unit)}]` : ""}</label>`;
  if (fd.type === "bool") return lab + `<input type="checkbox" ${k} ${v ? "checked" : ""}>`;
  if (fd.type === "enum") return lab + `<select ${k}>${["", ...fd.values].map(o =>
    `<option ${o === (v ?? "") ? "selected" : ""}>${esc(o)}</option>`).join("")}</select>`;
  const val = v == null ? "" : Array.isArray(v) ? v.join(", ") : v;
  const ph = fd.type === "vector" ? `${fd.length ?? "n"} numbers, comma separated` : (fd.description ?? "");
  return lab + `<input ${k} value="${esc(val)}" placeholder="${esc(ph)}" title="${esc(fd.description ?? "")}">`;
}

function renderComponents() {
  const comps = S.hardware.components, types = S.types;
  const conn = Object.fromEntries((S.report.components || []).map(c => [c.id, c]));
  const parents = ["cell", "arm", ...comps.map(c => c.id)];
  const sel = (attr, opts, cur) => `<select ${attr}>${opts.map(o => `<option ${o === cur ? "selected" : ""}>${esc(o)}</option>`).join("")}</select>`;
  $("comps").innerHTML = comps.map(c => {
    const t = types[c.type], info = conn[c.id] || {};
    const fields = Object.entries(t.fields).map(([k, fd]) => fieldInput(c.id, k, fd, c.spec[k])).join("");
    const extra = t.allow_extra_fields ? Object.fromEntries(Object.entries(c.spec).filter(([k]) => !(k in t.fields))) : null;
    return `<div class="robot"><div class="row"><b>${esc(c.name)}</b><span class="mut">${esc(t.label)} · ${esc(c.id)}${info.carried_by_arm ? " · on arm" : ""}</span></div>
      <div class="mut">${esc(info.connection || "")}</div>
      <div class="grid" style="margin-top:6px">
        <label>name</label><input data-c="${esc(c.id)}" data-m="name" value="${esc(c.name)}">
        <label>attached to</label>${sel(`data-c="${esc(c.id)}" data-m="attached_to"`, parents.filter(p => p !== c.id), c.attached_to)}
        <label>driver</label>${sel(`data-c="${esc(c.id)}" data-m="driver"`, ["", ...t.drivers.map(d => d.id)], c.driver ?? "")}
        <label>status</label>${sel(`data-c="${esc(c.id)}" data-m="status"`, ["placeholder", "datasheet", "measured"], c.status)}
        ${fields}
        ${extra ? `<label>extra fields (JSON)</label><textarea rows="3" data-c="${esc(c.id)}" data-x="1">${esc(JSON.stringify(extra))}</textarea>` : ""}
      </div>
      <div style="margin-top:6px"><button onclick="saveComp('${esc(c.id)}')">Save</button> <button onclick="removeComp('${esc(c.id)}')">Remove</button></div></div>`;
  }).join("") || `<p class="mut">No components yet.</p>`;
  const ts = $("c_type"), keep = ts.value;
  ts.innerHTML = Object.entries(types).map(([k, t]) => `<option value="${esc(k)}">${esc(t.label)} (${esc(k)})</option>`).join("");
  if (keep) ts.value = keep;
  $("c_parent").innerHTML = parents.map(p => `<option>${esc(p)}</option>`).join("");
}

function readComp(cid) {
  const t = S.types[S.hardware.components.find(c => c.id === cid).type];
  const body = { spec: {} };
  for (const e of document.querySelectorAll(`[data-c="${cid}"]`)) {
    if (e.dataset.m) { body[e.dataset.m] = e.value === "" && e.dataset.m === "driver" ? null : e.value; continue; }
    if (e.dataset.x) {
      try { Object.assign(body.spec, JSON.parse(e.value || "{}")); } catch { throw new Error("extra fields: invalid JSON"); }
      continue;
    }
    const fd = t.fields[e.dataset.f]; let v = e.type === "checkbox" ? e.checked : e.value.trim();
    if (v === "" && fd.type !== "bool") v = null;
    else if (fd.type === "number" || fd.type === "integer") v = Number(v);
    else if (fd.type === "vector") v = v.split(",").map(s => Number(s.trim()));
    body.spec[e.dataset.f] = v;
  }
  return body;
}

async function saveComp(cid) {
  let body; try { body = readComp(cid); } catch (e) { return msg(e.message, true); }
  if (await api(`/api/components/${encodeURIComponent(cid)}`, body)) msg(`${cid} saved`);
}
async function removeComp(cid) {
  if (confirm(`Remove ${cid}?`) && await api(`/api/components/${encodeURIComponent(cid)}/delete`, {})) msg(`${cid} removed`);
}
async function addComp() {
  const body = { id: $("c_id").value.trim(), name: $("c_name").value.trim(), type: $("c_type").value,
                 attached_to: $("c_parent").value, status: "placeholder", spec: {} };
  // required fields with defaults are filled in by the server; others are asked for after adding
  const t = S.types[body.type];
  for (const [k, fd] of Object.entries(t.fields)) if (fd.required && fd.default == null) {
    const raw = prompt(`${body.type}.${k}${fd.unit ? " [" + fd.unit + "]" : ""}${fd.values ? " (" + fd.values.join("/") + ")" : ""}${fd.length ? " (" + fd.length + " numbers, comma separated)" : ""}`);
    if (raw === null) return;
    body.spec[k] = fd.type === "number" || fd.type === "integer" ? Number(raw) : fd.type === "bool" ? /^(true|yes|1)$/i.test(raw)
      : fd.type === "vector" ? raw.split(",").map(s => Number(s.trim())) : raw;
  }
  if (await api("/api/components", body)) { msg(`${body.id} added`); $("c_id").value = ""; $("c_name").value = ""; }
}
