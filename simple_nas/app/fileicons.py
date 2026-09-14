#!/usr/bin/env python3
"""File-type icons in the style of a desktop file explorer.

One source for both web apps: the share site calls icon() from its templates,
the admin UI receives kinds() and svgs() as JSON and picks the same SVG in the
browser. The icons are drawn here (a page with a coloured badge or glyph) -
no vendor logos, no image files, nothing loaded from elsewhere, so they pass
the share site's strict Content-Security-Policy.
"""

from markupsafe import Markup

_KIND_EXT = {
    "word": ("doc", "docx", "docm", "dot", "dotx", "odt", "ott", "rtf", "pages"),
    "excel": ("xls", "xlsx", "xlsm", "xlsb", "xlt", "xltx", "ods", "ots", "csv", "tsv", "numbers"),
    "powerpoint": ("ppt", "pptx", "pptm", "pps", "ppsx", "pot", "potx", "odp", "otp", "key"),
    "pdf": ("pdf",),
    "image": ("jpg", "jpeg", "png", "gif", "webp", "avif", "heic", "heif", "bmp", "tif", "tiff", "svg", "ico",
              "raw", "cr2", "cr3", "nef", "arw", "dng", "psd", "xcf"),
    "video": ("mp4", "m4v", "mkv", "mov", "avi", "wmv", "webm", "mpg", "mpeg", "ts", "m2ts", "3gp", "flv", "ogv"),
    "audio": ("mp3", "m4a", "aac", "flac", "wav", "ogg", "oga", "opus", "wma", "aiff", "alac", "mid", "midi"),
    "archive": ("zip", "7z", "rar", "tar", "gz", "tgz", "bz2", "xz", "zst", "iso", "img", "dmg", "cab"),
    "text": ("txt", "md", "log", "nfo", "ini", "cfg", "conf", "env", "properties", "toml"),
    "code": ("py", "js", "ts", "jsx", "tsx", "html", "htm", "css", "scss", "json", "yaml", "yml", "xml", "sh",
             "bash", "ps1", "bat", "cmd", "php", "rb", "go", "rs", "java", "c", "h", "cpp", "hpp", "cs", "sql",
             "vue", "kt", "swift", "lua", "pl", "dockerfile"),
    "app": ("exe", "msi", "apk", "deb", "rpm", "appimage", "jar", "dll", "so", "bin"),
    "mail": ("eml", "msg", "mbox", "vcf", "ics"),
}
_EXT_KIND = {ext: kind for kind, exts in _KIND_EXT.items() for ext in exts}

_PAGE = ('<path d="M6 2h8l5 5v14a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1z" fill="#fff" stroke="#b3bac4"/>'
         '<path d="M14 2v4a1 1 0 0 0 1 1h4" fill="#e6e9ed" stroke="#b3bac4" stroke-linejoin="round"/>')
_FONT = 'font-family="Segoe UI,Roboto,Arial,sans-serif" font-weight="700" text-anchor="middle" fill="#fff"'


def _badge(color, letters):
    if len(letters) == 1:
        return (f'<rect x="1.5" y="10" width="11" height="10" rx="1.6" fill="{color}"/>'
                f'<text x="7" y="17.9" font-size="8" {_FONT}>{letters}</text>')
    return (f'<rect x="1" y="11" width="15" height="8" rx="1.6" fill="{color}"/>'
            f'<text x="8.5" y="17.3" font-size="5.6" {_FONT}>{letters}</text>')


_GLYPH = {
    "word": _badge("#2b579a", "W"),
    "excel": _badge("#1d7044", "X"),
    "powerpoint": _badge("#c43e1c", "P"),
    "pdf": _badge("#d93025", "PDF"),
    "image": ('<rect x="7" y="10" width="10" height="8.5" rx="1" fill="#7e57c2"/>'
              '<path d="M7.8 17.6l2.9-3.1 1.9 1.9 1.4-1.3 2.3 2.5z" fill="#fff"/>'
              '<circle cx="14.4" cy="12.3" r="1.1" fill="#fff"/>'),
    "video": ('<rect x="7" y="10" width="10" height="8.5" rx="1" fill="#c2185b"/>'
              '<path d="M10.8 12.2v4.1l3.4-2.05z" fill="#fff"/>'),
    "audio": ('<circle cx="10.3" cy="16.4" r="1.9" fill="#ef6c00"/><circle cx="15" cy="15.2" r="1.9" fill="#ef6c00"/>'
              '<path d="M12.2 16.4v-5.6l4.7-1.3v5.7" fill="none" stroke="#ef6c00" stroke-width="1.4"/>'),
    "archive": ('<path d="M10.6 2h2.8v11h-2.8z" fill="#f2b705"/>'
                '<path d="M10.6 3.5h1.4M12 5.5h1.4M10.6 7.5h1.4M12 9.5h1.4" stroke="#7a5c00" stroke-width="1"/>'
                '<rect x="10.1" y="12.5" width="3.8" height="4.5" rx=".8" fill="#7a5c00"/>'),
    "text": '<path d="M8 11h8M8 13.8h8M8 16.6h5.5" stroke="#8a939e" stroke-width="1.3" stroke-linecap="round"/>',
    "code": ('<path d="M10.2 11.3l-2.6 2.9 2.6 2.9M13.8 11.3l2.6 2.9-2.6 2.9" fill="none" stroke="#1e88e5" '
             'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>'),
    "app": ('<rect x="7" y="10" width="10" height="8.5" rx="1" fill="#546e7a"/>'
            '<path d="M7 12.3h10" stroke="#fff" stroke-width="1"/><circle cx="8.3" cy="11.1" r=".45" fill="#fff"/>'),
    "mail": ('<rect x="7" y="10.5" width="10" height="7.5" rx="1" fill="#0288d1"/>'
             '<path d="M7.5 11.2l4.5 3.6 4.5-3.6" fill="none" stroke="#fff" stroke-width="1"/>'),
    "file": "",
}

_FOLDER = ('<path d="M2 6.5A2.5 2.5 0 0 1 4.5 4H9l2.2 2.2h8.3A2.5 2.5 0 0 1 22 8.7V9H2z" fill="#e3a21a"/>'
           '<path d="M2 8.6h20v9.4a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2z" fill="#f9c74f"/>')


def kind(name, is_dir=False):
    if is_dir:
        return "folder"
    base = str(name or "").lower()
    ext = base.rsplit(".", 1)[-1] if "." in base else base
    return _EXT_KIND.get(ext, "file")


def svg_for_kind(k):
    body = _FOLDER if k == "folder" else _PAGE + _GLYPH.get(k, "")
    return f'<svg class="ficon ficon-{k}" viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">{body}</svg>'


def icon(name, is_dir=False):
    """Markup for a Jinja template."""
    return Markup(svg_for_kind(kind(name, is_dir)))


def svgs():
    """kind -> SVG, for the admin UI."""
    return {k: svg_for_kind(k) for k in list(_GLYPH) + ["folder"]}


def ext_kinds():
    """extension -> kind, for the admin UI."""
    return dict(_EXT_KIND)
