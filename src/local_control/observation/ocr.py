"""OCR provider protocol, RapidOCR ONNXRuntime adapter, and MockOCR for local-control."""

import io
from typing import Any, Protocol, runtime_checkable

import structlog
from PIL import Image

from local_control.core.actions import Rect
from local_control.core.types import OcrSpan

logger = structlog.get_logger(__name__)


@runtime_checkable
class OCRProvider(Protocol):
    """Protocol for OCR engines extracting text spans with bounding boxes."""

    name: str

    def recognize(
        self, image_bytes: bytes | Image.Image, region: Rect | None = None
    ) -> list[OcrSpan]:
        """Extract text spans from image bytes or Pillow image, optionally cropped to region."""
        ...


class NullOCR:
    """Null OCR implementation returning empty list conforming to OCRProvider."""

    name: str = "null_ocr"

    def recognize(
        self,
        image_bytes: bytes | Image.Image,
        region: Rect | None = None,
    ) -> list[OcrSpan]:
        """Return empty span list."""
        return []


class RapidOCRAdapter:
    """OCR provider adapter using RapidOCR onnxruntime engine."""

    name: str = "rapidocr"

    def __init__(self) -> None:
        self._engine: Any = None
        self._available: bool | None = None

    def _ensure_engine(self) -> Any:
        if self._engine is not None:
            return self._engine
        try:
            import importlib

            mod = importlib.import_module("rapidocr_onnxruntime")
            RapidOCR = mod.RapidOCR

            self._engine = RapidOCR()
            self._available = True
            logger.info("ocr.rapidocr_initialized")
            return self._engine
        except Exception as e:
            self._available = False
            logger.debug("ocr.rapidocr_unavailable", error=str(e))
            return None

    def is_available(self) -> bool:
        """Check if rapidocr_onnxruntime is available."""
        if self._available is None:
            self._ensure_engine()
        return bool(self._available)

    def recognize(
        self,
        image_bytes: bytes | Image.Image,
        region: Rect | None = None,
    ) -> list[OcrSpan]:
        """Run text recognition on image bytes or Image.Image."""
        engine = self._ensure_engine()
        if not engine:
            return []

        try:
            if isinstance(image_bytes, Image.Image):
                img = image_bytes.copy()
            else:
                if not image_bytes:
                    return []
                img = Image.open(io.BytesIO(image_bytes))
            offset_x, offset_y = 0, 0
            if region:
                box = (
                    max(0, region.x),
                    max(0, region.y),
                    min(img.width, region.x + region.width),
                    min(img.height, region.y + region.height),
                )
                if box[2] > box[0] and box[3] > box[1]:
                    img = img.crop(box)
                    offset_x, offset_y = box[0], box[1]

            buf = io.BytesIO()
            img.save(buf, format="PNG")
            ocr_result, _ = engine(buf.getvalue())
            if not ocr_result:
                return []

            spans: list[OcrSpan] = []
            for item in ocr_result:
                # item is [box_coords, text, score]
                # box_coords is [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
                box_coords, text, score = item[0], str(item[1]), float(item[2])
                xs = [pt[0] for pt in box_coords]
                ys = [pt[1] for pt in box_coords]
                min_x = int(min(xs)) + offset_x
                max_x = int(max(xs)) + offset_x
                min_y = int(min(ys)) + offset_y
                max_y = int(max(ys)) + offset_y

                spans.append(
                    OcrSpan(
                        text=text.strip(),
                        bbox=Rect(
                            x=min_x,
                            y=min_y,
                            width=max(1, max_x - min_x),
                            height=max(1, max_y - min_y),
                        ),
                        confidence=max(0.0, min(1.0, score)),
                    )
                )

            return spans
        except Exception as e:
            logger.warning("ocr.recognition_failed", error=str(e))
            return []


class MockOCRAdapter:
    """Mock OCR provider for unit tests and offline testing."""

    name: str = "mock_ocr"

    def __init__(self, predefined_spans: list[OcrSpan] | None = None) -> None:
        self.predefined_spans = predefined_spans or []
        self.calls: list[dict[str, Any]] = []

    def recognize(
        self,
        image_bytes: bytes | Image.Image,
        region: Rect | None = None,
    ) -> list[OcrSpan]:
        data_len = len(image_bytes) if isinstance(image_bytes, bytes) else 100
        self.calls.append({"bytes_len": data_len, "region": region})
        if self.predefined_spans:
            return list(self.predefined_spans)
        # Default mock output
        return [
            OcrSpan(
                text="OK",
                bbox=Rect(x=100, y=100, width=50, height=20),
                confidence=0.95,
            ),
            OcrSpan(
                text="Cancel",
                bbox=Rect(x=160, y=100, width=60, height=20),
                confidence=0.92,
            ),
        ]
