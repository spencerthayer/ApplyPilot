"""Tests for tailoring/pieces/renderer.py."""


class TestPiecesRenderer:
    def test_render_txt(self, tmp_path):
        from applypilot.tailoring.pieces.renderer import render_txt

        out = render_txt("Hello\nWorld", tmp_path / "test.txt")
        assert out.exists()
        assert out.read_text() == "Hello\nWorld"

    def test_render_html_uses_professional_template(self, tmp_path):
        from applypilot.tailoring.pieces.renderer import render_html

        text = (
            "John Doe\nSDE\nNYC\njohn@test.com\n\n"
            "SUMMARY\nExperienced dev.\n\n"
            "EXPERIENCE\nSDE at Amazon\n- Built API reducing latency 30%\n"
        )
        out = render_html(text, tmp_path / "test.html")
        html = out.read_text()
        assert "section-title" in html
        assert "John Doe" in html
