from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def select_trace(lines: list[str]) -> list[str]:
    for call_index, line in enumerate(lines):
        if "[🔧 TOOL CALL] Tool: read_official_source" not in line:
            continue

        for result_index in range(call_index + 1, len(lines)):
            candidate = lines[result_index]
            if "[✅ TOOL RESULT] read_official_source" in candidate:
                end_index = min(result_index + 12, len(lines))
                return lines[call_index:end_index]
            if (
                "[❌ TOOL ERROR]" in candidate
                or "[🔧 TOOL CALL] Tool:" in candidate
            ):
                break

    raise ValueError("No successful read_official_source trace found.")


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in (
        "/System/Library/Fonts/Menlo.ttc",
        "/System/Library/Fonts/Monaco.ttf",
        "/System/Library/Fonts/SFNSMono.ttf",
    ):
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def render(input_path: Path, output_path: Path) -> None:
    lines = input_path.read_text(
        encoding="utf-8",
        errors="replace",
    ).splitlines()
    trace = select_trace(lines)
    font = load_font(19)
    line_height = 28
    width = 1500
    height = 74 + line_height * min(len(trace), 30) + 30
    image = Image.new("RGB", (width, height), "#0d1117")
    draw = ImageDraw.Draw(image)

    draw.rectangle((0, 0, width, 52), fill="#161b22")
    for x, color in (
        (24, "#ff5f56"),
        (50, "#ffbd2e"),
        (76, "#27c93f"),
    ):
        draw.ellipse((x, 18, x + 14, 32), fill=color)
    draw.text(
        (116, 15),
        "phase 8 · actual benchmark stdout",
        font=font,
        fill="#c9d1d9",
    )

    y = 68
    for line in trace[:30]:
        clipped = (
            line
            .replace("🔧 ", "")
            .replace("✅ ", "")
            .replace("⏱ ", "")
        )[:136]
        color = "#c9d1d9"
        if "[🔧 TOOL CALL]" in clipped:
            color = "#58a6ff"
        elif "[✅ TOOL RESULT]" in clipped:
            color = "#3fb950"
        elif "[⏱ TOOL TIME]" in clipped:
            color = "#d2a8ff"
        draw.text((24, y), clipped, font=font, fill=color)
        y += line_height

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    render(args.input, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
