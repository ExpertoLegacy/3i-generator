"use strict";
const partKey = document.body.dataset.part;
const savedLabel = document.getElementById("saved");
let inputsTimer = null;
let draftTimer = null;
let pendingInputs = false;
let pendingDraft = false;
let pendingDiagram = false;
let quill = null;

async function post(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  return response.json();
}

function setSaved(text) {
  if (savedLabel) savedLabel.textContent = text;
}

function toast(message, canOpen) {
  const box = document.getElementById("toast");
  box.querySelector("span").textContent = message;
  document.getElementById("open-export").hidden = !canOpen;
  box.hidden = false;
  clearTimeout(box.timer);
  box.timer = setTimeout(() => { box.hidden = true; }, canOpen ? 15000 : 6000);
}

function readField(el) {
  return el.value;
}

function collect() {
  const data = {};
  document.querySelectorAll("[data-input]").forEach((el) => {
    const id = el.dataset.input;
    const kind = el.dataset.kind;
    if (kind === "value") data[id] = el.value;
    if (kind === "checks") {
      data[id] = [...el.querySelectorAll("input:checked")].map((c) => c.value);
    }
    if (kind === "repeater") {
      data[id] = [...el.querySelectorAll(":scope > .rows > .row")].map((row) => {
        const item = {};
        row.querySelectorAll("[data-field]").forEach((f) => { item[f.dataset.field] = readField(f); });
        return item;
      });
    }
    if (kind === "grid") {
      data[id] = [...el.querySelectorAll("tbody tr")].map((row) => {
        const item = {};
        row.querySelectorAll("[data-col]").forEach((f) => {
          item[f.dataset.col] = f.hasAttribute("data-readonly") ? f.textContent : f.value;
        });
        return item;
      });
    }
  });
  return data;
}

async function saveInputs() {
  clearTimeout(inputsTimer);
  if (!pendingInputs) return;
  pendingInputs = false;
  const result = await post(`/api/part/${partKey}/inputs`, collect());
  setSaved(result.ok ? "Saved" : "Could not save");
  if (result.ai_blocked) showAiNeeds(result.ai_blocked);
}

// The list of missing answers shrinks as the student types, without leaving the page.
function showAiNeeds(reasons) {
  const box = document.getElementById("ai-needs");
  if (!box) return;
  box.hidden = !reasons.length;
  document.getElementById("ai-privacy").hidden = Boolean(reasons.length);
  box.querySelector("ul").replaceChildren(...reasons.map((reason) => {
    const li = document.createElement("li");
    li.textContent = reason;
    return li;
  }));
  document.querySelectorAll("[data-needs-answers]").forEach((button) => {
    button.toggleAttribute("data-blocked", Boolean(reasons.length));
  });
  setAiButtons();
}

function inputsChanged() {
  pendingInputs = true;
  setSaved("Saving");
  clearTimeout(inputsTimer);
  inputsTimer = setTimeout(saveInputs, 450);
}

function editorHtml() {
  return quill.getSemanticHTML().replace(/&nbsp;/g, " ");
}

async function saveDraft() {
  clearTimeout(draftTimer);
  if (!pendingDraft || !quill) return;
  pendingDraft = false;
  const result = await post(`/api/part/${partKey}/draft`, { html: editorHtml() });
  setSaved(result.ok ? "Saved" : "Could not save");
  const box = document.getElementById("reviewed");
  if (box) box.checked = false;
  if (result.ai_issues) showAiIssues(result.ai_issues);
}

function showAiIssues(issues) {
  const holder = document.getElementById("ai-issues");
  if (!holder) return;
  holder.hidden = !issues.length;
  const list = holder.querySelector("ul");
  list.replaceChildren(...issues.map((issue) => {
    const li = document.createElement("li");
    li.textContent = issue.message;
    return li;
  }));
}

async function flush() {
  await saveInputs();
  await saveDraft();
  if (pendingDiagram) await saveDiagram();
}

document.addEventListener("input", (event) => {
  if (event.target.closest("[data-input]")) inputsChanged();
});
document.addEventListener("change", (event) => {
  if (event.target.closest("[data-input]") && event.target.id !== "reviewed") inputsChanged();
});

document.addEventListener("click", (event) => {
  const add = event.target.closest(".add");
  if (add) {
    const group = add.parentElement;
    const template = group.querySelector(":scope > template");
    const target = group.querySelector(":scope > .rows") || group.querySelector("tbody");
    const fresh = template.content.cloneNode(true);
    target.appendChild(fresh);
    const first = target.lastElementChild.querySelector("input:not([type=hidden]), textarea");
    if (first) first.focus();
    inputsChanged();
    return;
  }
  const remove = event.target.closest(".remove");
  if (remove) {
    (remove.closest(".row") || remove.closest("tr")).remove();
    inputsChanged();
  }
});

