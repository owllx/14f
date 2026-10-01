"""Генерує servo_sync/assets.py (вбудовані PNG) і app.ico — логотип і елементи інтерфейсу.

Запуск (потрібен Pillow і поруч папка label_designer з логотипом):
    python make_assets.py
Сама програма Pillow не потребує: Tk читає ці PNG напряму.
"""
import base64
import io
import os
import sys

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "label_designer"))
import label_designer as ld  # noqa: E402  (логотип: маски шестерні й блискавки)

TEXT = "#e8ebf0"
MUTED = "#7f8796"
LINE = "#2a303b"
ACCENT = "#22d3ee"
VIOLET = "#7c3aed"
CYAN = "#06b6d4"
SCALE = 4


def rgb(color):
    return Image.new("RGB", (1, 1), color).getpixel((0, 0))


def gradient(size, start, end, diagonal=True):
    width, height = size
    a, b = rgb(start), rgb(end)
    image = Image.new("RGBA", size)
    pixels = image.load()
    for y in range(height):
        for x in range(width):
            t = (x / max(1, width - 1)) * (0.75 if diagonal else 1) + (y / max(1, height - 1)) * (0.25 if diagonal else 0)
            pixels[x, y] = tuple(round(p + (q - p) * t) for p, q in zip(a, b)) + (255,)
    return image


def canvas(width, height):
    image = Image.new("RGBA", (width * SCALE, height * SCALE), (0, 0, 0, 0))
    return image, ImageDraw.Draw(image)


def finish(image, width, height):
    return image.resize((width, height), Image.Resampling.LANCZOS)


def logo(size):
    """Шестерня — світла, блискавка — фірмовий градієнт."""
    big = size * SCALE
    result = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    gear = ld._logo_mask(ld.LOGO_GEAR_MASK).resize((big, big), Image.Resampling.LANCZOS)
    bolt = ld._logo_mask(ld.LOGO_BOLT_MASK).resize((big, big), Image.Resampling.LANCZOS)
    layer = Image.new("RGBA", (big, big), TEXT)
    layer.putalpha(gear)
    result.alpha_composite(layer)
    layer = gradient((big, big), "#a78bfa", ACCENT)
    layer.putalpha(bolt)
    result.alpha_composite(layer)
    return finish(result, size, size)


def checkbox(state):
    size = 18
    image, draw = canvas(size, size)
    box = (1 * SCALE, 1 * SCALE, 17 * SCALE - 1, 17 * SCALE - 1)
    if state == "on":
        fill = gradient((image.width, image.height), VIOLET, CYAN)
        mask = Image.new("L", image.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle(box, radius=5 * SCALE, fill=255)
        image.paste(fill, (0, 0), mask)
        draw.line([(4.6 * SCALE, 9.2 * SCALE), (7.6 * SCALE, 12.2 * SCALE), (13.4 * SCALE, 5.8 * SCALE)],
                  fill="#ffffff", width=round(2.2 * SCALE), joint="curve")
    else:
        color = "#56607a" if state == "hover" else "#3a4250"
        draw.rounded_rectangle(box, radius=5 * SCALE, outline=color, width=round(1.6 * SCALE))
    return finish(image, size, size)


def switch(on):
    width, height = 36, 20
    image, draw = canvas(width, height)
    box = (0, 0, width * SCALE - 1, height * SCALE - 1)
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=height * SCALE // 2, fill=255)
    if on:
        image.paste(gradient(image.size, VIOLET, CYAN), (0, 0), mask)
        knob_x = width - 10
    else:
        image.paste(Image.new("RGBA", image.size, "#2a303b"), (0, 0), mask)
        knob_x = 10
    r = 7 * SCALE
    cx, cy = knob_x * SCALE, height * SCALE // 2
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill="#ffffff" if on else "#9aa3b2")
    return finish(image, width, height)


