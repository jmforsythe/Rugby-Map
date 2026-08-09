"""Analyse perceptual distance between every pair of colours in the shared palette."""

from __future__ import annotations

import math
from itertools import combinations

from core.colors import COLOR_PALETTE


def _hex_to_rgb(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4))


def _srgb_to_linear(channel: float) -> float:
    return ((channel + 0.055) / 1.055) ** 2.4 if channel > 0.04045 else channel / 12.92


def _xyz_f(t: float) -> float:
    delta = 6 / 29
    return t ** (1 / 3) if t > delta**3 else t / (3 * delta**2) + 4 / 29


def _rgb_to_lab(r: float, g: float, b: float) -> tuple[float, float, float]:
    rr = _srgb_to_linear(r)
    gg = _srgb_to_linear(g)
    bb = _srgb_to_linear(b)

    x = (rr * 0.4124564 + gg * 0.3575761 + bb * 0.1804375) / 0.95047
    y = rr * 0.2126729 + gg * 0.7151522 + bb * 0.0721750
    z = (rr * 0.0193339 + gg * 0.1191920 + bb * 0.9503041) / 1.08883

    fx = _xyz_f(x)
    fy = _xyz_f(y)
    fz = _xyz_f(z)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def delta_e(c1: str, c2: str) -> float:
    """CIE76 Delta E*ab between two hex colours."""
    lab1 = _rgb_to_lab(*_hex_to_rgb(c1))
    lab2 = _rgb_to_lab(*_hex_to_rgb(c2))
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(lab1, lab2, strict=True)))


def main() -> None:
    palette = COLOR_PALETTE
    pairs = [
        (i, j, palette[i], palette[j], delta_e(palette[i], palette[j]))
        for i, j in combinations(range(len(palette)), 2)
    ]
    pairs.sort(key=lambda row: row[4])

    distances = [d for *_rest, d in pairs]
    mean_de = sum(distances) / len(distances)
    print(f"Palette size: {len(palette)} colours, {len(pairs)} unique pairs\n")

    print("Summary (CIE76 Delta E*ab - higher = more distinguishable):")
    print(f"  min:    {min(distances):.1f}")
    print(f"  median: {sorted(distances)[len(distances) // 2]:.1f}")
    print(f"  mean:   {mean_de:.1f}")
    print(f"  max:    {max(distances):.1f}")
    print()

    thresholds = [
        (10, "often confused"),
        (20, "similar at a glance"),
        (30, "moderately distinct"),
    ]
    for threshold, label in thresholds:
        count = sum(1 for d in distances if d < threshold)
        print(f"  pairs with Delta E < {threshold} ({label}): {count}")

    print("\n20 closest pairs:")
    for i, j, c1, c2, de in pairs[:20]:
        print(f"  [{i:2d}] {c1}  <->  [{j:2d}] {c2}   Delta E={de:.1f}")

    print("\n20 most distinct pairs:")
    for i, j, c1, c2, de in pairs[-20:][::-1]:
        print(f"  [{i:2d}] {c1}  <->  [{j:2d}] {c2}   Delta E={de:.1f}")

    # Adjacent palette indices (as assigned on maps) - often the worst case visually.
    adj = [
        (i, i + 1, palette[i], palette[i + 1], delta_e(palette[i], palette[i + 1]))
        for i in range(len(palette) - 1)
    ]
    adj.sort(key=lambda row: row[4])
    print("\n10 closest adjacent-index pairs (consecutive leagues on a dense map):")
    for i, j, c1, c2, de in adj[:10]:
        print(f"  [{i:2d}]->[{j:2d}]  {c1}  <->  {c2}   Delta E={de:.1f}")


if __name__ == "__main__":
    main()
