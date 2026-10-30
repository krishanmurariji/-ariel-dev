"""
Candidate card (grid view): the AI score badge is green above 80, amber from 50 to 80 and red below 50.
"""

from types import SimpleNamespace

from django.template.loader import render_to_string
from horilla.isolated_testcase import IsolatedTestCase


def card(score):
    instance = SimpleNamespace(
        get_avatar="/static/avatar.png",
        get_full_name="Test Person",
        get_email="test@example.com",
        rating="<ul class='stars'><li></li></ul>",
        latest_ai_score=score,
    )
    return render_to_string("cbv/candidates/candidate_card_body.html", {"instance": instance})


class AiScoreBadgeTests(IsolatedTestCase):
    def test_colour_by_score(self):
        cases = [
            (100, "cc-ai--green"),
            (81, "cc-ai--green"),
            (80, "cc-ai--amber"),
            (65, "cc-ai--amber"),
            (50, "cc-ai--amber"),
            (49, "cc-ai--red"),
            (0, "cc-ai--red"),
            (None, "cc-ai--none"),
        ]
        for score, expected in cases:
            html = card(score)
            body = html[html.index('<div class="cc-body">'):]
            self.assertIn(f'class="cc-ai {expected}"', body, f"score {score}")
            for other in ("cc-ai--green", "cc-ai--amber", "cc-ai--red", "cc-ai--none"):
                if other != expected:
                    self.assertNotIn(f'class="cc-ai {other}"', body, f"score {score}")

    def test_shows_name_email_and_the_number(self):
        html = card(72)
        self.assertIn("Test Person", html)
        self.assertIn("test@example.com", html)
        self.assertIn(">72<", html)