// Save before moving to another page.
document.addEventListener("click", async (event) => {
  const link = event.target.closest("a[href^='/part/']");
  if (!link || (!pendingInputs && !pendingDraft && !pendingDiagram)) return;
  event.preventDefault();
  await flush();
  location.href = link.getAttribute("href");
});

function setup(id, handler) {
  const button = document.getElementById(id);
  if (!button) return;
  button.addEventListener("click", async () => {
    button.disabled = true;
    try { await handler(button); } finally { button.disabled = false; }
  });
}

setup("generate", async (button) => {
  if (button.hasAttribute("data-replace") && !window.confirm("Replace the text on this page with a new build? You can restore this version afterward.")) return;
  await flush();
  const result = await post(`/api/part/${partKey}/generate`);
  if (result.ok) location.reload();
  else toast(result.message || "This part could not be built.");
});

setup("restore", async () => {
  await post(`/api/part/${partKey}/restore`);
  location.reload();
});

async function reportResult(result) {
  if (result.message) toast(result.message, Boolean(result.can_open));
}

setup("download", async () => {
  await flush();
  reportResult(await post(`/api/part/${partKey}/download`));
});
setup("build-draft", async () => {
  reportResult(await post("/api/build", { final: false }));
});
setup("build-final", async () => {
  reportResult(await post("/api/build", { final: true }));
});
setup("open-export", async () => {
  const result = await post("/api/open-export");
  if (!result.ok && result.message) toast(result.message);
});

const reviewed = document.getElementById("reviewed");
if (reviewed) {
  reviewed.addEventListener("change", async () => {
    await flush();
    const result = await post(`/api/part/${partKey}/review`, { reviewed: reviewed.checked });
    if (!result.ok) { reviewed.checked = false; toast(result.message); return; }
    setSaved("Saved");
  });
}

document.querySelectorAll("[data-session]").forEach((button) => {
  button.addEventListener("click", async () => {
    await flush();
    const result = await post(`/api/session/${button.dataset.session}`);
    if (result.go) location.href = result.go;
    else if (result.ok) location.reload();
    if (result.message) toast(result.message);
  });
});

// Attachments
const fileAdd = document.getElementById("file-add");
if (fileAdd) {
  fileAdd.addEventListener("change", async () => {
    if (!fileAdd.files.length) return;
    await flush();
    const form = new FormData();
    [...fileAdd.files].forEach((f) => form.append("files", f));
    toast("Adding files");
    const response = await fetch(`/api/part/${partKey}/attachments`, { method: "POST", body: form });
    const result = await response.json();
    if (result.added) location.reload();
    else toast(result.message || "These files could not be added.");
  });
}

async function saveAttachment(figure) {
  const caption = figure.querySelector("[data-caption]");
  const consent = figure.querySelector("[data-consent]");
  await post(`/api/attachment/${figure.dataset.attachment}`, { caption: caption ? caption.value : "", consent: consent ? consent.checked : false });
  setSaved("Saved");
}
document.querySelectorAll(".file[data-attachment]").forEach((figure) => {
  figure.addEventListener("change", (event) => {
    if (event.target.matches("[data-caption],[data-consent]")) saveAttachment(figure);
  });
  figure.querySelector(".delete-file").addEventListener("click", async () => {
    if (!window.confirm("Remove this file from your report?")) return;
    await post(`/api/attachment/${figure.dataset.attachment}/delete`);
    location.reload();
  });
});

// Evaluation import
const importFile = document.getElementById("import-file");
if (importFile) {
  importFile.addEventListener("change", async () => {
    const form = new FormData();
    form.append("file", importFile.files[0]);
    const response = await fetch("/api/import/evaluation", { method: "POST", body: form });
    const info = await response.json();
    if (!info.ok) { toast(info.message); return; }
    const box = document.getElementById("import-map");
    box.hidden = false;
    box.replaceChildren();
    const note = document.createElement("p");
    note.className = "help";
    note.textContent = `${info.rows} responses found. Match each column. Leave name and email columns as Do not use.`;
    box.appendChild(note);
    const table = document.createElement("table");
    table.className = "grid";
    info.columns.forEach((column) => {
      const row = table.insertRow();
      row.insertCell().textContent = column;
      const select = document.createElement("select");
      select.dataset.column = column;
      select.setAttribute("aria-label", `Use of column ${column}`);
      [["", "Do not use"], ["__group__", "Respondent group"], ...info.characteristics.map((c) => [c, c])].forEach(([value, label]) => {
        select.add(new Option(label, value, false, info.guess[column] === value));
      });
      row.insertCell().appendChild(select);
    });
    box.appendChild(table);
    const apply = document.createElement("button");
    apply.type = "button";
    apply.className = "primary add-gap";
    apply.textContent = "Compute results";
    apply.addEventListener("click", async () => {
      const mapping = {};
      box.querySelectorAll("select").forEach((s) => { mapping[s.dataset.column] = s.value; });
      const result = await post("/api/import/evaluation/apply", { mapping });
      if (result.ok) { window.alert(result.message); location.reload(); }
      else toast(result.message);
    });
    box.appendChild(apply);
  });
}

