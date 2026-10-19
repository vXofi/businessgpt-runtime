import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.handlers import _download_image


JPEG_DATA = b"\xff\xd8\xff\xe0telegram-media-preview"


def make_media(file_kind: str, *, thumbnail=True):
    preview = (
        SimpleNamespace(
            file_id=f"{file_kind}-thumbnail",
            file_unique_id=f"{file_kind}-thumbnail-unique",
            file_size=512,
        )
        if thumbnail
        else None
    )
    return SimpleNamespace(
        file_unique_id=f"{file_kind}-unique",
        thumbnail=preview,
        duration=12,
        width=1280,
        height=720,
    )


def make_message(file_kind: str, *, thumbnail=True):
    return SimpleNamespace(
        photo=[],
        document=None,
        sticker=None,
        video=make_media("video", thumbnail=thumbnail)
        if file_kind == "video"
        else None,
        animation=make_media("animation", thumbnail=thumbnail)
        if file_kind == "animation"
        else None,
    )


def make_context(data: bytes = JPEG_DATA, *, image_max_bytes: int = 10 * 1024 * 1024):
    telegram_file = SimpleNamespace(
        download_as_bytearray=AsyncMock(return_value=bytearray(data))
    )
    bot = SimpleNamespace(
        get_file=AsyncMock(return_value=telegram_file),
    )
    config = SimpleNamespace(image_max_bytes=image_max_bytes)
    return SimpleNamespace(
        bot=bot,
        application=SimpleNamespace(
            bot_data={
                "config": config,
                "db": object(),
                "businessgpt": object(),
                "feedback_encryptor": object(),
                "context_cache": object(),
                "media_cache": object(),
            }
        ),
    )


class VideoPreviewDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_video_and_animation_use_telegram_thumbnail_only(self):
        for file_kind in ("video", "animation"):
            with self.subTest(file_kind=file_kind):
                context = make_context()

                image = await _download_image(make_message(file_kind), context)

                context.bot.get_file.assert_awaited_once_with(
                    f"{file_kind}-thumbnail"
                )
                self.assertEqual(image.mime_type, "image/jpeg")
                self.assertEqual(
                    image.filename,
                    f"telegram-{file_kind}-{file_kind}-unique-preview.jpg",
                )
                self.assertEqual(
                    image.media_metadata,
                    {
                        "kind": f"telegram_{file_kind}_preview",
                        "mime_type": "image/jpeg",
                        "filename": (
                            f"telegram-{file_kind}-{file_kind}-unique-preview.jpg"
                        ),
                        file_kind: {
                            "duration": 12,
                            "width": 1280,
                            "height": 720,
                            "representation": "telegram_thumbnail",
                        },
                    },
                )

    async def test_missing_preview_is_rejected_without_downloading_video(self):
        for file_kind in ("video", "animation"):
            with self.subTest(file_kind=file_kind):
                context = make_context()

                with self.assertRaisesRegex(
                    ValueError, f"{file_kind} preview is unavailable"
                ):
                    await _download_image(
                        make_message(file_kind, thumbnail=False), context
                    )

                context.bot.get_file.assert_not_awaited()

    async def test_invalid_preview_is_rejected(self):
        context = make_context(b"not-an-image")

        with self.assertRaisesRegex(ValueError, "must be JPEG or WebP"):
            await _download_image(make_message("video"), context)


if __name__ == "__main__":
    unittest.main()
