from src.publication.models import PublicationContent
from src.publisher.instagram.caption import assemble_instagram_caption


def test_assemble_caption_includes_cta_and_hashtags_as_separate_paragraphs():
    content = PublicationContent(caption="Great caption.", cta="Learn more", hashtags=["nrc", "brand"])

    assert assemble_instagram_caption(content) == "Great caption.\n\nLearn more\n\n#nrc #brand"


def test_assemble_caption_omits_missing_cta_and_hashtags_entirely():
    content = PublicationContent(caption="Great caption.", cta=None, hashtags=[])

    assert assemble_instagram_caption(content) == "Great caption."


def test_assemble_caption_never_rewrites_the_caption_text():
    exact = "This is the exact approved wording, unchanged, word for word."
    content = PublicationContent(caption=exact, cta=None, hashtags=[])

    assert exact in assemble_instagram_caption(content)


def test_assemble_caption_is_deterministic():
    content = PublicationContent(caption="Hi", cta="Learn more", hashtags=["a", "b"])

    assert assemble_instagram_caption(content) == assemble_instagram_caption(content)


def test_assemble_caption_never_duplicates_or_drops_hashtags():
    content = PublicationContent(caption="Hi", cta=None, hashtags=["one", "two", "three"])

    result = assemble_instagram_caption(content)
    for tag in ("one", "two", "three"):
        assert result.count(f"#{tag}") == 1
