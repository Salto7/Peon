(function () {
  const form = document.getElementById("skill-upload-form");
  const zone = document.getElementById("skill-drop");
  const picker = document.getElementById("skill-files");
  if (!form || !zone || !picker) return;

  function readEntries(reader) {
    return new Promise(function (resolve, reject) {
      reader.readEntries(resolve, reject);
    });
  }

  function fileOf(entry) {
    return new Promise(function (resolve, reject) {
      entry.file(resolve, reject);
    });
  }

  async function walk(entry, prefix, out) {
    if (entry.isFile) {
      const file = await fileOf(entry);
      out.push({ file: file, path: prefix + file.name });
      return;
    }
    if (!entry.isDirectory) return;
    const reader = entry.createReader();
    const next = prefix + entry.name + "/";
    for (;;) {
      const batch = await readEntries(reader);
      if (!batch.length) break;
      for (const child of batch) await walk(child, next, out);
    }
  }

  function submitPairs(pairs) {
    const body = new FormData();
    const token = form.querySelector("[name=csrfmiddlewaretoken]");
    if (token) body.append("csrfmiddlewaretoken", token.value);
    pairs.forEach(function (pair) {
      body.append("file", pair.file, pair.file.name);
      body.append("path", pair.path);
    });
    fetch(form.action, { method: "POST", body: body, credentials: "same-origin" })
      .then(function (response) {
        window.location = response.url || form.action;
      })
      .catch(function () {
        window.location.reload();
      });
  }

  picker.addEventListener("change", function () {
    const pairs = Array.from(picker.files || []).map(function (file) {
      return { file: file, path: file.webkitRelativePath || file.name };
    });
    if (pairs.length) submitPairs(pairs);
  });

  zone.addEventListener("dragover", function (event) {
    event.preventDefault();
    zone.classList.add("is-over");
  });
  zone.addEventListener("dragleave", function () {
    zone.classList.remove("is-over");
  });
  zone.addEventListener("drop", async function (event) {
    event.preventDefault();
    zone.classList.remove("is-over");
    const items = Array.from(event.dataTransfer.items || []);
    const pairs = [];
    for (const item of items) {
      const entry = item.webkitGetAsEntry && item.webkitGetAsEntry();
      if (entry) {
        await walk(entry, "", pairs);
        continue;
      }
      const file = item.getAsFile && item.getAsFile();
      if (file) pairs.push({ file: file, path: file.name });
    }
    if (!pairs.length) {
      Array.from(event.dataTransfer.files || []).forEach(function (file) {
        pairs.push({ file: file, path: file.name });
      });
    }
    if (pairs.length) submitPairs(pairs);
  });
})();