// Source search
const lookupButton = document.getElementById("lookup-go");
if (lookupButton) {
  lookupButton.addEventListener("click", async () => {
    const list = document.getElementById("lookup-results");
    list.replaceChildren();
    lookupButton.disabled = true;
    const result = await post("/api/sources/lookup", { query: document.getElementById("lookup-q").value });
    lookupButton.disabled = false;
    if (!result.ok) { toast(result.message); return; }
    if (!result.results.length) { toast("No matching sources were found."); return; }
    result.results.forEach((item) => {
      const li = document.createElement("li");
      const text = document.createElement("span");
      text.textContent = `${item.authors || "No author"} (${item.year}). ${item.title}. ${item.container}`;
      const add = document.createElement("button");
      add.type = "button";
      add.textContent = "Add";
      add.addEventListener("click", () => {
        const group = document.querySelector('[data-input="sources"]');
        group.querySelector(":scope > .add").click();
        const row = group.querySelector(":scope > .rows").lastElementChild;
        Object.entries(item).forEach(([key, value]) => {
          const field = row.querySelector(`[data-field="${key}"]`);
          if (field) field.value = value;
        });
        inputsChanged();
        li.remove();
      });
      li.append(text, add);
      list.appendChild(li);
    });
  });
}

// Built-in AI
const aiState = document.getElementById("ai-state");
let aiReady = false;

function setAiButtons() {
  document.querySelectorAll("[data-ai]").forEach((button) => {
    button.disabled = !aiReady || button.hasAttribute("data-blocked") || button.hasAttribute("data-working");
  });
}

async function refreshAi() {
  if (!aiState) return;
  let status;
  try {
    status = await (await fetch("/api/ai/status")).json();
  } catch {
    return;
  }
  aiReady = status.state === "ready";
  if (aiReady) {
    aiState.textContent = status.tasks_left ? `AI ready, about ${status.tasks_left} tasks left today` : "AI allowance used up for today";
    aiReady = status.tasks_left > 0;
  } else {
    aiState.textContent = "AI needs internet";
    aiState.title = status.message || "";
  }
  aiState.classList.toggle("ready", aiReady);
  if (aiReady) { recommendTopics(); autoSynthesize(); recommendOptions(); autoTable(); }
  document.getElementById("update-link").hidden = !status.update;
  setAiButtons();
}

if (aiState) {
  setAiButtons();
  refreshAi();
  setInterval(refreshAi, 60000);
}

// Show elapsed time because a draft can take most of a minute.
async function withProgress(button, label, work) {
  const line = document.getElementById("ai-progress");
  const started = Date.now();
  const update = () => {
    if (line) line.textContent = `${label} (${Math.round((Date.now() - started) / 1000)} seconds). You can keep reading while you wait.`;
  };
  button.setAttribute("data-working", "");
  document.querySelectorAll("[data-ai]").forEach((b) => { b.disabled = true; });
  if (line) line.hidden = false;
  update();
  const timer = setInterval(update, 1000);
  try {
    return await work();
  } catch {
    return { ok: false, message: "The app could not finish this AI request. Your work is saved." };
  } finally {
    clearInterval(timer);
    if (line) line.hidden = true;
    button.removeAttribute("data-working");
    setAiButtons();
  }
}

const aiWrite = document.getElementById("ai-write");
if (aiWrite) {
  aiWrite.addEventListener("click", async () => {
    if (aiWrite.hasAttribute("data-replace") && !window.confirm("Replace the text on this page with an AI draft? You can restore this version afterward.")) return;
    await flush();
    const result = await withProgress(aiWrite, "AI is drafting this part", () => post(`/api/part/${partKey}/ai/write`));
    if (result.ok) location.reload();
    else toast(result.message);
  });
}

function findingItem(text, className) {
  const li = document.createElement("li");
  if (className) li.className = className;
  li.textContent = text;
  return li;
}

const aiCheck = document.getElementById("ai-check");
if (aiCheck) {
  aiCheck.addEventListener("click", async () => {
    await flush();
    const result = await withProgress(aiCheck, "AI is checking this part", () => post(`/api/part/${partKey}/ai/check`));
    if (!result.ok) { toast(result.message); return; }
    const panel = document.getElementById("ai-findings");
    const heading = document.createElement("h3");
    const total = result.findings.length + result.rules.length;
    heading.textContent = total ? `${total} things to look at` : "No problems found";
    const list = document.createElement("ul");
    result.findings.forEach((f) => list.appendChild(findingItem(f.message, f.severity)));
    result.rules.forEach((f) => list.appendChild(findingItem(f.message, "fix")));
    const note = document.createElement("p");
    note.className = "help";
    note.textContent = "AI can be wrong. Use these as prompts for your own judgment.";
    panel.replaceChildren(heading, list, note);
    panel.hidden = false;
  });
}

