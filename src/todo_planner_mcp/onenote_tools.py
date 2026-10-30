"""Microsoft OneNote tools (READ-ONLY) over /me/onenote and my groups' /onenote.

Needs the delegated Notes.Read(.All) scope (no admin consent). /me/onenote covers notebooks
the user owns and ones shared with them; team notebooks live under /groups/{id}/onenote
and are NOT included there (verified live 2026-10-12), so we search both. Page bodies
come back as HTML; we flatten them to plain text so the assistant gets readable content.
"""
from __future__ import annotations

from html.parser import HTMLParser

from .graph import GraphClient, GraphError

_MAX_TOP = 100  # OneNote caps $top at 100 (default 20)


def register(mcp, gc: GraphClient) -> None:

    def sources() -> list[str]:
        """OneNote roots to search: mine, then each M365 group I'm in."""
        roots = ["me/onenote"]
        for g in gc.get_all("me/memberOf", params={"$select": "id"}):
            if g.get("@odata.type") == "#microsoft.graph.group":
                roots.append(f"groups/{g['id']}/onenote")
        return roots

    def gather(suffix: str, params: dict | None = None, max_items: int | None = None) -> list[dict]:
        """GET <root>/<suffix> for every root, skipping roots that error
        (e.g. 401 when the user's OneDrive isn't provisioned, or a group with no notebook)."""
        out: list[dict] = []
        errors: list[GraphError] = []
        for root in sources():
            try:
                out.extend(gc.get_all(f"{root}/{suffix}", params=params, max_items=max_items))
            except GraphError as e:
                errors.append(e)
        if not out and errors and len(errors) == len(sources()):
            raise errors[0]
        return out

    def find(suffix: str) -> tuple[str, dict]:
        """Locate an entity by id across roots; return (root, entity)."""
        last: GraphError | None = None
        for root in sources():
            try:
                return root, gc.get(f"{root}/{suffix}")
            except GraphError as e:
                last = e
        raise last or GraphError(404, f"not found: {suffix}")

    def recent(items: list[dict], top: int) -> list[dict]:
        items.sort(key=lambda p: p.get("lastModifiedDateTime") or "", reverse=True)
        return items[:top]

    @mcp.tool()
    def onenote_list_notebooks() -> list[dict]:
        """List my OneNote notebooks (owned, shared with me, and my groups' team notebooks)."""
        return [{"id": n["id"], "displayName": n.get("displayName"),
                 "isShared": n.get("isShared"), "userRole": n.get("userRole"),
                 "lastModified": n.get("lastModifiedDateTime"),
                 "webUrl": _web_url(n)}
                for n in gather("notebooks")]

    @mcp.tool()
    def onenote_list_sections(notebook_id: str | None = None) -> list[dict]:
        """List sections, either in one notebook or across all notebooks (includes nested section groups)."""
        if notebook_id:
            root, _ = find(f"notebooks/{notebook_id}")
            sections = gc.get_all(f"{root}/notebooks/{notebook_id}/sections")
        else:
            sections = gather("sections")
        return [{"id": s["id"], "displayName": s.get("displayName"),
                 "notebook": (s.get("parentNotebook") or {}).get("displayName"),
                 "lastModified": s.get("lastModifiedDateTime")}
                for s in sections]

    @mcp.tool()
    def onenote_list_pages(section_id: str | None = None, top: int = 20) -> list[dict]:
        """List pages in a section, or my most recently modified pages across all notebooks."""
        top = max(1, min(top, _MAX_TOP))
        params = {"$top": top}  # default order: last modified first
        if section_id:
            root, _ = find(f"sections/{section_id}")
            pages = gc.get_all(f"{root}/sections/{section_id}/pages", params=params, max_items=top)
        else:
            pages = recent(gather("pages", params=params, max_items=top), top)
        return [_page_summary(p) for p in pages]

    @mcp.tool()
    def onenote_search_pages(query: str, top: int = 20) -> list[dict]:
        """Find pages whose TITLE contains the text (case-insensitive). Does not search page bodies."""
        top = max(1, min(top, _MAX_TOP))
        q = query.lower().replace("'", "''")  # OData string literal escaping
        params = {"$filter": f"contains(tolower(title),'{q}')", "$top": top}
        return [_page_summary(p) for p in recent(gather("pages", params=params, max_items=top), top)]

    @mcp.tool()
    def onenote_get_page(page_id: str, max_chars: int = 20000) -> dict:
        """Read a page: metadata plus its content as plain text (to-dos shown as [ ] / [x])."""
        root, meta = find(f"pages/{page_id}")
        text = html_to_text(gc.get_text(f"{root}/pages/{page_id}/content"))
        truncated = len(text) > max_chars
        out = _page_summary(meta)
        out.update({"text": text[:max_chars], "truncated": truncated})
        return out


# ---------- helpers ----------
def _web_url(entity: dict) -> str | None:
    return ((entity.get("links") or {}).get("oneNoteWebUrl") or {}).get("href")


def _page_summary(p: dict) -> dict:
    return {
        "id": p.get("id"),
        "title": p.get("title"),
        "section": (p.get("parentSection") or {}).get("displayName"),
        "created": p.get("createdDateTime"),
        "lastModified": p.get("lastModifiedDateTime"),
        "webUrl": _web_url(p),
    }


_BLOCK = {"p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table", "ul", "ol", "title"}


class _TextExtractor(HTMLParser):
    """Flatten OneNote page HTML: block tags -> newlines, list items -> bullets,
    table cells -> ' | ', to-do tags -> checkboxes, images/attachments -> placeholders."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0  # inside <style>/<script>/<head> metadata

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("style", "script"):
            self._skip += 1
        elif tag in _BLOCK:
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag == "td":
            self.parts.append(" | ")
        elif tag == "img":
            self.parts.append(f"[image{': ' + a['alt'] if a.get('alt') else ''}]")
        elif tag == "object":
            self.parts.append(f"[attachment: {a.get('data-attachment', 'file')}]")
        tags = a.get("data-tag") or ""
        if "to-do" in tags:
            self.parts.append("[x] " if "to-do:completed" in tags else "[ ] ")

    def handle_endtag(self, tag):
        if tag in ("style", "script"):
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    p = _TextExtractor()
    p.feed(html)
    lines = [" ".join(line.split()) for line in "".join(p.parts).splitlines()]
    out: list[str] = []
    for line in lines:  # collapse runs of blank lines
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()
