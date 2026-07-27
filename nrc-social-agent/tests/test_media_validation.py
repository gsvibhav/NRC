from unittest.mock import MagicMock

import pytest

from src.media.errors import (
    MediaTooLargeError,
    MissingTelegramFileError,
    NoMediaError,
    UnknownMediaSizeError,
    UnsupportedMediaTypeError,
)
from src.media.types import MediaType
from src.media.validation import extract_media_info, validate_media

ONE_MB = 1024 * 1024


def _make_message(*, photo=None, video=None):
    message = MagicMock()
    message.photo = photo if photo is not None else []
    message.video = video
    return message


def _make_photo_size(file_id="photo-file-id", file_unique_id="photo-unique-id", file_size=1 * ONE_MB):
    photo_size = MagicMock()
    photo_size.file_id = file_id
    photo_size.file_unique_id = file_unique_id
    photo_size.file_size = file_size
    return photo_size


def _make_video(
    file_id="video-file-id",
    file_unique_id="video-unique-id",
    file_size=5 * ONE_MB,
    mime_type="video/mp4",
    file_name="clip.mp4",
):
    video = MagicMock()
    video.file_id = file_id
    video.file_unique_id = file_unique_id
    video.file_size = file_size
    video.mime_type = mime_type
    video.file_name = file_name
    return video


# --- extract_media_info --------------------------------------------------


def test_extract_media_info_picks_largest_photo_size():
    small = _make_photo_size(file_id="small", file_size=100)
    large = _make_photo_size(file_id="large", file_size=200)
    message = _make_message(photo=[small, large])

    info = extract_media_info(message)

    assert info.media_type is MediaType.PHOTO
    assert info.file_id == "large"
    assert info.mime_type == "image/jpeg"


def test_extract_media_info_reads_video_fields():
    message = _make_message(video=_make_video())

    info = extract_media_info(message)

    assert info.media_type is MediaType.VIDEO
    assert info.file_id == "video-file-id"
    assert info.mime_type == "video/mp4"
    assert info.file_name == "clip.mp4"


def test_extract_media_info_raises_when_no_media_present():
    message = _make_message()

    with pytest.raises(NoMediaError):
        extract_media_info(message)


def test_extract_media_info_prefers_photo_over_video_if_both_present():
    message = _make_message(photo=[_make_photo_size()], video=_make_video())

    info = extract_media_info(message)

    assert info.media_type is MediaType.PHOTO


# --- validate_media --------------------------------------------------------


def _validate(info):
    validate_media(info, max_image_size_bytes=20 * ONE_MB, max_video_size_bytes=20 * ONE_MB)


def test_validate_media_accepts_a_valid_photo():
    info = extract_media_info(_make_message(photo=[_make_photo_size(file_size=2 * ONE_MB)]))
    _validate(info)  # must not raise


def test_validate_media_accepts_a_valid_video():
    info = extract_media_info(_make_message(video=_make_video(file_size=10 * ONE_MB)))
    _validate(info)  # must not raise


def test_validate_media_rejects_unsupported_video_mime_type():
    info = extract_media_info(_make_message(video=_make_video(mime_type="video/webm")))

    with pytest.raises(UnsupportedMediaTypeError):
        _validate(info)


def test_validate_media_rejects_missing_video_mime_type():
    info = extract_media_info(_make_message(video=_make_video(mime_type=None)))

    with pytest.raises(UnsupportedMediaTypeError):
        _validate(info)


def test_validate_media_rejects_oversized_photo():
    info = extract_media_info(_make_message(photo=[_make_photo_size(file_size=21 * ONE_MB)]))

    with pytest.raises(MediaTooLargeError):
        _validate(info)


def test_validate_media_rejects_oversized_video():
    info = extract_media_info(_make_message(video=_make_video(file_size=21 * ONE_MB)))

    with pytest.raises(MediaTooLargeError):
        _validate(info)


def test_validate_media_rejects_unknown_size():
    info = extract_media_info(_make_message(video=_make_video(file_size=None)))

    with pytest.raises(UnknownMediaSizeError):
        _validate(info)


def test_validate_media_rejects_missing_file_id():
    info = extract_media_info(_make_message(video=_make_video(file_id="")))

    with pytest.raises(MissingTelegramFileError):
        _validate(info)


def test_validate_media_respects_configured_size_limits():
    info = extract_media_info(_make_message(photo=[_make_photo_size(file_size=6 * ONE_MB)]))

    with pytest.raises(MediaTooLargeError):
        validate_media(info, max_image_size_bytes=5 * ONE_MB, max_video_size_bytes=20 * ONE_MB)
