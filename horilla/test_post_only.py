"""
@post_only: the decorator the state-changing views now carry.

It refuses everything but POST, and keeps values that a page sends in the request body
readable through request.GET, so views written for GET keep working when called by POST.
"""

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase

from horilla.isolated_testcase import IsolatedTestCase
from horilla.post_only import post_only
from horilla.test_access_control import make_employee


@post_only
def echo(request):
    return HttpResponse(
        "|".join(
            [
                request.GET.get("a", "-"),
                ",".join(request.GET.getlist("many")) or "-",
                request.GET.get("csrfmiddlewaretoken", "-"),
                request.GET.urlencode(),
                request.POST.get("a", "-"),
            ]
        )
    )


class DecoratorTests(SimpleTestCase):
    def setUp(self):
        self.rf = RequestFactory()

    def test_anything_but_post_is_refused_with_405(self):
        for method in ("get", "head", "put", "patch", "delete", "options"):
            with self.subTest(method=method):
                response = getattr(self.rf, method)("/x/?a=1")
                response = echo(response)
                self.assertEqual(response.status_code, 405)
                self.assertEqual(response["Allow"], "POST")

    def test_post_runs_the_view(self):
        self.assertEqual(echo(self.rf.post("/x/")).status_code, 200)

    def test_a_value_sent_in_the_body_is_readable_as_request_get(self):
        response = echo(self.rf.post("/x/", {"a": "body"}))
        self.assertTrue(response.content.decode().startswith("body|"))

    def test_the_query_string_wins_over_the_body(self):
        response = echo(self.rf.post("/x/?a=query", {"a": "body"}))
        self.assertTrue(response.content.decode().startswith("query|"))
        # request.POST itself is untouched
        self.assertEqual(response.content.decode().split("|")[-1], "body")

    def test_several_values_for_one_name_are_all_kept(self):
        response = echo(self.rf.post("/x/", {"many": ["1", "2", "3"]}))
        self.assertEqual(response.content.decode().split("|")[1], "1,2,3")

    def test_the_csrf_token_is_never_copied(self):
        response = echo(self.rf.post("/x/", {"csrfmiddlewaretoken": "SECRET", "a": "1"}))
        text = response.content.decode()
        self.assertNotIn("SECRET", text)
        self.assertEqual(text.split("|")[2], "-")

    def test_urlencode_for_redirects_does_not_contain_the_token(self):
        response = echo(self.rf.post("/x/?page=2", {"csrfmiddlewaretoken": "SECRET"}))
        self.assertNotIn("csrfmiddlewaretoken", response.content.decode().split("|")[3])
        self.assertIn("page=2", response.content.decode().split("|")[3])

    def test_the_original_query_dict_is_not_mutated(self):
        request = self.rf.post("/x/?a=1", {"b": "2"})
        original = request.GET
        echo(request)
        self.assertNotIn("b", original)


class ConvertedFlowTests(IsolatedTestCase):
    """A real page action, now POST-only, that depends on a value in the request body."""

    URL = "default-meeting-provider"

    def setUp(self):
        from django.test import Client

        user, _ = make_employee("postonly_flow")
        user.is_superuser = True
        user.is_staff = True
        user.save()
        self.client = Client()
        self.client.force_login(user)

    def setting(self):
        from recruitment.models import RecruitmentGeneralSetting

        return RecruitmentGeneralSetting.objects.entire().filter(company_id__isnull=True).first()

    def test_post_with_the_value_in_the_body_updates_the_setting(self):
        from django.urls import reverse

        response = self.client.post(
            reverse(self.URL), {"default_meeting_provider": "teams"}, HTTP_HX_REQUEST="true"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.setting().default_meeting_provider, "teams")

    def test_post_with_the_value_in_the_url_still_works(self):
        from django.urls import reverse

        self.client.post(
            reverse(self.URL) + "?default_meeting_provider=teams", HTTP_HX_REQUEST="true"
        )
        self.assertEqual(self.setting().default_meeting_provider, "teams")

    def test_get_changes_nothing(self):
        from django.urls import reverse

        before = getattr(self.setting(), "default_meeting_provider", None)
        response = self.client.get(
            reverse(self.URL) + "?default_meeting_provider=teams", HTTP_HX_REQUEST="true"
        )
        self.assertEqual(response.status_code, 405)
        self.assertEqual(getattr(self.setting(), "default_meeting_provider", None), before)
