"""
recruitment
"""

import time
from typing import Any
from urllib.parse import quote

from django import forms
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse, reverse_lazy
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View

from base.forms import Form
from base.models import IntegrationApps
from horilla_views.cbv_methods import login_required, permission_required
from horilla_views.generic.cbv.views import (
    HorillaDetailedView,
    HorillaFormView,
    HorillaListView,
    HorillaNavView,
    TemplateView,
)
from recruitment.filters import RecruitmentFilter
from recruitment.forms import AddCandidateForm, RecruitmentCreationForm, SkillsForm
from recruitment.models import Candidate, LinkedInAccount, Platform, Recruitment, Skill
from recruitment.views.linkedin import delete_post, post_recruitment_in_linkedin


class DynamicLinkForm(Form):
    """
    Form used to pick (or create) a Platform when generating a
    dynamic, source-tagged application link for a Job Opening.
    """

    platform = forms.ModelChoiceField(
        queryset=Platform.objects.all(),
        required=False,
        label=_("Source"),
    )
    new_platform = forms.CharField(
        required=False,
        label=_("Or type a new source name (e.g. LinkedIn, Indeed)"),
    )


@method_decorator(login_required, name="dispatch")
@method_decorator(
    permission_required(perm="recruitment.change_recruitment"), name="dispatch"
)
class RecruitmentDynamicLinkView(View):
    """
    Generates a shareable application-form link for a Recruitment,
    tagged with a Platform (LinkedIn, Indeed, ...) via the standard
    `utm_source` query parameter, so submissions can be traced back
    to the channel they came from.
    """

    def get(self, *args, **kwargs):
        request = self.request
        recruitment = get_object_or_404(Recruitment, pk=self.kwargs.get("pk"))
        form = DynamicLinkForm()
        return render(
            request,
            "cbv/recruitment/dynamic_link_form.html",
            {"form": form, "recruitment": recruitment},
        )

    def post(self, *args, **kwargs):
        request = self.request
        recruitment = get_object_or_404(Recruitment, pk=self.kwargs.get("pk"))
        form = DynamicLinkForm(request.POST)
        platform = None
        if form.is_valid():
            new_platform = form.cleaned_data.get("new_platform", "").strip()
            if new_platform:
                platform, _created = Platform.objects.get_or_create(
                    title__iexact=new_platform, defaults={"title": new_platform}
                )
            else:
                platform = form.cleaned_data.get("platform")

        link = (
            f"{request.scheme}://{request.get_host()}"
            f"{reverse('application-form')}?recruitmentId={recruitment.pk}"
        )
        if platform:
            link += f"&utm_source={quote(platform.title)}"

        return render(
            request,
            "cbv/recruitment/dynamic_link_result.html",
            {"link": link, "recruitment": recruitment},
        )


@method_decorator(login_required, name="dispatch")
@method_decorator(
    permission_required(perm="recruitment.view_recruitment"), name="dispatch"
)
class RecruitmentView(TemplateView):
    """
    Recuitment page
    """

    template_name = "cbv/recruitment/recruitment.html"


