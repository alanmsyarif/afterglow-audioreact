#!/usr/bin/env python3
"""Build self-contained offline user guides and deterministic documentation ZIPs.

No Blender, web server, CDN, external fonts or Python dependencies are needed.
Run: python tools/build_docs.py --edition all
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import mimetypes
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def escape(value):
    return html.escape(str(value), quote=True)


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def data_url(path):
    mime = mimetypes.guess_type(path.name)[0]
    if mime not in {"image/png", "image/jpeg", "image/gif", "image/svg+xml", "image/webp"}:
        raise ValueError(f"Unsupported guide media: {path}")
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def visual_html(key, registry, source):
    if key not in registry:
        raise ValueError(f"Unknown visual: {key}")
    entry = registry[key]
    image = data_url(source / entry["path"])
    alt, caption = entry["alt"], entry["caption"]
    animation = ""
    if entry.get("animation"):
        gif = data_url(source / entry["animation"])
        animation = (f'<button class="media-toggle" type="button" aria-pressed="false" '
                     f'data-animation="{gif}">Play animation</button>')
    return (f'<figure class="visual"><button class="visual-open" type="button" '
            f'aria-label="Enlarge image: {escape(alt)}"><img src="{image}" alt="{escape(alt)}" '
            f'loading="lazy" decoding="async"></button><figcaption class="visual-caption">'
            f'<span class="caption-copy">{escape(caption)}</span><div class="visual-controls">'
            f'{animation}<span class="zoom-hint">Select the image to enlarge</span>'
            f'</div></figcaption></figure>')


def sections_for(edition, source):
    free = load_json(source / "free.json")
    if edition == "free":
        return free, free["sections"]
    pro = load_json(source / "pro.json")
    common = []
    for original in free["sections"]:
        if original.get("free_only"):
            continue
        section = dict(original)
        for name in ("body", "title", "summary", "visual", "group"):
            if f"pro_{name}" in section:
                section[name] = section[f"pro_{name}"]
        common.append(section)
    # Keep getting started and object tools first, then paid environment tools,
    # then shared rendering, cleanup, help and edition information.
    start = [s for s in common if group_for(s) == "Start here"]
    objects = [s for s in common if group_for(s) == "Object reaction"]
    help_sections = [s for s in common if group_for(s) == "Help & finishing"]
    environment_start, environment, environment_help = [], [], []
    for original in pro["sections"]:
        section = dict(original)
        if section["id"] == "env-start":
            section["group"] = "Start here"
            environment_start.append(section)
        elif section["id"] == "env-cleanup-help":
            section["group"] = "Help & finishing"
            environment_help.append(section)
        else:
            section["group"] = "Environment effects"
            environment.append(section)
    return pro, start + environment_start + objects + environment + environment_help + help_sections


def group_for(section):
    if section.get("group"):
        return "Help & finishing" if section["group"] == "Help" else section["group"]
    ident = section["id"]
    if re.search(r"install|start|first|quick", ident) and not ident.startswith("env"):
        return "Start here"
    if re.search(r"render|cleanup|trouble|upgrade|edition|help|faq", ident):
        return "Help & finishing"
    if ident.startswith(("env", "strike", "laser", "cascade", "gun", "signal", "venue", "live")):
        return "Environment effects"
    return "Object reaction"


def make_toc(sections):
    parts, previous = [], None
    for section in sections:
        group = group_for(section)
        if group != previous:
            parts.append(f'<p class="nav-group">{escape(group)}</p>')
            previous = group
        parts.append(f'<a href="#{escape(section["id"])}">{escape(section["title"])}</a>')
    return "".join(parts)


def make_guide(edition, source):
    info, sections = sections_for(edition, source)
    registry = load_json(source / "media.json")
    styles = (source / "style.css").read_text(encoding="utf-8")
    script = (source / "guide.js").read_text(encoding="utf-8")
    seen, content = set(), []
    for section in sections:
        ident = section["id"]
        if not re.fullmatch(r"[a-z][a-z0-9-]*", ident) or ident in seen:
            raise ValueError(f"Invalid or duplicate section ID: {ident}")
        seen.add(ident)
        body = section["body"]
        if re.search(r"<(script|style|iframe|h1|h2)\b", body, re.I):
            raise ValueError(f"Unexpected guide markup in {ident}")
        if ident == "install" and info.get("download_url"):
            body = f'<p><a href="{escape(info["download_url"])}">Download the {escape(info["title"])} {escape(info["version"])} add-on ZIP →</a></p>' + body
        visuals = section.get("visuals", [])
        if section.get("visual"):
            visuals = [section["visual"], *visuals]
        figures = "".join(visual_html(key, registry, source) for key in visuals)
        content.append(f'<section class="section" id="{ident}" aria-labelledby="{ident}-title">'
                       f'<span class="section-kicker">{escape(group_for(section))}</span>'
                       f'<h2 id="{ident}-title">{escape(section["title"])}</h2>'
                       f'<p class="section-summary">{escape(section.get("summary", ""))}</p>'
                       f'<div class="section-body">{body}</div>{figures}</section>')
    toc = make_toc(sections)
    first = next((s["id"] for s in sections if re.search(r"first|quick|start", s["id"])), sections[0]["id"])
    install = next((s["id"] for s in sections if "install" in s["id"]), sections[0]["id"])
    title, version = info["title"], info["version"]
    repository = "noctune-pro" if edition == "pro" else "afterglow-audioreact"
    intro = info["description"]
    shortcuts = (f'<a href="#env-start">Build an environment →</a><a href="#{first}">React your objects</a>'
                 if edition == "pro" else f'<a href="#{first}">Make your first reaction →</a>')
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="{escape(intro)}"><title>{escape(title)} · User guide</title><style>{styles}</style></head>
<body><a class="skip" href="#main">Skip to the guide</a><div class="layout">
<aside class="sidebar" aria-label="Guide navigation"><a class="wordmark" href="#top">NOCTUNE</a>
<span class="edition">{edition.upper()} · USER GUIDE</span><label class="search-label" for="guide-search">Find a feature or button</label>
<div class="search-wrap"><input class="search" id="guide-search" type="search" placeholder="Search this guide…" autocomplete="off" aria-describedby="search-status"><button class="search-clear" id="search-clear" aria-label="Clear search" type="button" hidden>×</button></div>
<output class="search-status" id="search-status" aria-live="polite"></output><nav class="toc" aria-label="Contents">{toc}</nav>
<div class="mobile-nav"><details id="mobile-contents"><summary>Browse all topics</summary><nav class="toc" aria-label="Mobile contents">{toc}</nav></details></div>
<div class="sidebar-bottom"><span>{escape(title)} {escape(version)}<br>Blender 5.2 or newer</span><a href="https://github.com/alanmsyarif/{repository}">Project and downloads →</a><button class="print-guide" type="button">Print / save as PDF</button></div></aside>
<main class="content" id="main"><header class="hero" id="top"><span class="eyebrow">Your music. Your scene.</span>
<h1>{escape(title)}<br>Made simple.</h1><p class="intro">{escape(intro)}</p>
<div class="badges"><span class="badge">Blender 5.2+</span><span class="badge">EEVEE &amp; Cycles</span><span class="badge">Guide for {escape(version)}</span><span class="badge">Works offline</span></div>
<div class="jump-row">{shortcuts}<a href="#{install}">Install the add-on</a></div>
<p class="offline-note">Start with the short steps. Open optional controls only when you need them. Images are included; animations play when you choose.</p></header>
<noscript><p class="note">The complete guide and images work without JavaScript. Search, image enlargement and animation playback need JavaScript enabled.</p></noscript>
<div id="no-results" class="empty-state" hidden>No topics match. Try “music”, “glow” or a shorter button name, or clear the search.</div>
{"".join(content)}<footer class="page-footer"><span>{escape(title)} {escape(version)} · Offline user guide</span><span>Example scenes illustrate the features; they are not included stages.</span><a href="#top">Back to top ↑</a></footer></main></div>
<dialog id="image-dialog" aria-label="Enlarged guide image"><button class="zoom-close" type="button">Close image ×</button><img class="zoom-image" alt=""><p class="zoom-caption"></p></dialog>
<script>{script}</script></body></html>
'''


def make_index(editions, source):
    styles = (source / "style.css").read_text(encoding="utf-8")
    cards = []
    for edition in editions:
        info = load_json(source / f"{edition}.json")
        cards.append(f'<a class="landing-card" href="noctune-{edition}.html"><span>{edition.upper()} · {escape(info["version"])}</span>'
                     f'<h2>{escape(info["title"])}</h2><p>{escape(info["description"])}</p><span>Open the guide →</span></a>')
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Noctune · Choose your guide</title><style>{styles}</style></head><body><main class="landing"><a class="wordmark" href="index.html">NOCTUNE</a><p class="eyebrow">Offline documentation</p><h1>Less tweaking.<br>More creating.</h1><p class="intro">Choose your edition. Each guide includes quick steps, feature examples and optional controls.</p><div class="landing-grid">{"".join(cards)}</div><p class="note">Open these files in a web browser. Images and animations are embedded. You only need the internet to follow download links.</p><p class="offline-note">Install one edition of the add-on at a time. Pro includes the Free object-reaction tools and adds environment generation.</p></main></body></html>'''


def write_zip(path, files):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, payload in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, payload, compresslevel=9)


def build(edition, source, output, dist):
    available = [name for name in ("free", "pro") if (source / f"{name}.json").is_file()]
    editions = available if edition == "all" else [edition]
    if not editions or any(name not in available for name in editions):
        raise ValueError(f"Edition source unavailable: {edition}")
    output.mkdir(parents=True, exist_ok=True)
    files = {}
    for name in editions:
        rendered = make_guide(name, source).encode("utf-8")
        filename = f"noctune-{name}.html"
        (output / filename).write_bytes(rendered)
        files[filename] = rendered
        print(f"Built {output / filename} ({len(rendered):,} bytes)")
    files["index.html"] = make_index(editions, source).encode("utf-8")
    (output / "index.html").write_bytes(files["index.html"])
    files["READ-ME.txt"] = ("Noctune offline user guides\n\n1. Extract the ZIP.\n2. Open index.html in a web browser.\n\nEach guide also works by itself. Images and animations are embedded.\nInternet is only needed for external project/download links.\nUse the search box or browse topics. Animations have Play/Pause controls.\n\nThese are documentation files; install the add-on ZIP separately in Blender.\n").encode("utf-8")
    files["LICENSE.txt"] = (ROOT / "LICENSE").read_bytes()
    filename = "noctune-documentation.zip" if len(editions) > 1 else f"noctune-{editions[0]}-documentation.zip"
    write_zip(dist / filename, files)
    print(f"Packed {dist / filename}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--edition", choices=("free", "pro", "all"), default="all")
    parser.add_argument("--source-dir", type=Path, default=ROOT / "docs" / "manual")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "docs" / "html")
    parser.add_argument("--dist-dir", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    build(args.edition, args.source_dir.resolve(), args.output_dir.resolve(), args.dist_dir.resolve())


if __name__ == "__main__":
    main()
