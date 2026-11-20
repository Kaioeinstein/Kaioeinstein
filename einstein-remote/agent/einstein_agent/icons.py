"""Ícones do agente desenhados com Pillow (sem arquivos binários no repositório)."""
from __future__ import annotations

from PIL import Image, ImageDraw

PURPLE = (168, 85, 247)
GREEN = (34, 227, 160)
STATE_COLORS = {
    "online": (34, 227, 160),
    "connecting": (245, 181, 68),
    "starting": (245, 181, 68),
    "paused": (245, 181, 68),
    "offline": (255, 77, 109),
    "rejected": (255, 77, 109),
    "unpaired": (138, 138, 160),
    "stopped": (138, 138, 160),
}


def _lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def make_icon(state: str | None = None, size: int = 64) -> Image.Image:
    """Quadrado arredondado com borda roxo→verde, letra "E" e ponto de status."""
    s = 4  # super-amostragem para bordas suaves
    W = size * s
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    grad = Image.new("RGBA", (W, W))
    gd = ImageDraw.Draw(grad)
    for y in range(W):
        gd.line([(0, y), (W, y)], fill=_lerp(PURPLE, GREEN, y / W) + (255,))
    mask = Image.new("L", (W, W), 0)
    md = ImageDraw.Draw(mask)
    r = int(W * 0.24)
    md.rounded_rectangle([0, 0, W - 1, W - 1], radius=r, fill=255)
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)
    b = int(W * 0.08)
    d.rounded_rectangle([b, b, W - 1 - b, W - 1 - b], radius=r - b, fill=(10, 10, 16, 255))
    # letra E geométrica
    x0, x1 = int(W * 0.33), int(W * 0.68)
    y0, y1 = int(W * 0.26), int(W * 0.74)
    t = int(W * 0.09)
    white = (255, 255, 255, 255)
    d.rectangle([x0, y0, x0 + t, y1], fill=white)
    d.rectangle([x0, y0, x1, y0 + t], fill=white)
    d.rectangle([x0, (y0 + y1) // 2 - t // 2, x1 - int(W * 0.04), (y0 + y1) // 2 + t // 2], fill=white)
    d.rectangle([x0, y1 - t, x1, y1], fill=white)
    if state:
        color = STATE_COLORS.get(state, STATE_COLORS["stopped"])
        R = int(W * 0.17)
        cx = cy = W - R - int(W * 0.02)
        d.ellipse([cx - R - b // 2, cy - R - b // 2, cx + R + b // 2, cy + R + b // 2], fill=(10, 10, 16, 255))
        d.ellipse([cx - R, cy - R, cx + R, cy + R], fill=color + (255,))
    return img.resize((size, size), Image.LANCZOS)


def save_ico(path: str) -> None:
    """Gera o .ico multi-resolução usado pelo executável e pelo instalador."""
    make_icon(None, 256).save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
