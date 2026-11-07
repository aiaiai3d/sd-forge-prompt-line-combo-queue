// Prompt Line Combo Queue (行組み合わせ予約マネージャ) — client side
// - line-number gutters on the Prompt / Negative prompt fields
// - queue list view (enable / reorder / delete), .txt save, drag & drop load
// - moves the panel right under the prompt fields
(function () {
    "use strict";

    const PROMPT_FIELDS = [
        ["txt2img_prompt", ""],
        ["txt2img_neg_prompt", "n"],
        ["img2img_prompt", ""],
        ["img2img_neg_prompt", "n"],
    ];
    const SIZE_RE = /^(\d+)\s*[x×*]\s*(\d+)$/i;
    const LS_KEY = "lcq_show_line_numbers";
    const LS_DOCK = "lcq_dock_under_prompt";

    const app = () => (typeof gradioApp === "function" ? gradioApp() : document);
    const byId = (id) => (app().getElementById ? app().getElementById(id) : app().querySelector("#" + id));

    function lsGet(key, dflt) {
        try {
            const v = localStorage.getItem(key);
            return v === null ? dflt : v === "1";
        } catch (_) {
            return dflt;
        }
    }
    function lsSet(key, v) {
        try { localStorage.setItem(key, v ? "1" : "0"); } catch (_) {}
    }

    let showNumbers = lsGet(LS_KEY, true);
    let dockOn = lsGet(LS_DOCK, true);

    // ------------------------------------------------------------ i18n

    let dict = null;
    function t(s, params) {
        if (dict === null) {
            const el = byId("plcq_i18n"); // rendered by Python with the active language's table
            if (el) {
                try { dict = JSON.parse(el.dataset.dict || "{}"); } catch (_) { dict = {}; }
            }
        }
        return fmt((dict && dict[s]) || s, params);
    }
    function fmt(s, params) {
        return params ? s.replace(/\{(\w+)\}/g, (m, k) => (k in params ? params[k] : m)) : s;
    }

    function fireInput(el) {
        if (typeof updateInput === "function") return updateInput(el);
        const e = new Event("input", { bubbles: true });
        Object.defineProperty(e, "target", { value: el });
        el.dispatchEvent(e);
    }

    function escapeHtml(s) {
        return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
    }

    // ------------------------------------------------------------ combo highlight

    function comboTextarea() {
        return byId("lcq_combo")?.querySelector("textarea") || null;
    }

    /** Every line the combo row under the caret (or the first row) can use. */
    function usedLines() {
        const ta = comboTextarea();
        const used = { "": new Set(), n: new Set() };
        if (!ta) return used;
        const rows = ta.value.split("\n");
        let idx = 0;
        if (document.activeElement === ta) idx = ta.value.slice(0, ta.selectionStart).split("\n").length - 1;
        let row = (rows[idx] || "").replace(/\s+/g, "");
        if (!row && idx !== 0) row = (rows[0] || "").replace(/\s+/g, "");
        row = row.replace(/:\d*$/, "");
        const re = /(n?)(\d+)(?:-(n?)(\d+))?/g;
        let m;
        while ((m = re.exec(row))) {
            const neg = m[1] === "n";
            const a = parseInt(m[2], 10);
            const b = m[4] !== undefined ? parseInt(m[4], 10) : a;
            if (m[4] !== undefined && (m[3] === "n") !== neg && m[3] !== "") continue;
            const lo = Math.min(a, b), hi = Math.min(Math.max(a, b), lo + 500);
            for (let i = lo; i <= hi; i++) if (i > 0) used[neg ? "n" : ""].add(i);
        }
        return used;
    }

    // ------------------------------------------------------------ line numbers

    let mirror = null;
    function getMirror() {
        if (!mirror) {
            mirror = document.createElement("div");
            mirror.setAttribute("aria-hidden", "true");
            mirror.style.cssText = "position:absolute;left:-99999px;top:0;visibility:hidden;pointer-events:none;";
            document.body.appendChild(mirror);
        }
        return mirror;
    }

    class Gutter {
        constructor(ta, prefix) {
            this.ta = ta;
            this.prefix = prefix;
            this.sig = "";
            this.origPad = parseFloat(getComputedStyle(ta).paddingLeft) || 0;
            this.el = document.createElement("div");
            this.el.className = "lcq-gutter";
            this.el.setAttribute("aria-hidden", "true");
            this.inner = document.createElement("div");
            this.inner.className = "lcq-gutter-inner";
            this.el.appendChild(this.inner);

            const host = ta.parentElement;
            if (getComputedStyle(host).position === "static") host.style.position = "relative";
            host.appendChild(this.el);

            const redraw = () => this.render();
            ["input", "keyup", "click", "focus", "blur", "select"].forEach((ev) => ta.addEventListener(ev, redraw));
            ta.addEventListener("scroll", () => this.syncScroll());
            if (window.ResizeObserver) new ResizeObserver(() => { this.sig = ""; this.render(); }).observe(ta);
        }

        alive() {
            return this.ta.isConnected;
        }

        render() {
            const ta = this.ta;
            if (!showNumbers) {
                this.el.style.display = "none";
                ta.style.paddingLeft = "";
                ta.classList.remove("lcq-numbered");
                this.sig = "";
                return;
            }
            if (ta.offsetParent === null) return; // hidden tab: draw when visible

            const cs = getComputedStyle(ta);
            const fontSize = parseFloat(cs.fontSize) || 14;
            const gw = Math.round(fontSize * (this.prefix ? 3.2 : 2.8));
            const wantPad = this.origPad + gw + 6 + "px";
            if (ta.style.paddingLeft !== wantPad) {
                ta.style.paddingLeft = wantPad;
                ta.classList.add("lcq-numbered");
            }

            const lines = ta.value.split("\n");
            const active = document.activeElement === ta ? ta.value.slice(0, ta.selectionStart).split("\n").length : 0;
            const used = usedLines()[this.prefix];
            const usedKey = Array.from(used).join(",");
            const sig = [ta.value, ta.clientWidth, cs.fontSize, cs.lineHeight, active, usedKey].join("\u0001");

            this.el.style.display = "";
            this.el.style.top = ta.offsetTop + (parseFloat(cs.borderTopWidth) || 0) + "px";
            this.el.style.left = ta.offsetLeft + (parseFloat(cs.borderLeftWidth) || 0) + "px";
            this.el.style.height = ta.clientHeight + "px";
            this.el.style.width = gw + "px";
            this.el.style.fontFamily = cs.fontFamily;
            this.el.style.fontSize = cs.fontSize;
            this.el.style.lineHeight = cs.lineHeight;
            this.inner.style.paddingTop = cs.paddingTop;

            if (sig !== this.sig) {
                this.sig = sig;
                const heights = this.measure(lines, cs);
                let out = "";
                for (let i = 0; i < lines.length; i++) {
                    let cls = "lcq-ln";
                    if (i + 1 === active) cls += " lcq-active";
                    if (used.has(i + 1)) cls += " lcq-used";
                    out += `<div class="${cls}" style="height:${heights[i]}px">${this.prefix}${i + 1}</div>`;
                }
                this.inner.innerHTML = out;
            }
            this.syncScroll();
        }

        measure(lines, cs) {
            const m = getMirror();
            const padL = parseFloat(cs.paddingLeft) || 0;
            const padR = parseFloat(cs.paddingRight) || 0;
            m.style.width = Math.max(1, this.ta.clientWidth - padL - padR) + "px";
            for (const k of ["fontFamily", "fontSize", "fontWeight", "fontStyle", "lineHeight", "letterSpacing", "wordSpacing", "textTransform", "tabSize", "wordBreak"]) {
                m.style[k] = cs[k];
            }
            m.style.whiteSpace = "pre-wrap";
            m.style.overflowWrap = cs.overflowWrap && cs.overflowWrap !== "normal" ? cs.overflowWrap : "break-word";
            m.innerHTML = lines.map((l) => `<div>${l === "" ? "​" : escapeHtml(l)}</div>`).join("");
            const out = Array.from(m.children).map((c) => c.getBoundingClientRect().height);
            m.innerHTML = "";
            return out;
        }

        syncScroll() {
            this.inner.style.transform = `translateY(${-this.ta.scrollTop}px)`;
        }
    }

    const gutters = new Map();

    function attachGutters() {
        for (const [id, prefix] of PROMPT_FIELDS) {
            const ta = byId(id)?.querySelector("textarea");
            if (!ta) continue;
            const g = gutters.get(id);
            if (g && g.ta === ta && g.alive()) continue;
            if (g) g.el.remove();
            gutters.set(id, new Gutter(ta, prefix));
        }
    }

    function renderGutters() {
        for (const g of gutters.values()) if (g.alive()) g.render();
    }

    window.lcqSetLineNumbers = function (v) {
        showNumbers = !!v;
        lsSet(LS_KEY, showNumbers);
        renderGutters();
    };

    // ------------------------------------------------------------ queue list

    function queueTextarea() {
        return byId("lcq_queue")?.querySelector("textarea") || null;
    }
    function metaTextarea() {
        return byId("lcq_meta")?.querySelector("textarea") || null;
    }

    function queueLines() {
        const ta = queueTextarea();
        if (!ta) return [];
        return ta.value.split("\n").map((s) => s.trim()).filter((s) => s && !s.startsWith("#"));
    }

    function readMeta() {
        try {
            const v = JSON.parse(metaTextarea()?.value || "[]");
            return Array.isArray(v) ? v : [];
        } catch (_) {
            return [];
        }
    }

    function writeQueue(lines) {
        const ta = queueTextarea();
        if (!ta) return;
        ta.value = lines.join("\n");
        fireInput(ta);
        renderList();
    }

    function splitItem(line) {
        const p = line.trim().split(/\s+/);
        return { expr: p[0] || "", size: p[1] || "", seed: p[2] || "-1", on: (p[3] || "").toLowerCase() !== "off" };
    }
    function joinItem(it) {
        const s = `${it.expr}  ${it.size}  ${it.seed}`;
        return it.on ? s : `${s}  off`;
    }

    /** Light shape check for dropped files; the full check happens in Python. */
    function normalizeFileLine(line) {
        const p = line.trim().split(/\s+/);
        if (p.length < 2 || p.length > 4 || !/^[n\d+\-,|(){}:]+$/.test(p[0])) return null;
        const size = normalizeSize(p[1]);
        if (!size) return null;
        const seed = p[2] === undefined ? "-1" : p[2];
        if (!/^-?\d+$/.test(seed)) return null;
        const on = p[3] === undefined || p[3].toLowerCase() === "on";
        if (!on && p[3].toLowerCase() !== "off") return null;
        return joinItem({ expr: p[0], size, seed, on });
    }

    /** "832x1216" or "{832x1216|1216x832}" (random per image) -> normalized, or null. */
    function normalizeSize(tok) {
        const inner = /^\{(.*)\}$/.exec(tok);
        const parts = inner ? inner[1].split("|") : [tok];
        const out = [];
        for (const x of parts) {
            const m = SIZE_RE.exec(x);
            if (!m) return null;
            out.push(`${m[1]}x${m[2]}`);
        }
        return out.length > 1 ? `{${out.join("|")}}` : out[0];
    }

    function sizeLabel(tok) {
        const inner = /^\{(.*)\}$/.exec(tok);
        if (!inner) return { text: tok, title: "" };
        const list = inner[1].split("|");
        return { text: t("random size ({n})", { n: list.length }), title: list.join(", ") };
    }

    let lastQueue = null;
    let lastMeta = null;
    let notice = { text: "", ok: true };

    const ICON_UP = '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 13V3M3.5 7.5 8 3l4.5 4.5"/></svg>';
    const ICON_DOWN = '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 3v10M3.5 8.5 8 13l4.5-4.5"/></svg>';
    const ICON_DEL = '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8"/></svg>';

    function buildList(mount) {
        mount.innerHTML = `
<div class="lcq-list-head">
  <span class="lcq-list-title">${escapeHtml(t("Queue"))}</span>
  <span class="lcq-head-tools">
    <button type="button" class="lcq-link lcq-all-on">${escapeHtml(t("Enable all"))}</button>
    <button type="button" class="lcq-link lcq-all-off">${escapeHtml(t("Disable all"))}</button>
    <span class="lcq-summary"></span>
  </span>
</div>
<div class="lcq-drop" tabindex="-1">
  <div class="lcq-rows"></div>
  <div class="lcq-drop-hint">${escapeHtml(t("Drop a saved .txt here to load it (added to the end)"))}</div>
</div>
<div class="lcq-actions">
  <button type="button" class="lcq-save">${escapeHtml(t("Save as text file"))}</button>
  <button type="button" class="lcq-clear">${escapeHtml(t("Clear queue"))}</button>
  <span class="lcq-notice" role="status"></span>
</div>`;
        const drop = mount.querySelector(".lcq-drop");
        const rows = mount.querySelector(".lcq-rows");

        rows.addEventListener("click", (e) => {
            const btn = e.target.closest("button[data-act]");
            if (!btn) return;
            const i = parseInt(btn.dataset.i, 10);
            const lines = queueLines();
            if (btn.dataset.act === "up" && i > 0) [lines[i - 1], lines[i]] = [lines[i], lines[i - 1]];
            else if (btn.dataset.act === "down" && i < lines.length - 1) [lines[i + 1], lines[i]] = [lines[i], lines[i + 1]];
            else if (btn.dataset.act === "del") lines.splice(i, 1);
            else return;
            // keep meta rows aligned until Python recomputes them
            const meta = readMeta();
            if (meta.length === lines.length + (btn.dataset.act === "del" ? 1 : 0)) {
                if (btn.dataset.act === "del") meta.splice(i, 1);
                else {
                    const j = btn.dataset.act === "up" ? i - 1 : i + 1;
                    [meta[i], meta[j]] = [meta[j], meta[i]];
                }
                const mt = metaTextarea();
                if (mt) mt.value = JSON.stringify(meta);
            }
            writeQueue(lines);
        });

        rows.addEventListener("change", (e) => {
            const cb = e.target.closest("input[data-act=toggle]");
            if (!cb) return;
            const i = parseInt(cb.dataset.i, 10);
            const lines = queueLines();
            const it = splitItem(lines[i]);
            it.on = cb.checked;
            lines[i] = joinItem(it);
            writeQueue(lines);
        });

        const setAll = (on) => writeQueue(queueLines().map((l) => joinItem({ ...splitItem(l), on })));
        mount.querySelector(".lcq-all-on").addEventListener("click", () => setAll(true));
        mount.querySelector(".lcq-all-off").addEventListener("click", () => setAll(false));

        mount.querySelector(".lcq-save").addEventListener("click", () => {
            const lines = queueLines();
            if (!lines.length) return setNotice(t("The queue is empty"), false);
            const blob = new Blob([lines.join("\n") + "\n"], { type: "text/plain;charset=utf-8" });
            const a = document.createElement("a");
            a.href = URL.createObjectURL(blob);
            a.download = "line_combo_queue.txt";
            document.body.appendChild(a);
            a.click();
            a.remove();
            setTimeout(() => URL.revokeObjectURL(a.href), 1000);
            setNotice(t("Saved {n} item(s) to line_combo_queue.txt", { n: lines.length }), true);
        });

        mount.querySelector(".lcq-clear").addEventListener("click", () => {
            writeQueue([]);
            setNotice("", true);
        });

        const stop = (e) => { e.preventDefault(); e.stopPropagation(); };
        drop.addEventListener("dragenter", (e) => { stop(e); drop.classList.add("lcq-dragover"); });
        drop.addEventListener("dragover", (e) => { stop(e); if (e.dataTransfer) e.dataTransfer.dropEffect = "copy"; drop.classList.add("lcq-dragover"); });
        drop.addEventListener("dragleave", (e) => { if (!drop.contains(e.relatedTarget)) drop.classList.remove("lcq-dragover"); });
        drop.addEventListener("drop", async (e) => {
            stop(e);
            drop.classList.remove("lcq-dragover");
            const files = Array.from(e.dataTransfer?.files || []);
            if (!files.length) return;
            const added = [];
            let bad = 0;
            for (const f of files) {
                const text = await f.text();
                for (const raw of text.split(/\r?\n/)) {
                    const s = raw.trim();
                    if (!s || s.startsWith("#")) continue;
                    const n = normalizeFileLine(s);
                    if (n) added.push(n); else bad++;
                }
            }
            writeQueue(queueLines().concat(added));
            const names = files.map((f) => f.name).join(", ");
            let text = t("Loaded {n} item(s) from {file}", { n: added.length, file: names });
            if (bad) text += " " + t("({n} line(s) skipped: unreadable format)", { n: bad });
            setNotice(text, bad === 0);
        });
    }

    function setNotice(text, ok) {
        notice = { text, ok };
        const el = byId("lcq_list_mount")?.querySelector(".lcq-notice");
        if (el) {
            el.textContent = text;
            el.classList.toggle("lcq-msg-err", !ok);
            el.classList.toggle("lcq-msg-ok", ok);
        }
    }

    function renderList() {
        const mount = byId("lcq_list_mount");
        if (!mount) return;
        if (!mount.querySelector(".lcq-rows")) {
            buildList(mount);
            setNotice(notice.text, notice.ok);
        }
        const lines = queueLines();
        const meta = readMeta();
        const metaOk = meta.length === lines.length;
        lastQueue = queueTextarea()?.value ?? "";
        lastMeta = metaTextarea()?.value ?? "";

        let enabledCount = 0, images = 0;
        const html = lines.map((line, i) => {
            const it = splitItem(line);
            const m = metaOk ? meta[i] : null;
            const err = m && m.error;
            if (it.on && m && !err) { enabledCount++; images += m.images; }
            const no = "#" + String(i + 1).padStart(2, "0");
            const sz = sizeLabel(it.size);
            let count = "…";
            if (m && !err) {
                count = t("{n} image(s)", { n: m.images });
                if (m.random) count += " · " + t("random");
            } else if (err) count = t("error");
            const rowCls = "lcq-row" + (it.on ? "" : " lcq-off") + (err ? " lcq-bad" : "");
            return `<div class="${rowCls}">
  <label class="lcq-check"><input type="checkbox" data-act="toggle" data-i="${i}" ${it.on ? "checked" : ""} aria-label="${escapeHtml(t("Generate this item"))}"></label>
  <span class="lcq-no">${no}</span>
  <div class="lcq-body">
    <div class="lcq-combo">${escapeHtml(it.expr)}</div>
    <div class="lcq-sub"><span class="lcq-count">${escapeHtml(count)}</span> · <span class="lcq-size" title="${escapeHtml(sz.title)}">${escapeHtml(sz.text)}</span> · <span class="lcq-seed">seed ${escapeHtml(it.seed)}</span></div>
    ${err ? `<div class="lcq-err">${escapeHtml(err)}</div>` : ""}
  </div>
  <span class="lcq-btns">
    <button type="button" data-act="up" data-i="${i}" aria-label="${escapeHtml(t("Move up"))}" title="${escapeHtml(t("Move up"))}" ${i === 0 ? "disabled" : ""}>${ICON_UP}</button>
    <button type="button" data-act="down" data-i="${i}" aria-label="${escapeHtml(t("Move down"))}" title="${escapeHtml(t("Move down"))}" ${i === lines.length - 1 ? "disabled" : ""}>${ICON_DOWN}</button>
    <button type="button" data-act="del" data-i="${i}" aria-label="${escapeHtml(t("Delete this item"))}" title="${escapeHtml(t("Delete"))}" class="lcq-del">${ICON_DEL}</button>
  </span>
</div>`;
        }).join("");
        mount.querySelector(".lcq-rows").innerHTML = html || `<div class="lcq-empty">${escapeHtml(t("The queue is empty"))}</div>`;
        mount.querySelector(".lcq-summary").textContent = t("{on}/{all} enabled · {images} image(s)", { on: enabledCount, all: lines.length, images });
    }

    // ------------------------------------------------------------ docking
    // Scripts are rendered at the bottom of the Generation tab, far from the
    // prompts. Move the panel right under the prompt fields (and back).

    let homeMarker = null;

    function dockTarget() {
        const settings = byId("txt2img_settings");
        if (!settings) return null;
        // "Compact" prompt style: prompts live inside the settings column
        const promptBox = byId("txt2img_prompt_container");
        if (promptBox && settings.contains(promptBox)) {
            const style = settings.querySelector(".toprow-compact-stylerow");
            let node = style && promptBox.compareDocumentPosition(style) & Node.DOCUMENT_POSITION_FOLLOWING ? style : promptBox;
            while (node.parentElement && node.parentElement !== settings) node = node.parentElement;
            return { parent: settings, before: node.nextSibling };
        }
        // "Classic" style: prompts sit above the tabs -> top of the settings column
        return { parent: settings, before: settings.firstChild };
    }

    function applyDock() {
        const panel = byId("lcq_accordion");
        if (!panel) return;
        if (dockOn) {
            if (panel.classList.contains("lcq-docked")) return;
            const tg = dockTarget();
            if (!tg || !tg.parent) return;
            if (!homeMarker) {
                homeMarker = document.createComment("lcq-home");
                panel.parentNode.insertBefore(homeMarker, panel);
            }
            tg.parent.insertBefore(panel, tg.before);
            panel.classList.add("lcq-docked");
        } else if (homeMarker && panel.classList.contains("lcq-docked")) {
            homeMarker.parentNode.insertBefore(panel, homeMarker);
            panel.classList.remove("lcq-docked");
        }
        renderGutters();
    }

    window.lcqSetDock = function (v) {
        dockOn = !!v;
        lsSet(LS_DOCK, dockOn);
        applyDock();
    };

    // ------------------------------------------------------------ boot

    function tick() {
        attachGutters();
        renderGutters();
        const ta = queueTextarea();
        const mount = byId("lcq_list_mount");
        if (!ta || !mount) return;
        const mv = metaTextarea()?.value ?? "";
        if (ta.value !== lastQueue || mv !== lastMeta || !mount.querySelector(".lcq-rows")) renderList();
    }

    function syncCheckbox(id, value) {
        const cb = byId(id)?.querySelector("input[type=checkbox]");
        if (cb && cb.checked !== value) {
            cb.checked = value;
            fireInput(cb);
        }
    }

    function boot() {
        syncCheckbox("lcq_show_ln", showNumbers);
        syncCheckbox("lcq_dock", dockOn);
        applyDock();
        const combo = comboTextarea();
        if (combo) ["input", "keyup", "click"].forEach((ev) => combo.addEventListener(ev, renderGutters));
        tick();
        setInterval(tick, 400);
    }

    if (typeof onUiLoaded === "function") onUiLoaded(boot);
    else document.addEventListener("DOMContentLoaded", boot);
})();
