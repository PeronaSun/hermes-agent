"""从源 Excel 抽图并注入到现有 HTML(注入式,文字表格结构不变)。

用法:cd scripts && python3 build_html.py
输入:Project Tracking V3.backup.html(纯净文字版,只读)
输出:Project Tracking V3.html + images/
"""
import html, os, re, shutil, zipfile, posixpath
import xml.etree.ElementTree as ET

XLSX_PATH   = "source_data /Project Tracking V3.xlsx"   # 目录名结尾有空格,勿删
BACKUP_PATH = "Project Tracking V3.backup.html"
OUTPUT_PATH = "Project Tracking V3.html"
IMAGES_DIR  = "images"

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL  = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_DML  = "http://schemas.openxmlformats.org/drawingml/2006/main"
NS_XDR  = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"


def parse_workbook_sheets(xlsx_path: str) -> list[str]:
    with zipfile.ZipFile(xlsx_path) as z:
        wb = ET.fromstring(z.read("xl/workbook.xml"))
    return [s.get("name") for s in wb.find(f"{{{NS_MAIN}}}sheets")]


def slugify(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)   # 非字母数字 → 连字符
    return s.strip("-")


EXTRA_CSS = """
/* ── LVMH Theme: Image Gallery ── */
.image-gallery {
  display: flex; flex-wrap: wrap; gap: 20px;
  padding: 20px 12px 28px; align-items: flex-start;
}
.image-gallery .gallery-img {
  max-width: min(100%, 720px); height: auto;
  border-radius: 6px;
  box-shadow: 0 2px 12px rgba(27,27,27,.12);
  cursor: zoom-in; background: #fff;
  transition: box-shadow .2s ease, transform .2s ease;
  border: 1px solid #E0DAD0;
}
.image-gallery .gallery-img:hover {
  box-shadow: 0 6px 24px rgba(27,27,27,.2);
  transform: translateY(-2px);
}

/* ── LVMH Theme: Lightbox ── */
.lightbox-overlay {
  display: none; position: fixed; inset: 0; z-index: 1000;
  background: rgba(27,27,27,.88); cursor: zoom-out;
  align-items: center; justify-content: center; padding: 24px;
  backdrop-filter: blur(4px); -webkit-backdrop-filter: blur(4px);
}
.lightbox-overlay.open { display: flex; }
.lightbox-overlay img {
  max-width: 95vw; max-height: 95vh;
  border-radius: 4px;
  box-shadow: 0 8px 40px rgba(0,0,0,.4);
}

/* ── LVMH Theme: Scrollbar ── */
.sheet-content::-webkit-scrollbar { height: 8px; width: 8px; }
.sheet-content::-webkit-scrollbar-track { background: #F0EBE3; border-radius: 4px; }
.sheet-content::-webkit-scrollbar-thumb {
  background: #C6A86C; border-radius: 4px;
  border: 2px solid #F0EBE3;
}
.sheet-content::-webkit-scrollbar-thumb:hover { background: #B0944F; }

/* ── LVMH Theme: Status cell refinements ── */
td[style*=\"#C6EFCE\"] { border-color: #B8DFC2 !important; }
td[style*=\"#FFEB9C\"] { border-color: #F0D88A !important; }
"""

LIGHTBOX_HTML = '<div class="lightbox-overlay" id="lightbox"></div>'

LIGHTBOX_JS = """
(function () {
  var box = document.getElementById('lightbox');
  var big = document.createElement('img'); big.alt = '';
  if (box) box.appendChild(big);
  document.addEventListener('click', function (e) {
    var im = e.target.closest ? e.target.closest('.gallery-img') : null;
    if (im) { if (box) { big.src = im.src; box.classList.add('open'); } return; }
    if (box && box.classList.contains('open')) box.classList.remove('open');
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && box) box.classList.remove('open');
  });
})();
"""


def inject(backup_html, sheet_galleries):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(backup_html, "html.parser")

    # (a) CSS 追加到现有 <style>
    style = soup.find("style")
    if style is None:
        raise ValueError("backup HTML has no <style> tag to inject CSS into")
    style.append(EXTRA_CSS)

    # (b) gallery append 到对应 .sheet-content
    contents = soup.select(".sheet-content")
    for idx in sheet_galleries:
        if idx >= len(contents):
            raise ValueError(
                f"sheet index {idx} has no matching .sheet-content div "
                f"(HTML has {len(contents)}); is the backup HTML out of sync with the xlsx?"
            )
    for idx, gal_html in sheet_galleries.items():
        frag = BeautifulSoup(gal_html, "html.parser")
        contents[idx].append(frag)

    # (c) lightbox overlay + JS,放在 </body> 前(现有 <script> 之后)
    body = soup.find("body")
    body.append(BeautifulSoup(LIGHTBOX_HTML, "html.parser"))
    js_tag = soup.new_tag("script")
    js_tag.string = LIGHTBOX_JS
    body.append(js_tag)

    return str(soup)


