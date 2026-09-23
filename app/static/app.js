const categories = [
  {
    id: "before-units",
    title: "1. Какие подразделения были раньше",
    helper: "Подразделения и роли, найденные в редакции до.",
    types: ["unit_preserved", "unit_removed", "unit_transformed"],
  },
  {
    id: "new-units",
    title: "2. Какие появились после реорганизации",
    helper: "Новые или выделенные подразделения из редакции после.",
    types: ["unit_created"],
  },
  {
    id: "kept-functions",
    title: "3. Какие функции остались",
    helper: "Функции, которые сохранились или были перенесены.",
    types: ["function_preserved", "function_moved", "wording_changed"],
  },
  {
    id: "lost-functions",
    title: "4. Какие функции пропали",
    helper: "Функции, которые были в старой редакции и требуют проверки в новой.",
    types: ["function_missing"],
  },
  {
    id: "duplicates",
    title: "5. Какие функции стали дублироваться",
    helper: "Возможные пересечения функций между подразделениями.",
    types: ["possible_duplicate"],
  },
  {
    id: "conflicts",
    title: "6. Где может быть конфликт ответственности",
    helper: "Зоны, где полномочия могут конфликтовать или требовать уточнения.",
    types: ["possible_conflict", "insufficient_evidence"],
  },
  {
    id: "sources",
    title: "7. Подтверждающие источники",
    helper: "Все выводы с привязкой к документам, пунктам и цитатам.",
    types: ["*"],
  },
];

const state = {
  step: "upload",
  files: { before: [], after: [] },
  jobId: null,
  status: null,
  statusMessage: "",
  error: "",
  report: null,
  documents: { before: null, after: null },
  activeCategory: categories[0].id,
  activeSource: null,
};

const root = document.getElementById("root");