@method_decorator(login_required, name="dispatch")
@method_decorator(
    permission_required(perm="recruitment.view_recruitment"), name="dispatch"
)
class RecruitmentList(HorillaListView):
    """
    List view of recruitment
    """

    model = Recruitment
    filter_class = RecruitmentFilter
    view_id = "rec-view-container"

    bulk_update_fields = ["vacancy", "start_date", "end_date", "closed"]

    template_name = "cbv/recruitment/rec_main.html"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.search_url = reverse("list-recruitment")

    def get_queryset(self, queryset=None, filtered=False, *args, **kwargs):
        self.queryset = (
            super()
            .get_queryset(queryset, filtered, *args, **kwargs)
            .filter(is_active=self.request.GET.get("is_active", True))
        )
        return self.queryset

    columns = [
        (_("Recruitment"), "recruitment_column"),
        (_("Interviewers"), "managers_column"),
        (_("Open Jobs"), "open_job_col"),
        (_("Vacancy"), "vacancy"),
        (_("Total Hires"), "tot_hires"),
        (_("Start Date"), "start_date"),
        (_("End date"), "end_date"),
        (_("Status"), "status_col"),
    ]
    actions_menu = True
    show_filter_tags = False

    actions = [
        {
            "action": _("Create Dynamic Link"),
            "accessibility": "recruitment.cbv.accessibility.change_recruitment",
            "icon": "link-outline",
            "attrs": """
                hx-get="{get_dynamic_link_url}"
                hx-target="#genericModalBody"
                hx-swap="innerHTML"
                data-toggle="oh-modal-toggle"
                data-target="#genericModal"
                onclick="event.stopPropagation();"
            """,
        },
        {
            "action": _("Edit"),
            "accessibility": "recruitment.cbv.accessibility.change_recruitment",
            "icon": "create-outline",
            "attrs": """
                hx-get="{get_update_url}?instance_ids={ordered_ids}"
                hx-target="#genericModalBody"
                hx-swap="innerHTML"
                data-toggle="oh-modal-toggle"
                data-target="#genericModal"
                onclick="event.stopPropagation();"
            """,
        },
        {
            "action": _("Duplicate"),
            "icon": "copy-outline",
            "attrs": """
                hx-get="{get_duplicate_url}"
                hx-target="#genericModalBody"
                hx-swap="innerHTML"
                data-toggle="oh-modal-toggle"
                data-target="#genericModal"
                onclick="event.stopPropagation();"
            """,
        },
        {
            "action": _("Archive"),
            "accessibility": "recruitment.cbv.accessibility.archive_recruitment",
            "icon": "archive",
            "attrs": """
                hx-post="{get_archive_url}"
                hx-swap="none"
                hx-confirm="Are you sure you want to archive this recruitment?"
                hx-on::after-request="event.detail.successful && ($('#applyFilter').click(), $('#reloadMessagesButton').click())"
                onclick="event.stopPropagation();"
            """,
        },
        {
            "action": _("Unarchive"),
            "accessibility": "recruitment.cbv.accessibility.unarchive_recruitment",
            "icon": "archive",
            "attrs": """
                hx-post="{get_archive_url}"
                hx-swap="none"
                hx-confirm="Are you sure you want to unarchive this recruitment?"
                hx-on::after-request="event.detail.successful && ($('#applyFilter').click(), $('#reloadMessagesButton').click())"
                onclick="event.stopPropagation();"
            """,
        },
        {
            "action": _("Delete"),
            "accessibility": "recruitment.cbv.accessibility.delete_recruitment",
            "icon": "trash-outline",
            "attrs": """
                hx-post="{get_delete_url}"
                hx-swap="none"
                hx-confirm="Are you sure you want to delete this recruitment?"
                hx-on::after-request="event.detail.successful && ($('#applyFilter').click(), $('#reloadMessagesButton').click())"
                onclick="event.stopPropagation();"
            """,
        },
    ]

    header_attrs = {
        "recruitment_column": 'style="width : 200px !important"',
        "action": 'style="width : 90px !important; min-width : 90px !important;"',
    }

    row_status_indications = [
        (
            "closed--dot",
            _("Closed"),
            """
            onclick="
                $('#applyFilter').closest('form').find('[name=closed]').val('true');
                $('#applyFilter').click();

            "
            """,
        ),
        (
            "open--dot",
            _("Open"),
            """
            onclick="
                $('#applyFilter').closest('form').find('[name=closed]').val('false');
                $('#applyFilter').click();

            "
            """,
        ),
    ]

    row_status_class = "closed-{closed}"

    sortby_mapping = [
        (_("Recruitment"), "recruitment_column"),
        (_("Vacancy"), "vacancy"),
        (_("Start Date"), "start_date"),
        (_("End date"), "end_date"),
    ]

    row_attrs = """
                class="oh-permission-table--collapsed"
                hx-get='{recruitment_detail_view}?instance_ids={ordered_ids}'
                hx-target="#genericModalBody"
                data-target="#genericModal"
                data-toggle="oh-modal-toggle"
                """


