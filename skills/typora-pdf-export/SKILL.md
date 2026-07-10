---
name: typora-pdf-export
description: Configure, install, repair, or validate portable Typora Markdown-to-PDF export with Pandoc and XeLaTeX. Use when a user needs consistent Chinese/English typography, six Markdown heading levels, controlled lists, centred fixed-position images, wrapping tables, code blocks, endnotes, or image paths that keep working when the PDF is exported outside the Markdown directory.
---

# Typora PDF Export

Install this skill's bundled configuration instead of creating a new template. The assets assume macOS fonts `Times New Roman`, `Songti SC`, `Heiti SC`, and `Menlo`; confirm substitutes before changing them.

## Install

Run the installer from the skill directory. It copies only three files and refuses to overwrite unless `--force` is passed.

```bash
python3 scripts/install.py
```

For another Typora configuration directory:

```bash
python3 scripts/install.py --config-dir /path/to/.typora
```

Use these Typora PDF export arguments:

```text
--pdf-engine=/Library/TeX/texbin/xelatex --resource-path=. --include-in-header=~/.typora/header.tex --include-after-body=~/.typora/after.tex --lua-filter=~/.typora/wrap-tables.lua -V monofont=Menlo -V geometry:margin=2cm -V fontsize=11pt -V linestretch=1.3 -V colorlinks=true -V linkcolor=black -V urlcolor=[HTML]{1A6FCC} --syntax-highlighting=tango
```

Always use an absolute XeLaTeX path in `--pdf-engine`, for example `/Library/TeX/texbin/xelatex`. Do not enter bare `xelatex`, `~/...`, or a shell substitution such as `$(which xelatex)`: Typora passes this field as an executable path, not through an interactive shell.

Keep `--resource-path=.`. Pandoc resolves Markdown image paths before LaTeX runs; this option prevents the PDF output directory from becoming the image search base.

## Behaviour

- Map `#` through `######` to a compact six-level hierarchy. English heading text is scaled to 90% without shrinking CJK glyphs.
- Render standalone images centred. Render Pandoc `figure` environments with `[H]`; an image moves to the next page only when it cannot fit at its source position.
- Apply nested list labels: `•`, `–`, `◦`; `1.`, `a)`, `i.`.
- Give table columns relative widths so cells wrap instead of exceeding the text width.
- Style quotations and syntax-highlighted code blocks; keep long code lines breakable.
- Collect Markdown footnotes as endnotes under `注释`.

Do not claim that arbitrary HTML, Typora extensions, or browser-only widgets can be represented in XeLaTeX. Keep standard Markdown and Pandoc-supported extensions as the compatibility boundary.

## Verify

Export a Markdown file containing Chinese/English text, levels 1–6, nested ordered/unordered lists, a relative image, a wide table, code, a quote, a link, math, and a footnote. Write the PDF to a different directory from the Markdown source. Confirm that the image stays centred at its source position and that the PDF compiles without missing-image errors.

If compilation fails, first run:

```bash
/usr/bin/test -x /Library/TeX/texbin/xelatex
/Library/TeX/texbin/xelatex --version
fc-match 'Songti SC'
```

Use `--resource-path=/absolute/markdown-directory` for command-line exports run outside the Markdown directory. Do not change `\graphicspath` to chase arbitrary source folders: it runs too late to repair Pandoc resource resolution.
