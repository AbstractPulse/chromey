from __future__ import annotations

from collections import deque
from pathlib import Path

from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = PROJECT_ROOT / "logo.png"
ASSETS_DIR = PROJECT_ROOT / "extension" / "chromey-extension" / "assets"
MASTER_OUTPUT_PATH = ASSETS_DIR / "logo-transparent.png"
HEADER_OUTPUT_PATH = ASSETS_DIR / "logo-header.png"
ICON_SIZES = (16, 32, 48, 128)
BACKGROUND_TOLERANCE = 22.0
CONTENT_PADDING_RATIO = 0.12


def color_distance(left: tuple[int, int, int], right: tuple[int, int, int]) -> float:
    return sum((left[index] - right[index]) ** 2 for index in range(3)) ** 0.5


def edge_background_mask(image: Image.Image, tolerance: float) -> tuple[list[bool], tuple[int, int, int]]:
    rgb = image.convert("RGB")
    width, height = rgb.size
    pixels = rgb.load()
    visited = [False] * (width * height)
    edge_samples = []

    for x in range(width):
        edge_samples.append(pixels[x, 0])
        edge_samples.append(pixels[x, height - 1])
    for y in range(height):
        edge_samples.append(pixels[0, y])
        edge_samples.append(pixels[width - 1, y])

    base = tuple(round(sum(sample[index] for sample in edge_samples) / len(edge_samples)) for index in range(3))
    queue: deque[tuple[int, int]] = deque()

    def mark(x: int, y: int) -> None:
        offset = y * width + x
        if visited[offset]:
            return
        visited[offset] = True
        if color_distance(pixels[x, y], base) <= tolerance:
            queue.append((x, y))

    for x in range(width):
        mark(x, 0)
        mark(x, height - 1)
    for y in range(height):
        mark(0, y)
        mark(width - 1, y)

    while queue:
        x, y = queue.popleft()
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= nx < width and 0 <= ny < height:
                offset = ny * width + nx
                if visited[offset]:
                    continue
                visited[offset] = True
                if color_distance(pixels[nx, ny], base) <= tolerance:
                    queue.append((nx, ny))

    return visited, base


def transparent_logo(source_path: Path, output_path: Path) -> Image.Image:
    image = Image.open(source_path).convert("RGBA")
    width, height = image.size
    background_mask, base = edge_background_mask(image, BACKGROUND_TOLERANCE)
    pixels = image.load()

    for y in range(height):
        for x in range(width):
            if background_mask[y * width + x]:
                pixels[x, y] = (0, 0, 0, 0)

    bbox = image.getbbox()
    if bbox is None:
        raise RuntimeError("The processed logo is empty.")

    cropped = image.crop(bbox)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cropped.save(output_path)
    print(f"background color ~ {base}")
    print(f"saved transparent master to {output_path}")
    return cropped


def contain_on_square(image: Image.Image, size: int) -> Image.Image:
    square = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    padding = max(1, round(size * CONTENT_PADDING_RATIO))
    target = size - padding * 2
    fitted = image.copy()
    fitted.thumbnail((target, target), Image.Resampling.LANCZOS)
    left = (size - fitted.width) // 2
    top = (size - fitted.height) // 2
    square.alpha_composite(fitted, (left, top))
    return square


def main() -> None:
    master = transparent_logo(SOURCE_PATH, MASTER_OUTPUT_PATH)
    header = contain_on_square(master, 96)
    header.save(HEADER_OUTPUT_PATH)
    print(f"saved header logo to {HEADER_OUTPUT_PATH}")

    for size in ICON_SIZES:
        icon = contain_on_square(master, size)
        icon_path = ASSETS_DIR / f"icon-{size}.png"
        icon.save(icon_path)
        print(f"saved {size}px icon to {icon_path}")


if __name__ == "__main__":
    main()
