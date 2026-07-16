"""
My attendances
"""

import calendar as pycalendar
import datetime
from typing import Any

from django.apps import apps
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _

from attendance.filters import AttendanceFilters
from attendance.models import Attendance, AttendanceActivity, GraceTime
from base.models import CompanyLeaves, EmployeeShiftSchedule, Holidays
from horilla_views.cbv_methods import login_required
from horilla_views.generic.cbv.views import (
    HorillaDetailedView,
    HorillaListView,
    HorillaNavView,
    TemplateView,
)


@method_decorator(login_required, name="dispatch")
class MyAttendances(TemplateView):
    """
    My attendances
    """

    template_name = "cbv/my_attendances/my_attendances.html"


class MyAttendancesListView(HorillaListView):

    model = Attendance
    filter_class = AttendanceFilters
    columns = [
        (_("Employee"), "employee_id", "employee_id__get_avatar"),
        (_("Date"), "attendance_date"),
        (_("Day"), "attendance_day"),
        (_("Check-In"), "attendance_clock_in"),
        (_("In Date"), "attendance_clock_in_date"),
        (_("Check-Out"), "attendance_clock_out"),
        (_("Out Date"), "attendance_clock_out_date"),
        (_("Shift"), "shift_id"),
        (_("Work Type"), "work_type_id"),
        (_("Min Hour"), "minimum_hour"),
        (_("At Work"), "attendance_worked_hour"),
        (_("Pending Hour"), "hours_pending"),
        (_("Overtime"), "attendance_overtime"),
    ]

    row_attrs = """
                hx-get='{my_attendance_detail}?instance_ids={ordered_ids}'
                hx-target="#genericModalBody"
                data-target="#genericModal"
                data-toggle="oh-modal-toggle"
                """
    records_per_page = 20

    sortby_mapping = [
        (_("Employee"), "employee_id__get_full_name", "employee_id__get_avatar"),
        (_("Date"), "attendance_date"),
        (_("Day"), "attendance_day__day"),
        (_("Check-In"), "attendance_clock_in"),
        (_("Shift"), "shift_id__employee_shift"),
        (_("Work Type"), "work_type_id__work_type"),
        (_("Min Hour"), "minimum_hour"),
        (_("Pending Hour"), "hours_pending"),
        (_("In Date"), "attendance_clock_in_date"),
        (_("Check-Out"), "attendance_clock_out"),
        (_("Out Date"), "attendance_clock_out_date"),
        (_("At Work"), "attendance_worked_hour"),
        (_("Overtime"), "attendance_overtime"),
    ]