function useSuggestion(box, text, target) {
  const name = box.dataset.suggestFor;
  const field = document.querySelector(`[data-input="${name}"]`);
  const kind = box.dataset.kind;
  if (kind === "repeater" || kind === "grid") {
    const group = kind === "grid" ? field.closest(".group") : field;
    group.querySelector(":scope > .add").click();
    const rows = kind === "grid" ? field.querySelector("tbody") : field.querySelector(":scope > .rows");
    rows.lastElementChild.querySelector(`[data-field="${target}"],[data-col="${target}"]`).value = text;
  } else if (kind === "textarea" && field.value.trim() && name !== "title") {
    field.value = `${field.value.replace(/\s+$/, "")}\n${text}`;
  } else {
    field.value = text;
  }
  inputsChanged();
}

document.querySelectorAll(".suggest-box").forEach((box) => {
  const button = box.querySelector(".suggest");
  button.addEventListener("click", async () => {
    await flush();
    const result = await withProgress(button, "AI is thinking of ideas", () => post(`/api/part/${partKey}/ai/suggest`, { field: box.dataset.suggestFor }));
    if (!result.ok) { toast(result.message); return; }
    const list = box.querySelector(".results");
    list.hidden = false;
    list.replaceChildren(...result.suggestions.map((text) => {
      const li = document.createElement("li");
      const span = document.createElement("span");
      span.textContent = text;
      const use = document.createElement("button");
      use.type = "button";
      use.textContent = "Use";
      use.addEventListener("click", () => { useSuggestion(box, text, result.target); li.remove(); });
      li.append(span, use);
      return li;
    }));
  });
});

// Diagram table
const diagramData = document.getElementById("diagram-data");
let diagram = null;
let diagramTimer = null;

function diagramKind() {
  const select = document.getElementById("diagram-kind");
  return select ? select.value : diagram.kind;
}

function diagramPayload() {
  return { kind: diagramKind(), spec: { nodes: diagram.spec.nodes, edges: diagram.spec.edges, options: diagram.spec.options || {} } };
}

async function saveDiagram() {
  clearTimeout(diagramTimer);
  pendingDiagram = false;
  const result = await post(`/api/part/${partKey}/diagram`, diagramPayload());
  setSaved(result.ok ? "Saved" : "Could not save");
}

function diagramChanged() {
  pendingDiagram = true;
  setSaved("Saving");
  clearTimeout(diagramTimer);
  diagramTimer = setTimeout(saveDiagram, 500);
}

function el(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
}

function selectFrom(choices, value, label, onChange) {
  const select = el("select", { ariaLabel: label });
  choices.forEach(([id, text]) => select.add(new Option(text, id, false, id === value)));
  select.addEventListener("change", () => onChange(select.value));
  return select;
}

function drawEdgeRows(config, body) {
  const choices = diagram.spec.nodes.map((n) => [n.id, n.label || "(unnamed)"]);
  body.replaceChildren(...diagram.spec.edges.map((edge, index) => {
    const row = el("tr");
    const from = selectFrom(choices, edge.from, "From", (v) => { edge.from = v; diagramChanged(); });
    const to = selectFrom(choices, edge.to, "To", (v) => { edge.to = v; diagramChanged(); });
    let last;
    if (config.edge_choices) {
      last = selectFrom(config.edge_choices.map((c) => [c, c]), edge.label || config.edge_choices[1], config.edge_label, (v) => { edge.label = v; diagramChanged(); });
    } else if (config.edge_label) {
      last = el("input", { value: edge.label || "", ariaLabel: config.edge_label });
      last.addEventListener("input", () => { edge.label = last.value; diagramChanged(); });
    }
    const remove = el("button", { type: "button", className: "quiet", textContent: "×", ariaLabel: "Remove connection" });
    remove.addEventListener("click", () => { diagram.spec.edges.splice(index, 1); drawEditor(); diagramChanged(); });
    row.append(el("td", {}, from), el("td", {}, to));
    if (last) row.append(el("td", {}, last));
    row.append(el("td", {}, remove));
    return row;
  }));
}

