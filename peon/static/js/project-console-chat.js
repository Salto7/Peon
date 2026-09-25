/* Project console chat + file uploads (war-room). */
(function () {
  var _el = function (id) { return document.getElementById(id); };
  var _csrf = function () {
    return (window.PeonUI && window.PeonUI.csrfToken && window.PeonUI.csrfToken()) || "";
  };
  var _append = null;

  function el(id) { return _el(id); }
  function csrfToken() { return _csrf(); }
  function appendChatBubble(msg) {
    if (_append) return _append(msg);
    if (window.PeonProjectOps && window.PeonProjectOps.appendChatBubble) {
      return window.PeonProjectOps.appendChatBubble(msg);
    }
  }

  function useDeps(deps) {
    deps = deps || {};
    if (deps.el) _el = deps.el;
    if (deps.csrfToken) _csrf = deps.csrfToken;
    if (deps.appendChatBubble) _append = deps.appendChatBubble;
  }

  function formatBytes(n) {
    var v = Number(n) || 0;
    if (v < 1024) return v + " B";
    if (v < 1024 * 1024) return (v / 1024).toFixed(1) + " KiB";
    return (v / (1024 * 1024)).toFixed(1) + " MiB";
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function escapeAttr(s) {
    return escapeHtml(s).replace(/'/g, "&#39;");
  }

  function renderProjectFilesList(inputs) {
    var list = el("project-files-list");
    if (!list) return;
    var rows = Array.isArray(inputs) ? inputs : [];
    if (!rows.length) {
      list.innerHTML =
        '<li class="meta project-files-empty" id="project-files-empty">' +
        "No files uploaded yet — use + in Chat.</li>";
      return;
    }
    list.innerHTML = rows
      .map(function (f) {
        var name = String(f.name || "");
        var path = String(f.sandbox_path || "");
        var size = formatBytes(f.size);
        return (
          '<li class="project-file-row" data-name="' +
          escapeAttr(name) +
          '"><div class="project-file-meta"><code class="project-file-name">' +
          escapeHtml(name) +
          '</code><span class="meta">' +
          escapeHtml(size) +
          " · <code>" +
          escapeHtml(path) +
          "</code></span></div>" +
          '<button type="button" class="secondary project-file-remove" data-name="' +
          escapeAttr(name) +
          '" aria-label="Remove ' +
          escapeAttr(name) +
          '">Remove</button></li>'
        );
      })
      .join("");
  }

  function bindProjectFiles(projectPk) {
    const panel = el("project-files");
    const form = el("project-chat-form");
    const fileInput = el("project-chat-file");
    const plus = el("project-chat-plus");
    if ((!panel && !fileInput) || (panel && panel.dataset.boundFiles)) return;
    if (panel) panel.dataset.boundFiles = "1";
    else if (form) form.dataset.boundFiles = "1";

    const uploadUrl =
      (form && form.getAttribute("data-inputs-url")) ||
      (panel && panel.getAttribute("data-inputs-url")) ||
      "/projects/" + projectPk + "/inputs/";
    const deleteUrl =
      (panel && panel.getAttribute("data-inputs-delete-url")) ||
      "/projects/" + projectPk + "/inputs/delete/";

    async function uploadFiles(fileList) {
      const files = Array.from(fileList || []);
      if (!files.length) return;
      const body = new FormData();
      files.forEach(function (f) {
        body.append("uploads", f);
      });
      try {
        const res = await fetch(uploadUrl, {
          method: "POST",
          headers: {
            Accept: "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRFToken": csrfToken(),
          },
          body: body,
        });
        const data = await res.json().catch(function () {
          return null;
        });
        if (!res.ok || !data || !data.ok) {
          const err =
            (data && data.errors && data.errors.join("; ")) ||
            (data && data.error) ||
            "Upload failed";
          if (window.PeonUI) window.PeonUI.toast(err, "error");
          return;
        }
        renderProjectFilesList(data.inputs || []);
        if (window.PeonUI) {
          window.PeonUI.toast(
            "Uploaded " + ((data.saved && data.saved.length) || 0) + " file(s)",
            "ok"
          );
        }
      } catch (_) {
        if (window.PeonUI) window.PeonUI.toast("Upload failed", "error");
      }
    }

    async function removeFile(name) {
      if (!name) return;
      try {
        const res = await fetch(deleteUrl, {
          method: "POST",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRFToken": csrfToken(),
          },
          body: JSON.stringify({ name: name }),
        });
        const data = await res.json().catch(function () {
          return null;
        });
        if (!res.ok || !data || !data.ok) {
          if (window.PeonUI) {
            window.PeonUI.toast(
              (data && data.error) || "Could not remove file",
              "error"
            );
          }
          return;
        }
        renderProjectFilesList(data.inputs || []);
        if (window.PeonUI) window.PeonUI.toast("Removed " + name, "ok");
      } catch (_) {
        if (window.PeonUI) window.PeonUI.toast("Could not remove file", "error");
      }
    }

    if (plus && fileInput) {
      plus.addEventListener("click", function () {
        if (plus.disabled || fileInput.disabled) return;
        fileInput.click();
      });
    }
    if (fileInput) {
      fileInput.addEventListener("change", function () {
        const files = fileInput.files;
        uploadFiles(files).finally(function () {
          fileInput.value = "";
        });
      });
    }

    if (panel) {
      panel.addEventListener("click", function (ev) {
        const btn = ev.target.closest(".project-file-remove");
        if (!btn || !panel.contains(btn)) return;
        const name = btn.getAttribute("data-name") || "";
        if (!name) return;
        if (!window.confirm("Remove " + name + " from this project?")) return;
        removeFile(name);
      });
    }
  }

  function bindProjectChat(projectPk) {
    const form = el("project-chat-form");
    const input = el("project-chat-input");
    const send = el("project-chat-send");
    if (!form || !input || form.dataset.boundChat) return;
    form.dataset.boundChat = "1";
    const url = form.getAttribute("data-chat-url") || "/projects/" + projectPk + "/chat/";
    let chatMode = "chat";
    let replyPromptId = "";
    let selectedJobId = "";

    const modeHints = {
      chat: "Ask about the project, or describe work to replan…",
      instruct: "Force-steer running agents only…",
      replan: "Force a full replan from this prompt…",
      stop: "Optional note, then send to pause…",
      reply: "Answer the pending request…",
    };
    const modeLabels = {
      chat: "Send",
      instruct: "Instruct",
      replan: "Replan",
      stop: "Stop",
      reply: "Reply",
    };

    function setMode(mode) {
      if (mode === "reply") chatMode = "reply";
      else if (mode === "replan") chatMode = "replan";
      else if (mode === "stop") chatMode = "stop";
      else if (mode === "instruct") chatMode = "instruct";
      else chatMode = "chat";
      if (chatMode !== "reply") replyPromptId = "";
      form.querySelectorAll("[data-chat-mode]").forEach(function (btn) {
        var m = btn.getAttribute("data-chat-mode");
        btn.classList.toggle(
          "is-active",
          m === chatMode || (chatMode === "reply" && m === "chat")
        );
      });
      input.placeholder = modeHints[chatMode] || modeHints.chat;
      if (send) send.textContent = modeLabels[chatMode] || "Send";
      setBusy(false);
    }

    form.querySelectorAll("[data-chat-mode]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        setMode(btn.getAttribute("data-chat-mode") || "chat");
      });
    });

    function setBusy(busy) {
      const paused = form.dataset.disabled === "1";
      // Chat (answer) / replan / reply / stop still useful when paused; instruct needs active work.
      const block =
        !!busy ||
        (paused && chatMode === "instruct");
      input.disabled = block && chatMode !== "stop" && chatMode !== "chat";
      if (send)
        send.disabled = !!busy || (paused && chatMode === "instruct");
      setThinking(!!busy);
    }

    function setThinking(on) {
      var thread = el("chat-thread");
      if (!thread) return;
      var existing = thread.querySelector(".chat-bubble-thinking");
      if (!on) {
        if (existing) existing.remove();
        return;
      }
      if (existing) {
        thread.appendChild(existing);
        thread.scrollTop = thread.scrollHeight;
        return;
      }
      var empty = thread.querySelector(".chat-thread-empty");
      if (empty) empty.remove();
      var bubble = document.createElement("div");
      bubble.className = "chat-bubble chat-bubble-thinking";
      bubble.setAttribute("aria-live", "polite");
      bubble.setAttribute("aria-label", "Peon is thinking");
      var meta = document.createElement("div");
      meta.className = "chat-bubble-meta";
      var who = document.createElement("span");
      who.textContent = "Peon";
      meta.appendChild(who);
      var dots = document.createElement("div");
      dots.className = "chat-thinking-dots";
      dots.innerHTML = "<span></span><span></span><span></span>";
      bubble.appendChild(meta);
      bubble.appendChild(dots);
      thread.appendChild(bubble);
      thread.scrollTop = thread.scrollHeight;
    }

    window.addEventListener("peon:agent-selected", function (ev) {
      selectedJobId = (ev.detail && ev.detail.jobId) || "";
    });

    async function submit() {
      const message = (input.value || "").trim();
      if (chatMode !== "stop" && !message) return;
      if (form.dataset.disabled === "1" && chatMode === "instruct") return;
      // Show the operator line immediately, then thinking while Peon works.
      if (message) {
        appendChatBubble({
          id: "local-u-" + Date.now(),
          content: message,
          created_at: new Date().toISOString(),
          metadata: { role: "user", tag: "you", event: "console_local" },
        });
      }
      setBusy(true);
      var body = {
        message: message,
        mode: chatMode === "reply" ? "reply" : chatMode,
      };
      if (chatMode === "reply" && replyPromptId) body.prompt_id = replyPromptId;
      if (chatMode === "instruct" && selectedJobId) body.job_id = selectedJobId;
      try {
        const res = await fetch(url, {
          method: "POST",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRFToken": csrfToken(),
          },
          body: JSON.stringify(body),
        });
        const data = await res.json().catch(function () {
          return null;
        });
        if (!res.ok || !data || !data.ok) {
          setThinking(false);
          if (window.PeonUI) {
            window.PeonUI.toast(
              (data && data.error) || "Could not send message",
              "error"
            );
          }
          return;
        }
        input.value = "";
        replyPromptId = "";
        if (chatMode === "reply") setMode("chat");
        var resultMode = data.mode || chatMode;
        setThinking(false);
        if (data.reply) {
          var replyTag = "ask";
          if (
            resultMode === "replan" ||
            resultMode === "replan_from_prompt" ||
            resultMode === "instruct" ||
            resultMode === "instruct_selected" ||
            resultMode === "continue" ||
            resultMode === "reply"
          ) {
            replyTag = "steer";
          } else if (resultMode === "stop") {
            replyTag = "stop";
          }
          appendChatBubble({
            id: "local-a-" + Date.now(),
            content: data.reply,
            created_at: new Date().toISOString(),
            metadata: {
              role: "assistant",
              tag: replyTag,
              event: "console_reply",
            },
          });
        }
        if (window.PeonUI) {
          var toast = "Sent";
          if (resultMode === "stop") toast = "Engagement paused";
          else if (resultMode === "answer") toast = "Answered";
          else if (
            resultMode === "replan" ||
            resultMode === "replan_from_prompt" ||
            resultMode === "work"
          ) {
            toast =
              "Replanned — " +
              (data.objectives != null ? data.objectives : "?") +
              " objectives";
          } else if (resultMode === "reply") toast = "Reply injected into agent";
          else if (resultMode === "instruct" || resultMode === "continue") {
            toast =
              "Instructed " +
              ((data.job_ids && data.job_ids.length) || 0) +
              " agent(s)";
          }
          window.PeonUI.toast(toast, "ok");
        }
      } catch (_) {
        setThinking(false);
        if (window.PeonUI) window.PeonUI.toast("Could not send message", "error");
      } finally {
        setBusy(false);
        input.focus();
      }
    }

    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      submit();
    });
    input.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter" && !ev.shiftKey) {
        ev.preventDefault();
        submit();
      }
    });

    var answerBtn = el("hitl-answer-btn");
    if (answerBtn && !answerBtn.dataset.bound) {
      answerBtn.dataset.bound = "1";
      answerBtn.addEventListener("click", function () {
        var banner = el("hitl-banner");
        var pid = banner && banner.dataset.promptId;
        if (pid && !String(pid).startsWith("obj-")) {
          replyPromptId = pid;
          setMode("reply");
        } else {
          setMode("chat");
        }
        if (window.PeonProjectOps.showWorkspaceTab) {
          window.PeonProjectOps.showWorkspaceTab("chat");
        } else if (window.PeonProjectOps.focusChat) {
          window.PeonProjectOps.focusChat();
        } else {
          input.focus();
        }
      });
    }

    setMode("chat");
  }

  function updatePendingInputs(pending) {
    var banner = el("hitl-banner");
    var q = el("hitl-question");
    if (!banner) return;
    var rows = Array.isArray(pending) ? pending : [];
    if (!rows.length) {
      banner.hidden = true;
      banner.dataset.promptId = "";
      return;
    }
    var first = rows[0];
    banner.hidden = false;
    banner.dataset.promptId = String(first.id || "");
    if (q) {
      q.textContent = first.question || "Input required";
      if (first.job_title) {
        q.textContent = first.question + " · " + first.job_title;
      }
    }
  }

  window.PeonProjectConsoleChat = {
    bindFiles: function (projectPk, deps) {
      useDeps(deps);
      bindProjectFiles(projectPk);
    },
    bindChat: function (projectPk, deps) {
      useDeps(deps);
      bindProjectChat(projectPk);
    },
    updatePendingInputs: function (pending, deps) {
      useDeps(deps);
      updatePendingInputs(pending);
    },
  };
})();
