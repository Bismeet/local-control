"""Unit tests for OCRProvider protocol and NullOCR."""

import pytest
from PIL import Image

from local_control.observation.ocr import NullOCR, OCRProvider


@pytest.mark.unit
def test_null_ocr_conforms_to_ocr_provider_protocol() -> None:
    ocr: OCRProvider = NullOCR()
    assert isinstance(ocr, OCRProvider)

    # Recognize on dummy image
    img = Image.new("RGB", (100, 100), color="white")
    results = ocr.recognize(img)
    assert isinstance(results, list)
    assert len(results) == 0


@pytest.mark.unit
def test_mock_ocr_adapter() -> None:
    from local_control.core.actions import Rect
    from local_control.core.types import OcrSpan
    from local_control.observation.ocr import MockOCRAdapter

    adapter = MockOCRAdapter()
    assert isinstance(adapter, OCRProvider)

    img = Image.new("RGB", (200, 200), color="white")
    spans = adapter.recognize(img)
    assert len(spans) == 2
    assert spans[0].text == "OK"
    assert spans[1].text == "Cancel"
    assert len(adapter.calls) == 1

    custom_span = OcrSpan(
        text="Submit", bbox=Rect(x=10, y=10, width=50, height=20), confidence=0.99
    )
    custom_adapter = MockOCRAdapter(predefined_spans=[custom_span])
    custom_results = custom_adapter.recognize(b"fake_bytes")
    assert len(custom_results) == 1
    assert custom_results[0].text == "Submit"


@pytest.mark.unit
def test_rapid_ocr_adapter_fallback() -> None:
    from unittest.mock import patch

    from local_control.observation.ocr import RapidOCRAdapter

    adapter = RapidOCRAdapter()
    # When rapidocr_onnxruntime is not installed or import fails
    with patch.dict("sys.modules", {"rapidocr_onnxruntime": None}):
        adapter._engine = None
        adapter._available = None
        assert adapter.is_available() is False
        results = adapter.recognize(b"fake_bytes")
        assert results == []