@method_decorator(login_required, name="dispatch")
@method_decorator(
    permission_required(perm="recruitment.view_recruitment"), name="dispatch"
)
class RecruitmentNav(HorillaNavView):
    """
    For nav bar
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.search_url = reverse("list-recruitment")

        self.create_attrs = f"""
                            hx-get='{reverse_lazy('recruitment-create')}'
                            hx-target="#genericModalBody"
                            data-target="#genericModal"
                            data-toggle="oh-modal-toggle"
                            """

    nav_title = _("Recruitment")
    filter_instance = RecruitmentFilter()
    filter_form_context_name = "form"
    search_swap_target = "#listContainer"
    filter_body_template = "cbv/recruitment/filters.html"
    filter_icon_only = True
    show_clear_filter = True


class RecruitmentCreationFormExtended(RecruitmentCreationForm):
    """
    extended form view for create
    """

    cols = {
        "title": 12,
        "description": 12,
        "is_published": 4,
        "closed": 4,
        "optional_profile_image": 4,
        "optional_resume": 4,
    }

    class Meta:
        """
        Meta class to add the additional info
        """

        model = Recruitment
        fields = [
            "title",
            "description",
            "recruitment_managers",
            "recruitment_heads",
            "open_positions",
            "start_date",
            "end_date",
            "vacancy",
            "company_id",
            "survey_templates",
            "skills",
            "is_published",
            "closed",
            "optional_profile_image",
            "optional_resume",
            "publish_in_linkedin",
            "linkedin_accounts",
        ]
        exclude = ["is_active"]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
            "description": forms.Textarea(attrs={"data-summernote": ""}),
        }
        labels = {
            "description": _("Description"),
            "start_date": _("Start Date"),
            "end_date": _("End Date"),
            "survey_templates": _("Survey Templates"),
            "is_published": _("Is Published?"),
            "closed": _("Closed"),
            "vacancy": _("Vacancy"),
            "open_positions": _("Job Position"),
            "recruitment_managers": _("Interviewers"),
            "recruitment_heads": _("Recruitment Heads"),
            "optional_profile_image": _("Optional Profile Image?"),
            "optional_resume": _("Optional Resume?"),
            "linkedin_accounts": _("LinkedIn Accounts"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not IntegrationApps.objects.filter(
            app_label="linkedin", is_enabled=True
        ).exists():
            self.fields["publish_in_linkedin"].initial = False
            self.fields["publish_in_linkedin"].widget = forms.HiddenInput()
            self.fields["linkedin_accounts"].widget = forms.MultipleHiddenInput()


@method_decorator(login_required, name="dispatch")
class RecruitmentNewSkillForm(HorillaFormView):
    """
    form view for add new skill
    """

    model = Skill
    form_class = SkillsForm
    new_display_title = _("Skills")
    is_dynamic_create_view = True

    def form_valid(self, form: SkillsForm) -> HttpResponse:
        if form.is_valid():
            message = _("New Skill Created Successfully")
            form.save()
            messages.success(self.request, message)
            return self.HttpResponse()
        return super().form_valid(form)


@method_decorator(login_required, name="dispatch")
@method_decorator(
    permission_required(perm="recruitment.view_recruitment"), name="dispatch"
)
class RecruitmentForm(HorillaFormView):
    """
    Form View
    """

    model = Recruitment
    form_class = RecruitmentCreationFormExtended
    new_display_title = _("Add Recruitment")
    dynamic_create_fields = [("skills", RecruitmentNewSkillForm)]
    # template_name = "cbv/recruitment/recruitment_form.html"

    def get_context_data(self, **kwargs):
        """
        Return context data with optional verbose name for form based on instance state.
        """
        context = super().get_context_data(**kwargs)

        if self.form.instance.pk:
            self.form_class.verbose_name = "Edit Recruitment"
        return context

    @staticmethod
    def _linkedin_content_signature(pk):
        """
        A cheap fingerprint of everything that actually shows up in a
        LinkedIn post for this recruitment (title, description, company,
        vacancy count, skills). Used to tell a save that changed one of
        these from a save that didn't, so editing something unrelated
        (interviewers, dates, ...) while "Post on LinkedIn" is on doesn't
        burn a delete+publish round trip against LinkedIn's API on every
        save - repeatedly doing that is what was tripping LinkedIn's rate
        limiting (a 429) on frequently-edited jobs.
        """
        row = Recruitment.objects.filter(pk=pk).values(
            "title", "description", "company_id", "vacancy"
        ).first()
        if not row:
            return None
        skill_ids = tuple(
            Recruitment.objects.get(pk=pk).skills.order_by("id").values_list(
                "id", flat=True
            )
        )
        return (
            row["title"],
            row["description"],
            row["company_id"],
            row["vacancy"],
            skill_ids,
        )

    @staticmethod
    def _remove_post(request, recruitment, account, post_id):
        """Delete one LinkedIn post and tell the user when LinkedIn would not."""
        if not post_id:
            return
        if not delete_post(recruitment, account, post_id=post_id):
            messages.warning(
                request,
                _(
                    "The LinkedIn post on the %(account)s account could not be "
                    "removed automatically. Please delete it on LinkedIn."
                )
                % {"account": account},
            )

    @staticmethod
    def _sync_linkedin_posts(
        request, recruitment, previous_account_ids, previous_post_ids, content_changed
    ):
        """
        Publishes/refreshes/removes this recruitment's LinkedIn posts so
        they match the just-saved publish_in_linkedin flag and
        linkedin_accounts selection - must run after form.save(), since
        that's what commits the new M2M selection recruitment.linkedin_accounts
        reads here.

        An account newly added to the selection always gets a fresh post
        (it's never been posted for this recruitment before). An account
        that was already selected only gets re-published if the content a
        LinkedIn post actually shows (title/description/company/vacancy/
        skills) changed - otherwise every unrelated save (interviewers,
        dates, ...) would burn a delete+publish round trip and risk
        LinkedIn's rate limiting. An account removed from the selection
        gets its post deleted.
        """
        if not recruitment.publish_in_linkedin:
            # Switching "Publish in LinkedIn" off clears the stored post ids and
            # accounts in Recruitment.save(), so the live posts have to come down
            # here, from the ids read before the save - otherwise they stay on
            # LinkedIn with nothing left that could ever delete them.
            stored_ids = [int(key) for key in previous_post_ids if str(key).isdigit()]
            for account in LinkedInAccount.objects.filter(id__in=stored_ids):
                RecruitmentForm._remove_post(
                    request, recruitment, account, previous_post_ids.get(str(account.id))
                )
            return
        current_account_ids = set(
            recruitment.linkedin_accounts.values_list("id", flat=True)
        )

        removed_ids = previous_account_ids - current_account_ids
        for account in LinkedInAccount.objects.filter(id__in=removed_ids):
            RecruitmentForm._remove_post(
                request, recruitment, account, previous_post_ids.get(str(account.id))
            )

        accounts_to_publish_ids = current_account_ids - previous_account_ids
        if content_changed:
            accounts_to_publish_ids |= current_account_ids & previous_account_ids

        for index, account in enumerate(
            LinkedInAccount.objects.filter(id__in=accounts_to_publish_ids)
        ):
            if index:
                # LinkedIn's abuse/velocity checks can reject a post fired
                # immediately after a previous one from the same app - seen
                # in practice as the first selected account posting fine
                # and every other one failing on the very next call. A
                # short gap between accounts avoids tripping that.
                time.sleep(2)
            # Publish the refreshed post first and only remove the old one
            # once that succeeds - if LinkedIn rate-limits or otherwise
            # rejects the new post, the job keeps showing its last-
            # published (still valid) post instead of ending up with none.
            old_post_id = previous_post_ids.get(str(account.id))
            published = post_recruitment_in_linkedin(request, recruitment, account)
            if published and old_post_id:
                delete_post(recruitment, account, post_id=old_post_id)

    def form_valid(self, form: RecruitmentCreationFormExtended) -> HttpResponse:
        """
        Process form submission to save or update a Recruitment object and display success message.
        """
        targets_to_reload = []

        if form.instance.pk:
            previous_signature = self._linkedin_content_signature(form.instance.pk)
            previous_account_ids = set(
                form.instance.linkedin_accounts.values_list("id", flat=True)
            )
            previous_post_ids = dict(form.instance.linkedin_post_ids or {})
            recruitment = form.save()
            recruitment_managers = self.request.POST.getlist("recruitment_managers")
            if recruitment_managers:
                recruitment.recruitment_managers.set(recruitment_managers)
            recruitment_heads = self.request.POST.getlist("recruitment_heads")
            recruitment.recruitment_heads.set(recruitment_heads)
            content_changed = (
                self._linkedin_content_signature(recruitment.pk) != previous_signature
            )
            self._sync_linkedin_posts(
                self.request,
                recruitment,
                previous_account_ids,
                previous_post_ids,
                content_changed,
            )
            message = _("Recruitment Updated Successfully")
        else:
            recruitment = form.save()
            recruitment_managers = self.request.POST.getlist("recruitment_managers")
            if recruitment_managers:
                recruitment.recruitment_managers.set(recruitment_managers)
            recruitment_heads = self.request.POST.getlist("recruitment_heads")
            recruitment.recruitment_heads.set(recruitment_heads)
            self._sync_linkedin_posts(
                self.request, recruitment, set(), {}, content_changed=True
            )
            message = _("Recruitment Created Successfully")
        messages.success(self.request, message)
        if self.request.GET.get("pipeline") == "true" or (
            self.request.resolver_match
            and self.request.resolver_match.url_name == "recruitment-update-pipeline"
        ):
            # Refresh pipeline container only, instead of reloading the whole page.
            targets_to_reload.append("#applyFilter")

        return self.HttpResponse(targets_to_reload=targets_to_reload)

    def form_invalid(self, form):
        return self.render_to_response(self.get_context_data(form=form))


@method_decorator(login_required, name="dispatch")
class AddCandidateFormView(HorillaFormView):
    """
    form view for add candidate
    """

    form_class = AddCandidateForm
    model = Candidate
    new_display_title = _("Add Candidate")
    template_name = "cbv/candidates/add_candidate_form.html"

    def get_initial(self) -> dict:
        initial = super().get_initial()
        stage_id = self.request.GET.get("stage_id")
        rec_id = self.request.GET.get("rec_id")
        initial["stage_id"] = stage_id
        initial["rec_id"] = rec_id
        return initial

    def form_valid(self, form: AddCandidateForm) -> HttpResponse:
        if form.is_valid():
            message = _("Candidate Added successfully.")
            form.save()
            self._send_portal_login_mail(form.instance)
            messages.success(self.request, message)
            return self.HttpResponse("<script>window.location.reload</script>")
        return super().form_valid(form)

    def _send_portal_login_mail(self, candidate):
        """
        The "Candidate Portal Login" automation only fires on_create for
        candidates landing in an "applied"-type stage (it's phrased as an
        application-received acknowledgement) - a candidate added here by
        staff can be dropped into any stage, so that condition silently
        skips them. Send it explicitly instead, gated on the same portal
        mail toggle (Settings > Recruitment > Candidate Self Tracking) so
        it still respects that setting.
        """
        from horilla_automations.models import MailAutomation
        from horilla_automations.signals import send_mail as automation_send_mail
        from recruitment.models import RecruitmentGeneralSetting

        selected_company = self.request.session.get("selected_company")
        company_id = (
            None if not selected_company or selected_company == "all" else selected_company
        )
        settings_row = RecruitmentGeneralSetting.objects.filter(
            company_id_id=company_id
        ).first()
        if not (
            settings_row
            and settings_row.candidate_self_tracking
            and settings_row.send_candidate_portal_mail
        ):
            return
        automation = MailAutomation.objects.filter(
            model="recruitment.models.Candidate",
            mail_template__title="Candidate Portal Login",
            is_active=True,
        ).first()
        if automation:
            automation_send_mail(self.request, automation, candidate)


@method_decorator(login_required, name="dispatch")
@method_decorator(
    permission_required(perm="recruitment.view_recruitment"), name="dispatch"
)
class RecruitmentFormDuplicate(HorillaFormView):
    """
    Duplicate form view
    """

    model = Recruitment
    form_class = RecruitmentCreationFormExtended

    def get_context_data(self, **kwargs):
        """
        Return context data for duplicating a Recruitment object form with modified initial values.
        """
        context = super().get_context_data(**kwargs)
        original_object = Recruitment.objects.get(id=self.kwargs["pk"])
        form = self.form_class(instance=original_object)
        for field_name, field in form.fields.items():
            if isinstance(field, forms.CharField):
                if field.initial:
                    initial_value = field.initial
                else:
                    initial_value = f"{form.initial.get(field_name, '')} (copy)"
                form.initial[field_name] = initial_value
                form.fields[field_name].initial = initial_value
        context["form"] = form
        self.form_class.verbose_name = _("Duplicate")
        return context

    def form_valid(self, form: RecruitmentCreationFormExtended) -> HttpResponse:
        """
        Process form submission to add a new recruitment.
        """
        form = self.form_class(self.request.POST)
        if form.is_valid():
            recruitment = form.save()
            message = _("Recruitment added")
            recruitment.save()
            recruitment_managers = self.request.POST.getlist("recruitment_managers")
            job_positions = self.request.POST.getlist("open_positions")
            if recruitment_managers:
                recruitment.recruitment_managers.set(recruitment_managers)
            if job_positions:
                recruitment.open_positions.set(job_positions)
            messages.success(self.request, message)
            return self.HttpResponse(targets_to_reload=["#applyFilter"])

        return super().form_valid(form)


@method_decorator(login_required, name="dispatch")
@method_decorator(
    permission_required(perm="recruitment.view_recruitment"), name="dispatch"
)
class RecruitmentDetailView(HorillaDetailedView):
    """
    detail view of page
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.body = [
            (_("Interviewers"), "managers_detail"),
            (_("Open Jobs"), "open_job_detail"),
            (_("Vacancy"), "vacancy"),
            (_("Total Hires"), "tot_hires"),
            (_("Start Date"), "start_date"),
            (_("End date"), "end_date"),
        ]

    action_method = "detail_actions"

    model = Recruitment
    title = _("Details")
    header = {
        "title": "title",
        "subtitle": "status_col",
        "avatar": "get_avatar",
    }
