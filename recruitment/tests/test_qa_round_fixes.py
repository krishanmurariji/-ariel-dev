"""
Fixes from a manual QA round:

* switching "Publish in LinkedIn" off now takes the live posts down
* a built-in mail stays quiet only while a SWITCHED-ON automation sends the same mail
* the onboarding job-profile dropdowns depend on each other and never end in a server error
* the Reporting Manager / Buddy mails go out at Finish Configuration even when the portal link
  was sent first
* the Personal Details step no longer fails with "multiple authentication backends"
"""

from unittest.mock import patch

from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import Client, RequestFactory
from django.urls import reverse

from base.mail_helpers import automation_sends
from horilla.isolated_testcase import IsolatedTestCase
from horilla.test_access_control import make_employee
from horilla_automations.models import MailAutomation
from recruitment.factories import (
    CandidateFactory,
    DepartmentFactory,
    JobPositionFactory,
    RecruitmentFactory,
)
from recruitment.models import LinkedInAccount


def make_automation(title, model, mail_to="['get_email']", trigger="on_create", active=True, channel="email"):
    MailAutomation.objects.bulk_create(
        [
            MailAutomation(
                title=title,
                method_title=title.replace(" ", "_").lower(),
                model=model,
                mail_to=mail_to,
                mail_details="instance",
                trigger=trigger,
                is_active=active,
                delivery_channel=channel,
            )
        ]
    )


# ---------------------------------------------------------------- 1. LinkedIn


class LinkedInSwitchOffTests(IsolatedTestCase):
    def setUp(self):
        from recruitment.cbv.recruitment_view import RecruitmentForm

        self.form = RecruitmentForm
        self.recruitment = RecruitmentFactory()
        self.recruitment.publish_in_linkedin = False
        self.account = LinkedInAccount.objects.create(username="acc", email="acc@example.com")
        self.request = RequestFactory().post("/")
        self.request.session = {}
        self.request._messages = FallbackStorage(self.request)

    def sync(self, delete_result=True, previous_accounts=None):
        with patch("recruitment.cbv.recruitment_view.delete_post", return_value=delete_result) as mocked:
            self.form._sync_linkedin_posts(
                self.request,
                self.recruitment,
                previous_accounts if previous_accounts is not None else {self.account.id},
                {str(self.account.id): "urn:li:share:123"},
                False,
            )
        return mocked

    def test_switching_it_off_deletes_the_live_post(self):
        mocked = self.sync()
        mocked.assert_called_once()
        self.assertEqual(mocked.call_args.kwargs["post_id"], "urn:li:share:123")

    def test_a_failed_delete_is_shown_to_the_user(self):
        self.sync(delete_result=False)
        texts = [str(m) for m in get_messages(self.request)]
        self.assertTrue(any("could not be removed" in text for text in texts), texts)

    def test_a_successful_delete_shows_no_warning(self):
        self.sync(delete_result=True)
        self.assertEqual(list(get_messages(self.request)), [])

    def test_nothing_is_deleted_when_there_was_no_post(self):
        with patch("recruitment.cbv.recruitment_view.delete_post") as mocked:
            self.form._sync_linkedin_posts(self.request, self.recruitment, set(), {}, False)
        mocked.assert_not_called()

    def test_the_delete_request_names_its_method(self):
        from recruitment.views.linkedin import delete_post

        self.account.api_token = "token"
        self.recruitment.linkedin_post_ids = {str(self.account.id): "urn:li:share:123"}

        class Response:
            status_code = 204

        with patch("recruitment.views.linkedin.requests.delete", return_value=Response()) as mocked:
            with patch.object(type(self.recruitment), "save"):
                self.assertTrue(delete_post(self.recruitment, self.account))
        self.assertEqual(mocked.call_args.kwargs["headers"]["X-RestLi-Method"], "DELETE")


# ------------------------------------------------------- 2. mail vs automation


class AutomationCheckTests(IsolatedTestCase):
    MODEL = "recruitment.models.CandidateDocumentRequest"

    def test_no_automation_means_the_built_in_mail_is_sent(self):
        self.assertFalse(automation_sends(self.MODEL))

    def test_a_switched_on_automation_wins(self):
        make_automation("Document Request Genrated", self.MODEL)
        self.assertTrue(automation_sends(self.MODEL))

    def test_a_switched_off_automation_does_not_count(self):
        make_automation("Document Request Genrated", self.MODEL, active=False)
        self.assertFalse(automation_sends(self.MODEL))

    def test_a_notification_only_automation_does_not_count(self):
        make_automation("Document Request Genrated", self.MODEL, channel="notification")
        self.assertFalse(automation_sends(self.MODEL))

    def test_another_model_does_not_count(self):
        make_automation("Other", "recruitment.models.Candidate")
        self.assertFalse(automation_sends(self.MODEL))

    def test_recipient_must_be_the_records_own_address(self):
        candidate = "recruitment.models.Candidate"
        make_automation(
            "To the manager", candidate, mail_to="['employee_id__employee_work_info__reporting_manager_id__get_email']"
        )
        self.assertFalse(automation_sends(candidate, "on_create", "get_email"))
        make_automation("To the candidate", candidate, mail_to="['get_email']")
        self.assertTrue(automation_sends(candidate, "on_create", "get_email"))

    def test_trigger_must_match(self):
        make_automation("On update", "recruitment.models.Candidate", trigger="on_update")
        self.assertFalse(automation_sends("recruitment.models.Candidate", "on_create"))