@method_decorator(login_required, name="dispatch")
class MyAttendanceList(MyAttendancesListView):
    """
    List view
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.search_url = reverse("my-attendance-list")

    row_status_indications = [
        (
            "approved-request--dot",
            _("Approved Request"),
            """
            onclick="
                $('#applyFilter').closest('form').find('[name=is_validate_request_approved]').val('true');
                $('[name=attendance_validated]').val('unknown').change();
                $('[name=is_validate_request]').val('unknown').change();
                $('#applyFilter').click();

            "
            """,
        ),
        (
            "requested--dot",
            _("Requested"),
            """
            onclick="
                $('#applyFilter').closest('form').find('[name=is_validate_request]').val('true');
                $('[name=attendance_validated]').val('unknown').change();
                $('[name=is_validate_request_approved]').val('unknown').change();
                $('#applyFilter').click();

            "
            """,
        ),
        (
            "not-validated--dot",
            _("Not Validated"),
            """
            onclick="
                $('#applyFilter').closest('form').find('[name=attendance_validated]').val('false');
                $('[name=is_validate_request]').val('unknown').change();
                $('[name=is_validate_request_approved]').val('unknown').change();
                $('#applyFilter').click();
            "
            """,
        ),
        (
            "validated--dot",
            _("Validated"),
            """
            onclick="
                $('#applyFilter').closest('form').find('[name=attendance_validated]').val('true');
                $('[name=is_validate_request]').val('unknown').change();
                $('[name=is_validate_request_approved]').val('unknown').change();
                $('#applyFilter').click();

            "
            """,
        ),
    ]

    row_status_class = "validated-{attendance_validated}  requested-{is_validate_request} approved-request-{is_validate_request_approved}"

    def get_queryset(self):
        queryset = super().get_queryset()
        employee = self.request.user.employee_get
        queryset = queryset.filter(employee_id=employee)
        return queryset


@method_decorator(login_required, name="dispatch")
class MyAttendancestNav(HorillaNavView):
    """
    Nav bar
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.search_url = reverse("my-attendance-list")
        self.search_in = [
            ("shift_id__employee_shift", "Shift"),
            ("work_type_id__work_type", "Work Type"),
        ]
        self.view_types = [
            {
                "type": "list",
                "icon": "list-outline",
                "url": reverse("my-attendance-list"),
                "attrs": f"""
                        title ='{_("List")}'
                        """,
            },
            {
                "type": "calendar",
                "icon": "calendar-outline",
                "url": reverse("my-attendance-calendar"),
                "attrs": f"""
                        title ='{_("Calendar")}'
                        """,
            },
            {
                "type": "log",
                "icon": "time-outline",
                "url": reverse("my-attendance-log"),
                "attrs": f"""
                        title ='{_("Log")}'
                        """,
            },
        ]

    nav_title = _("My Attendances")
    filter_body_template = "cbv/my_attendances/my_attendance_filter.html"
    filter_instance = AttendanceFilters()
    filter_form_context_name = "form"
    search_swap_target = "#listContainer"
    search_input_attrs = """ hidden """


@method_decorator(login_required, name="dispatch")
class MyAttendancesDetailView(HorillaDetailedView):
    """
    Detail View
    """

    model = Attendance

    title = _("Details")

    header = {
        "title": "employee_id__get_full_name",
        "subtitle": "my_attendance_subtitle",
        "avatar": "employee_id__get_avatar",
    }

    body = [
        (_("Date"), "attendance_date"),
        (_("Day"), "attendance_day"),
        (_("Check-In"), "attendance_clock_in"),
        (_("Check-in Date"), "attendance_clock_in_date"),
        (_("Check-Out"), "attendance_clock_out"),
        (_("Check-out Date"), "attendance_clock_out_date"),
        (_("Shift"), "shift_id"),
        (_("Work Type"), "work_type_id"),
        (_("Min Hour"), "minimum_hour"),
        (_("At Work"), "attendance_worked_hour"),
        (_("Pending Hour"), "hours_pending"),
        (_("Overtime"), "attendance_overtime"),
    ]


# ---------------------------------------------------------------------------
# Calendar / Log views for My Attendance
# ---------------------------------------------------------------------------


def _fmt_hm(seconds):
    """Format seconds as `9h 46m`."""
    seconds = max(int(seconds or 0), 0)
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m"


def _fmt_hms(seconds):
    """Format seconds as `0:08:49` (used for the late-by duration)."""
    seconds = max(int(seconds or 0), 0)
    return f"{seconds // 3600}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


def _fmt_time(time_obj):
    """Format a time as `8:12` without a leading zero on the hour."""
    if time_obj is None:
        return ""
    return f"{time_obj.hour}:{time_obj.minute:02d}"


def _day_percent(time_obj):
    """Position of a time inside a 24h track, as percent."""
    if time_obj is None:
        return None
    secs = time_obj.hour * 3600 + time_obj.minute * 60 + time_obj.second
    return round(secs / 86400 * 100, 2)


def _is_weekly_off(day, company_leaves):
    """Check a date against the configured company leaves (weekly offs)."""
    for leave in company_leaves:
        if int(leave.based_on_week_day) != day.weekday():
            continue
        if not leave.based_on_week:
            return True
        if int(leave.based_on_week) == (day.day - 1) // 7:
            return True
    return False


