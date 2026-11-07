# Prompt Line Combo Queue

An extension for **Forge Neo** (Haoming02/sd-webui-forge-classic, `neo` branch).

Write your prompt one idea per line, then build prompts by picking line numbers: `1+2+(4-6)+n1`.
Queue as many combinations as you like, each with its own image size, seed and count, and generate the enabled ones with a single **Generate**.

日本語の説明は [README_ja.md](README_ja.md) にあります。

## Install

Put the folder in `extensions/` and restart the WebUI:

```
<Forge Neo>/extensions/sd-forge-prompt-line-combo-queue/
```

## Quick start

1. Open **Prompt Line Combo Queue** in the txt2img Generation tab (open = on). It moves itself right under the prompt fields.
2. Write the prompt one line per part (subject / outfit / pose / place / style …). Line numbers appear on the left; wrapped lines get no number.
3. Type one expression per line in **Line combinations**, pick a size and seed, and press **Add to queue**.
4. Press **Generate**. Enabled queue items run from the top.

## Expression syntax

| Write | Means |
|---|---|
| `3` | line 3 of the Prompt field |
| `n2` | line 2 of the Negative prompt field |
| `1+2+5` | lines 1, 2 and 5, joined in the written order |
| `(3-6)` | range: same as `3+4+5+6` (`(n1-n3)` for negative lines) |
| `(3,4,5)` | alternatives: one image per alternative, in order |
| `{3\|4\|5}` | random: one of them is picked for every image (Dynamic Prompts style) |
| `:N` at the end | number of images |

Groups can be nested, and an alternative can be a whole sequence:
`1+((3-5),7)+{n1|n2}` · `(2+3,4+(5,6))`

**Image count**
- Omitted: every combination once. `2+(3,4,5)+7` makes 3 images: `2+3+7`, `2+4+7`, `2+5+7`.
- `:N`: N images, going through the combinations from the first one (wrapping around if N is larger).
- Several groups multiply: `(1,2)+(3,4)` → `1+3`, `1+4`, `2+3`, `2+4`.
- `{…}` does not add combinations; each image draws again. Anything inside `{}` is random too.

## How prompts are built

- The chosen lines are joined **with newlines**, in the written order. Styles are applied afterwards.
- An item without any `n` line gets an empty negative prompt.
- Seed `-1` is random for every image. A fixed seed counts up within an item: seed, seed+1, seed+2 …
- Line numbers are resolved against the prompt fields **when you press Generate**, not when the item was added.
- While the queue is in use, Batch count / Batch size are ignored (one image at a time). No grid is made, since sizes differ.
- Every image's infotext records `Line Combo: 2+4+7+n1` (the lines actually used) and `Line Combo Source: #1 2+(3,4,5)+7+n1`.
- Hires. fix works; its scale is based on each item's size.

## Image size

- Pick a size from the dropdown, type your own (`1024x1536`), or choose **Random (whole list)**: every image of that item then gets a random size from the list. The list is copied into the item when you add it, written as `{832x1216|1216x832|…}`.
- Edit the list under **Size list**: type sizes one per line and press **Save list**, add one by width/height, or add one from an aspect ratio and a megapixel count (rounded to multiples of 8). **Replace with default list** loads the SDXL, SD 1.5 or Anima set; **Sort by area** orders them.
- The list is saved to `plcq_sizes.json` in the WebUI data folder (next to `config.json`), so updating the extension keeps it. The `768,1280;512,512` format of other random-resolution extensions can be pasted too.

## Queue

- The checkbox on each item decides whether it is generated. **Enable all / Disable all** are in the header.
- ↑ ↓ reorder, ✕ deletes. Each item shows its image count; problems (for example a line number that does not exist) are shown in red.
- **Save as text file** downloads `line_combo_queue.txt`. Drop a saved file onto the list to append it.

File format: one item per line, whitespace separated; `#` comments and blank lines are ignored.

```
<expression>[:N]  <size>  <seed>  [off]        size: 832x1216 or {832x1216|1216x832|...} (random)

2+(3,4,5)+7+n1      832x1216  -1
1+{5|6}+n1:4        1216x832  12345  off
(1,2)+3             {832x1216|1216x832|1024x1024}  -1
```

## Settings

**Settings → Prompt Line Combo Queue → UI language**: `Auto` (Japanese when the WebUI Localization setting is a Japanese one, otherwise English), `English`, `日本語`. Needs a UI reload.

The checkboxes at the bottom of the panel turn the line numbers on/off and choose whether the panel sits under the prompt fields. Both are remembered by the browser.

## Limitations

- txt2img only (img2img fixes the latent size before the generation loop).
- If an enabled item cannot be used (syntax error, missing line), the whole run stops and the reason is printed to the console. Fix it or untick it.
- Using it together with extensions that rewrite the prompt list (e.g. Dynamic Prompts) lets one overwrite the other.
