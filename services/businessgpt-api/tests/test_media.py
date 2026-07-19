from __future__ import annotations

import base64
import io
import unittest

from PIL import Image

from media import ImageValidationError, normalize_image_data_url


def image_data_url(fmt: str, size: tuple[int, int]) -> str:
    output = io.BytesIO()
    Image.new("RGBA", size, (255, 0, 0, 128)).save(output, format=fmt)
    mime = "image/png" if fmt == "PNG" else "image/webp"
    return f"data:{mime};base64,{base64.b64encode(output.getvalue()).decode('ascii')}"


class MediaTests(unittest.TestCase):
    def test_normalizes_and_resizes_image(self) -> None:
        result = normalize_image_data_url(image_data_url("PNG", (1600, 800)))

        self.assertEqual(result.mime_type, "image/jpeg")
        self.assertEqual((result.width, result.height), (1280, 640))
        self.assertTrue(result.data_url.startswith("data:image/jpeg;base64,"))
        self.assertNotEqual(result.source_sha256, result.normalized_sha256)

    def test_rejects_invalid_data_url(self) -> None:
        with self.assertRaises(ImageValidationError):
            normalize_image_data_url("https://example.com/image.jpg")


if __name__ == "__main__":
    unittest.main()
