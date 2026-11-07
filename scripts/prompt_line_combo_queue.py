"""Prompt Line Combo Queue (行組み合わせ予約マネージャ) for Forge Neo.

Build prompts from numbered lines of the Prompt / Negative prompt fields,
queue several jobs (each with its own expression, size, seed and count) and
generate the enabled ones in a single Generate run.
"""

from __future__ import annotations

import html
import json
import logging
import os

import gradio as gr

from lib_plcq.core import (
    ASPECT_PRESETS,
    DEFAULT_SIZE_LISTS,
    ComboErrors,
    aspect_size,
    format_queue,
    format_size_list,
    format_size_token,
    make_jobs,
    parse_size_list,
    plan_queue,
    queue_meta,
)
from lib_plcq.i18n import LANG_CHOICES, current_lang, table_for, translator
from modules import script_callbacks, scripts, shared
from modules.processing import StableDiffusionProcessingTxt2Img
from modules.rng import ImageRNG
from modules.ui_components import InputAccordion

logger = logging.getLogger("prompt_line_combo_queue")

RANDOM_SIZE = "random"


class SizeStore:
    """The dropdown's size list, kept next to the webui config so updates do not wipe it."""

    def __init__(self):
        try:
            from modules.paths_internal import data_path
        except Exception:
            data_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.path = os.path.join(data_path, "plcq_sizes.json")
        self.sizes = self._load()

    def _load(self):
        try:
            with open(self.path, encoding="utf8") as f:
                sizes = [tuple(map(int, x)) for x in json.load(f)]
            if sizes:
                return sizes
        except (OSError, ValueError, TypeError):
            pass
        return list(DEFAULT_SIZE_LISTS["SDXL"])

    def save(self, sizes):
        self.sizes = list(sizes)
        try:
            with open(self.path, "w", encoding="utf8") as f:
                json.dump([list(x) for x in self.sizes], f)
        except OSError as e:
            logger.error(f"Prompt Line Combo Queue: could not save {self.path}: {e}")

    def choices(self, t):
        return [(t("Random (whole list)"), RANDOM_SIZE)] + [(f"{w}x{h}", f"{w}x{h}") for w, h in self.sizes]


SIZES = SizeStore()


def _t():
    return translator(current_lang())


def _msg(text: str, ok: bool) -> str:
    cls = "lcq-msg-ok" if ok else "lcq-msg-err"
    body = "<br>".join(html.escape(t) for t in text.split("\n"))
    return f'<div class="{cls}">{body}</div>'


def on_ui_settings():
    section = ("prompt_line_combo_queue", "Prompt Line Combo Queue")
    shared.opts.add_option(
        "plcq_language",
        shared.OptionInfo(
            "Auto",
            "UI language",
            gr.Radio,
            {"choices": LANG_CHOICES},
            section=section,
        )
        .info("Auto = Japanese when the Localization setting is a Japanese one, otherwise English")
        .needs_reload_ui(),
    )


script_callbacks.on_ui_settings(on_ui_settings)


