# SVG Annotation Transfer

Copy custom Inkscape/Matplotlib annotation groups from one SVG into another
without opening a GUI editor. The tool scans the source SVG, extracts any
non-`figure_1` top-level groups (for example arrows, labels, or highlight
boxes), collects the supporting `<defs>` entries they reference, and merges
everything into the target SVG.

## Installation

The module is a single-file Python package. Copy the
`svg_annotation_transfer` folder into your project or install it directly from
the Git

## Command-line usage

You can invoke the tool directly with Python:

```bash
python -m svg_annotation_transfer source.svg target.svg
```

If you prefer to express direction with arrows, pass a quoted expression that
contains either `>` or `<`:

```bash
python -m svg_annotation_transfer --expr 'source.svg>target.svg'
python -m svg_annotation_transfer 'source.svg>target.svg'
```

The expression form is symmetric:

```bash
python -m svg_annotation_transfer 'output.svg<overlay.svg'
```

When using the expression form, remember to quote it so your shell does not treat the arrow as output redirection.

Additional options:

- `--dry-run` – Preview changes without writing to the target file.
- `--include GROUP_ID ...` – Copy only the specified group IDs.
- `--exclude GROUP_ID ...` – Skip the listed group IDs.
- `--list-groups SVG` – Show detected overlay groups in a file and exit.

Example dry run:

```bash
python -m svg_annotation_transfer --dry-run --expr 'source.svg>target.svg'
```

## Library usage

```python
from pathlib import Path
from svg_annotation_transfer import copy_annotations

copied = copy_annotations(Path("source.svg"), Path("target.svg"))
print("Copied groups:", copied)
```

Use the `include` and `exclude` arguments to fine-tune which groups move:

```python
copy_annotations("source.svg", "target.svg", include=["g3216"], dry_run=True)
```

## How it works

1. Parse the source SVG with `xml.etree.ElementTree` and list top-level groups
   other than `figure_1`.
2. Extract the exact XML snippets for those groups and any referenced
   definitions (`url(#id)`, markers, clip-paths, etc.).
3. Replace existing snippets with the same IDs in the destination SVG or append
   them above the trailing `<defs>` section.
4. Write the merged document back to disk (unless `--dry-run` is active).

The original layout/formatting is preserved because snippets are copied from the
raw SVG text rather than regenerated from the XML tree.

## Requirements

- Python 3.8+
- Standard library only (no extra dependencies)

## Notes

- The tool assumes manual annotations live at the top level of the SVG (as is
  typical for Matplotlib exports subsequently edited in Inkscape). For other
  layouts, use `--include` to target the groups you need.
- Existing annotations in the target with the same IDs are replaced, enabling
  repeatable workflows.