def build_gallery_html(rel_filenames):
    imgs = "\n".join(
        f'    <img class="gallery-img" src="{html.escape(p, quote=True)}" loading="lazy" alt="">'
        for p in rel_filenames
    )
    return f'<div class="image-gallery">\n{imgs}\n</div>'


def export_images(xlsx_path, anchors, sheet_names, images_dir):
    if os.path.isdir(images_dir):
        shutil.rmtree(images_dir)
    os.makedirs(images_dir, exist_ok=True)
    galleries: dict[int, list[str]] = {}
    with zipfile.ZipFile(xlsx_path) as z:
        for idx, media_list in anchors.items():
            slug = slugify(sheet_names[idx])
            rels = []
            for n, media in enumerate(media_list, start=1):
                fname = f"{idx:02d}_{slug}_{n:02d}.png"
                with z.open(media) as src, open(os.path.join(images_dir, fname), "wb") as dst:
                    shutil.copyfileobj(src, dst)
                rels.append(f"{images_dir}/{fname}")
            galleries[idx] = rels
    return galleries


def _relpath(base: str, target: str) -> str:
    """把相对 Target(如 '../drawings/drawing2.xml')解析成 zip 内绝对路径。"""
    return posixpath.normpath(posixpath.join(posixpath.dirname(base), target))


def extract_image_anchors(xlsx_path: str) -> dict[int, list[str]]:
    with zipfile.ZipFile(xlsx_path) as z:
        names = set(z.namelist())
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        wrels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        rid2t = {r.get("Id"): r.get("Target") for r in wrels}

        result: dict[int, list[str]] = {}
        sheets = wb.find(f"{{{NS_MAIN}}}sheets")
        for idx, s in enumerate(sheets):
            ws_target = rid2t[s.get(f"{{{NS_REL}}}id")]          # 'worksheets/sheet6.xml'
            ws_path = "xl/" + ws_target
            ws_rels = posixpath.join(posixpath.dirname(ws_path), "_rels", posixpath.basename(ws_path) + ".rels")
            if ws_rels not in names:
                continue
            draw = None
            for r in ET.fromstring(z.read(ws_rels)):
                if r.get("Type", "").endswith("/drawing"):
                    draw = _relpath(ws_path, r.get("Target"))
            if not draw or draw not in names:
                continue
            drels_p = posixpath.join(posixpath.dirname(draw), "_rels",
                                     posixpath.basename(draw) + ".rels")
            drid2t = {r.get("Id"): r.get("Target")
                      for r in ET.fromstring(z.read(drels_p))} if drels_p in names else {}

            d = ET.fromstring(z.read(draw))
            placements = []   # (row, media_zip_path)
            for anc in list(d):
                tag = anc.tag.split("}")[-1]
                if tag not in ("twoCellAnchor", "oneCellAnchor"):
                    continue
                frm = anc.find(f"{{{NS_XDR}}}from")
                row_elem = frm.find(f"{{{NS_XDR}}}row") if frm is not None else None
                row = int(row_elem.text) if row_elem is not None and row_elem.text else 0
                blip = anc.find(f".//{{{NS_DML}}}blip")
                if blip is None:
                    continue
                embed = blip.get(f"{{{NS_REL}}}embed")
                media = _relpath(draw, drid2t.get(embed, ""))
                if not media.lower().endswith(".png"):
                    continue   # 跳过非 PNG(SVG 主 blip 不会到这,因为主 blip 是 png)
                placements.append((row, media))
            if not placements:
                continue
            placements.sort(key=lambda x: x[0])     # 同 sheet 内按 from-row 升序
            result[idx] = [m for _, m in placements]
        return result


def main():
    sheet_names = parse_workbook_sheets(XLSX_PATH)
    anchors = extract_image_anchors(XLSX_PATH)
    galleries = export_images(XLSX_PATH, anchors, sheet_names, IMAGES_DIR)
    sheet_html = {idx: build_gallery_html(rels) for idx, rels in galleries.items()}
    with open(BACKUP_PATH, encoding="utf-8") as f:
        backup_html = f.read()
    out = inject(backup_html, sheet_html)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(out)
    n_imgs = sum(len(v) for v in galleries.values())
    print(f"✅ wrote {OUTPUT_PATH}: {len(galleries)} sheets, {n_imgs} images -> {IMAGES_DIR}/")


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()