function drawEditor() {
  const holder = document.getElementById("diagram-editor");
  const config = diagram.kinds[diagramKind()];
  const rows = el("tbody");
  const edgeBody = el("tbody");
  diagram.spec.nodes.forEach((node, index) => {
    const row = el("tr");
    const label = el("input", { value: node.label || "", ariaLabel: config.item });
    label.addEventListener("input", () => { node.label = label.value; drawEdgeRows(config, edgeBody); diagramChanged(); });
    row.append(el("td", {}, label));
    if (config.types) {
      if (!config.types.includes(node.type)) node.type = config.types[0];
      row.append(el("td", {}, selectFrom(config.types.map((t) => [t, t]), node.type, "Type", (v) => { node.type = v; diagramChanged(); })));
    }
    if (config.details) {
      const details = el("textarea", { value: node.details || "", rows: 3, ariaLabel: config.details });
      details.addEventListener("input", () => { node.details = details.value; diagramChanged(); });
      row.append(el("td", {}, details));
    }
    const remove = el("button", { type: "button", className: "quiet", textContent: "×", ariaLabel: `Remove ${config.item}` });
    remove.addEventListener("click", () => {
      diagram.spec.nodes.splice(index, 1);
      diagram.spec.edges = diagram.spec.edges.filter((e) => e.from !== node.id && e.to !== node.id);
      drawEditor();
      diagramChanged();
    });
    row.append(el("td", {}, remove));
    rows.append(row);
  });
  const heads = [config.item, ...(config.types ? ["Type"] : []), ...(config.details ? [config.details] : []), ""];
  const parts = [el("div", { className: "grid-wrap" }, el("table", { className: "grid" }, el("thead", {}, el("tr", {}, ...heads.map((h) => el("th", { textContent: h })))), rows))];
  const addNode = el("button", { type: "button", className: "add-row", textContent: `Add ${config.item.toLowerCase()}` });
  addNode.addEventListener("click", () => {
    let number = diagram.spec.nodes.length + 1;
    while (diagram.spec.nodes.some((n) => n.id === `n${number}`)) number += 1;
    diagram.spec.nodes.push({ id: `n${number}`, label: "", type: config.types ? config.types[0] : "", details: "" });
    drawEditor();
    rows.lastElementChild.querySelector("input").focus();
    diagramChanged();
  });
  parts.push(addNode);
  if (config.edges) {
    drawEdgeRows(config, edgeBody);
    const edgeHeads = ["From", "To", ...(config.edge_label ? [config.edge_label] : []), ""];
    const addEdge = el("button", { type: "button", className: "add-row", textContent: "Add connection" });
    addEdge.addEventListener("click", () => {
      if (diagram.spec.nodes.length < 2) { toast("Add at least two boxes first."); return; }
      diagram.spec.edges.push({ from: diagram.spec.nodes[0].id, to: diagram.spec.nodes[1].id, label: config.edge_choices ? config.edge_choices[1] : "" });
      drawEditor();
      diagramChanged();
    });
    parts.push(el("h4", { textContent: "Connections" }));
    if (config.edge_note) parts.push(el("p", { className: "help", textContent: config.edge_note }));
    parts.push(el("div", { className: "grid-wrap" }, el("table", { className: "grid" }, el("thead", {}, el("tr", {}, ...edgeHeads.map((h) => el("th", { textContent: h })))), edgeBody)), addEdge);
  }
  Object.entries(config.options || {}).forEach(([name, text]) => {
    diagram.spec.options = diagram.spec.options || {};
    const box = el("input", { type: "checkbox", checked: Boolean(diagram.spec.options[name]) });
    box.addEventListener("change", () => { diagram.spec.options[name] = box.checked; diagramChanged(); });
    parts.push(el("label", { className: "check" }, box, ` ${text}`));
  });
  holder.replaceChildren(...parts);
}

if (diagramData) {
  diagram = JSON.parse(diagramData.textContent);
  diagram.spec.edges = diagram.spec.edges || [];
  drawEditor();
  const kindSelect = document.getElementById("diagram-kind");
  if (kindSelect) kindSelect.addEventListener("change", () => { drawEditor(); diagramChanged(); });
  document.getElementById("diagram-draw").addEventListener("click", async (event) => {
    event.target.disabled = true;
    clearTimeout(diagramTimer);
    pendingDiagram = false;
    await flush();
    const result = await post(`/api/part/${partKey}/diagram/draw`, diagramPayload());
    event.target.disabled = false;
    if (!result.ok) { toast(result.message); return; }
    if (result.warnings.length) window.alert(result.warnings.join(" "));
    location.reload();
  });
  document.getElementById("diagram-start").addEventListener("click", async () => {
    if (diagram.spec.nodes.length && !window.confirm("Replace this table with one built from your answers?")) return;
    await flush();
    const result = await post(`/api/part/${partKey}/diagram/start`, { kind: diagramKind() });
    if (!result.ok) { toast(result.message); return; }
    if (!result.spec.nodes.length) toast("Your answers do not list items for this diagram yet. Add them to the table.");
    diagram.spec = result.spec;
    drawEditor();
    diagramChanged();
  });
  const removeDrawn = document.getElementById("diagram-remove");
  if (removeDrawn) {
    removeDrawn.addEventListener("click", async () => {
      if (!window.confirm("Remove this diagram from your report? Your table stays here.")) return;
      await flush();
      await post(`/api/attachment/${removeDrawn.dataset.attachmentId}/delete`);
      location.reload();
    });
  }
  const aiDiagram = document.getElementById("ai-diagram");
  if (aiDiagram) {
    aiDiagram.addEventListener("click", async () => {
      if (diagram.spec.nodes.length && !window.confirm("Replace this table with an AI proposal?")) return;
      await flush();
      const result = await withProgress(aiDiagram, "AI is proposing a table", () => post(`/api/part/${partKey}/ai/diagram`, { kind: diagramKind() }));
      if (result.ok) location.reload();
      else toast(result.message);
    });
  }
}

