#!/usr/bin/env python3
"""Generate PWA icons for HelmHub using Pillow."""

import os
import math
from PIL import Image, ImageDraw, ImageFont

def draw_icon(size):
    """Draw HelmHub icon - a compass/helm wheel design."""
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Background circle with gradient-like color
    margin = int(size * 0.05)
    # Dark navy background
    draw.ellipse([0, 0, size-1, size-1], fill=(15, 15, 26, 255))

    # Inner circle (accent)
    cx, cy = size // 2, size // 2
    r = size // 2 - margin

    # Draw helm/wheel spokes
    num_spokes = 8
    spoke_color = (67, 97, 238, 255)  # accent-blue
    spoke_width = max(2, size // 48)
    inner_r = int(r * 0.35)
    outer_r = int(r * 0.75)

    for i in range(num_spokes):
        angle = math.radians(i * 360 / num_spokes)
        x1 = int(cx + inner_r * math.cos(angle))
        y1 = int(cy + inner_r * math.sin(angle))
        x2 = int(cx + outer_r * math.cos(angle))
        y2 = int(cy + outer_r * math.sin(angle))
        draw.line([x1, y1, x2, y2], fill=spoke_color, width=spoke_width)

    # Outer ring
    ring_w = max(3, size // 24)
    draw.ellipse(
        [cx - outer_r, cy - outer_r, cx + outer_r, cy + outer_r],
        outline=spoke_color, width=ring_w
    )

    # Center hub
    hub_r = inner_r - max(2, size // 48)
    draw.ellipse(
        [cx - hub_r, cy - hub_r, cx + hub_r, cy + hub_r],
        fill=spoke_color
    )

    # Small center dot
    dot_r = max(2, size // 32)
    draw.ellipse(
        [cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r],
        fill=(255, 255, 255, 255)
    )

    return img

def main():
    icons_dir = os.path.join(os.path.dirname(__file__), 'app', 'static', 'icons')
    os.makedirs(icons_dir, exist_ok=True)

    sizes = [192, 512]
    for size in sizes:
        icon = draw_icon(size)
        path = os.path.join(icons_dir, f'icon-{size}.png')
        icon.save(path, 'PNG')
        print(f'Generated {path}')

    # Also generate apple-touch-icon (180x180)
    apple_icon = draw_icon(180)
    apple_path = os.path.join(icons_dir, 'apple-touch-icon.png')
    apple_icon.save(apple_path, 'PNG')
    print(f'Generated {apple_path}')

    # favicon 32x32
    favicon = draw_icon(32)
    favicon_path = os.path.join(os.path.dirname(__file__), 'app', 'static', 'favicon.ico')
    favicon.save(favicon_path, 'ICO', sizes=[(32, 32)])
    print(f'Generated {favicon_path}')

if __name__ == '__main__':
    main()
