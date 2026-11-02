"""
horilla_views/kanban_access.py

Rules for the generic kanban endpoints (drag a card to another column, reorder
cards or columns, count cards in a column).

Those endpoints used to accept *any* model name and *any* field name from the
browser, so any logged-in employee could rewrite any record of any table. They
now work only for the boards that really use them, only through the fields
those boards move cards by, and only for people who may move that kind of card.

To let a new board use the drag-and-drop endpoints, add an entry to ``BOARDS``.
"""

from django.apps import apps
from django.db import models

from horilla_views.cbv_methods import get_nested_field


def _employee(user):
    from employee.models import Employee

    return Employee.objects.filter(employee_user_id=user).first()


class Board:
    """
    model_path      "app_label.ModelName" exactly as the board's template sends it
    group_keys      field(s) a card is grouped (moved between columns) by
    card_perm       permission that lets a user move any card of this board
    group_perm      permission that lets a user reorder the columns (None = not allowed)
    """

    model_path = ""
    group_keys = frozenset()
    card_perm = ""
    group_perm = None

    def can_open(self, user):
        """Whether the user may see this board's card counts at all."""
        raise NotImplementedError

    def can_move(self, user, obj):
        """Whether the user may move this particular card."""
        raise NotImplementedError

    def can_arrange(self, user):
        """Whether the user may reorder the columns of this board."""
        if not self.group_perm:
            return False
        manager = getattr(self, "_manager", None)
        return user.has_perm(self.group_perm) or bool(manager and manager(user))

    def column_ok(self, obj, column):
        """Whether ``column`` (a related object or a choice value) is a valid target."""
        return True


class RecruitmentCandidateBoard(Board):
    model_path = "recruitment.Candidate"
    group_keys = frozenset({"stage_id"})
    card_perm = "recruitment.change_candidate"
    group_perm = "recruitment.change_stage"

    def _manager(self, user, obj=None):
        from recruitment.models import Recruitment, Stage

        employee = _employee(user)
        if employee is None:
            return False
        if obj is None:
            return (
                Stage.objects.filter(stage_managers=employee).exists()
                or Recruitment.objects.filter(recruitment_managers=employee).exists()
            )
        recruitment = obj.recruitment_id
        return bool(
            (obj.stage_id_id and obj.stage_id.stage_managers.filter(pk=employee.pk).exists())
            or (recruitment and recruitment.recruitment_managers.filter(pk=employee.pk).exists())
        )

    def can_open(self, user):
        return user.has_perm("recruitment.view_recruitment") or self._manager(user)

    def can_move(self, user, obj):
        return user.has_perm(self.card_perm) or self._manager(user, obj)

    def column_ok(self, obj, column):
        # a candidate can only move between the stages of its own recruitment
        return getattr(column, "recruitment_id_id", None) == obj.recruitment_id_id


class OnboardingCandidateBoard(Board):
    model_path = "onboarding.OnboardingCandidate"
    group_keys = frozenset({"onboarding_stage__onboarding_stage_id"})
    card_perm = "onboarding.change_candidatestage"
    group_perm = "onboarding.change_onboardingstage"

    def _manager(self, user, obj=None):
        from onboarding.models import OnboardingStage
        from recruitment.models import Recruitment

        employee = _employee(user)
        if employee is None:
            return False
        if obj is None:
            return (
                OnboardingStage.objects.filter(employee_id=employee).exists()
                or Recruitment.objects.filter(recruitment_managers=employee).exists()
            )
        current = getattr(obj, "onboarding_stage", None)
        stage = getattr(current, "onboarding_stage_id", None)
        recruitment = obj.recruitment_id
        return bool(
            (stage and stage.employee_id.filter(pk=employee.pk).exists())
            or (recruitment and recruitment.recruitment_managers.filter(pk=employee.pk).exists())
        )

    def can_open(self, user):
        return user.has_perm("onboarding.view_candidatestage") or self._manager(user)

    def can_move(self, user, obj):
        return user.has_perm(self.card_perm) or self._manager(user, obj)

    def column_ok(self, obj, column):
        return getattr(column, "recruitment_id_id", None) == obj.recruitment_id_id


class ChecklistTaskBoard(Board):
    model_path = "onboarding.CandidateChecklistTask"
    group_keys = frozenset({"status"})
    card_perm = "onboarding.change_candidatechecklisttask"

    def can_open(self, user):
        return user.has_perm("onboarding.view_candidatechecklisttask") or _employee(user) is not None

    def can_move(self, user, obj):
        if user.has_perm(self.card_perm):
            return True
        employee = _employee(user)
        return employee is not None and obj.assignee_id_id == employee.pk


class TicketBoard(Board):
    model_path = "helpdesk.Ticket"
    group_keys = frozenset({"status"})
    card_perm = "helpdesk.change_ticket"

    def can_open(self, user):
        return _employee(user) is not None

    def can_move(self, user, obj):
        # the same people change_ticket_status lets in
        if user.has_perm(self.card_perm):
            return True
        employee = _employee(user)
        return employee is not None and (
            obj.employee_id_id == employee.pk
            or obj.assigned_to.filter(pk=employee.pk).exists()
        )


class OffboardingBoard(Board):
    model_path = "offboarding.OffboardingEmployee"
    group_keys = frozenset({"stage_id"})
    card_perm = "offboarding.change_offboarding"
    group_perm = "offboarding.change_offboardingstage"

    def _manager(self, user, obj=None):
        from offboarding.models import Offboarding, OffboardingStage

        employee = _employee(user)
        if employee is None:
            return False
        if obj is None:
            return (
                Offboarding.objects.filter(managers=employee).exists()
                or OffboardingStage.objects.filter(managers=employee).exists()
            )
        stage = obj.stage_id
        return bool(
            stage
            and (
                stage.managers.filter(pk=employee.pk).exists()
                or stage.offboarding_id.managers.filter(pk=employee.pk).exists()
            )
        )

    def can_open(self, user):
        return user.has_perm("offboarding.view_offboarding") or self._manager(user)

    def can_move(self, user, obj):
        return user.has_perm(self.card_perm) or self._manager(user, obj)

    def column_ok(self, obj, column):
        return (
            getattr(column, "offboarding_id_id", None) == obj.stage_id.offboarding_id_id
            if obj.stage_id_id
            else True
        )


BOARDS = {
    board.model_path: board
    for board in (
        RecruitmentCandidateBoard(),
        OnboardingCandidateBoard(),
        ChecklistTaskBoard(),
        TicketBoard(),
        OffboardingBoard(),
    )
}


def board_for(model_path):
    """The board registered for ``model_path`` or None (also for junk input)."""
    return BOARDS.get(model_path or "")


def board_model(board):
    app_label, model_name = board.model_path.split(".")
    return apps.get_model(app_label, model_name)


def usable_order_field(model, order_by):
    """
    Only a column literally called ``sequence`` may be written as the card order.
    (Boards also order by ``id`` or by expressions; those must never be written.)
    """
    if not order_by or order_by.split("__")[-1] != "sequence":
        return False
    field = get_nested_field(model, order_by)
    return isinstance(field, models.IntegerField)


def choice_ok(model, group_key, value):
    """For a column made of choices: the value must be one of the field's choices."""
    field = model._meta.get_field(group_key)
    return str(value) in {str(v) for v, _ in (field.choices or [])}
