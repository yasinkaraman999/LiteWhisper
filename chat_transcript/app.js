/* Chat transcript renderer.
 *
 * Runs inside a local, offline WKWebView loaded from this bundle — no
 * network access, no remote content. Python drives it entirely through the
 * window.LW.* functions below (see chat_window.py's _webview_* helpers);
 * this file talks back to Python only through window.webkit.messageHandlers
 * .bridge.postMessage() for things a native button can't do from inside the
 * page (copy to the real system pasteboard, regenerate/edit a message,
 * like/dislike, retry).
 */
(function () {
  "use strict";

  var VISIBLE_LINES = 15; // matches .code-block.collapsed .code-scroll max-height in style.css
  var INITIAL_MESSAGE_BATCH = 80;
  var LOAD_MORE_BATCH = 60;

  // While a reply is actively streaming in, updateStreaming() replaces the
  // whole message body's innerHTML on every chunk — any fold the user just
  // clicked open would get overwritten by the very next token and snap back
  // shut. Simplest fix: don't collapse code blocks at all until the reply
  // has finished, so there's nothing to lose in the first place.
  var STREAMING_ACTIVE = false;

  var md = window.markdownit({
    html: false, // never trust/emit raw HTML from a model reply
    linkify: true,
    breaks: false,
    typographer: false,
  });
  md.use(window.markdownitTaskLists, { enabled: true, label: true });

  md.renderer.rules.fence = function (tokens, idx) {
    var token = tokens[idx];
    var info = (token.info || "").trim();
    var lang = info.split(/\s+/)[0] || "";
    var code = token.content.replace(/\n$/, "");
    var highlighted;
    try {
      highlighted =
        lang && window.hljs.getLanguage(lang)
          ? window.hljs.highlight(code, { language: lang }).value
          : window.hljs.highlightAuto(code).value;
    } catch (e) {
      highlighted = escapeHtml(code);
    }
    var lineCount = code.length ? code.split("\n").length : 0;
    var collapsible = !STREAMING_ACTIVE && lineCount > VISIBLE_LINES;
    var hidden = Math.max(0, lineCount - VISIBLE_LINES);
    var label = lang || "plain text";

    var html =
      '<div class="code-block' + (collapsible ? " collapsed" : "") + '">' +
      '<div class="code-header"><span>' + escapeHtml(label) + "</span>" +
      '<button class="code-copy" data-action="copy-code">' + copyIconSvg() + "<span>Kopyala</span></button></div>" +
      '<div class="code-scroll"><pre><code class="hljs">' + highlighted + "</code></pre></div>";
    if (collapsible) {
      html +=
        '<button class="code-fold" data-action="toggle-fold" data-hidden="' + hidden + '">' +
        "... " + hidden + " satır daha</button>";
    }
    html += "</div>";
    return html;
  };

  // ------------------------------------------------------------- state

  var messageStore = new Map(); // id (string) -> message object, for actions
  var renderCache = new Map(); // id (string) -> rendered inner HTML, memoized
  var allMessages = []; // full ordered list for the current conversation
  var renderedFrom = 0; // allMessages[renderedFrom:] is currently in the DOM

  var scrollRoot = document.getElementById("scroll-root");
  var messagesEl = document.getElementById("messages");
  var jumpBtn = document.getElementById("jump-bottom");
  var atBottom = true;
  var suppressScrollCheck = false;

  function checkAtBottom() {
    if (suppressScrollCheck) return;
    var distance = scrollRoot.scrollHeight - scrollRoot.scrollTop - scrollRoot.clientHeight;
    atBottom = distance < 80;
    jumpBtn.classList.toggle("show", !atBottom);
  }
  scrollRoot.addEventListener("scroll", checkAtBottom, { passive: true });

  jumpBtn.addEventListener("click", function () {
    scrollRoot.scrollTo({ top: scrollRoot.scrollHeight, behavior: "smooth" });
  });

  function scrollToBottomInstant() {
    suppressScrollCheck = true;
    scrollRoot.scrollTop = scrollRoot.scrollHeight;
    atBottom = true;
    jumpBtn.classList.remove("show");
    requestAnimationFrame(function () {
      suppressScrollCheck = false;
    });
  }

  function maybeAutoScroll() {
    if (atBottom) {
      scrollRoot.scrollTop = scrollRoot.scrollHeight;
    } else {
      jumpBtn.classList.add("show");
    }
  }

  // ---------------------------------------------------------- utilities

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function post(type, payload) {
    try {
      var msg = Object.assign({ type: type }, payload || {});
      window.webkit.messageHandlers.bridge.postMessage(msg);
    } catch (e) {
      /* not running inside the app's WKWebView (e.g. opened in a plain
         browser for style work) — no bridge, nothing to do */
    }
  }

  function renderMath(el) {
    if (window.renderMathInElement) {
      try {
        window.renderMathInElement(el, {
          delimiters: [
            { left: "$$", right: "$$", display: true },
            { left: "\\[", right: "\\]", display: true },
            { left: "$", right: "$", display: false },
            { left: "\\(", right: "\\)", display: false },
          ],
          throwOnError: false,
        });
      } catch (e) {}
    }
  }

  function renderMarkdownCached(id, text) {
    var key = String(id);
    if (id !== "streaming" && renderCache.has(key)) {
      return renderCache.get(key);
    }
    var html = md.render(text || "");
    if (id !== "streaming") renderCache.set(key, html);
    return html;
  }

  // -------------------------------------------------------------- icons

  function copyIconSvg() {
    return '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><rect x="5.5" y="5.5" width="8" height="8" rx="1.5"/><path d="M3.5 10.5V3a1 1 0 0 1 1-1H10"/></svg>';
  }
  function regenIconSvg() {
    return '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M13 8A5 5 0 1 1 11.5 4.3"/><path d="M13 2.5V5.5H10"/></svg>';
  }
  function editIconSvg() {
    return '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M11 2.5l2.5 2.5L6 12.5H3.5V10z"/></svg>';
  }
  function likeIconSvg() {
    return '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 7v6H3V7h2zm0 0l2.5-4.5c.3-.5 1-.6 1.4-.1.3.3.4.8.2 1.2L8 6h4a1.3 1.3 0 0 1 1.2 1.8l-1.6 4.4A1.5 1.5 0 0 1 10.2 13H5"/></svg>';
  }
  function dislikeIconSvg() {
    return '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" transform="rotate(180 8 8)"><path d="M5 7v6H3V7h2zm0 0l2.5-4.5c.3-.5 1-.6 1.4-.1.3.3.4.8.2 1.2L8 6h4a1.3 1.3 0 0 1 1.2 1.8l-1.6 4.4A1.5 1.5 0 0 1 10.2 13H5"/></svg>';
  }
  function errorIconSvg() {
    return '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><circle cx="8" cy="8" r="6.3"/><path d="M8 5.2v3.6" stroke-linecap="round"/><circle cx="8" cy="11" r="0.7" fill="currentColor" stroke="none"/></svg>';
  }
  function chevronSvg() {
    return '<svg class="chev" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3l5 5-5 5"/></svg>';
  }
  function fileIconSvg() {
    return '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3"><path d="M4 2h5l3 3v9H4z"/><path d="M9 2v3h3"/></svg>';
  }

  // --------------------------------------------------------- row builder

  function actionButton(action, svg, title) {
    var b = document.createElement("button");
    b.className = "action-btn " + action;
    b.dataset.action = action;
    b.title = title;
    b.innerHTML = svg;
    return b;
  }

  function buildActionBar(msg) {
    var bar = document.createElement("div");
    bar.className = "msg-actions";
    bar.appendChild(actionButton("copy", copyIconSvg(), "Kopyala"));

    if (msg.role === "assistant") {
      bar.appendChild(actionButton("regenerate", regenIconSvg(), "Yeniden üret"));
      bar.appendChild(actionButton("like", likeIconSvg(), "Beğen"));
      bar.appendChild(actionButton("dislike", dislikeIconSvg(), "Beğenme"));
    } else {
      bar.appendChild(actionButton("edit", editIconSvg(), "Düzenle"));
    }
    return bar;
  }

  function buildAttachments(images) {
    var wrap = document.createElement("div");
    wrap.className = "attachments";
    (images || []).forEach(function (img) {
      var el = document.createElement("img");
      el.className = "attach-thumb";
      el.src = img.data;
      el.alt = img.name || "attachment";
      wrap.appendChild(el);
    });
    return wrap;
  }

  function buildFileCard(file) {
    var wrap = document.createElement("div");
    wrap.className = "attach-file";
    var previewHtml = "";
    if (file.preview) {
      previewHtml = '<div class="file-preview">' + escapeHtml(file.preview) + "</div>";
    }
    wrap.innerHTML =
      '<div class="file-icon">' + fileIconSvg() + "</div>" +
      '<div class="file-meta"><div class="file-name">' + escapeHtml(file.name || "dosya") + "</div>" +
      '<div class="file-size">' + escapeHtml(file.size || "") + "</div></div>" +
      previewHtml;
    return wrap;
  }

  function buildErrorCard(msg) {
    var wrap = document.createElement("div");
    wrap.className = "error-card";
    wrap.innerHTML =
      '<span class="error-icon">' + errorIconSvg() + "</span>" +
      '<span class="error-text">' + escapeHtml(msg.error) + "</span>" +
      '<button class="retry-btn" data-action="retry">Tekrar dene</button>';
    return wrap;
  }

  function buildReasoning(text) {
    var wrap = document.createElement("div");
    wrap.className = "reasoning";
    wrap.innerHTML =
      '<button class="reasoning-toggle" data-action="toggle-reasoning">' +
      chevronSvg() + "<span>Düşünme sürecini göster</span></button>" +
      '<div class="reasoning-body">' + escapeHtml(text) + "</div>";
    return wrap;
  }

  function buildToolCard(tool) {
    var wrap = document.createElement("div");
    wrap.className = "tool-card";
    var params = "";
    try {
      params = typeof tool.params === "string" ? tool.params : JSON.stringify(tool.params, null, 2);
    } catch (e) {
      params = String(tool.params || "");
    }
    var resultHtml = tool.result
      ? '<div class="tool-result">' + escapeHtml(tool.result) + "</div>"
      : "";
    wrap.innerHTML =
      '<div class="tool-head" data-action="toggle-tool">' + chevronSvg() +
      "<span>" + escapeHtml(tool.name || "tool") + "()</span></div>" +
      '<div class="tool-params">' + escapeHtml(params) + "</div>" + resultHtml;
    return wrap;
  }

  function buildRow(msg) {
    messageStore.set(String(msg.id), msg);

    var row = document.createElement("div");
    row.className = "msg-row " + msg.role;
    row.dataset.id = String(msg.id);

    if (msg.role === "assistant") {
      var av = document.createElement("div");
      av.className = "avatar";
      av.textContent = "A";
      row.appendChild(av);
    }

    var col = document.createElement("div");
    col.className = "msg-col";

    if (msg.images && msg.images.length) {
      col.appendChild(buildAttachments(msg.images));
    }
    if (msg.files && msg.files.length) {
      msg.files.forEach(function (f) {
        col.appendChild(buildFileCard(f));
      });
    }

    if (msg.reasoning) {
      col.appendChild(buildReasoning(msg.reasoning));
    }

    if (msg.error) {
      col.appendChild(buildErrorCard(msg));
    } else if (msg.text || msg.streaming) {
      var shell = document.createElement("div");
      shell.className = msg.role === "user" ? "bubble bubble-shell" : "bubble-shell";
      var body = document.createElement("div");
      body.className = "msg-body";
      if (msg.streaming && !msg.text) {
        body.innerHTML = '<div class="stream-indicator"><span></span><span></span><span></span></div>';
      } else {
        body.innerHTML = renderMarkdownCached(msg.id, msg.text) + (msg.streaming ? '<span class="typing-cursor"></span>' : "");
        renderMath(body);
      }
      shell.appendChild(body);
      col.appendChild(shell);
    }

    if (msg.tool_calls && msg.tool_calls.length) {
      msg.tool_calls.forEach(function (t) {
        col.appendChild(buildToolCard(t));
      });
    }

    if (msg.time && !msg.streaming) {
      var meta = document.createElement("div");
      meta.className = "msg-meta";
      meta.textContent = msg.time;
      col.appendChild(meta);
    }

    if (!msg.streaming) {
      col.appendChild(buildActionBar(msg));
    }

    row.appendChild(col);
    return row;
  }

  function updateStreamingRow(text) {
    var row = messagesEl.querySelector('.msg-row[data-id="streaming"]');
    if (!row) return;
    var body = row.querySelector(".msg-body");
    if (!body) return;
    body.innerHTML = renderMarkdownCached("streaming", text) + '<span class="typing-cursor"></span>';
    renderMath(body);
  }

  // --------------------------------------------------------- edit mode

  function beginEdit(msg) {
    var row = messagesEl.querySelector('.msg-row[data-id="' + msg.id + '"]');
    if (!row) return;
    var shell = row.querySelector(".bubble-shell");
    if (!shell) return;
    var original = shell.innerHTML;

    shell.innerHTML = "";
    var ta = document.createElement("textarea");
    ta.className = "edit-textarea";
    ta.value = msg.text || "";
    ta.rows = Math.min(12, Math.max(2, (msg.text || "").split("\n").length + 1));

    var actions = document.createElement("div");
    actions.className = "edit-actions";
    var cancel = document.createElement("button");
    cancel.className = "edit-cancel";
    cancel.textContent = "Vazgeç";
    cancel.addEventListener("click", function () {
      shell.innerHTML = original;
    });
    var save = document.createElement("button");
    save.className = "edit-save";
    save.textContent = "Kaydet ve gönder";
    save.addEventListener("click", function () {
      var text = ta.value.trim();
      if (text) post("edit", { id: msg.id, text: text });
    });

    actions.appendChild(cancel);
    actions.appendChild(save);
    shell.appendChild(ta);
    shell.appendChild(actions);
    ta.focus();
  }

  // ------------------------------------------------------------- copy

  function copyText(text, btn) {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand("copy");
    } catch (e) {}
    document.body.removeChild(ta);
    flashCopied(btn);
  }

  function flashCopied(btn) {
    if (!btn) return;
    var rect = btn.getBoundingClientRect();
    var toast = document.createElement("div");
    toast.className = "copy-toast";
    toast.textContent = "Kopyalandı";
    toast.style.position = "fixed";
    toast.style.left = rect.left + "px";
    toast.style.top = rect.top - 24 + "px";
    document.body.appendChild(toast);
    requestAnimationFrame(function () {
      toast.classList.add("show");
    });
    setTimeout(function () {
      toast.classList.remove("show");
      setTimeout(function () {
        toast.remove();
      }, 200);
    }, 1800);
  }

  // ------------------------------------------------------------ lightbox

  var lightbox = document.getElementById("lightbox");
  if (!lightbox) {
    lightbox = document.createElement("div");
    lightbox.id = "lightbox";
    lightbox.innerHTML = "<img>";
    document.body.appendChild(lightbox);
  }
  function openLightbox(src) {
    lightbox.querySelector("img").src = src;
    lightbox.classList.add("show");
  }
  function closeLightbox() {
    lightbox.classList.remove("show");
  }
  lightbox.addEventListener("click", closeLightbox);

  // -------------------------------------------------------- delegation

  document.body.addEventListener("click", function (e) {
    var actionBtn = e.target.closest(".action-btn");
    if (actionBtn) {
      var row = actionBtn.closest(".msg-row");
      var id = row && row.dataset.id;
      var msg = messageStore.get(id);
      var action = actionBtn.dataset.action;
      if (action === "copy") {
        copyText((msg && msg.text) || "", actionBtn);
      } else if (action === "regenerate") {
        post("regenerate", { id: id });
      } else if (action === "edit") {
        if (msg) beginEdit(msg);
      } else if (action === "like" || action === "dislike") {
        var active = actionBtn.classList.toggle("active");
        var siblingSelector = action === "like" ? ".dislike" : ".like";
        var sibling = row.querySelector(".action-btn" + siblingSelector);
        if (sibling) sibling.classList.remove("active");
        post("feedback", { id: id, value: active ? action : null });
      }
      return;
    }

    var copyCodeBtn = e.target.closest('[data-action="copy-code"]');
    if (copyCodeBtn) {
      var block = copyCodeBtn.closest(".code-block");
      var codeEl = block && block.querySelector("code");
      copyText(codeEl ? codeEl.textContent : "", copyCodeBtn);
      return;
    }

    var foldBtn = e.target.closest('[data-action="toggle-fold"]');
    if (foldBtn) {
      var codeBlock = foldBtn.closest(".code-block");
      var collapsed = codeBlock.classList.toggle("collapsed");
      foldBtn.textContent = collapsed ? "... " + foldBtn.dataset.hidden + " satır daha" : "Daralt";
      return;
    }

    var reasonToggle = e.target.closest('[data-action="toggle-reasoning"]');
    if (reasonToggle) {
      reasonToggle.closest(".reasoning").classList.toggle("open");
      return;
    }

    var toolHead = e.target.closest('[data-action="toggle-tool"]');
    if (toolHead) {
      toolHead.closest(".tool-card").classList.toggle("open");
      return;
    }

    var retryBtn = e.target.closest('[data-action="retry"]');
    if (retryBtn) {
      var errRow = retryBtn.closest(".msg-row");
      post("retry", { id: errRow && errRow.dataset.id });
      return;
    }

    var thumb = e.target.closest(".attach-thumb");
    if (thumb) {
      openLightbox(thumb.src);
      return;
    }

    var loadMoreBtn = e.target.closest("#load-more");
    if (loadMoreBtn) {
      renderOlderBatch();
      return;
    }
  });

  // ------------------------------------------------- lazy older-message load

  function renderOlderBatch() {
    var existingBtn = document.getElementById("load-more");
    var from = Math.max(0, renderedFrom - LOAD_MORE_BATCH);
    var batch = allMessages.slice(from, renderedFrom);
    var frag = document.createDocumentFragment();
    batch.forEach(function (m) {
      frag.appendChild(buildRow(m));
    });

    var previousHeight = scrollRoot.scrollHeight;
    if (existingBtn) existingBtn.remove();
    messagesEl.insertBefore(frag, messagesEl.firstChild);
    if (from > 0) {
      messagesEl.insertBefore(buildLoadMoreButton(from), messagesEl.firstChild);
    }
    renderedFrom = from;
    // Keep the viewport pinned to the same content instead of jumping to
    // the top now that taller content sits above it.
    scrollRoot.scrollTop += scrollRoot.scrollHeight - previousHeight;
  }

  function buildLoadMoreButton(remaining) {
    var btn = document.createElement("button");
    btn.id = "load-more";
    btn.className = "code-fold";
    btn.style.borderRadius = "10px";
    btn.style.border = "1px solid var(--color-border)";
    btn.style.marginBottom = "8px";
    btn.textContent = "Daha eski " + Math.min(remaining, LOAD_MORE_BATCH) + " mesajı göster";
    return btn;
  }

  // ------------------------------------------------------------------ API

  window.LW = {
    setTheme: function (name) {
      document.documentElement.setAttribute("data-theme", name || "");
    },

    setMessages: function (list) {
      messagesEl.innerHTML = "";
      renderCache.clear();
      messageStore.clear();
      allMessages = list || [];

      if (!allMessages.length) {
        messagesEl.innerHTML = '<div class="empty-state">Henüz mesaj yok. Hızlı bir soru sorun ya da mikrofonla söyleyin.</div>';
        renderedFrom = 0;
        scrollToBottomInstant();
        return;
      }

      renderedFrom = Math.max(0, allMessages.length - INITIAL_MESSAGE_BATCH);
      var frag = document.createDocumentFragment();
      if (renderedFrom > 0) {
        frag.appendChild(buildLoadMoreButton(renderedFrom));
      }
      allMessages.slice(renderedFrom).forEach(function (m) {
        frag.appendChild(buildRow(m));
      });
      messagesEl.appendChild(frag);
      scrollToBottomInstant();
    },

    appendMessage: function (msg) {
      var empty = messagesEl.querySelector(".empty-state");
      if (empty) empty.remove();
      allMessages.push(msg);
      renderedFrom = Math.min(renderedFrom, allMessages.length - 1);
      messagesEl.appendChild(buildRow(msg));
      maybeAutoScroll();
    },

    startStreaming: function () {
      STREAMING_ACTIVE = true;
      var placeholder = { id: "streaming", role: "assistant", text: "", streaming: true };
      messagesEl.appendChild(buildRow(placeholder));
      atBottom = true;
      scrollRoot.scrollTop = scrollRoot.scrollHeight;
    },

    updateStreaming: function (text) {
      updateStreamingRow(text);
      maybeAutoScroll();
    },

    endStreaming: function (finalMessage) {
      STREAMING_ACTIVE = false;
      var row = messagesEl.querySelector('.msg-row[data-id="streaming"]');
      if (row) row.remove();
      if (finalMessage) {
        window.LW.appendMessage(finalMessage);
      }
    },

    setStreamError: function (message, meta) {
      STREAMING_ACTIVE = false;
      var row = messagesEl.querySelector('.msg-row[data-id="streaming"]');
      if (row) row.remove();
      var errMsg = Object.assign({ id: "err-" + Date.now(), role: "assistant", error: message }, meta || {});
      window.LW.appendMessage(errMsg);
    },

    clear: function () {
      window.LW.setMessages([]);
    },

    scrollToBottom: function () {
      scrollToBottomInstant();
    },
  };
})();
