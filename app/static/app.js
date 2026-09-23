const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function formatSize(bytes) {
  if (!Number.isFinite(bytes)) return "";
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} КБ`;
  return `${(bytes / 1024 / 1024).toFixed(1)} МБ`;
}

function setFile(input, file) {
  const transfer = new DataTransfer();
  if (file) transfer.items.add(file);
  input.files = transfer.files;
  input.dispatchEvent(new Event("change"));
}

function initUpload() {
  const form = $("#upload-form");
  if (!form) return;
  const inputs = {
    before_file: $("#before-file"),
    after_file: $("#after-file"),
  };

  Object.entries(inputs).forEach(([name, input]) => {
    input.addEventListener("change", () => {
      const file = input.files[0];
      $(`[data-file-meta="${name}"]`).textContent = file
        ? `${file.name} · ${formatSize(file.size)}`
        : "Файл не выбран";
      $(`[data-error-for="${name}"]`).textContent = "";
      input.closest(".file-card").classList.toggle("has-file", Boolean(file));
    });
  });

  $("#swap-files").addEventListener("click", () => {
    const before = inputs.before_file.files[0];
    const after = inputs.after_file.files[0];
    setFile(inputs.before_file, after);
    setFile(inputs.after_file, before);
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = $("#start-analysis");
    const formError = $("#form-error");
    formError.textContent = "";
    $$(".field-error").forEach((node) => { node.textContent = ""; });
    let valid = true;
    Object.entries(inputs).forEach(([name, input]) => {
      if (!input.files[0]) {
        $(`[data-error-for="${name}"]`).textContent = "Выберите файл DOCX.";
        valid = false;
      }
    });
    if (!valid) return;

    button.disabled = true;
    button.textContent = "Загружаем…";
    try {
      const upload = await fetch("/api/jobs", { method: "POST", body: new FormData(form) });
      const payload = await upload.json();
      if (!upload.ok) {
        const field = payload.error?.field;
        if (field && $(`[data-error-for="${field}"]`)) {
          $(`[data-error-for="${field}"]`).textContent = payload.error.message;
        } else {
          formError.textContent = payload.error?.message || "Не удалось загрузить документы.";
        }
        return;
      }
      let analyze = await fetch(`/api/jobs/${payload.job_id}/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm_order: false, force_restart: false }),
      });
      if (analyze.status === 409) {
        const reversed = window.confirm("Похоже, редакции загружены в обратном порядке. Продолжить в этом порядке?");
        if (!reversed) return;
        analyze = await fetch(`/api/jobs/${payload.job_id}/analyze`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ confirm_order: true, force_restart: false }),
        });
      }
      const analysisPayload = await analyze.json();
      if (!analyze.ok) throw new Error(analysisPayload.error?.message || "Анализ не запущен.");
      window.location.assign(`/status/${payload.job_id}`);
    } catch (error) {
      formError.textContent = error.message || "Сервис временно недоступен.";
    } finally {
      button.disabled = false;
      button.innerHTML = "Запустить анализ <span>→</span>";
    }
  });
}

function initStatus() {
  const page = $("[data-status-page]");
  if (!page) return;
  const jobId = page.dataset.jobId;
  const stageCopy = {
    extracting: ["Извлекаем разделы и пункты", "Подготавливаем устойчивые источники для сравнения."],
    analyzing: ["Сопоставляем функции", "Ищем смысловые изменения и осторожно оцениваем риски."],
    verifying: ["Проверяем доказательства", "Восстанавливаем цитаты только из исходных документов."],
  };
  let timer;

  async function poll() {
    try {
      const response = await fetch(`/api/jobs/${jobId}`, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error?.message || "Статус недоступен.");
      const progress = Math.round(data.progress * 100);
      $("#progress-bar").style.width = `${progress}%`;
      $("#progress-value").textContent = `${progress}%`;
      $("#stage-label").textContent = data.stage;
      const copy = stageCopy[data.status];
      if (copy) {
        $("#status-title").textContent = copy[0];
        $("#status-description").textContent = copy[1];
      }
      const stages = ["extracting", "analyzing", "verifying"];
      const activeIndex = Math.max(0, stages.indexOf(data.status));
      $$('[data-stage-item]').forEach((item, index) => {
        item.classList.toggle("active", index === activeIndex);
        item.classList.toggle("done", index < activeIndex);
      });
      if (["completed", "completed_with_warnings"].includes(data.status)) {
        window.location.replace(`/result/${jobId}`);
        return;
      }
      if (data.status === "failed") {
        showFailure(data.error?.message || "Не удалось завершить анализ.");
        return;
      }
      timer = window.setTimeout(poll, 1200);
    } catch (error) {
      showFailure(error.message);
    }
  }

  function showFailure(message) {
    window.clearTimeout(timer);
    $("#status-error-message").textContent = message;
    $("#status-error").hidden = false;
    $(".pulse-mark").classList.add("failed");
  }

  $("#retry-analysis").addEventListener("click", async () => {
    $("#status-error").hidden = true;
    const response = await fetch(`/api/jobs/${jobId}/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm_order: true, force_restart: true }),
    });
    if (response.ok) poll();
    else {
      const data = await response.json();
      showFailure(data.error?.message || "Повторный запуск не удался.");
    }
  });
  poll();
}

function initFilters() {
  if (!$("[data-result-page]")) return;
  const change = $("#change-filter");
  const severity = $("#severity-filter");
  const cards = $$(".finding-card");
  function apply() {
    let visible = 0;
    cards.forEach((card) => {
      const show = (!change.value || card.dataset.changeType === change.value)
        && (!severity.value || card.dataset.severity === severity.value);
      card.hidden = !show;
      if (show) visible += 1;
    });
    $("#visible-count").textContent = `Показано: ${visible}`;
  }
  change.addEventListener("change", apply);
  severity.addEventListener("change", apply);
}

document.addEventListener("DOMContentLoaded", () => {
  initUpload();
  initStatus();
  initFilters();
});