class PromptLineComboQueue(scripts.Script):
    def __init__(self):
        super().__init__()
        self.prompt_box = None
        self.neg_box = None
        self._plan = None  # per-image plan while a queued run is active

    def title(self):
        return "Prompt Line Combo Queue"

    def show(self, is_img2img):
        if is_img2img:
            return False  # img2img fixes the latent size before the loop; txt2img only
        self.on_after_component(lambda c: setattr(self, "prompt_box", c.component), elem_id="txt2img_prompt")
        self.on_after_component(lambda c: setattr(self, "neg_box", c.component), elem_id="txt2img_neg_prompt")
        return scripts.AlwaysVisible

    # ------------------------------------------------------------------ UI

    def ui(self, is_img2img):
        lang = current_lang()
        t = translator(lang)
        i18n_json = html.escape(json.dumps(table_for(lang), ensure_ascii=False), quote=True)
        help_html = (
            '<div class="lcq-help">'
            + html.escape(t("When this is on and the queue has enabled items, Generate runs them from the top."))
            + "<br>"
            + html.escape(t("Line numbers joined with “+”; n = Negative prompt line. One line = one queue item."))
            + "<br><code>(3-6)</code> " + html.escape(t("range"))
            + " · <code>(3,4,5)</code> " + html.escape(t("one image each"))
            + " · <code>{3|4|5}</code> " + html.escape(t("random pick"))
            + " · <code>:N</code> " + html.escape(t("image count (omit = every combination once)"))
            + "<br>" + html.escape(t("Example")) + " <code>1+(3-5)+(7,8)+{9|10}+n1:4</code></div>"
        )

        with InputAccordion(False, label=t("Prompt Line Combo Queue"), elem_id="lcq_accordion") as enabled:
            gr.HTML(f'<div id="plcq_i18n" data-lang="{lang}" data-dict="{i18n_json}" hidden></div>' + help_html)
            combo = gr.Textbox(
                label=t("Line combinations"),
                lines=3,
                placeholder="1+2+(4-5)+n1\n1+(3,4)+{6|7}+n1:4",
                elem_id="lcq_combo",
            )
            with gr.Row(elem_id="lcq_row"):
                first = f"{SIZES.sizes[0][0]}x{SIZES.sizes[0][1]}" if SIZES.sizes else RANDOM_SIZE
                size = gr.Dropdown(
                    label=t("Image size (W x H)"),
                    choices=SIZES.choices(t),
                    value=first,
                    allow_custom_value=True,
                    elem_id="lcq_size",
                    scale=3,
                )
                seed = gr.Textbox(label=t("Seed (-1 = random per image)"), value="-1", elem_id="lcq_seed", scale=2)
                reserve = gr.Button(t("Add to queue"), variant="primary", elem_id="lcq_reserve", scale=1)
            msg = gr.HTML("", elem_id="lcq_msg")
            with gr.Accordion(t("Size list"), open=False, elem_id="lcq_sizes_acc"):
                sizes_box = gr.Textbox(
                    label=t("Sizes in the dropdown (one per line, W x H)"),
                    value=format_size_list(SIZES.sizes),
                    lines=5,
                    elem_id="lcq_sizes",
                )
                with gr.Row():
                    new_w = gr.Number(label=t("Width"), precision=0, value=832, minimum=64, maximum=8192, step=8, scale=2)
                    new_h = gr.Number(label=t("Height"), precision=0, value=1216, minimum=64, maximum=8192, step=8, scale=2)
                    add_wh = gr.Button(t("Add size"), scale=2)
                with gr.Row():
                    aspect = gr.Dropdown(label=t("Aspect ratio"), choices=[(t(k), k) for k in ASPECT_PRESETS], value="Portrait (2:3)", scale=2)
                    mpix = gr.Slider(0.2, 4.0, value=1.0, step=0.05, label=t("Megapixels"), scale=3, min_width=200)
                    add_ar = gr.Button(t("Add from aspect ratio"), scale=2)
                with gr.Row():
                    preset = gr.Dropdown(label=t("Default list"), choices=list(DEFAULT_SIZE_LISTS), value="SDXL")
                    reset_btn = gr.Button(t("Replace with default list"))
                    sort_btn = gr.Button(t("Sort by area"))
                    save_btn = gr.Button(t("Save list"), variant="primary")
                sizes_msg = gr.HTML("", elem_id="lcq_sizes_msg")
            # Source of truth for the list; the JS list view reads and writes it.
            queue = gr.Textbox(lines=6, visible=False, elem_id="lcq_queue")
            # Per-item image counts / errors computed here, rendered by the JS list.
            meta = gr.Textbox(value="[]", visible=False, elem_id="lcq_meta")
            gr.HTML('<div id="lcq_list_mount"></div>', elem_id="lcq_list_html")
            with gr.Row():
                show_ln = gr.Checkbox(label=t("Show line numbers in the prompt fields"), value=True, elem_id="lcq_show_ln")
                dock = gr.Checkbox(label=t("Place under the prompt fields"), value=True, elem_id="lcq_dock")

        size_outs = [sizes_box, size, sizes_msg]
        save_btn.click(fn=lambda txt, cur: self._save_sizes(txt, cur), inputs=[sizes_box, size], outputs=size_outs, show_progress=False)
        add_wh.click(fn=self._add_wh, inputs=[sizes_box, new_w, new_h, size], outputs=size_outs, show_progress=False)
        add_ar.click(fn=self._add_ar, inputs=[sizes_box, aspect, mpix, size], outputs=size_outs, show_progress=False)
        reset_btn.click(fn=lambda name, cur: self._save_sizes(format_size_list(DEFAULT_SIZE_LISTS[name]), cur), inputs=[preset, size], outputs=size_outs, show_progress=False)
        sort_btn.click(fn=self._sort_sizes, inputs=[sizes_box, size], outputs=size_outs, show_progress=False)

        prompts = [b for b in (self.prompt_box, self.neg_box) if b is not None]
        reserve.click(fn=self._reserve, inputs=[combo, size, seed, queue] + prompts, outputs=[queue, msg], show_progress=False)
        queue.change(fn=self._meta, inputs=[queue] + prompts, outputs=[meta], show_progress=False)
        show_ln.change(fn=None, _js="(v) => { window.lcqSetLineNumbers && window.lcqSetLineNumbers(v); }", inputs=[show_ln])
        dock.change(fn=None, _js="(v) => { window.lcqSetDock && window.lcqSetDock(v); }", inputs=[dock])

        self.infotext_fields = []
        return [enabled, queue]

    # ---- size list editing (each action saves the list and refreshes the dropdown)

    def _save_sizes(self, text, current):
        t = _t()
        sizes, bad = parse_size_list(text)
        if not sizes:
            return gr.update(), gr.update(), _msg(t("The list needs at least one size"), False)
        SIZES.save(sizes)
        keep = current if current == RANDOM_SIZE or current in {f"{w}x{h}" for w, h in sizes} else f"{sizes[0][0]}x{sizes[0][1]}"
        note = t("Saved {n} size(s)", n=len(sizes))
        if bad:
            note += " " + t("(ignored: {items})", items=", ".join(bad))
        return format_size_list(sizes), gr.update(choices=SIZES.choices(t), value=keep), _msg(note, not bad)

    def _add_wh(self, text, w, h, current):
        if not w or not h:
            t = _t()
            return gr.update(), gr.update(), _msg(t("Enter both width and height"), False)
        return self._save_sizes(f"{text}\n{int(w)}x{int(h)}", current)

    def _add_ar(self, text, name, mp, current):
        w, h = aspect_size(ASPECT_PRESETS.get(name, 1.0), float(mp or 1.0))
        return self._save_sizes(f"{text}\n{w}x{h}", current)

    def _sort_sizes(self, text, current):
        sizes, bad = parse_size_list(text)
        sizes.sort(key=lambda s: (s[0] * s[1], s[0]))
        return self._save_sizes(format_size_list(sizes) + "".join(f"\n{b}" for b in bad), current)

    # ---- queue

    def _reserve(self, combo, size, seed, queue, prompt=None, negative=None):
        t = _t()
        if size == RANDOM_SIZE or size == t("Random (whole list)"):
            if not SIZES.sizes:
                return gr.update(), _msg(t("The list needs at least one size"), False)
            size = format_size_token(SIZES.sizes)
        try:
            jobs = make_jobs(combo, size, seed, prompt, negative)
        except ComboErrors as e:
            return gr.update(), _msg(e.render(t), False)
        existing = (queue or "").rstrip("\n")
        added = format_queue(jobs)
        new_queue = f"{existing}\n{added}" if existing else added
        total = sum(j.combo.images for j in jobs)
        return new_queue, _msg(t("Added {n} item(s) ({images} image(s))", n=len(jobs), images=total), True)

    def _meta(self, queue, prompt=None, negative=None):
        return json.dumps(queue_meta(queue, prompt, negative, _t()), ensure_ascii=False)

    # --------------------------------------------------------- generation

    def process(self, p, enabled, queue, *args):
        self._plan = None
        if not enabled or not (queue or "").strip():
            return
        if not isinstance(p, StableDiffusionProcessingTxt2Img):
            return
        if not isinstance(p.prompt, str) or not isinstance(p.negative_prompt, str):
            logger.warning("Prompt Line Combo Queue: the prompt is a list (API / another script); skipped")
            return

        try:
            plan = plan_queue(queue, p.prompt, p.negative_prompt)
        except ComboErrors as e:
            # Script hook exceptions are swallowed by the runner, which would then
            # silently generate the plain prompt. Stop the run instead.
            logger.error("Prompt Line Combo Queue: generation stopped\n" + e.render())
            shared.state.interrupted = True
            return
        total = len(plan.prompts)
        if total == 0:
            logger.warning("Prompt Line Combo Queue: no enabled queue items; generating normally")
            return

        styled_p = [shared.prompt_styles.apply_styles_to_prompt(x, p.styles) for x in plan.prompts]
        styled_n = [shared.prompt_styles.apply_negative_styles_to_prompt(x, p.styles) for x in plan.negatives]

        if p.enable_hr:
            # setup_prompts() copied the main prompt into hr_prompt when the Hires prompt was blank
            same_p = p.hr_prompt == p.prompt
            same_n = p.hr_negative_prompt == p.negative_prompt
            hr_p = shared.prompt_styles.apply_styles_to_prompt(p.hr_prompt, p.styles) if isinstance(p.hr_prompt, str) else None
            hr_n = shared.prompt_styles.apply_negative_styles_to_prompt(p.hr_negative_prompt, p.styles) if isinstance(p.hr_negative_prompt, str) else None
            p.all_hr_prompts = list(styled_p) if same_p or hr_p is None else [hr_p] * total
            p.all_hr_negative_prompts = list(styled_n) if same_n or hr_n is None else [hr_n] * total

        p.batch_size = 1
        p.n_iter = total
        p.all_prompts = styled_p
        p.all_negative_prompts = styled_n
        p.main_prompt = styled_p[0]
        p.main_negative_prompt = styled_n[0]
        p.all_seeds = plan.seeds
        base_sub = int(p.all_subseeds[0]) if p.all_subseeds else 0
        p.all_subseeds = [base_sub + i for i in range(total)]
        p.seed = plan.seeds[0]
        p.do_not_save_grid = True  # sizes differ; a grid would be meaningless

        if shared.state.job_count not in (-1, 0):
            shared.state.job_count = total

        self._plan = plan
        logger.info(f"Prompt Line Combo Queue: {plan.jobs_used} item(s), {total} image(s)")

    def before_process_batch(self, p, enabled, queue, *args, **kwargs):
        plan = self._plan
        if not plan:
            return
        n = kwargs.get("batch_number", getattr(p, "iteration", 0))
        if n >= len(plan.sizes):
            return

        prev_w = p.width
        w, h = plan.sizes[n]
        if hasattr(shared.sd_model, "fix_dimensions"):
            w, h = shared.sd_model.fix_dimensions(w, h)
        p.width, p.height = w, h

        if p.enable_hr:
            p.calculate_target_resolution()

        # Forge Neo builds p.rng before this hook, with the old size: rebuild it.
        old = p.rng
        if old is not None and len(old.shape) >= 2 and old.shape[-1] > 0:
            factor = max(1, round(prev_w / old.shape[-1]))
            shape = tuple(old.shape[:-2]) + (p.height // factor, p.width // factor)
            p.rng = ImageRNG(
                shape,
                p.seeds,
                subseeds=p.subseeds,
                subseed_strength=p.subseed_strength,
                seed_resize_from_h=p.seed_resize_from_h,
                seed_resize_from_w=p.seed_resize_from_w,
            )

        p.extra_generation_params["Line Combo"] = plan.resolved[n]
        p.extra_generation_params["Line Combo Source"] = plan.sources[n].rsplit(" (", 1)[0]
        shared.state.job = f"Line Combo {plan.sources[n]}"

    def postprocess(self, p, processed, *args):
        self._plan = None
