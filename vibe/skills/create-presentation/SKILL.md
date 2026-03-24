---
name: create-presentation
description: Create PowerPoint presentations with python-pptx. Handles templates, layouts, fonts, and image placement.
user-invocable: true
allowed-tools:
  - bash
  - write_file
  - read_file
---

# Create Presentation

Build a PowerPoint (.pptx) file using python-pptx.

## Setup

First install the library:
```bash
pip install python-pptx
```

## Workflow

1. **Plan the deck**: List the slides, titles, and bullet points BEFORE writing any code.
2. **Write a Python script** (using `write_file`) that creates the presentation.
3. **Run the script** with `bash`.
4. **Verify the output** by reading back the pptx metadata.

## Script Template

Write this to `create_pptx.py`, then run with `python3 create_pptx.py`:

```python
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.enum.text import PP_ALIGN
from pptx.dml.color import RGBColor

# Use a template if provided, otherwise blank
# prs = Presentation('template.pptx')  # with template
prs = Presentation()

# Slide dimensions (widescreen 16:9)
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)

# --- Title Slide ---
slide_layout = prs.slide_layouts[0]  # 0 = Title Slide
slide = prs.slides.add_slide(slide_layout)
slide.placeholders[0].text = "Presentation Title"
slide.placeholders[1].text = "Subtitle text here"

# --- Content Slide ---
slide_layout = prs.slide_layouts[1]  # 1 = Title + Content
slide = prs.slides.add_slide(slide_layout)
slide.placeholders[0].text = "Slide Title"
tf = slide.placeholders[1].text_frame
tf.text = "First bullet point"
p = tf.add_paragraph()
p.text = "Second bullet point"
p.level = 0

# --- Custom positioned text (no overlap) ---
from pptx.util import Inches, Pt
slide = prs.slides.add_slide(prs.slide_layouts[6])  # 6 = Blank
txBox = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(10), Inches(1))
tf = txBox.text_frame
tf.word_wrap = True
p = tf.paragraphs[0]
p.text = "Custom text"
p.font.size = Pt(24)
p.font.color.rgb = RGBColor(0x1A, 0x1A, 0x2E)

# --- Image slide ---
# slide.shapes.add_picture('image.png', Inches(1), Inches(2), width=Inches(5))

prs.save('output.pptx')
print(f"Saved: {len(prs.slides)} slides")
```

## Critical Rules

- **Always use `Inches()` and `Pt()`** for positioning and font sizes. Raw numbers create tiny or huge elements.
- **Set `word_wrap = True`** on text frames to prevent text from running off the slide.
- **Set font size explicitly**: `p.font.size = Pt(18)` — the default is often too large.
- **Calculate vertical positions** to avoid overlap: `top = Inches(1.5 + i * 0.6)` for each text block.
- **Use placeholders** (index 0 = title, 1 = body) for standard layouts. Only use `add_textbox` for custom positioning.
- **Common layout indices**: 0=Title Slide, 1=Title+Content, 2=Section Header, 5=Blank, 6=Content Only.

## Template Usage

```python
prs = Presentation('template.pptx')
# List available layouts:
for i, layout in enumerate(prs.slide_layouts):
    print(f"  Layout {i}: {layout.name}")
    for ph in layout.placeholders:
        print(f"    Placeholder {ph.placeholder_format.idx}: {ph.name}")
```

Always enumerate the template's layouts and placeholders BEFORE adding content — templates vary wildly.

## Verification

After creating the file, ALWAYS verify:
```bash
python3 -c "
from pptx import Presentation
p = Presentation('output.pptx')
print(f'Slides: {len(p.slides)}')
for i, s in enumerate(p.slides):
    print(f'  Slide {i+1}: {len(s.shapes)} shapes')
    for sh in s.shapes:
        if sh.has_text_frame:
            text = sh.text_frame.text[:60]
            print(f'    Text: {text}')
"
```

This catches blank slides, missing text, and wrong shape counts.
