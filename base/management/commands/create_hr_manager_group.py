"""
Management command to create (or update) the "HR Manager" group with every
permission needed to use the Recruitment module end-to-end, including the
Onboarding module by default since a couple of Recruitment flows (candidate
creation/update, the onboarding tab on a candidate profile) accept either
recruitment.* or onboarding.* permissions.

Usage:
    python manage.py create_hr_manager_group
    python manage.py create_hr_manager_group --group-name "HR Manager"
    python manage.py create_hr_manager_group --no-onboarding
"""

from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

DEFAULT_ACTIONS = ["add", "change", "delete", "view"]

RECRUITMENT_MODELS = [
    "surveytemplate",
    "skill",
    "platform",
    "recruitment",
    "stage",
    "candidate",
    "candidateeducation",
    "candidateexperience",
    "candidateemergencycontact",
    "rejectreason",
    "rejectedcandidate",
    "stagefiles",
    "stagenote",
    "recruitmentsurvey",
    "questionordering",
    "recruitmentsurveyanswer",
    "skillzone",
    "skillzonecandidate",
    "candidaterating",
    "recruitmentgeneralsetting",
    "interviewschedule",
    "resume",
    "candidatedocumentrequest",
    "candidatedocument",
    "linkedinaccount",
    "candidateintakemailbox",
    "candidateintakelog",
    "intakepolllock",
    "candidateaiscore",
    "candidatejobsuggestion",
    "candidatehistorysummary",
    "aisettings",
]

# Custom (non add/change/delete/view) permissions defined on the recruitment
# app's models. Note: "archive_recruitment", "archive_Stage" and
# "archive_candidate" exist in the DB but aren't referenced by any view today
# (dead/reserved permissions) - included anyway so the group is future-proof.
RECRUITMENT_CUSTOM_CODENAMES = [
    "archive_recruitment",
    "archive_Stage",
    "view_history",
    "archive_candidate",
]

ONBOARDING_MODELS = [
    "onboardingstage",
    "onboardingtask",
    "onboardingcandidate",
    "candidatestage",
    "candidatetask",
    "onboardingportal",
    "tasktemplate",
    "tasktemplatetask",
    "candidatechecklisttask",
    "onboardingcandidatedetail",
]

# view/add/change only (no delete) - lets HR Manager view and configure
# Outlook mail servers (Settings > Outlook) without being able to remove
# an existing Azure AD app registration by mistake.
OUTLOOK_AUTH_CODENAMES = [
    "view_azureapi",
    "add_azureapi",
    "change_azureapi",
    # Needed for the "Send Meeting Link" (Generate Meeting Link) button on an
    # interview to show up when Teams is the configured meeting provider.
    "add_outlookmeeting",
]

# Needed for the same "Send Meeting Link" button when Google Meet (via
# horilla_meet) is the configured provider instead of Teams.
HORILLA_MEET_CODENAMES = [
    "add_googlemeeting",
]


class Command(BaseCommand):
    help = (
        "Creates (or updates) a group with every permission needed to use "
        "the Recruitment module (and Onboarding, by default)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--group-name",
            type=str,
            default="HR Manager",
            help="Name of the group to create/update (default: 'HR Manager')",
        )
        parser.add_argument(
            "--no-onboarding",
            action="store_true",
            help=(
                "Exclude Onboarding module permissions. Candidate "
                "creation/update and the onboarding tab on a candidate "
                "profile accept onboarding.* permissions as an alternative "
                "to recruitment.*, so leaving this out keeps those working."
            ),
        )

    def handle(self, *args, **options):
        group_name = options["group_name"]
        include_onboarding = not options["no_onboarding"]

        group, created = Group.objects.get_or_create(name=group_name)
        self.stdout.write(
            self.style.SUCCESS(
                f'{"Created" if created else "Found existing"} group "{group_name}"'
            )
        )

        codenames = set(RECRUITMENT_CUSTOM_CODENAMES)
        for model in RECRUITMENT_MODELS:
            codenames.update(f"{action}_{model}" for action in DEFAULT_ACTIONS)

        app_labels = ["recruitment", "outlook_auth", "horilla_meet"]
        codenames.update(OUTLOOK_AUTH_CODENAMES)
        codenames.update(HORILLA_MEET_CODENAMES)
        if include_onboarding:
            app_labels.append("onboarding")
            for model in ONBOARDING_MODELS:
                codenames.update(f"{action}_{model}" for action in DEFAULT_ACTIONS)

        permissions = Permission.objects.filter(
            content_type__app_label__in=app_labels,
            codename__in=codenames,
        )

        found_codenames = set(permissions.values_list("codename", flat=True))
        missing = sorted(codenames - found_codenames)

        group.permissions.add(*permissions)

        self.stdout.write(
            self.style.SUCCESS(
                f"Assigned {permissions.count()} permissions to \"{group_name}\" "
                f"({' + '.join(app_labels)})."
            )
        )
        if missing:
            self.stdout.write(
                self.style.WARNING(
                    "Not found in DB (skipped, likely stale/renamed model): "
                    + ", ".join(missing)
                )
            )
