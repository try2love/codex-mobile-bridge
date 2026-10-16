"""Draw a monochrome mobile terminal mark and Windows-compatible DIB icons."""
from pathlib import Path
import math
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent
SCALE = 2
SIZE = 512 * SCALE


def box(values):
    return tuple(round(value * SCALE) for value in values)


def rounded(draw, bounds, radius, **kwargs):
    draw.rounded_rectangle(box(bounds), radius=round(radius * SCALE), **kwargs)


def line(draw, points, color, width):
    positions = [(round(x * SCALE), round(y * SCALE)) for x, y in points]
    draw.line(positions, fill=color, width=round(width * SCALE), joint='curve')
    radius = width * SCALE / 2
    for x, y in positions:
        draw.ellipse((round(x-radius), round(y-radius), round(x+radius), round(y+radius)), fill=color)


def make_icon():
    mask = Image.new('L', (SIZE, SIZE))
    rounded(ImageDraw.Draw(mask), (20, 14, 492, 486), 110, fill=255)
    shadow_mask = Image.new('L', (SIZE, SIZE))
    rounded(ImageDraw.Draw(shadow_mask), (26, 30, 486, 490), 106, fill=125)
    shadow_mask = shadow_mask.filter(ImageFilter.GaussianBlur(8 * SCALE))
    image = Image.new('RGBA', (SIZE, SIZE), '#151515')
    image.putalpha(shadow_mask)

    gradient = Image.new('RGBA', (SIZE, SIZE))
    pixels = gradient.load()
    for y in range(SIZE):
        for x in range(SIZE):
            px, py = x / SCALE, y / SCALE
            blend = min(1, max(0, (px + py) / 950))
            glow = math.exp(-((px-385)**2 + (py-105)**2) / 40000)
            top, bottom = (38, 38, 38), (16, 16, 16)
            rgb = [round(a*(1-blend) + b*blend) for a, b in zip(top, bottom)]
            pixels[x, y] = (*rgb, 255)
    gradient.putalpha(mask)
    image.alpha_composite(gradient)
    draw = ImageDraw.Draw(image)
    rounded(draw, (21, 15, 491, 485), 109, outline='#424242', width=2 * SCALE)

    # A clean phone silhouette with a terminal prompt and a small AI sparkle.
    rounded(draw, (155, 86, 357, 426), 46, fill='#111111')
    rounded(draw, (153, 80, 355, 420), 46, fill='#202020', outline='#ffffff', width=14 * SCALE)
    rounded(draw, (225, 112, 283, 120), 4, fill='#a6a6a6')
    line(draw, [(202, 213), (243, 251), (202, 289)], '#ffffff', 19)
    line(draw, [(270, 289), (306, 289)], '#ffffff', 18)
    rounded(draw, (225, 386, 283, 393), 3.5, fill='#a6a6a6')

    # The negative-space outline keeps the sparkle readable over the phone.
    sparkle = [(356, 120), (367, 151), (398, 162), (367, 173),
               (356, 204), (345, 173), (314, 162), (345, 151)]
    points = [(x * SCALE, y * SCALE) for x, y in sparkle]
    draw.polygon(points, fill='#ffffff')
    draw.line(points + [points[0]], fill='#202020', width=8 * SCALE, joint='curve')
    smaller = [(356 + (x-356)*.84, 162 + (y-162)*.84) for x, y in sparkle]
    draw.polygon([(round(x*SCALE), round(y*SCALE)) for x, y in smaller], fill='#ffffff')
    return image.resize((512, 512), Image.Resampling.LANCZOS)


if __name__ == '__main__':
    icon = make_icon()
    icon.save(ROOT / 'codex-mobile.png')
    icon.resize((128, 128), Image.Resampling.LANCZOS).save(ROOT / 'codex-mobile-preview.png')
    icon.save(ROOT / 'codex-mobile.ico', format='ICO', bitmap_format='bmp', sizes=[(size, size) for size in (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)])