def _holiday_for(day, holidays):
    """Return the holiday instance covering the date, if any."""
    for holiday in holidays:
        start = holiday.start_date
        end = holiday.end_date or holiday.start_date
        if holiday.recurring:
            try:
                start = start.replace(year=day.year)
                end = end.replace(year=day.year)
            except ValueError:  # Feb 29 on a non-leap year
                continue
        if start <= day <= end:
            return holiday
    return None


class MyAttendanceDayContext:
    """
    Builds per-day context dictionaries for the calendar and log views of
    the logged-in employee, resolving attendance, activities, shift
    schedule, weekly offs, holidays and approved leaves.
    """

    WEEKDAYS = [
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    ]

    def __init__(self, employee, start, end):
        self.employee = employee
        self.start = start
        self.end = end
        self.today = datetime.date.today()
        self.now = datetime.datetime.now()

        self.attendances = {
            a.attendance_date: a
            for a in Attendance.objects.filter(
                employee_id=employee,
                attendance_date__gte=start,
                attendance_date__lte=end,
            ).select_related("shift_id")
        }
        self.activities = {}
        for activity in AttendanceActivity.objects.filter(
            employee_id=employee,
            attendance_date__gte=start,
            attendance_date__lte=end,
        ).order_by("clock_in_date", "clock_in"):
            self.activities.setdefault(activity.attendance_date, []).append(activity)

        self.company_leaves = list(CompanyLeaves.objects.all())
        self.holidays = list(Holidays.objects.all())

        self.leaves = []
        if apps.is_installed("leave"):
            from leave.models import LeaveRequest

            self.leaves = list(
                LeaveRequest.objects.filter(
                    employee_id=employee,
                    status="approved",
                    start_date__lte=end,
                    end_date__gte=start,
                ).select_related("leave_type_id")
            )

        work_info = getattr(employee, "employee_work_info", None)
        self.shift = getattr(work_info, "shift_id", None)
        self.location = getattr(work_info, "location", "") or ""
        self.schedules = {}
        if self.shift:
            for schedule in EmployeeShiftSchedule.objects.filter(
                shift_id=self.shift
            ).select_related("day"):
                self.schedules[schedule.day.day] = schedule
        if self.shift and getattr(self.shift, "grace_time_id", None):
            self.grace = self.shift.grace_time_id
        else:
            self.grace = GraceTime.objects.filter(allowed_clock_in=True).first()

    def _leave_for(self, day):
        for leave in self.leaves:
            if leave.start_date <= day <= (leave.end_date or leave.start_date):
                return leave
        return None

    def _swipe_pairs(self, day, attendance):
        """
        Swipe pairs for the day: real activities when available, otherwise a
        single pair synthesised from the attendance first-in/last-out.
        """
        pairs = []
        activities = self.activities.get(day)
        if activities:
            for activity in activities:
                pairs.append({"in": activity.clock_in, "out": activity.clock_out})
        elif attendance and attendance.attendance_clock_in:
            pairs.append(
                {
                    "in": attendance.attendance_clock_in,
                    "out": attendance.attendance_clock_out,
                }
            )
        return pairs

    def build_day(self, day):
        """Return the fully-resolved context dict for one date."""
        attendance = self.attendances.get(day)
        holiday = _holiday_for(day, self.holidays)
        weekly_off = _is_weekly_off(day, self.company_leaves)
        leave = self._leave_for(day)
        schedule = self.schedules.get(self.WEEKDAYS[day.weekday()])
        is_today = day == self.today
        is_future = day > self.today

        ctx = {
            "date": day,
            "is_today": is_today,
            "attendance": attendance,
            "holiday": holiday,
            "weekly_off": weekly_off,
            "leave": leave,
            "schedule": schedule,
            "shift_name": str(self.shift) if self.shift else "",
            "location": self.location,
            "swipes": [],
            "segments": [],
            "effective": "",
            "break_taken": "",
            "gross": "",
            "arrival": None,
            "late_by": "",
            "missing_swipe": False,
            "open_swipe": False,
            "effective_secs": 0,
        }

        if attendance:
            ctx["kind"] = "present"
        elif holiday:
            ctx["kind"] = "holiday"
        elif weekly_off:
            ctx["kind"] = "weekoff"
        elif leave:
            ctx["kind"] = "leave"
        elif is_future:
            ctx["kind"] = "future"
        elif is_today:
            ctx["kind"] = "today"
        else:
            ctx["kind"] = "absent"

        if not attendance:
            return ctx

        pairs = self._swipe_pairs(day, attendance)
        ctx["swipes"] = [
            {
                "in": _fmt_time(pair["in"]),
                "in_full": pair["in"].strftime("%H:%M:%S") if pair["in"] else "",
                "out": _fmt_time(pair["out"]),
                "out_full": pair["out"].strftime("%H:%M:%S") if pair["out"] else "",
            }
            for pair in pairs
        ]

        effective_secs = 0
        used_live_now = False
        has_real_activities = bool(self.activities.get(day))
        if has_real_activities:
            for pair in pairs:
                if pair["in"] is None:
                    continue
                if pair["out"] is None:
                    if is_today:
                        start_dt = datetime.datetime.combine(day, pair["in"])
                        effective_secs += max(
                            (self.now - start_dt).total_seconds(), 0
                        )
                        used_live_now = True
                        ctx["open_swipe"] = True
                    else:
                        ctx["missing_swipe"] = True
                    continue
                start_dt = datetime.datetime.combine(day, pair["in"])
                end_dt = datetime.datetime.combine(day, pair["out"])
                if end_dt < start_dt:
                    end_dt += datetime.timedelta(days=1)
                effective_secs += (end_dt - start_dt).total_seconds()
        else:
            effective_secs = attendance.at_work_second or 0
            if pairs and pairs[0]["out"] is None:
                if is_today:
                    ctx["open_swipe"] = True
                else:
                    ctx["missing_swipe"] = True

        first_in = pairs[0]["in"] if pairs else None
        last_out = pairs[-1]["out"] if pairs else None
        gross_secs = 0
        if first_in:
            start_dt = datetime.datetime.combine(day, first_in)
            if last_out:
                end_dt = datetime.datetime.combine(day, last_out)
                if end_dt < start_dt:
                    end_dt += datetime.timedelta(days=1)
            elif is_today:
                end_dt = self.now
                used_live_now = True
            else:
                end_dt = start_dt
            gross_secs = max((end_dt - start_dt).total_seconds(), 0)

        if not has_real_activities and not gross_secs:
            gross_secs = effective_secs

        break_secs = max(gross_secs - effective_secs, 0)
        suffix = " +" if used_live_now or ctx["open_swipe"] else ""
        ctx["effective"] = _fmt_hm(effective_secs) + suffix
        ctx["break_taken"] = _fmt_hm(break_secs)
        ctx["gross"] = _fmt_hm(gross_secs) + suffix
        ctx["effective_secs"] = int(effective_secs)

        # timeline segments over a 24h track
        for pair in pairs:
            if pair["in"] is None:
                continue
            left = _day_percent(pair["in"])
            out_time = pair["out"]
            if out_time is None and is_today:
                out_time = self.now.time()
            right = _day_percent(out_time) if out_time else left
            if right is None or right < left:
                right = 100.0
            tooltip = f"Logged In {_fmt_time(pair['in'])}"
            if out_time:
                tooltip += f" - {_fmt_time(out_time)}"
            ctx["segments"].append(
                {
                    "left": left,
                    "width": max(round(right - left, 2), 0.6),
                    "tooltip": tooltip,
                }
            )

        # arrival status against shift schedule (+ grace time)
        if schedule and schedule.start_time and first_in:
            start_secs = (
                schedule.start_time.hour * 3600 + schedule.start_time.minute * 60
            )
            in_secs = first_in.hour * 3600 + first_in.minute * 60 + first_in.second
            grace_secs = (
                self.grace.allowed_time_in_secs
                if self.grace and self.grace.allowed_clock_in
                else 0
            )
            if in_secs <= start_secs + grace_secs:
                ctx["arrival"] = "ontime"
            else:
                ctx["arrival"] = "late"
                ctx["late_by"] = _fmt_hms(in_secs - start_secs)
        return ctx


