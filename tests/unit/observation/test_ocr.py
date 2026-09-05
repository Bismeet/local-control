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
