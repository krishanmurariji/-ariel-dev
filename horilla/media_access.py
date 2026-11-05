"""
horilla/media_access.py

Who may download an uploaded file from /media/?

``protected_media`` used to ask only "is somebody signed in?", so any employee holding the URL of a
payslip-sized secret - a colleague's document, a leave attachment, a candidate's resume - could open
it. File names carry a random suffix, so the URLs cannot be guessed, but a URL that leaks (a
notification, a forwarded mail, a browser history) was enough.

Uploads are stored as ``<app>/<model>/<field>/<name>``, which says which record owns the file. The
rules below cover the files that hold personal data; every other folder (company icons, profile
pictures, announcement and project files, ...) keeps the old rule of "any signed-in user".
"""

from django.apps import apps


def _employees_of(instance, field):
    owners = getattr(instance, field, None)
    if owners is None:
        return []
    if hasattr(owners, "all"):
        return list(owners.all())
    return [owners]


def _employee_owned(owner_field, perm, request_type=None):
    """
    The file belongs to employee(s): them, their managers, or a holder of ``perm``. With a
    ``request_type`` the employee's second managers and manager-of-manager, who may be given
    access to that kind of request, count as managers too.
    """

    def check(user, instance):
        from horilla.access import can_access_employee, can_manager_view, is_manager_in_chain

        for owner in _employees_of(instance, owner_field):
            if can_access_employee(user, owner, perm) or is_manager_in_chain(user, owner):
                return True
            if request_type and can_manager_view(user, owner, request_type):
                return True
        return False

    return check


def manages_candidate(user, candidate, perm="recruitment.view_candidate"):
    """
    Staff who may deal with ``candidate``: holders of ``perm`` and the managers and heads of the
    candidate's recruitment, and the managers of the candidate's stage.
    """
    from horilla.access import current_employee

    if user.has_perm(perm):
        return True
    me = current_employee(user)
    if candidate is None or me is None:
        return False
    recruitment = candidate.recruitment_id
    if recruitment is not None and (
        recruitment.recruitment_managers.filter(pk=me.pk).exists()
        or recruitment.recruitment_heads.filter(pk=me.pk).exists()
    ):
        return True
    stage = candidate.stage_id
    return stage is not None and stage.stage_managers.filter(pk=me.pk).exists()


def _candidate_file(candidate_of):
    """A candidate's file: holders of view_candidate and the managers of that recruitment."""

    def check(user, instance):
        return manages_candidate(user, candidate_of(instance))

    return check


RULES = {
    ("horilla_documents", "document", "document"): _employee_owned("employee_id", "horilla_documents.view_document"),
    ("payroll", "contract", "contract_document"): _employee_owned("employee_id", "payroll.view_contract"),
    ("payroll", "reimbursement", "attachment"): _employee_owned("employee_id", "payroll.view_reimbursement"),
    ("leave", "leaverequest", "attachment"): _employee_owned("employee_id", "leave.view_leaverequest"),
    ("leave", "leaveallocationrequest", "attachment"): _employee_owned(
        "employee_id", "leave.view_leaveallocationrequest"
    ),
    ("employee", "disciplinaryaction", "attachment"): _employee_owned(
        "employee_id", "employee.view_disciplinaryaction"
    ),
    ("offboarding", "resignationletter", "attachment"): _employee_owned(
        "employee_id", "offboarding.view_resignationletter", request_type="resignation"
    ),
    ("recruitment", "candidate", "resume"): _candidate_file(lambda candidate: candidate),
    ("recruitment", "candidatedocument", "document"): _candidate_file(lambda document: document.candidate_id),
    (
        "onboarding",
        "onboardingcandidatedetail",
        "offer_letter",
    ): _candidate_file(lambda detail: detail.candidate_id),
    # Vertex project documents open only for people who can see that project (and document)
    ("vertex", "projectdocument", "file"): lambda user, document: __import__(
        "vertex.access", fromlist=["can_open_document"]
    ).can_open_document(user, document),
}


def may_open(user, path):
    """
    True when ``user`` may open the media file ``path``. Files in a folder with no rule are
    allowed (the caller has already required a signed-in user); a file that belongs to a ruled
    field needs the record that owns it to be found and the user to be allowed to see it.

    The folder is ``<app>/<model>/<field>/`` when the upload could tell which field it came from,
    and just ``<app>/<model>/`` when it could not - so a three-part path is matched against every
    ruled field of that model.
    """
    name = path.strip("/")
    parts = name.split("/")
    if len(parts) < 3:
        return True
    app_label, model_name = parts[:2]
    ruled = {
        field: rule for (app, model, field), rule in RULES.items() if (app, model) == (app_label, model_name)
    }
    if not ruled:
        return True
    explicit_field = len(parts) >= 4
    if explicit_field and parts[2] not in ruled:
        return True  # another field of that model, such as a profile picture
    try:
        model = apps.get_model(app_label, model_name)
    except LookupError:
        return False
    for field, rule in ruled.items():
        if explicit_field and parts[2] != field:
            continue
        instance = model._base_manager.filter(**{field: name}).first()
        if instance is None:
            continue
        if user is None:
            return False
        return bool(user.is_superuser or rule(user, instance))
    # nothing owns it: an orphaned file of a ruled field is not handed out, but a file of
    # some other field of the same model (found only by elimination) keeps the old rule
    return not explicit_field