function icon(label) {
  return `<span class="text-icon" aria-hidden="true">${label}</span>`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function render() {
  if (!root) return;
  if (state.step === "analysis") root.innerHTML = renderAnalysis();
  else root.innerHTML = renderUpload();
  bindEvents();
}

function renderUpload() {
  return `
    <main class="app-shell upload-shell">
      <header class="topbar">
        <div>
          <p class="eyebrow">RedRobin · HackAlem AI</p>
          <h1>Анализ организационной структуры и функционала</h1>
        </div>
        <div class="topbar-status">${icon("✓")}<span>FastAPI backend</span></div>
      </header>

      <section class="upload-layout">
        <div class="intro-panel">
          <p class="eyebrow">Сценарий</p>
          <h2>Загрузите документы до и после реорганизации</h2>
          <p>
            Фронт подключён к backend-ручкам: загрузка создаёт задание, анализ
            выполняется на сервере, справа открывается полный распарсенный документ.
          </p>
          <div class="flow-strip">
            <span>Загрузка</span><span>→</span><span>Анализ</span><span>→</span><span>Документ с подсветкой</span>
          </div>
        </div>

        <div class="upload-grid">
          ${renderDropZone("before", "Документы ДО", "Редакция 8, старые положения, оргструктура")}
          ${renderDropZone("after", "Документы ПОСЛЕ", "Редакция 9, новые положения, приложения")}
        </div>
      </section>

      ${state.statusMessage ? `<div class="status-strip">${escapeHtml(state.statusMessage)}</div>` : ""}
      ${state.error ? `<div class="form-error">${escapeHtml(state.error)}</div>` : ""}

      <footer class="upload-actions">
        <button class="primary-button" data-action="analyze" ${canAnalyze() ? "" : "disabled"}>
          ${state.status ? `<span class="spinner"></span>` : icon("↔")}
          ${state.status ? "Обрабатываем" : "Запустить анализ"}
        </button>
      </footer>
    </main>
  `;
}

function renderDropZone(kind, title, subtitle) {
  const files = state.files[kind];
  const fileList =
    files.length === 0
      ? `<span class="empty-files">DOCX</span>`
      : files
          .map(
            (file) => `
              <span class="file-chip">
                ${icon("□")}
                ${escapeHtml(file.name)}
              </span>
            `,
          )
          .join("");

  return `
    <label class="drop-zone">
      <input data-file-kind="${kind}" type="file" accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document" />
      <span class="drop-icon">${icon("↑")}</span>
      <span class="drop-title">${title}</span>
      <span class="drop-subtitle">${subtitle}</span>
      <span class="file-list">${fileList}</span>
    </label>
  `;
}

function renderAnalysis() {
  const beforeName = state.files.before[0]?.name || documentName("before") || "Редакция до";
  const afterName = state.files.after[0]?.name || documentName("after") || "Редакция после";

  return `
    <main class="app-shell analysis-shell">
      <header class="analysis-header">
        <button class="ghost-button" data-action="back">${icon("←")}Назад</button>
        <div>
          <p class="eyebrow">Результаты сравнения</p>
          <h1>${escapeHtml(summaryTitle())}</h1>
        </div>
        <div class="header-files">
          ${icon("□")}
          <span>${escapeHtml(beforeName)}</span>
          <span class="file-divider"></span>
          <span>${escapeHtml(afterName)}</span>
        </div>
      </header>

      ${renderWarnings()}

      <section class="${state.activeSource ? "workspace with-viewer" : "workspace"}">
        <aside class="category-rail" aria-label="Разделы анализа">
          ${categories.map(renderCategoryBlock).join("")}
        </aside>
        ${state.activeSource ? renderDocumentViewer() : renderEmptyDocumentState()}
      </section>
    </main>
  `;
}

function renderWarnings() {
  const warnings = state.report?.warnings || [];
  if (!warnings.length) return "";
  return `
    <section class="warnings">
      ${warnings.map((item) => `<p>${escapeHtml(item)}</p>`).join("")}
    </section>
  `;
}

function renderCategoryBlock(category) {
  const isActive = category.id === state.activeCategory;
  const findings = findingsForCategory(category);

  return `
    <div class="${isActive ? "category-group active" : "category-group"}">
      <button class="${isActive ? "category-tab active" : "category-tab"}" data-category="${category.id}">
        <span>${category.title}</span>
        <span class="category-count">${findings.length}</span>
      </button>
      ${
        isActive
          ? `
            <div class="category-details">
              <p>${category.helper}</p>
              <div class="finding-list compact">
                ${
                  findings.length
                    ? findings.map(renderFinding).join("")
                    : `<div class="empty-report">Backend не вернул выводы для этого блока.</div>`
                }
              </div>
            </div>
          `
          : ""
      }
    </div>
  `;
}

function renderFinding(finding) {
  return `
    <article class="finding-card ${severityToUi(finding.severity)}">
      <div class="finding-main">
        ${renderSeverity(finding)}
        <h3>${escapeHtml(finding.subject?.name || finding.description)}</h3>
        <p>${escapeHtml(finding.description)}</p>
      </div>
      <div class="source-list">
        ${evidenceSources(finding).map(renderSourceButton).join("")}
      </div>
    </article>
  `;
}

function renderSeverity(finding) {
  const labels = {
    info: "Найдено",
    low: "Низкий риск",
    medium: "Проверить",
    high: "Риск",
  };
  const severity = severityToUi(finding.severity);
  return `
    <span class="severity-badge ${severity}">
      ${icon("✓")}
      ${labels[finding.severity] || finding.change_type || "Вывод"}
    </span>
  `;
}

function renderSourceButton(source) {
  const encoded = encodeURIComponent(JSON.stringify(source));
  const active =
    state.activeSource &&
    state.activeSource.doc === source.doc &&
    state.activeSource.clause_id === source.clause_id &&
    state.activeSource.quote === source.quote;

  return `
    <button class="${active ? "source-pill active" : "source-pill"}" data-source="${encoded}">
      ${icon("▣")}
      ${source.doc === "before" ? "До" : "После"}, п. ${escapeHtml(source.point || "без номера")}
    </button>
  `;
}

function renderEmptyDocumentState() {
  return `
    <aside class="doc-viewer empty-viewer">
      <div class="doc-viewer-head">
        <div>
          <p class="eyebrow">Документ</p>
          <h2>Выберите источник слева</h2>
        </div>
      </div>
    </aside>
  `;
}

function renderDocumentViewer() {
  const source = state.activeSource;
  const doc = state.documents[source.doc];
  const docName = documentName(source.doc);
  const lines = doc?.clauses || [];

  return `
    <aside class="doc-viewer">
      <div class="doc-viewer-head">
        <div>
          <p class="eyebrow">${source.doc === "before" ? "Документ ДО" : "Документ ПОСЛЕ"}</p>
          <h2>${escapeHtml(docName)}</h2>
        </div>
        <button class="icon-button" data-action="close-viewer" aria-label="Закрыть просмотр документа">×</button>
      </div>

      <div class="document-page">
        <div class="document-toolbar">${icon("□")}<span>Найдено в пункте ${escapeHtml(source.point || "без номера")}</span></div>
        <div class="document-content">
          <article class="document-sheet">
            ${lines.map((line) => renderDocumentLine(line, source)).join("")}
          </article>
        </div>
      </div>
    </aside>
  `;
}

function renderDocumentLine(line, source) {
  const isActive = source.clause_id
    ? line.id === source.clause_id
    : line.number === source.point;
  const text = line.raw_text || line.normalized_text || "";
  const number = line.number || line.label || "";

  if (isSectionLike(text, number)) {
    return `<h4 class="doc-section-title">${number ? `${escapeHtml(number)}. ` : ""}${escapeHtml(text.replace(`${number}.`, "").trim())}</h4>`;
  }

  return `
    <p class="${isActive ? "document-paragraph active" : "document-paragraph"}" data-clause-id="${escapeHtml(line.id)}">
      ${number ? `<span class="doc-point">${escapeHtml(number)}.</span>` : ""}
      ${isActive ? highlightQuote(text, source.quote) : escapeHtml(text)}
    </p>
  `;
}

function highlightQuote(text, quote) {
  if (!quote) return `<mark class="text-highlight">${escapeHtml(text)}</mark>`;
  const normalizedText = text.toLowerCase();
  const normalizedQuote = quote.toLowerCase().trim();
  const index = normalizedText.indexOf(normalizedQuote);
  if (index < 0) return `<mark class="text-highlight">${escapeHtml(text)}</mark>`;
  return `${escapeHtml(text.slice(0, index))}<mark class="text-highlight">${escapeHtml(text.slice(index, index + quote.length))}</mark>${escapeHtml(text.slice(index + quote.length))}`;
}

function isSectionLike(text, number) {
  return number && /^\d+$/.test(number) && text.length < 160;
}

function canAnalyze() {
  return !state.status && state.files.before.length > 0 && state.files.after.length > 0;
}

async function startAnalysis() {
  state.error = "";
  state.status = "uploading";
  state.statusMessage = "Загружаем документы в FastAPI...";
  render();

  try {
    const form = new FormData();
    form.append("before_file", state.files.before[0]);
    form.append("after_file", state.files.after[0]);

    const upload = await apiFetch("/api/jobs", { method: "POST", body: form });
    state.jobId = upload.job_id;
    state.statusMessage = "Запускаем анализ...";
    render();

    await runAnalyze(false);
    await pollStatus();
    await loadResult();
    state.step = "analysis";
    pickInitialSource();
    state.status = null;
    state.statusMessage = "";
    render();
    scrollToActiveSource();
  } catch (error) {
    state.status = null;
    state.statusMessage = "";
    state.error = error.message || "Не удалось выполнить анализ.";
    render();
  }
}

async function runAnalyze(confirmOrder) {
  try {
    await apiFetch(`/api/jobs/${state.jobId}/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm_order: confirmOrder, force_restart: false }),
    });
  } catch (error) {
    if (error.status === 409 && window.confirm(`${error.message} Продолжить?`)) {
      await runAnalyze(true);
      return;
    }
    throw error;
  }
}

async function pollStatus() {
  while (true) {
    const status = await apiFetch(`/api/jobs/${state.jobId}`, { cache: "no-store" });
    state.status = status.status;
    state.statusMessage = `${stageLabel(status.status)} · ${Math.round(status.progress * 100)}%`;
    render();

    if (["completed", "completed_with_warnings"].includes(status.status)) return;
    if (status.status === "failed") {
      throw new Error(status.error?.message || "Backend не завершил анализ.");
    }
    await delay(3000);
  }
}

async function loadResult() {
  const [report, beforeDoc, afterDoc] = await Promise.all([
    apiFetch(`/api/jobs/${state.jobId}/result`, { cache: "no-store" }),
    apiFetch(`/api/jobs/${state.jobId}/documents/before`, { cache: "no-store" }),
    apiFetch(`/api/jobs/${state.jobId}/documents/after`, { cache: "no-store" }),
  ]);
  state.report = report;
  state.documents.before = beforeDoc;
  state.documents.after = afterDoc;
}

async function apiFetch(url, options = {}) {
  const response = await fetch(url, options);
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    const error = new Error(payload?.error?.message || `Ошибка запроса ${response.status}`);
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return payload;
}

function bindEvents() {
  document.querySelectorAll("[data-file-kind]").forEach((input) => {
    input.addEventListener("change", (event) => {
      const kind = event.currentTarget.dataset.fileKind;
      state.files[kind] = Array.from(event.currentTarget.files || []);
      state.error = "";
      render();
    });
  });

  document.querySelectorAll("[data-category]").forEach((button) => {
    button.addEventListener("click", () => {
      state.activeCategory = button.dataset.category;
      const first = findingsForCategory(categories.find((item) => item.id === state.activeCategory))[0];
      state.activeSource = first ? evidenceSources(first)[0] || null : state.activeSource;
      render();
      scrollToActiveSource();
    });
  });

  document.querySelectorAll("[data-source]").forEach((button) => {
    button.addEventListener("click", () => {
      state.activeSource = JSON.parse(decodeURIComponent(button.dataset.source));
      render();
      scrollToActiveSource();
    });
  });

  document.querySelectorAll("[data-action]").forEach((button) => {
    button.addEventListener("click", () => {
      const action = button.dataset.action;
      if (action === "analyze") startAnalysis();
      if (action === "back") {
        state.step = "upload";
        render();
      }
      if (action === "close-viewer") {
        state.activeSource = null;
        render();
      }
    });
  });
}

function findingsForCategory(category) {
  if (!category || !state.report) return [];
  if (category.types.includes("*")) return state.report.findings || [];
  return (state.report.findings || []).filter((finding) =>
    category.types.includes(finding.change_type),
  );
}

function evidenceSources(finding) {
  const before = (finding.before_evidence || [])
    .filter((item) => item.evidence_type === "presence")
    .map((item) => evidenceToSource("before", item));
  const after = (finding.after_evidence || [])
    .filter((item) => item.evidence_type === "presence")
    .map((item) => evidenceToSource("after", item));
  return [...before, ...after];
}

function evidenceToSource(doc, evidence) {
  return {
    doc,
    clause_id: evidence.clause_id,
    point: evidence.clause_number,
    quote: evidence.quote || "",
  };
}

function pickInitialSource() {
  const category = categories.find((item) => item.id === state.activeCategory);
  const finding = findingsForCategory(category)[0] || (state.report?.findings || [])[0];
  if (!finding) {
    state.activeSource = null;
    return;
  }
  state.activeSource = evidenceSources(finding)[0] || null;
}

function documentName(role) {
  return state.documents[role]?.document?.original_name;
}

function summaryTitle() {
  return state.report?.summary || "Редакция 8 против редакции 9";
}

function severityToUi(value) {
  if (value === "high") return "risk";
  if (value === "medium" || value === "low") return "warning";
  return "info";
}

function stageLabel(status) {
  const labels = {
    uploading: "Загрузка",
    ready: "Готово к анализу",
    extracting: "Извлекаем пункты",
    analyzing: "Сопоставляем функции",
    verifying: "Проверяем источники",
  };
  return labels[status] || status;
}

function delay(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function scrollToActiveSource() {
  requestAnimationFrame(() => {
    document.querySelector(".document-paragraph.active")?.scrollIntoView({ block: "center" });
  });
}

render();
