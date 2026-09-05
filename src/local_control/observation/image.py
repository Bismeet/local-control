"""Image processing utilities: downscaling, encoding, dHash, and heuristics."""

import io

from PIL import Image

from local_control.observation.screen import RawFrame


def frame_to_pillow(frame: RawFrame) -> Image.Image:
    """Convert a RawFrame (BGRA bytes) into an RGB Pillow Image."""
    return Image.frombytes("RGB", (frame.width, frame.height), frame.raw_bytes, "raw", "BGRX")


def compute_downscale_scale(width: int, max_width: int) -> float:
    """Compute the downscaling factor according to the normative rule:

    scale = max(0.5, min(1.0, max_width / width))
    """
    if width <= 0 or max_width <= 0:
        return 1.0
    return max(0.5, min(1.0, max_width / width))


def downscale_image(img: Image.Image, max_width: int) -> tuple[Image.Image, float]:
    """Downscale an image preserving aspect ratio with Lanczos resampling.

    Returns the resized image and the scale factor used.
    """
    scale = compute_downscale_scale(img.width, max_width)
    if scale >= 1.0:
        return img, 1.0

    target_w = round(img.width * scale)
    target_h = round(img.height * scale)
    resized = img.resize((target_w, target_h), Image.Resampling.LANCZOS)
    return resized, scale


def compute_dhash(img: Image.Image) -> str:
    """Compute a deterministic 64-bit difference hash (dHash).

    1. Convert to grayscale.
    2. Resize to 9x8 (9 columns, 8 rows = 72 pixels).
    3. Compare adjacent horizontal pixels (pixel[c, r] > pixel[c+1, r]).
    4. Pack into a 64-bit integer and format as a 16-character hex string.
    """
    # Convert to grayscale and resize to 9x8
    gray = img.convert("L")
    small = gray.resize((9, 8), Image.Resampling.LANCZOS)
    pixels = small.tobytes()

    diff_bits = 0
    for row in range(8):
        row_offset = row * 9
        for col in range(8):
            left_pixel = pixels[row_offset + col]
            right_pixel = pixels[row_offset + col + 1]
            diff_bits = (diff_bits << 1) | (1 if left_pixel > right_pixel else 0)

    return f"{diff_bits:016x}"


def hamming_distance(hash1: str, hash2: str) -> int:
    """Calculate the Hamming distance (number of bit differences) between two 16-hex hashes."""
    int1 = int(hash1, 16)
    int2 = int(hash2, 16)
    return bin(int1 ^ int2).count("1")


def is_black_frame(img: Image.Image) -> bool:
    """Determine if a frame is black/invalid based on 99th percentile luminance < 8."""
    gray = img.convert("L")
    histogram = gray.histogram()
    total_pixels = sum(histogram)
    if total_pixels == 0:
        return True

    # Find the 99th percentile luminance value
    target_count = 0.99 * total_pixels
    running_count = 0
    p99_lum = 0
    for lum, count in enumerate(histogram):
        running_count += count
        if running_count >= target_count:
            p99_lum = lum
            break

    return p99_lum < 8


def encode_png(img: Image.Image) -> bytes:
    """Encode Pillow Image to PNG bytes."""
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
