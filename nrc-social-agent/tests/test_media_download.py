import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.error import TelegramError

from src.media.download import cleanup_temp_file, download_to_temp_file
from src.media.errors import TelegramDownloadError
from src.media.types import MediaType


async def test_download_to_temp_file_returns_path_and_downloads_via_bot():
    telegram_file = MagicMock()
    telegram_file.download_to_drive = AsyncMock()

    bot = MagicMock()
    bot.get_file = AsyncMock(return_value=telegram_file)

    path = await download_to_temp_file(bot, "file-id-123", MediaType.PHOTO)

    try:
        bot.get_file.assert_awaited_once_with("file-id-123")
        telegram_file.download_to_drive.assert_awaited_once_with(custom_path=path)
        assert path.endswith(".jpg")
        assert os.path.exists(path)  # mkstemp reserves the path up front
    finally:
        cleanup_temp_file(path)


async def test_download_to_temp_file_uses_video_extension():
    telegram_file = MagicMock()
    telegram_file.download_to_drive = AsyncMock()
    bot = MagicMock()
    bot.get_file = AsyncMock(return_value=telegram_file)

    path = await download_to_temp_file(bot, "file-id", MediaType.VIDEO)

    try:
        assert path.endswith(".mp4")
    finally:
        cleanup_temp_file(path)


async def test_download_to_temp_file_wraps_telegram_errors_in_download_error():
    bot = MagicMock()
    bot.get_file = AsyncMock(side_effect=TelegramError("network problem"))

    with pytest.raises(TelegramDownloadError):
        await download_to_temp_file(bot, "file-id", MediaType.PHOTO)


async def test_download_to_temp_file_cleans_up_on_download_failure():
    bot = MagicMock()
    bot.get_file = AsyncMock(side_effect=TelegramError("boom"))

    created_paths = []
    from src.media import download as download_module

    original_mkstemp = download_module.tempfile.mkstemp

    def _tracking_mkstemp(*args, **kwargs):
        fd, path = original_mkstemp(*args, **kwargs)
        created_paths.append(path)
        return fd, path

    with patch.object(download_module.tempfile, "mkstemp", side_effect=_tracking_mkstemp):
        with pytest.raises(TelegramDownloadError):
            await download_to_temp_file(bot, "file-id", MediaType.PHOTO)

    assert len(created_paths) == 1
    assert not os.path.exists(created_paths[0])


def test_cleanup_temp_file_is_a_noop_for_missing_file():
    cleanup_temp_file("/tmp/nrc-social-agent-does-not-exist.jpg")  # must not raise


def test_cleanup_temp_file_logs_but_does_not_raise_on_os_error(caplog):
    with patch("src.media.download.os.remove", side_effect=OSError("disk error")):
        cleanup_temp_file("/tmp/some-path.jpg")  # must not raise