def button(width, height, state):
    image, _draw = canvas(width, height)
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, image.width - 1, image.height - 1), radius=10 * SCALE, fill=255)
    if state == "disabled":
        fill = Image.new("RGBA", image.size, "#262b35")
    else:
        start, end = {"normal": (VIOLET, CYAN), "hover": ("#8b5cf6", "#22d3ee"),
                      "down": ("#6d28d9", "#0891b2")}[state]
        fill = gradient(image.size, start, end)
    image.paste(fill, (0, 0), mask)
    if state != "disabled":
        # Ледь помітний світлий верхній край — кнопка «скляна», а не пласка.
        shine = Image.new("RGBA", image.size, (255, 255, 255, 0))
        ImageDraw.Draw(shine).rounded_rectangle((0, 0, image.width - 1, image.height // 2), radius=10 * SCALE,
                                                fill=(255, 255, 255, 22))
        image.alpha_composite(Image.composite(shine, Image.new("RGBA", image.size, (0, 0, 0, 0)), mask))
    return finish(image, width, height)


def icon(name, color, size=18):
    image, draw = canvas(size, size)
    unit = size * SCALE / 24.0
    stroke = round(1.9 * unit)

    def p(x, y):
        return x * unit, y * unit

    if name == "pin":
        draw.rounded_rectangle((*p(7.5, 2.5), *p(16.5, 5.2)), radius=1.2 * unit, fill=color)
        draw.polygon([p(9, 5), p(15, 5), p(16.2, 12), p(7.8, 12)], fill=color)
        draw.rounded_rectangle((*p(5.5, 11.6), *p(18.5, 14)), radius=1.2 * unit, fill=color)
        draw.line([p(12, 14), p(12, 21.5)], fill=color, width=stroke)
    elif name == "tune":
        for y, knob in ((6, 15.5), (12, 8.5), (18, 13)):
            draw.line([p(3.5, y), p(20.5, y)], fill=color, width=stroke)
            r = 2.7 * unit
            cx, cy = p(knob, y)
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill="#0d0f13", outline=color, width=stroke)
    return finish(image, size, size)


def png_b64(image):
    buffer = io.BytesIO()
    image.save(buffer, "PNG", optimize=True)
    return base64.b64encode(buffer.getvalue()).decode()


def main():
    assets = {
        "logo": logo(34),
        "icon16": ld.render_app_icon(16),
        "icon32": ld.render_app_icon(32),
        "icon64": ld.render_app_icon(64),
        "check_on": checkbox("on"),
        "check_off": checkbox("off"),
        "check_hover": checkbox("hover"),
        "switch_on": switch(True),
        "switch_off": switch(False),
        "pin_off": icon("pin", MUTED),
        "pin_hover": icon("pin", TEXT),
        "pin_on": icon("pin", ACCENT),
        "tune": icon("tune", MUTED),
        "tune_hover": icon("tune", TEXT),
    }
    for state in ("normal", "hover", "down", "disabled"):
        assets[f"primary_{state}"] = button(250, 40, state)
    lines = [
        '"""Вбудована графіка Servo Trim Sync (PNG у base64). Згенеровано make_assets.py — не редагуйте вручну."""',
        "",
        "ASSETS = {",
    ]
    for name, image in assets.items():
        data = png_b64(image)
        chunks = [data[i:i + 96] for i in range(0, len(data), 96)]
        lines.append(f'    "{name}": (')
        lines.extend(f'        "{chunk}"' for chunk in chunks)
        lines.append("    ),")
    lines.append("}")
    with open(os.path.join(HERE, "assets.py"), "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")
    icon_image = ld.render_app_icon(256)
    icon_image.save(os.path.join(HERE, "app.ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)])
    preview = Image.new("RGBA", (640, 120), "#0d0f13")
    x = 10
    for name, image in assets.items():
        preview.alpha_composite(image, (x, 10 if image.height < 60 else 10))
        x += image.width + 10
        if x > 560:
            break
    preview.save(os.path.join(HERE, "..", "servo_assets_preview.png")) if "--preview" in sys.argv else None
    print("assets:", len(assets))


if __name__ == "__main__":
    main()