// USB drives
const usbState = document.getElementById("usb-state");
if (usbState) {
  const usbText = {
    saved: "Saved to the drive",
    missing: "Drive not found. Your work is safe on this computer.",
    failed: "Could not save to the drive. Your work is safe on this computer.",
  };
  const syncDrive = async () => {
    await flush();
    const result = await (await fetch("/api/session/sync")).json();
    if (usbText[result.state]) usbState.textContent = usbText[result.state];
  };
  syncDrive();
  setInterval(syncDrive, 15000);
}

const updateLink = document.getElementById("update-link");
if (updateLink) updateLink.addEventListener("click", () => post("/api/open-update"));

if (document.body.dataset.notice) toast(document.body.dataset.notice);

// Pilot reliability
const pilotFile = document.getElementById("pilot-file");
if (pilotFile) {
  pilotFile.addEventListener("change", async () => {
    await flush();
    const form = new FormData();
    form.append("file", pilotFile.files[0]);
    const result = await (await fetch("/api/import/pilot", { method: "POST", body: form })).json();
    if (result.ok) { window.alert(result.message); location.reload(); }
    else toast(result.message);
  });
}

// Moving through the report
setup("approve-next", async (button) => {
  await flush();
  const result = await post(`/api/part/${partKey}/review`, { reviewed: true });
  if (result.ok) location.href = button.dataset.next;
  else toast(result.message);
});
setup("skip-part", async () => {
  if (!window.confirm("Hindi kailangan ang bahaging ito? Aalisin ito sa report, at maibabalik mo ito anumang oras. (Leave this part out of the report? You can bring it back.)")) return;
  await flush();
  await post(`/api/part/${partKey}/skip`, { skip: true });
  location.href = "/";
});
setup("restore-part", async () => {
  await post(`/api/part/${partKey}/skip`, { skip: false });
  location.reload();
});

// Word master
async function showMaster() {
  const holder = document.getElementById("master-view");
  if (!holder || !window.docx) return;
  const response = await fetch("/api/master-preview");
  if (response.status !== 200) return;
  const page = el("div", {});
  await window.docx.renderAsync(await response.blob(), page, null, { inWrapper: true, breakPages: true, ignoreLastRenderedPageBreak: false });
  holder.replaceChildren(page);
}
showMaster();
setup("build-chapter", async (button) => {
  await flush();
  const result = await post(`/api/chapter/${button.dataset.tab}/build`);
  if (result.message) toast(result.message);
  if (result.ok) await showMaster();
});
setup("download-instrument", async () => { reportResult(await post("/api/instrument/download")); });

// Reading the student's code
setup("repo-github", async () => {
  const result = await post("/api/repo/github", { link: document.getElementById("repo-link").value });
  if (result.message) toast(result.message);
  if (result.ok) location.reload();
});
setup("repo-folder", async () => {
  const result = await post("/api/repo/folder");
  if (result.message) toast(result.message);
  if (result.ok) location.reload();
});

// Choices and prepared tables
let optionsRequested = false;
async function recommendOptions() {
  const list = document.getElementById("choice-list");
  if (!list || !list.hasAttribute("data-suggest") || optionsRequested) return;
  optionsRequested = true;
  const line = document.createElement("p");
  line.textContent = "Inihahanda ang mga mungkahi...";
  list.append(line);
  const result = await post(`/api/part/${partKey}/ai/options`);
  if (result.ok) location.reload();
  else toast(result.message);
}
document.querySelectorAll(".choice").forEach((button) => {
  button.addEventListener("click", async () => {
    if (document.getElementById("editor") && !window.confirm("Palitan ang kasalukuyang draft? Maibabalik mo ito. (Replace the current draft? You can restore it.)")) return;
    document.querySelector('[data-input="choice"]').value = button.dataset.choice;
    pendingInputs = true;
    await flush();
    const result = await withProgress(button, "Isinusulat ang draft", () => post(`/api/part/${partKey}/ai/synthesize`));
    if (result.ok) location.reload();
    else toast(result.message);
  });
});
let tableRequested = false;
async function runTable() {
  const button = document.getElementById("ai-table");
  await flush();
  const result = await withProgress(button, "Inihahanda ang talahanayan", () => post(`/api/part/${partKey}/ai/synthesize`, { rows_only: true }));
  if (result.ok) location.reload();
  else toast(result.message);
}
function autoTable() {
  const button = document.getElementById("ai-table");
  if (!button || tableRequested || !button.hasAttribute("data-auto")) return;
  tableRequested = true;
  runTable();
}
const tableButton = document.getElementById("ai-table");
if (tableButton) tableButton.addEventListener("click", runTable);

