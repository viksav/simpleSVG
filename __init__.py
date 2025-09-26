"""Utilities for copying manually created annotations between SVG files."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import xml.etree.ElementTree as ET

__all__ = ["copy_annotations", "list_overlay_groups", "parse_expression", "main"]


class AnnotationError(RuntimeError):
    """Raised when annotations cannot be processed."""


_MATPLOTLIB_PREFIXES = (
    "figure_",
    "axes_",
    "patch_",
    "legend_",
    "line",
    "xtick_",
    "ytick_",
    "matplotlib.axis_",
    "polycollection_",
    "pathcollection_",
    "streamplot_",
    "quiver_",
    "table_",
    "text_",
    "image_",
    "spine_",
    "pane_",
    "eventplot_",
    "barcontainer_",
    "mpl_toolkits",
)


@dataclass
class _CandidateGroup:
    group_id: str
    element: ET.Element
    snippet: str
    start: int
    end: int


def _local_name(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def _ensure_trailing_newline(text: str) -> str:
    return text if text.endswith("\n") else text + "\n"


def _extract_single_tag_with_pos(svg_text: str, element_id: str) -> Optional[Tuple[str, int]]:
    pattern = re.compile(
        r"<([\\w:-]+)[^>]*id=\"%s\"[^>]*?(?:/>|>.*?</\\1>)" % re.escape(element_id),
        re.DOTALL,
    )
    match = pattern.search(svg_text)
    if not match:
        return None
    return match.group(0), match.start()


def _extract_tag_block_with_pos(svg_text: str, element_id: str, tag: str = "g") -> Optional[Tuple[str, int]]:
    id_token = f'id="{element_id}"'
    id_pos = svg_text.find(id_token)
    if id_pos == -1:
        return None
    open_start = svg_text.rfind(f"<{tag}", 0, id_pos)
    if open_start == -1:
        return None
    segment = svg_text[open_start:]
    depth = 0
    for match in re.finditer(rf"</?{tag}\b", segment):
        token = match.group()
        if token == f"<{tag}":
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                close_idx = segment.find('>', match.start())
                if close_idx == -1:
                    return None
                end_pos = open_start + close_idx + 1
                return svg_text[open_start:end_pos], open_start
    return None


def _looks_like_matplotlib_group(group_id: str) -> bool:
    lid = group_id.lower()
    return any(lid.startswith(prefix) for prefix in _MATPLOTLIB_PREFIXES)


def _discover_candidate_groups(root: ET.Element, svg_text: str) -> List[_CandidateGroup]:
    parent_map = {child: parent for parent in root.iter() for child in parent}

    raw_candidates: List[_CandidateGroup] = []
    for element in root.iter():
        if _local_name(element.tag) != 'g':
            continue
        gid = element.get('id')
        if not gid or gid == 'figure_1':
            continue
        if _looks_like_matplotlib_group(gid):
            continue

        parent = parent_map.get(element)
        skip = False
        while parent is not None:
            pid = parent.get('id')
            if pid and (pid == 'figure_1' or _looks_like_matplotlib_group(pid)):
                skip = True
                break
            parent = parent_map.get(parent)
        if skip:
            continue
        if len(element.attrib) == 1 and element.attrib.get('id'):
            continue

        extracted = _extract_tag_block_with_pos(svg_text, gid, tag='g')
        if extracted is None:
            continue
        snippet, start = extracted
        if 'id="figure_1"' in snippet:
            continue
        raw_candidates.append(_CandidateGroup(gid, element, snippet, start, start + len(snippet)))

    raw_candidates.sort(key=lambda cand: cand.start)

    candidates: List[_CandidateGroup] = []
    for cand in raw_candidates:
        if any(cand.start >= existing.start and cand.end <= existing.end for existing in candidates):
            continue
        candidates.append(cand)
    return candidates


def _collect_referenced_ids(element: ET.Element) -> List[str]:
    refs: List[str] = []
    seen = set()
    for node in element.iter():
        for attr, value in node.attrib.items():
            if not isinstance(value, str):
                continue
            for ref in re.findall(r"url\(#([^\)]+)\)", value):
                if ref not in seen:
                    refs.append(ref)
                    seen.add(ref)
            if (
                ("path-effect" in attr or attr in {"marker-start", "marker-mid", "marker-end", "filter", "clip-path"})
                and value.startswith('#')
            ):
                ref = value[1:]
                if ref not in seen:
                    refs.append(ref)
                    seen.add(ref)
    return refs


def _insert_or_replace_definition(dest_text: str, element_id: str, snippet: str) -> Tuple[str, bool]:
    prepared = _ensure_trailing_newline(snippet)
    pattern = re.compile(
        r"[ \t]*<[\\w:-]+[^>]*id=\"%s\"[^>]*?(?:/>|>.*?</[\\w:-]+>)\n?" % re.escape(element_id),
        re.DOTALL,
    )
    match = pattern.search(dest_text)
    if match:
        new_text = dest_text[: match.start()] + prepared + dest_text[match.end():]
        return new_text, new_text != dest_text

    close_idx = dest_text.find("</defs>")
    if close_idx == -1:
        raise AnnotationError("Destination SVG missing </defs> tag for annotation defs")

    insertion = prepared
    if close_idx > 0 and dest_text[close_idx - 1] != "\n":
        insertion = "\n" + insertion
    new_text = dest_text[:close_idx] + insertion + dest_text[close_idx:]
    return new_text, True


def _insert_or_replace_group(dest_text: str, element_id: str, snippet: str) -> Tuple[str, bool]:
    prepared = _ensure_trailing_newline(snippet)
    pattern = re.compile(r"[ \t]*<g[^>]*id=\"%s\"[^>]*>.*?</g>\n?" % re.escape(element_id), re.DOTALL)
    match = pattern.search(dest_text)
    if match:
        new_text = dest_text[: match.start()] + prepared + dest_text[match.end():]
        return new_text, new_text != dest_text

    marker = "\n </g>\n <defs>"
    marker_idx = dest_text.find(marker)
    if marker_idx == -1:
        marker = "\n</svg>"
        marker_idx = dest_text.find(marker)
        if marker_idx == -1:
            raise AnnotationError("Could not locate insertion point for annotation groups in target SVG")

    insertion = prepared
    if marker_idx > 0 and dest_text[marker_idx - 1] != "\n":
        insertion = "\n" + insertion
    new_text = dest_text[:marker_idx] + insertion + dest_text[marker_idx:]
    return new_text, True


def list_overlay_groups(svg_path: Path) -> List[str]:
    text = svg_path.read_text()
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise AnnotationError(f"Could not parse {svg_path}: {exc}") from exc
    return [cand.group_id for cand in _discover_candidate_groups(root, text)]


def copy_annotations(
    source_svg: Path | str,
    target_svg: Path | str,
    *,
    include: Optional[Sequence[str]] = None,
    exclude: Optional[Sequence[str]] = None,
    dry_run: bool = False,
) -> List[str]:
    """Copy overlay groups and referenced defs from *source_svg* into *target_svg*."""

    source_path = Path(source_svg)
    target_path = Path(target_svg)

    if not source_path.exists():
        raise AnnotationError(f"Source SVG not found: {source_path}")
    if not target_path.exists():
        raise AnnotationError(f"Target SVG not found: {target_path}")

    src_text = source_path.read_text()
    dest_text = target_path.read_text()

    try:
        src_root = ET.fromstring(src_text)
    except ET.ParseError as exc:
        raise AnnotationError(f"Could not parse {source_path}: {exc}") from exc

    candidates = _discover_candidate_groups(src_root, src_text)

    if include:
        include_set = set(include)
        candidates = [cand for cand in candidates if cand.group_id in include_set]
    if exclude:
        exclude_set = set(exclude)
        candidates = [cand for cand in candidates if cand.group_id not in exclude_set]

    if not candidates:
        raise AnnotationError("No annotation groups available after filtering.")

    definition_snippets: List[Tuple[int, str, str]] = []
    group_snippets: List[Tuple[int, str, str]] = []
    ref_seen = set()

    for cand in candidates:
        snippet = cand.snippet
        group_snippets.append((cand.start, cand.group_id, snippet))
        for ref in _collect_referenced_ids(cand.element):
            if ref not in ref_seen:
                extracted = _extract_single_tag_with_pos(src_text, ref)
                if extracted is not None:
                    snippet_def, pos = extracted
                    definition_snippets.append((pos, ref, snippet_def))
                    ref_seen.add(ref)

    definition_snippets.sort(key=lambda item: item[0])
    group_snippets.sort(key=lambda item: item[0])

    new_text = dest_text
    changed = False

    for _, def_id, snippet in definition_snippets:
        new_text, updated = _insert_or_replace_definition(new_text, def_id, snippet)
        changed = changed or updated

    copied_groups: List[str] = []
    for _, gid, snippet in group_snippets:
        new_text, updated = _insert_or_replace_group(new_text, gid, snippet)
        if updated:
            copied_groups.append(gid)
            changed = True

    if changed and not dry_run:
        target_path.write_text(new_text)

    return copied_groups


def parse_expression(value: str) -> Tuple[str, str]:
    expr = value.strip()
    if '>' in expr:
        left, right = expr.split('>', 1)
        source, target = left.strip(), right.strip()
    elif '<' in expr:
        left, right = expr.split('<', 1)
        source, target = right.strip(), left.strip()
    else:
        raise AnnotationError("Expression must contain '>' or '<' to indicate direction.")
    if not source or not target:
        raise AnnotationError("Both source and target paths are required in the expression.")
    return source, target


def _resolve_paths(args: argparse.Namespace) -> Tuple[Path, Path]:
    if args.expr:
        source, target = parse_expression(args.expr)
        return Path(source), Path(target)

    if args.source and args.target:
        return Path(args.source), Path(args.target)

    if args.paths:
        if len(args.paths) == 1 and ('>' in args.paths[0] or '<' in args.paths[0]):
            source, target = parse_expression(args.paths[0])
            return Path(source), Path(target)
        if len(args.paths) == 2:
            return Path(args.paths[0]), Path(args.paths[1])

    raise AnnotationError("Unable to determine source and target SVG paths from the provided arguments.")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Copy manual annotation groups from one SVG into another.",
        epilog="Examples:\n  python -m svg_annotation_transfer source.svg target.svg\n  python -m svg_annotation_transfer --expr 'source.svg>target.svg'\n  python -m svg_annotation_transfer --list-groups source.svg",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('paths', nargs='*', help='Optional source/target or an expression containing ">" or "<".')
    parser.add_argument('-e', '--expr', help='Expression of the form "source.svg>target.svg" or "target.svg<source.svg".')
    parser.add_argument('-s', '--source', help='Path to the source SVG.')
    parser.add_argument('-t', '--target', help='Path to the target SVG.')
    parser.add_argument('--include', nargs='+', help='Only copy the specified group IDs.')
    parser.add_argument('--exclude', nargs='+', help='Skip the specified group IDs.')
    parser.add_argument('--dry-run', action='store_true', help='Preview the transfer without writing to the target file.')
    parser.add_argument('--list-groups', metavar='SVG', help='List candidate annotation groups found in the given SVG and exit.')

    args = parser.parse_args(argv)

    try:
        if args.list_groups:
            groups = list_overlay_groups(Path(args.list_groups))
            if not groups:
                print("No candidate annotation groups found.")
            else:
                for gid in groups:
                    print(gid)
            return 0

        source_path, target_path = _resolve_paths(args)
        copied = copy_annotations(
            source_path,
            target_path,
            include=args.include,
            exclude=args.exclude,
            dry_run=args.dry_run,
        )
        if copied:
            action = "(dry run) would copy" if args.dry_run else "Copied"
            print(f"{action} groups: {', '.join(copied)}")
        else:
            print("No changes were necessary; target SVG already contains the requested annotations.")
        return 0
    except AnnotationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