@method_decorator(login_required, name="dispatch")
class MyAttendanceCalendar(TemplateView):
    """
    Calendar view fragment for My Attendance.
    """

    template_name = "cbv/my_attendances/calendar_view.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        employee = self.request.user.employee_get
        today = datetime.date.today()

        month_param = self.request.GET.get("month", "")
        try:
            year, month = (int(part) for part in month_param.split("-"))
            datetime.date(year, month, 1)
        except (ValueError, AttributeError):
            year, month = today.year, today.month

        first = datetime.date(year, month, 1)
        last = datetime.date(year, month, pycalendar.monthrange(year, month)[1])
        builder = MyAttendanceDayContext(employee, first, last)

        days = [
            builder.build_day(first + datetime.timedelta(days=offset))
            for offset in range((last - first).days + 1)
        ]

        weeks = []
        week = [None] * first.weekday()
        for day_ctx in days:
            week.append(day_ctx)
            if len(week) == 7:
                weeks.append(week)
                week = []
        if week:
            week += [None] * (7 - len(week))
            weeks.append(week)

        prev_month = (first - datetime.timedelta(days=1)).replace(day=1)
        next_month = (last + datetime.timedelta(days=1)).replace(day=1)

        present_days = sum(1 for d in days if d["kind"] == "present")
        total_secs = sum(d.get("effective_secs", 0) for d in days)
        context.update(
            {
                "weeks": weeks,
                "month_label": first.strftime("%B %Y"),
                "prev_month": prev_month.strftime("%Y-%m"),
                "next_month": next_month.strftime("%Y-%m"),
                "summary": {
                    "present": present_days,
                    "absent": sum(1 for d in days if d["kind"] == "absent"),
                    "leave": sum(1 for d in days if d["kind"] == "leave"),
                    "weekoff": sum(1 for d in days if d["kind"] == "weekoff"),
                    "holiday": sum(1 for d in days if d["kind"] == "holiday"),
                    "worked": _fmt_hm(total_secs),
                },
            }
        )
        return context