// Parts written from what was already approved
let synthesisRequested = false;
async function runSynthesis() {
  const button = document.getElementById("ai-synthesize");
  await flush();
  const result = await withProgress(button, "Isinusulat mula sa mga naaprubahang bahagi", () => post(`/api/part/${partKey}/ai/synthesize`));
  if (result.ok) location.reload();
  else toast(result.message);
}
function autoSynthesize() {
  const button = document.getElementById("ai-synthesize");
  if (!button || synthesisRequested || !button.hasAttribute("data-auto") || button.hasAttribute("data-blocked")) return;
  synthesisRequested = true;
  runSynthesis();
}
const synthesizeButton = document.getElementById("ai-synthesize");
if (synthesizeButton) {
  synthesizeButton.addEventListener("click", () => {
    if (synthesizeButton.hasAttribute("data-replace") && !window.confirm("Palitan ang kasalukuyang draft? Maibabalik mo ito. (Replace the current draft? You can restore it.)")) return;
    runSynthesis();
  });
}
setup("refresh-tables", async () => {
  await flush();
  const result = await post(`/api/part/${partKey}/refresh-tables`);
  if (result.ok) location.reload();
  else toast(result.message);
});

// Research parts: recommend topics, then research and draft once they are approved
let topicsRequested = false;
async function recommendTopics() {
  const area = document.getElementById("topic-area");
  const researchButton = document.getElementById("ai-research");
  if (!area || !area.hasAttribute("data-recommend") || topicsRequested) return;
  topicsRequested = true;
  const result = await withProgress(researchButton, "Inihahanda ang mga inirerekomendang paksa", () => post(`/api/part/${partKey}/ai/topics`));
  if (result.ok) location.reload();
  else toast(result.message);
}

const aiResearch = document.getElementById("ai-research");
if (aiResearch) {
  aiResearch.addEventListener("click", async () => {
    if (aiResearch.hasAttribute("data-replace") && !window.confirm("Palitan ang kasalukuyang draft? Maibabalik mo ito. (Replace the current draft? You can restore it.)")) return;
    await flush();
    const result = await withProgress(aiResearch, "Naghahanap ng mga pag-aaral at sumusulat ng draft", () => post(`/api/part/${partKey}/ai/research`));
    if (result.ok) location.reload();
    else toast(result.message);
  });
}
document.querySelectorAll(".source-remove").forEach((button) => {
  button.addEventListener("click", async () => {
    if (!window.confirm("Alisin ang sangguniang ito? Ayusin din ang draft kung binanggit ito. (Remove this source? Also fix the draft if it is cited.)")) return;
    await flush();
    await post("/api/sources/remove", { title: button.dataset.title });
    location.reload();
  });
});

// Guided drafting
const aiGuided = document.getElementById("ai-guided");
if (aiGuided) {
  aiGuided.addEventListener("click", async () => {
    if (aiGuided.hasAttribute("data-replace") && !window.confirm("Palitan ang kasalukuyang teksto ng bagong unang draft? Maibabalik mo ito. (Replace the current text? You can restore it.)")) return;
    await flush();
    const result = await withProgress(aiGuided, "Ginagawa ang unang draft", () => post(`/api/part/${partKey}/ai/guided`));
    if (result.ok) location.reload();
    else toast(result.message);
  });
}

async function searchStudies() {
  const list = document.getElementById("study-results");
  const button = document.getElementById("study-go");
  button.disabled = true;
  list.replaceChildren(el("li", { textContent: "Naghahanap ng mga pag-aaral..." }));
  const result = await post("/api/sources/search", { query: document.getElementById("study-q").value });
  button.disabled = false;
  list.replaceChildren();
  if (!result.ok) { toast(result.message); return; }
  if (!result.results.length) { list.append(el("li", { textContent: "Walang nakita. Subukan ang ibang salita." })); return; }
  result.results.forEach((study) => {
    const add = el("button", { type: "button", textContent: "Idagdag" });
    add.addEventListener("click", async () => {
      const saved = await post("/api/sources/add", { source: study });
      if (!saved.ok) { toast(saved.message); return; }
      document.getElementById("source-count").textContent = saved.count;
      add.textContent = "Naidagdag";
      add.disabled = true;
    });
    const details = el("div", {},
      el("strong", { textContent: `${study.title} (${study.year})` }),
      el("small", { textContent: `${study.authors}. ${study.container}${study.origin === "Local" ? ". Philippines" : ""}` }),
      el("p", { textContent: study.notes ? `${study.notes.slice(0, 320)}...` : "Walang buod. Basahin muna ang mismong pag-aaral." }));
    if (study.link) details.append(el("small", { textContent: study.link }));
    list.append(el("li", {}, details, add));
  });
}
const studyGo = document.getElementById("study-go");
if (studyGo) {
  studyGo.addEventListener("click", searchStudies);
  const terms = (document.querySelector('[data-input="search_terms"]').value || "").split("\n").filter(Boolean);
  document.getElementById("term-list").replaceChildren(...terms.map((term) => {
    const choice = el("button", { type: "button", className: "term", textContent: term });
    choice.addEventListener("click", () => { document.getElementById("study-q").value = term; searchStudies(); });
    return choice;
  }));
  if (document.getElementById("study-results").dataset.auto === "yes") searchStudies();
}

