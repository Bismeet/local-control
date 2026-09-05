"""OCRProvider interface and NullOCR stub for screen text detection."""

from typing import Protocol, runtime_checkable

from PIL import Image

from local_control.core.types import OcrSpan


@runtime_checkable
class OCRProvider(Protocol):
    """Protocol for Optical Character Recognition providers."""

    def recognize(self, image: Image.Image) -> list[OcrSpan]:
        """Detect text spans with bounding boxes and confidence scores in an image."""
        ...


class NullOCR:
    """Null OCR implementation returning empty detections."""

    def recognize(self, image: Image.Image) -> list[OcrSpan]:
        """Return an empty list of OCR detections."""
        return []
