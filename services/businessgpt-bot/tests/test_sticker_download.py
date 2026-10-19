import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.handlers import _download_image


JPEG_DATA = b"\xff\xd8\xff\xe0telegram-sticker-preview"
WEBP_DATA = b"RIFF\x10\x00\x00\x00WEBPtelegram-sticker"


def make_sticker(
    *,
    is_animated: bool = False,
    is_video: bool = False,
    thumbnail=None,
    file_size: int | None = 1024,
):
    return SimpleNamespace(
        file_id="sticker-original",
        file_unique_id="unique-sticker",
        file_size=file_size,
        is_animated=is_animated,
        is_video=is_video,
        thumbnail=thumbnail,
        emoji="🙂",
        set_name="test_set",
        type="regular",
    )


def make_message(sticker):
    return SimpleNamespace(
        photo=[],
        document=None,
        sticker=sticker,
        video=None,
        animation=None,
    )


def make_context(data: bytes, *, image_max_bytes: int = 10 * 1024 * 1024):
    telegram_file = SimpleNamespace(
        download_as_bytearray=AsyncMock(return_value=bytearray(data))
    )
    bot = SimpleNamespace(get_file=AsyncMock(return_value=telegram_file))
    bot_data = {
        "config": SimpleNamespace(image_max_bytes=image_max_bytes),
        "db": object(),
        "businessgpt": object(),
        "feedback_encryptor": object(),
        "context_cache": object(),
        "media_cache": object(),
    }
    return SimpleNamespace(
        bot=bot,
        application=SimpleNamespace(bot_data=bot_data),
    )


class StickerDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_static_sticker_uses_original_webp(self):
        sticker = make_sticker()
        context = make_context(WEBP_DATA)

        image = await _download_image(make_message(sticker), context)

        context.bot.get_file.assert_awaited_once_with("sticker-original")
        self.assertEqual(image.mime_type, "image/webp")
        self.assertEqual(image.filename, "telegram-sticker-unique-sticker.webp")
        self.assertEqual(
            image.media_metadata["sticker"],
            {
                "emoji": "🙂",
                "set_name": "test_set",
                "type": "regular",
                "format": "static",
                "representation": "original",
            },
        )

    async def test_animated_and_video_stickers_use_thumbnail(self):
        cases = (
            (True, False, JPEG_DATA, "animated", "image/jpeg", "jpg"),
            (False, True, WEBP_DATA, "video", "image/webp", "webp"),
        )
        for animated, video, data, expected_format, expected_mime, extension in cases:
            with self.subTest(format=expected_format):
                thumbnail = SimpleNamespace(
                    file_id=f"{expected_format}-thumbnail",
                    file_unique_id=f"{expected_format}-thumbnail-unique",
                    file_size=512,
                )
                sticker = make_sticker(
                    is_animated=animated,
                    is_video=video,
                    thumbnail=thumbnail,
                )
                context = make_context(data)

                image = await _download_image(make_message(sticker), context)

                context.bot.get_file.assert_awaited_once_with(
                    f"{expected_format}-thumbnail"
                )
                self.assertEqual(image.mime_type, expected_mime)
                self.assertTrue(image.filename.endswith(f"-preview.{extension}"))
                self.assertEqual(
                    image.media_metadata["sticker"]["representation"],
                    "thumbnail",
                )
                self.assertEqual(
                    image.media_metadata["sticker"]["format"], expected_format
                )

    async def test_animated_sticker_without_thumbnail_is_rejected(self):
        context = make_context(JPEG_DATA)
        sticker = make_sticker(is_animated=True, thumbnail=None)

        with self.assertRaisesRegex(ValueError, "preview is unavailable"):
            await _download_image(make_message(sticker), context)

        context.bot.get_file.assert_not_awaited()

    async def test_invalid_sticker_preview_signature_is_rejected(self):
        thumbnail = SimpleNamespace(
            file_id="thumbnail",
            file_unique_id="thumbnail-unique",
            file_size=100,
        )
        context = make_context(b"not-an-image")
        sticker = make_sticker(is_video=True, thumbnail=thumbnail)

        with self.assertRaisesRegex(ValueError, "must be JPEG or WebP"):
            await _download_image(make_message(sticker), context)

    async def test_sticker_size_is_checked_before_and_after_download(self):
        too_large = make_sticker(file_size=101)
        context = make_context(WEBP_DATA, image_max_bytes=100)

        with self.assertRaisesRegex(ValueError, "configured size limit"):
            await _download_image(make_message(too_large), context)
        context.bot.get_file.assert_not_awaited()

        unknown_size = make_sticker(file_size=None)
        context = make_context(WEBP_DATA, image_max_bytes=10)
        with self.assertRaisesRegex(ValueError, "configured size limit"):
            await _download_image(make_message(unknown_size), context)


if __name__ == "__main__":
    unittest.main()
