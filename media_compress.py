# Compress attachments only when over Discord's size budget (~10MB hard limit).
# Small files pass through untouched.
from __future__ import annotations

import io

DISCORD_SOFT_MAX = 9 * 1024 * 1024  # compress only above this


def _is_image_name(name: str) -> bool:
    n = (name or '').lower().split('?')[0]
    return n.endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.tif', '.tiff'))


def _is_pdf_name(name: str) -> bool:
    n = (name or '').lower().split('?')[0]
    return n.endswith('.pdf')


def _looks_like_image(data: bytes) -> bool:
    if len(data) < 8:
        return False
    if data[:3] == b'\xff\xd8\xff':
        return True
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return True
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return True
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return True
    return False


def _compress_image(data: bytes, filename: str, max_bytes: int) -> tuple[bytes, str]:
    try:
        from PIL import Image
    except ImportError:
        print('[COMPRESS] Pillow not installed — cannot shrink image')
        return data, filename

    img = Image.open(io.BytesIO(data))
    if img.mode in ('RGBA', 'P', 'LA'):
        bg = Image.new('RGB', img.size, (255, 255, 255))
        rgba = img.convert('RGBA')
        bg.paste(rgba, mask=rgba.split()[-1])
        img = bg
    elif img.mode != 'RGB':
        img = img.convert('RGB')

    base = (filename or 'image').rsplit('.', 1)[0] or 'image'
    out_name = f'{base}.jpg'
    w, h = img.size
    out = data

    for scale in (1.0, 0.85, 0.7, 0.55, 0.4):
        work = img if scale == 1.0 else img.resize(
            (max(1, int(w * scale)), max(1, int(h * scale))),
            Image.Resampling.LANCZOS,
        )
        for quality in (85, 75, 65, 55, 45, 35):
            buf = io.BytesIO()
            work.save(buf, format='JPEG', quality=quality, optimize=True)
            out = buf.getvalue()
            if len(out) <= max_bytes:
                return out, out_name
    return out, out_name


def _pdf_save_bytes(doc) -> bytes:
    buf = io.BytesIO()
    try:
        doc.save(buf, garbage=4, deflate=True, clean=True, deflate_images=True, deflate_fonts=True)
    except TypeError:
        # older PyMuPDF without deflate_images
        doc.save(buf, garbage=4, deflate=True, clean=True)
    return buf.getvalue()


def _compress_pdf(data: bytes, filename: str, max_bytes: int) -> tuple[bytes, str]:
    try:
        import fitz  # PyMuPDF
    except ImportError:
        print('[COMPRESS] PyMuPDF not installed — cannot shrink PDF')
        return data, filename

    name = filename if _is_pdf_name(filename) else 'file.pdf'
    doc = fitz.open(stream=data, filetype='pdf')
    try:
        out = _pdf_save_bytes(doc)
        if len(out) <= max_bytes:
            return out, name

        # still big: rasterize pages at lower DPI into a new PDF (lossy)
        print('[COMPRESS] PDF still large — rasterizing pages')
        out = data
        for zoom in (1.0, 0.75, 0.55, 0.4):
            new_doc = fitz.open()
            try:
                mat = fitz.Matrix(zoom, zoom)
                for page in doc:
                    pix = page.get_pixmap(matrix=mat, alpha=False)
                    img_bytes = pix.tobytes('jpeg', jpg_quality=55)
                    rect = fitz.Rect(0, 0, pix.width, pix.height)
                    npage = new_doc.new_page(width=pix.width, height=pix.height)
                    npage.insert_image(rect, stream=img_bytes)
                out = _pdf_save_bytes(new_doc)
            finally:
                new_doc.close()
            if len(out) <= max_bytes:
                return out, name
        return out, name
    finally:
        doc.close()


def fit_discord_attachment(data: bytes, filename: str, max_bytes: int = DISCORD_SOFT_MAX):
    """
    Return (bytes, filename, compressed?).
    Untouched if already under max_bytes. Compress images/PDFs only when needed.
    """
    if not data:
        return data, filename, False
    if len(data) <= max_bytes:
        return data, filename, False

    before = len(data)
    name = filename or 'file.bin'
    print(f'[COMPRESS] {name} is {before / 1024 / 1024:.1f}MB — shrinking for Discord')

    if _is_image_name(name) or _looks_like_image(data):
        out, out_name = _compress_image(data, name, max_bytes)
    elif _is_pdf_name(name) or data[:4] == b'%PDF':
        out, out_name = _compress_pdf(data, name, max_bytes)
    else:
        print(f'[COMPRESS] unsupported type for {name} — leave as-is')
        return data, name, False

    after = len(out)
    print(f'[COMPRESS] {before / 1024 / 1024:.1f}MB → {after / 1024 / 1024:.1f}MB')
    return out, out_name, True
