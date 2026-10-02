import pytest

ANSWER = "\n".join(
    [
        "plain line",
        "> quoted",
        "# heading",
        "- dash item",
        "+ plus item",
        "1. numbered",
        "2) numbered again",
        "---",
        "| table | row |",
        "    indented code",
        "mid *line* `code` [link]",
    ]
)

ESCAPED = "\n".join(
    [
        "plain line",
        "\\> quoted",
        "\\# heading",
        "\\- dash item",
        "\\+ plus item",
        "1\\. numbered",
        "2\\) numbered again",
        "\\---",
        "\\| table | row |",
        "indented code",
        "mid \\*line\\* \\`code\\` \\[link\\]",
    ]
)


def test_block_markdown_in_an_answer_is_escaped(app_instance, fake_zulip):
    assert app_instance.apply(bio=ANSWER).status_code == 200

    assert f"**Tell us a bit about yourself.**\n{ESCAPED}" in fake_zulip.posts[0]["content"]


@pytest.mark.parametrize("line_break", ["\n", "\r\n"])
def test_line_endings_from_browsers_are_handled(app_instance, fake_zulip, line_break):
    assert app_instance.apply(bio=f"first line here{line_break}> second line here").status_code == 200

    assert "first line here\n\\> second line here" in fake_zulip.posts[0]["content"]
