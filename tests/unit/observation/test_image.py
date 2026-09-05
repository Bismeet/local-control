"""Unit tests for image processing utilities, downscale rules, dHash, and heuristics."""

import pytest
from PIL import Image, ImageDraw

from local_control.observation.image import (
    compute_dhash,
    compute_downscale_scale,
    downscale_image,
    encode_png,
    hamming_distance,
    is_black_frame,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("width", "expected_scale"),
    [
        (1280, 1.0),
        (1366, 1280 / 1366),
        (1920, 1280 / 1920),
        (2560, 0.5),  # 1280 / 2560 = 0.5 exactly
        (3840, 0.5),  # 1280 / 3840 = 0.333 -> clamped to floor 0.5
    ],
)
def test_downscale_rule(width: int, expected_scale: float) -> None:
    """Verify normative downscale scale rule and 0.5 minimum floor constraint."""
    scale = compute_downscale_scale(width=width, max_width=1280)
    assert pytest.approx(scale, 0.001) == expected_scale


@pytest.mark.unit
def test_downscale_image_dimensions() -> None:
    """Verify downscale_image resizes proportionally."""
    img = Image.new("RGB", (1920, 1080), color="blue")
    resized, scale = downscale_image(img, max_width=1280)

    assert pytest.approx(scale, 0.001) == (1280 / 1920)
    assert resized.width == 1280
    assert resized.height == 720


@pytest.mark.unit
def test_dhash_identical_images() -> None:
    """Verify identical images have dHash Hamming distance 0."""
    img = Image.new("RGB", (200, 200), color="white")
    draw = ImageDraw.Draw(img)
    draw.rectangle([20, 20, 80, 80], fill="black")

    h1 = compute_dhash(img)
    h2 = compute_dhash(img.copy())

    assert h1 == h2
    assert hamming_distance(h1, h2) == 0


@pytest.mark.unit
def test_dhash_minor_visual_change() -> None:
    """Verify small modifications result in low Hamming distance (1-6)."""
    img1 = Image.new("RGB", (200, 200), color="white")
    draw1 = ImageDraw.Draw(img1)
    draw1.rectangle([20, 20, 100, 100], fill="black")

    img2 = img1.copy()
    draw2 = ImageDraw.Draw(img2)
    # Add a tiny dot
    draw2.rectangle([22, 22, 25, 25], fill="gray")

    h1 = compute_dhash(img1)
    h2 = compute_dhash(img2)

    dist = hamming_distance(h1, h2)
    assert dist >= 0
    assert dist <= 6


@pytest.mark.unit
def test_dhash_substantially_different_images() -> None:
    """Verify different images result in large Hamming distance (>= 10)."""
    img1 = Image.new("RGB", (200, 200), color="white")
    draw1 = ImageDraw.Draw(img1)
    for x in range(0, 200, 20):
        draw1.line([(x, 0), (x, 200)], fill="black", width=5)

    img2 = Image.new("RGB", (200, 200), color="black")
    draw2 = ImageDraw.Draw(img2)
    for y in range(0, 200, 20):
        draw2.line([(0, y), (200, y)], fill="white", width=5)

    h1 = compute_dhash(img1)
    h2 = compute_dhash(img2)

    dist = hamming_distance(h1, h2)
    assert dist >= 10


@pytest.mark.unit
def test_black_frame_detection() -> None:
    """Verify luminance-based black-frame heuristic flags dark frames."""
    # Solid black
    black_img = Image.new("RGB", (100, 100), color=(0, 0, 0))
    assert is_black_frame(black_img) is True

    # Dark noisy image (< 8 luminance)
    dark_img = Image.new("RGB", (100, 100), color=(5, 5, 5))
    assert is_black_frame(dark_img) is True

    # Normal bright image
    normal_img = Image.new("RGB", (100, 100), color=(128, 128, 128))
    assert is_black_frame(normal_img) is False

    # 98% dark but 2% bright
    mostly_dark = Image.new("RGB", (100, 100), color=(0, 0, 0))
    draw = ImageDraw.Draw(mostly_dark)
    # 5x5 out of 100x100 is < 1%, so 99th percentile is still dark
    draw.rectangle([0, 0, 4, 4], fill=(255, 255, 255))
    assert is_black_frame(mostly_dark) is True


@pytest.mark.unit
def test_png_encoding() -> None:
    """Verify encode_png produces valid PNG header bytes."""
    img = Image.new("RGB", (50, 50), color="red")
    png_bytes = encode_png(img)
    assert png_bytes.startswith(b"\x89PNG\r\n\x1a\n")