class DocumentMailsFollowTheAutomationTests(IsolatedTestCase):
    def setUp(self):
        self.candidate = CandidateFactory()
        from recruitment.models import CandidateDocumentRequest

        self.doc_request = CandidateDocumentRequest.objects.create(title="PAN card", format="any")

    def requested(self):
        from onboarding.document_mails import notify_candidate_document_requested

        with patch("onboarding.document_mails.send_branded_mail", return_value=True) as sent:
            result = notify_candidate_document_requested(self.candidate, self.doc_request, "http://x/")
        return result, sent

    def test_mail_goes_out_when_no_automation_exists(self):
        result, sent = self.requested()
        self.assertTrue(result)
        sent.assert_called_once()

    def test_mail_is_skipped_while_the_automation_is_on(self):
        make_automation("Document Request Genrated", "recruitment.models.CandidateDocumentRequest")
        result, sent = self.requested()
        self.assertFalse(result)
        sent.assert_not_called()

    def test_mail_comes_back_when_the_automation_is_switched_off(self):
        make_automation("Document Request Genrated", "recruitment.models.CandidateDocumentRequest", active=False)
        result, sent = self.requested()
        self.assertTrue(result)

    def test_rejection_and_portal_mails_use_the_same_rule(self):
        from recruitment.portal_mail import automation_sends_it
        from recruitment.rejection_mail import automation_owns_it

        self.assertFalse(automation_sends_it())
        self.assertFalse(automation_owns_it())
        make_automation("Application Received", "recruitment.models.Candidate")
        make_automation("Application Status Update", "recruitment.models.RejectedCandidate", mail_to="['candidate_id__get_email']")
        self.assertTrue(automation_sends_it())
        self.assertTrue(automation_owns_it())

    def test_resignation_and_exit_mails_use_the_same_rule(self):
        from offboarding.mailers import notify_exit_initiated, notify_manager_of_resignation

        make_automation("Resignation", "offboarding.models.ResignationLetter")
        make_automation("Exit", "offboarding.models.OffboardingEmployee")
        self.assertFalse(notify_manager_of_resignation(object()))
        self.assertEqual(notify_exit_initiated(object(), None), 0)


# ---------------------------------------------------- 3. job profile dropdowns


