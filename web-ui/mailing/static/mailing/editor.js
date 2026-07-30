(() => {
  "use strict";

  const root = document.querySelector("[data-email-template-editor]");
  if (!root) return;

  const form = root.querySelector("[data-email-template-form]");
  const editor = root.querySelector("[data-rich-editor]");
  const hiddenInput = form.querySelector("[name='content_html']");
  const subjectInput = form.querySelector("[name='subject_template']");
  const preview = root.querySelector("[data-email-preview]");
  const previewWrap = root.querySelector("[data-preview-wrap]");
  const previewSubject = root.querySelector("[data-preview-subject]");
  const fileInputs = root.querySelector("[data-template-file-inputs]");
  const maxBytes =
    Number(root.dataset.imageMaxBytes) || 5 * 1024 * 1024;
  const totalMaxBytes =
    Number(root.dataset.totalImageMaxBytes) || 20 * 1024 * 1024;
  let savedRange = null;
  let previewTimer = null;

  const readJson = (id, fallback) => {
    try {
      return JSON.parse(document.getElementById(id).textContent);
    } catch (_error) {
      return fallback;
    }
  };

  const initialHtml = readJson("email-template-html", "");
  const defaultHtml = readJson("email-template-default-html", "");
  const defaultSubject = readJson("email-template-default-subject", "");
  editor.innerHTML = initialHtml;

  const escapeHtml = (value) =>
    String(value || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  const personalize = (value) =>
    String(value || "").replaceAll("{{ creator_name }}", "Creator One");
  const validHttpUrl = (value) => {
    try {
      const parsed = new URL(value);
      return ["http:", "https:"].includes(parsed.protocol)
        ? parsed.href
        : "";
    } catch (_error) {
      return "";
    }
  };

  const replaceExpiredUploads = () => {
    editor.querySelectorAll("img[data-upload-token]").forEach((image) => {
      if (
        image.hasAttribute("src") &&
        String(image.src || "").startsWith("blob:")
      ) {
        return;
      }
      const warning = document.createElement("span");
      warning.className = "mail-image-reselect";
      warning.contentEditable = "false";
      warning.textContent = "图片未保存，请使用工具栏重新插入";
      image.replaceWith(warning);
    });
  };
  replaceExpiredUploads();

  const saveSelection = () => {
    const selection = window.getSelection();
    if (!selection?.rangeCount) return;
    const range = selection.getRangeAt(0);
    if (editor.contains(range.commonAncestorContainer)) {
      savedRange = range.cloneRange();
    }
  };

  const restoreSelection = () => {
    editor.focus();
    if (!savedRange) return false;
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(savedRange);
    return true;
  };

  const htmlForStorage = () => {
    const clone = editor.cloneNode(true);
    clone.querySelectorAll(".mail-image-reselect").forEach((item) =>
      item.remove(),
    );
    clone.querySelectorAll("img").forEach((image) => {
      image.removeAttribute("src");
      image.removeAttribute("class");
      image.removeAttribute("style");
      image.removeAttribute("width");
      image.removeAttribute("height");
    });
    return clone.innerHTML;
  };

  const personalizedPreviewHtml = () => {
    const clone = editor.cloneNode(true);
    const walker = document.createTreeWalker(
      clone,
      NodeFilter.SHOW_TEXT,
    );
    let node = walker.nextNode();
    while (node) {
      node.nodeValue = personalize(node.nodeValue);
      node = walker.nextNode();
    }
    clone.querySelectorAll("img").forEach((image) => {
      image.alt = personalize(image.alt);
    });
    return clone.innerHTML;
  };

  const renderPreview = () => {
    previewSubject.textContent = personalize(subjectInput.value) || "（无主题）";
    const content = personalizedPreviewHtml();
    preview.srcdoc = `<!doctype html>
      <html><head><meta charset="utf-8"><base href="${location.origin}/">
      <style>
        *{box-sizing:border-box}
        body{margin:0;padding:24px 12px;background:#f5f5f5;color:#222;
          font:16px/1.65 Arial,Helvetica,sans-serif}
        .email{width:100%;max-width:680px;min-height:300px;margin:0 auto;
          padding:32px;background:#fff;border-radius:10px}
        p{margin:0 0 16px} h2{margin:24px 0 10px;font-size:22px;
          line-height:1.35} h3{margin:22px 0 8px;font-size:17px;
          line-height:1.45} ul,ol{margin:0 0 16px;padding-left:24px}
        li{margin:5px 0} blockquote{margin:16px 0;padding:10px 14px;
          border-left:3px solid #9eb7a7;color:#4d5a52}
        a{color:#604713;font-weight:bold}
        img{display:block;width:100%;max-width:680px;height:auto;
          margin:12px 0 20px;border:0;border-radius:6px}
        [data-align="center"]{text-align:center}
        [data-align="right"]{text-align:right}
        .mail-image-reselect{display:block;padding:24px;border:1px dashed #bbb;
          border-radius:6px;color:#777;text-align:center}
      </style></head><body><div class="email">${content}</div></body></html>`;
  };

  const syncAndPreview = () => {
    hiddenInput.value = htmlForStorage();
    window.clearTimeout(previewTimer);
    previewTimer = window.setTimeout(renderPreview, 80);
  };

  const runCommand = (command, value = null) => {
    restoreSelection();
    document.execCommand(command, false, value);
    saveSelection();
    syncAndPreview();
  };

  root.querySelectorAll("[data-rich-command]").forEach((button) => {
    button.addEventListener("mousedown", (event) => event.preventDefault());
    button.addEventListener("click", () =>
      runCommand(button.dataset.richCommand),
    );
  });

  root.querySelector("[data-rich-format]").addEventListener("change", (event) => {
    runCommand("formatBlock", event.target.value);
    event.target.value = "p";
  });

  root.querySelector("[data-rich-link]").addEventListener("mousedown", () => {
    saveSelection();
  });
  root.querySelector("[data-rich-link]").addEventListener("click", () => {
    const selectedText = savedRange?.toString().trim() || "";
    const enteredUrl = window.prompt("请输入完整链接地址（http/https）：", "");
    if (enteredUrl === null) return;
    const url = validHttpUrl(enteredUrl);
    if (!url) {
      window.alert("链接必须是完整的 HTTP/HTTPS 地址。");
      return;
    }
    if (selectedText) {
      restoreSelection();
      document.execCommand("createLink", false, url);
    } else {
      const label = window.prompt("请输入链接显示文字：", "查看详情");
      if (!label) return;
      restoreSelection();
      document.execCommand(
        "insertHTML",
        false,
        `<a href="${escapeHtml(url)}">${escapeHtml(label)}</a>`,
      );
    }
    saveSelection();
    syncAndPreview();
  });

  const insertImage = (file, token) => {
    restoreSelection();
    const image = document.createElement("img");
    image.dataset.uploadToken = token;
    image.src = URL.createObjectURL(file);
    image.alt = file.name.replace(/\.[^.]+$/, "") || "邮件图片";

    const selection = window.getSelection();
    if (selection?.rangeCount) {
      const range = selection.getRangeAt(0);
      range.deleteContents();
      range.insertNode(image);
      const lineBreak = document.createElement("br");
      image.after(lineBreak);
      range.setStartAfter(lineBreak);
      range.collapse(true);
      selection.removeAllRanges();
      selection.addRange(range);
    } else {
      editor.append(image);
      editor.append(document.createElement("p"));
    }
    saveSelection();
    syncAndPreview();
  };

  const imageButton = root.querySelector("[data-rich-image]");
  imageButton.addEventListener("mousedown", () => saveSelection());
  imageButton.addEventListener("click", () => {
    if (editor.querySelectorAll("img").length >= 10) {
      window.alert("邮件正文最多支持 10 张图片。");
      return;
    }
    const token =
      `upload-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
    const input = document.createElement("input");
    input.type = "file";
    input.name = `asset_${token}`;
    input.accept = "image/png,image/jpeg";
    fileInputs.append(input);
    input.addEventListener("change", () => {
      const file = input.files?.[0];
      if (!file) {
        input.remove();
        return;
      }
      if (
        !["image/png", "image/jpeg"].includes(file.type) ||
        file.size > maxBytes
      ) {
        window.alert(
          `请选择不超过 ${Math.floor(maxBytes / 1024 / 1024)} MB ` +
            "的 PNG 或 JPG 图片。",
        );
        input.remove();
        return;
      }
      const selectedBytes = Array.from(
        fileInputs.querySelectorAll("input[type='file']"),
      ).reduce(
        (total, item) => total + (item.files?.[0]?.size || 0),
        0,
      );
      if (selectedBytes > totalMaxBytes) {
        window.alert(
          `本次新上传的图片总计不能超过 ` +
            `${Math.floor(totalMaxBytes / 1024 / 1024)} MB。`,
        );
        input.remove();
        return;
      }
      insertImage(file, token);
    });
    input.click();
  });

  root
    .querySelector("[data-insert-creator-name]")
    .addEventListener("click", () => {
      const token = "{{ creator_name }}";
      const start = subjectInput.selectionStart ?? subjectInput.value.length;
      const end = subjectInput.selectionEnd ?? start;
      subjectInput.setRangeText(token, start, end, "end");
      subjectInput.dispatchEvent(new Event("input", { bubbles: true }));
      subjectInput.focus();
    });

  root.querySelector("[data-restore-default]").addEventListener("click", () => {
    if (!window.confirm("恢复为系统默认内容？保存前仍可继续修改。")) return;
    editor.querySelectorAll("img[src^='blob:']").forEach((image) =>
      URL.revokeObjectURL(image.src),
    );
    fileInputs.innerHTML = "";
    editor.innerHTML = defaultHtml;
    subjectInput.value = defaultSubject;
    savedRange = null;
    syncAndPreview();
  });

  root.querySelectorAll("[data-preview-width]").forEach((button) => {
    button.addEventListener("click", () => {
      root
        .querySelectorAll("[data-preview-width]")
        .forEach((item) => item.classList.remove("is-active"));
      button.classList.add("is-active");
      previewWrap.classList.toggle(
        "is-mobile",
        button.dataset.previewWidth === "mobile",
      );
    });
  });

  editor.addEventListener("input", syncAndPreview);
  editor.addEventListener("keyup", saveSelection);
  editor.addEventListener("mouseup", saveSelection);
  editor.addEventListener("focus", saveSelection);
  editor.addEventListener("paste", (event) => {
    event.preventDefault();
    const text = event.clipboardData.getData("text/plain");
    document.execCommand("insertText", false, text);
  });
  editor.addEventListener("drop", (event) => event.preventDefault());
  subjectInput.addEventListener("input", syncAndPreview);
  form.addEventListener("submit", () => {
    hiddenInput.value = htmlForStorage();
  });
  window.addEventListener("beforeunload", () => {
    editor.querySelectorAll("img[src^='blob:']").forEach((image) =>
      URL.revokeObjectURL(image.src),
    );
  });

  hiddenInput.value = htmlForStorage();
  renderPreview();
})();