@method_decorator(login_required, name="dispatch")
class MyAttendanceLog(TemplateView):
    """
    Keka-style attendance log fragment for My Attendance.
    """

    template_name = "cbv/my_attendances/log_view.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        employee = self.request.user.employee_get
        today = datetime.date.today()

        month_param = self.request.GET.get("log_month", "")
        active_tab = "30days"
        start = today - datetime.timedelta(days=29)
        end = today
        if month_param:
            try:
                year, month = (int(part) for part in month_param.split("-"))
                start = datetime.date(year, month, 1)
                end = min(
                    datetime.date(
                        year, month, pycalendar.monthrange(year, month)[1]
                    ),
                    today,
                )
                active_tab = month_param
            except ValueError:
                pass

        builder = MyAttendanceDayContext(employee, start, end)
        days = [
            builder.build_day(end - datetime.timedelta(days=offset))
            for offset in range((end - start).days + 1)
        ]

        month_tabs = []
        cursor = today.replace(day=1)
        for _index in range(6):
            cursor = (cursor - datetime.timedelta(days=1)).replace(day=1)
            month_tabs.append(
                {
                    "param": cursor.strftime("%Y-%m"),
                    "label": cursor.strftime("%b").upper(),
                }
            )

        context.update(
            {
                "days": days,
                "month_tabs": month_tabs,
                "active_tab": active_tab,
            }
        )
        return context