class JobProfileDropdownTests(IsolatedTestCase):
    def setUp(self):
        from onboarding.forms import OnboardingDetailsForm

        self.form_class = OnboardingDetailsForm
        self.recruitment = RecruitmentFactory()
        self.position = self.recruitment.job_position_id
        self.department = self.position.department_id
        from base.models import JobRole

        self.role = JobRole.objects.create(job_position_id=self.position, job_role="Lead")
        self.other_department = DepartmentFactory()
        self.other_position = JobPositionFactory(department_id=self.other_department)
        self.other_role = JobRole.objects.create(job_position_id=self.other_position, job_role="Other")
        self.candidate = CandidateFactory(recruitment_id=self.recruitment)
        # the stepper only opens a step the candidate has already reached
        from onboarding.stepper import _get_detail

        detail = _get_detail(self.candidate)
        detail.furthest_step_key = "finalize"
        detail.save()

    def bound(self, **data):
        return self.form_class(data, candidate=self.candidate)

    def test_only_positions_open_for_the_recruitment_are_offered(self):
        form = self.form_class(candidate=self.candidate)
        ids = {str(value) for value, _label in form.fields["job_position_id"].choices if value}
        self.assertEqual(ids, {str(self.position.pk)})

    def test_options_carry_their_parent_for_the_page_to_filter_on(self):
        html = str(self.form_class(candidate=self.candidate)["job_position_id"])
        self.assertIn(f'data-parent="{self.department.pk}"', html)
        html = str(self.form_class(candidate=self.candidate)["job_role_id"])
        self.assertIn(f'data-parent="{self.position.pk}"', html)

    def test_a_consistent_choice_is_valid(self):
        form = self.bound(
            department_id=self.department.pk, job_position_id=self.position.pk, job_role_id=self.role.pk
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_a_position_that_is_not_open_is_refused(self):
        form = self.bound(job_position_id=self.other_position.pk)
        self.assertFalse(form.is_valid())
        self.assertIn("job_position_id", form.errors)

    def test_a_position_from_another_department_is_refused(self):
        self.recruitment.open_positions.add(self.other_position)
        form = self.bound(department_id=self.department.pk, job_position_id=self.other_position.pk)
        self.assertFalse(form.is_valid())
        self.assertIn("job_position_id", form.errors)

    def test_a_role_from_another_position_is_refused(self):
        form = self.bound(job_position_id=self.position.pk, job_role_id=self.other_role.pk)
        self.assertFalse(form.is_valid())
        self.assertIn("job_role_id", form.errors)

    def test_the_page_script_filters_the_lists(self):
        user, _ = make_employee("jp_hr")
        user.is_superuser = True
        user.is_staff = True
        user.save()
        client = Client()
        client.force_login(user)
        response = client.get(
            reverse("onboarding-stepper-step", args=[self.candidate.pk, "details"]),
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-parent")
        self.assertContains(response, "Department -> Job Position -> Job Role")

    def test_a_bad_choice_shows_a_message_not_a_server_error(self):
        user, _ = make_employee("jp_hr2")
        user.is_superuser = True
        user.is_staff = True
        user.save()
        client = Client()
        client.force_login(user)
        url = reverse("onboarding-stepper-step", args=[self.candidate.pk, "details"])
        response = client.post(url, {"job_position_id": self.other_position.pk}, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please check the job profile details")


# ---------------------------------------------- 4. reporting manager / buddy mails


class AssignmentMailTests(IsolatedTestCase):
    def setUp(self):
        user, _ = make_employee("fin_hr")
        user.is_superuser = True
        user.is_staff = True
        user.save()
        self.client = Client()
        self.client.force_login(user)
        _u1, self.manager = make_employee("fin_rm")
        _u2, self.buddy = make_employee("fin_buddy")
        self.candidate = CandidateFactory()
        from onboarding.stepper import _get_detail

        self.detail = _get_detail(self.candidate)
        self.detail.reporting_manager_id = self.manager
        self.detail.buddy_id = self.buddy
        self.detail.furthest_step_key = "finalize"
        self.detail.save()
        self.url = reverse("onboarding-stepper-finalize", args=[self.candidate.pk])

    def finish(self):
        with patch("onboarding.stepper.notify_reporting_manager_assigned") as rm, patch(
            "onboarding.stepper.notify_onboarding_buddy_assigned"
        ) as buddy:
            self.client.post(self.url)
        return rm, buddy

    def test_mails_go_out_at_finish_configuration(self):
        rm, buddy = self.finish()
        rm.assert_called_once()
        buddy.assert_called_once()

    def test_mails_still_go_out_when_the_portal_link_was_sent_first(self):
        from recruitment.models import Candidate

        Candidate.objects.filter(pk=self.candidate.pk).update(start_onboard=True)
        rm, buddy = self.finish()
        rm.assert_called_once()
        buddy.assert_called_once()

    def test_finishing_twice_does_not_mail_twice(self):
        self.finish()
        rm, buddy = self.finish()
        rm.assert_not_called()
        buddy.assert_not_called()


# ------------------------------------------- 5. personal details: login backend


class PersonalDetailsLoginTests(IsolatedTestCase):
    def test_login_with_the_named_backend_does_not_need_authenticate(self):
        from django.contrib.auth import login
        from django.contrib.sessions.backends.signed_cookies import SessionStore

        from horilla_auth.models import HorillaUser

        request = RequestFactory().get("/")
        request.session = SessionStore()
        user = HorillaUser.objects.create(username="pd_user@example.com")
        # without backend= this raised "multiple authentication backends configured"
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        self.assertEqual(request.session["_auth_user_id"], str(user.pk))

    def test_the_view_names_the_backend(self):
        from pathlib import Path

        from django.conf import settings

        text = (Path(settings.BASE_DIR) / "onboarding" / "views.py").read_text(encoding="utf-8")
        start = text.index("def employee_creation")
        end = text.index("def employee_education")
        self.assertIn('backend="django.contrib.auth.backends.ModelBackend"', text[start:end])


# ------------------------------------- 6. forms that crashed on an empty choice


class EmptyChoiceFormTests(IsolatedTestCase):
    def setUp(self):
        user, _ = make_employee("ec_hr")
        user.is_superuser = True
        user.is_staff = True
        user.save()
        self.client = Client(raise_request_exception=False)
        self.client.force_login(user)
        self.candidate = CandidateFactory()

    def test_adding_a_candidate_without_a_recruitment_shows_a_field_error(self):
        response = self.client.post(
            reverse("candidate-create"),
            {"name": "No Recruitment", "email": "no.recruitment@example.com", "recruitment_id": ""},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"missing from the request", response.content)
        self.assertContains(response, "This field is required")

    def test_adding_a_candidate_to_a_skill_zone_without_choosing_one_does_not_crash(self):
        response = self.client.post(
            reverse("to-skill-zone", args=[self.candidate.pk]), {"reason": "x"}, HTTP_HX_REQUEST="true"
        )
        self.assertEqual(response.status_code, 200)