// Adviser notes
const noteAuthor = document.getElementById("note-author");
if (noteAuthor) {
  try { noteAuthor.value = localStorage.getItem("noteAuthor") || ""; } catch { /* a remembered name is only a convenience */ }
  document.getElementById("note-add").addEventListener("click", async () => {
    await flush();
    const result = await post(`/api/part/${partKey}/notes`, { author: noteAuthor.value, body: document.getElementById("note-body").value });
    if (!result.ok) { toast(result.message); return; }
    try { localStorage.setItem("noteAuthor", noteAuthor.value); } catch { /* see above */ }
    location.reload();
  });
  document.querySelectorAll(".note-toggle").forEach((button) => {
    button.addEventListener("click", async () => {
      await flush();
      await post(`/api/note/${button.dataset.note}`, { resolved: button.dataset.resolved === "0" });
      location.reload();
    });
  });
}

// AI-use statement
const disclosureBox = document.getElementById("disclosure-confirm");
if (disclosureBox) {
  disclosureBox.addEventListener("change", async () => {
    await post("/api/disclosure", { confirmed: disclosureBox.checked });
    setSaved("Saved");
  });
}

// Defense Prep
setup("defense-fill", async () => {
  if (!window.confirm("Replace your slides and questions with an outline built from your report?")) return;
  await flush();
  await post("/api/defense/fill");
  location.reload();
});
setup("defense-download", async () => {
  await flush();
  reportResult(await post("/api/defense/download"));
});
const aiDefense = document.getElementById("ai-defense");
if (aiDefense) {
  aiDefense.addEventListener("click", async () => {
    if (!window.confirm("Replace your slides and questions with an AI draft?")) return;
    await flush();
    const result = await withProgress(aiDefense, "AI is preparing your outline", () => post(`/api/part/${partKey}/ai/defense`));
    if (result.ok) location.reload();
    else toast(result.message);
  });
}

// AI images
const aiImage = document.getElementById("ai-image");
if (aiImage) {
  aiImage.addEventListener("click", async () => {
    await flush();
    const description = document.getElementById("image-description").value;
    const result = await withProgress(aiImage, "AI is creating an image", () => post(`/api/part/${partKey}/ai/image`, { description }));
    if (result.ok) location.reload();
    else toast(result.message);
  });
}
document.querySelectorAll("[data-option]").forEach((figure) => {
  const id = figure.dataset.option;
  figure.querySelector(".discard-option").addEventListener("click", async () => {
    await post(`/api/attachment/${id}/delete`);
    location.reload();
  });
  const use = figure.querySelector(".use-option");
  if (use) {
    use.addEventListener("click", async () => {
      const result = await post(`/api/attachment/${id}/keep`);
      if (result.ok) location.reload();
      else toast(result.message);
    });
  }
  const save = figure.querySelector(".save-option");
  if (save) {
    save.addEventListener("click", async () => {
      const result = await post(`/api/attachment/${id}/save-file`);
      if (result.message) toast(result.message);
    });
  }
});

const source = document.getElementById("draft-html");
if (source && window.Quill) {
  quill = new Quill("#editor", {
    theme: "snow",
    modules: { toolbar: [["bold", "italic"], [{ list: "ordered" }, { list: "bullet" }], [{ header: 3 }]] },
    formats: ["bold", "italic", "list", "header"],
  });
  quill.clipboard.dangerouslyPasteHTML(JSON.parse(source.textContent), "silent");
  quill.history.clear();
  quill.on("text-change", (_delta, _old, origin) => {
    if (origin !== "user") return;
    pendingDraft = true;
    setSaved("Saving");
    clearTimeout(draftTimer);
    draftTimer = setTimeout(saveDraft, 700);
  });
}

window.addEventListener("beforeunload", () => {
  if (pendingDiagram) navigator.sendBeacon?.(`/api/part/${partKey}/diagram`, new Blob([JSON.stringify(diagramPayload())], { type: "application/json" }));
  if (pendingInputs) navigator.sendBeacon?.(`/api/part/${partKey}/inputs`, new Blob([JSON.stringify(collect())], { type: "application/json" }));
});
