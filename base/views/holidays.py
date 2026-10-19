"""
base/views/holidays.py

Holidays and company leaves: creating, listing, filtering, importing and exporting holidays, and the company-leave settings.
"""

from horilla.post_only import post_only
import csv
import json
import os
import uuid
from urllib.parse import parse_qs, unquote

import pandas as pd
from django.conf import settings
from django.contrib import messages
from django.core.files.base import ContentFile
from django.db.models import ProtectedError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from base.filters import CompanyLeaveFilter, HolidayFilter
from base.forms import CompanyLeaveForm, HolidayForm, HolidaysColumnExportForm
from base.methods import (
    export_data,
    format_date,
    generate_colors,
    get_key_instances,
    paginator_qry,
    sortby,
)
from base.models import WEEK_DAYS, WEEKS, CompanyLeaves, Holidays
from horilla.decorators import hx_request_required, login_required, permission_required
from horilla.http.response import HorillaRedirect
from horilla.methods import remove_dynamic_url


def generate_error_report(error_list, error_data, file_name):
    for item in error_list:
        for key, value in error_data.items():
            if key in item:
                value.append(item[key])
            else:
                value.append(None)

    keys_to_remove = [
        key for key, value in error_data.items() if all(v is None for v in value)
    ]
    for key in keys_to_remove:
        del error_data[key]

    data_frame = pd.DataFrame(error_data, columns=error_data.keys())
    styled_data_frame = data_frame.style.map(
        lambda x: "text-align: center", subset=pd.IndexSlice[:, :]
    )

    response = HttpResponse(content_type="application/ms-excel")
    response["Content-Disposition"] = f'attachment; filename="{file_name}"'
    writer = pd.ExcelWriter(response, engine="xlsxwriter")
    styled_data_frame.to_excel(writer, index=False, sheet_name="Sheet1")

    worksheet = writer.sheets["Sheet1"]
    worksheet.set_column("A:Z", 30)
    writer.close()

    def get_error_sheet(request):
        remove_dynamic_url(path_info)
        return response

    from base.urls import path, urlpatterns

    # Create a unique path for the error file download
    path_info = f"error-sheet-{uuid.uuid4()}"
    urlpatterns.append(path(path_info, get_error_sheet, name=path_info))
    settings.DYNAMIC_URL_PATTERNS.append(path_info)
    for key in error_data:
        error_data[key] = []
    return path_info


@login_required
@hx_request_required
def get_upcoming_holidays(request):
    """
    Retrieve and display a list of upcoming holidays for the current month and year.
    """
    today = timezone.localdate()
    current_year = today.year
    holidays = Holidays.objects.filter(
        start_date__year=current_year, start_date__gte=today
    )
    colors = generate_colors(len(holidays))
    for i, holiday in enumerate(holidays):
        holiday.background_color = colors[i]
    return render(request, "holiday/upcoming_holidays.html", {"holidays": holidays})


@login_required
@hx_request_required
@permission_required("base.add_holidays")
def holiday_creation(request):
    """
    function used to create holidays.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET : return holiday creation form template
    POST : return holiday view template
    """

    previous_data = request.GET.urlencode()
    form = HolidayForm()
    if request.method == "POST":
        form = HolidayForm(request.POST)
        if form.is_valid():
            form.save()
            form = HolidayForm()
            messages.success(request, _("New holiday created successfully.."))
            if Holidays.objects.filter().count() == 1:
                return HorillaRedirect(request)
    return render(
        request, "holiday/holiday_form.html", {"form": form, "pd": previous_data}
    )


@login_required
def holidays_excel_template(request):
    try:
        columns = [
            "Holiday Name",
            "Start Date",
            "End Date",
            "Recurring",
        ]
        data_frame = pd.DataFrame(columns=columns)
        response = HttpResponse(content_type="application/ms-excel")
        response["Content-Disposition"] = (
            'attachment; filename="assign_leave_type_excel.xlsx"'
        )
        data_frame.to_excel(response, index=False)
        return response
    except Exception as exception:
        return HttpResponse(exception)


def csv_holiday_import(file):
    """
    Imports holiday data from a CSV file.

    This function reads a CSV file containing holiday information, validates the data,
    and saves valid holiday records to the database using bulk creation for efficiency.

    The expected format for the CSV file is:
    - "Holiday Name": Name of the holiday (string)
    - "Start Date": Start date of the holiday (date string in a recognized format)
    - "End Date": End date of the holiday (date string in a recognized format)
    - "Recurring": Indicates whether the holiday recurs ("yes" or "no")
    """
    holiday_list, error_list = [], []
    file_name = settings.FILE_STORAGE.save(
        "holiday_import.csv", ContentFile(file.read())
    )
    holiday_file = settings.FILE_STORAGE.path(file_name)

    with open(holiday_file, errors="ignore") as csv_file:
        save = True
        reader = csv.reader(csv_file)
        next(reader)

        for total_rows, row in enumerate(reader, start=1):
            try:
                name, start_date, end_date, recurring = row
                holiday_dict = {
                    "Holiday Name": name,
                    "Start Date": start_date,
                    "End Date": end_date,
                    "Recurring": recurring,
                }

                try:
                    start_date = format_date(start_date)
                except:
                    save = False
                    holiday_dict["Start Date Error"] = _("Invalid start date format.")
                    error_list.append(holiday_dict)

                try:
                    end_date = format_date(end_date)
                except:
                    save = False
                    holiday_dict["End Date Error"] = _("Invalid end date format.")
                    error_list.append(holiday_dict)

                if recurring.lower() not in ["yes", "no"]:
                    save = False
                    holiday_dict["Recurring Field Error"] = _(
                        "Recurring must be yes or no."
                    )
                    error_list.append(holiday_dict)

                if save:
                    holiday_list.append(
                        Holidays(
                            name=name,
                            start_date=start_date,
                            end_date=end_date,
                            recurring=recurring.lower() == "yes",
                        )
                    )

            except Exception as e:
                holiday_dict["Other Errors"] = str(e)
                error_list.append(holiday_dict)

    if holiday_list:
        Holidays.objects.bulk_create(holiday_list)

    if os.path.exists(holiday_file):
        os.remove(holiday_file)

    return (error_list, total_rows)


def excel_holiday_import(file):
    """
    Imports holiday data from an Excel file.

    This function reads an Excel file containing holiday information, validates the data,
    and saves valid holiday records to the database using bulk creation for efficiency

    The expected format for the Excel file is:
    - "Holiday Name": Name of the holiday (string)
    - "Start Date": Start date of the holiday (date string in a recognized format)
    - "End Date": End date of the holiday (date string in a recognized format)
    - "Recurring": Indicates whether the holiday recurs ("yes" or "no")

    """
    error_list = []
    valid_holidays = []
    data_frame = pd.read_excel(file)
    holiday_dicts = data_frame.to_dict("records")

    for holiday in holiday_dicts:
        save = True
        try:
            name = holiday["Holiday Name"]

            try:
                start_date = pd.to_datetime(holiday["Start Date"]).date()
            except Exception:
                save = False
                holiday["Start Date Error"] = _("Invalid start date format {}").format(
                    holiday["Start Date"]
                )

            try:
                end_date = pd.to_datetime(holiday["End Date"]).date()
            except Exception:
                save = False
                holiday["End Date Error"] = _("Invalid end date format {}").format(
                    holiday["End Date"]
                )

            recurring_str = holiday.get("Recurring", "").lower()
            if recurring_str in ["yes", "no"]:
                recurring = recurring_str == "yes"
            else:
                save = False
                holiday["Recurring Field Error"] = _(
                    "Recurring must be {} or {}"
                ).format("yes", "no")

            if save:
                holiday_instance = Holidays(
                    name=name,
                    start_date=start_date,
                    end_date=end_date,
                    recurring=recurring,
                )
                valid_holidays.append(holiday_instance)
            else:
                error_list.append(holiday)

        except Exception as e:
            holiday["Other errors"] = str(e)
            error_list.append(holiday)

    if valid_holidays:
        Holidays.objects.bulk_create(valid_holidays)

    return error_list, len(holiday_dicts)


@login_required
@hx_request_required
@permission_required("base.add_holiday")
def holidays_info_import(request):
    result = None
    file_name = "HolidaysImportError.xlsx"
    path_info = None
    error_data = {
        "Holiday Name": [],
        "Start Date": [],
        "End Date": [],
        "Recurring": [],
        "Start Date Error": [],
        "End Date Error": [],
        "Recurring Field Error": [],
        "Other Errors": [],
    }

    # is_hx_request = request.headers.get('HX-Request') == 'true'

    if request.method == "POST":
        file = request.FILES.get("holidays_import")
        if file:
            content_type = file.content_type
            if content_type == "text/csv":
                error_list, total_count = csv_holiday_import(file)
                if error_list:
                    path_info = generate_error_report(error_list, error_data, file_name)
            elif (
                content_type
                == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ):
                error_list, total_count = excel_holiday_import(file)
                if error_list:
                    path_info = generate_error_report(error_list, error_data, file_name)
            else:
                messages.error(
                    request, _("The file you attempted to import is unsupported")
                )
                return HorillaRedirect(request)

            created_holidays_count = total_count - len(error_list)
            context = {
                "created_count": created_holidays_count,
                "error_count": len(error_list),
                "model": _("Holidays"),
                "path_info": path_info,
            }
            result = render_to_string("import_popup.html", context)

    return HttpResponse(result)


@login_required
def holiday_info_export(request):
    if request.META.get("HTTP_HX_REQUEST"):
        export_filter = HolidayFilter()
        export_column = HolidaysColumnExportForm()
        content = {
            "export_filter": export_filter,
            "export_column": export_column,
        }
        return render(
            request, "holiday/holiday_export_filter_form.html", context=content
        )
    return export_data(
        request=request,
        model=Holidays,
        filter_class=HolidayFilter,
        form_class=HolidaysColumnExportForm,
        file_name="Holidays_export",
    )


@login_required
@permission_required("base.view_holidays")
def holidays_settings_view(request):
    """
    Holidays ("Public Holidays") settings page. Migrated from the Configuration
    menu into Settings > Organization; reuses the existing nav/list HTMX
    endpoints.
    """
    return render(request, "base/settings/holidays.html")


@login_required
def holiday_view(request):
    """
    function used to view holidays.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET : return holiday view  template
    """
    queryset = Holidays.objects.all()[::-1]
    previous_data = request.GET.urlencode()
    page_number = request.GET.get("page")
    page_obj = paginator_qry(queryset, page_number)
    holiday_filter = HolidayFilter()

    return render(
        request,
        "holiday/holiday_view.html",
        {
            "holidays": page_obj,
            "form": holiday_filter.form,
            "pd": previous_data,
        },
    )


@login_required
@hx_request_required
def holiday_filter(request):
    """
    function used to filter holidays.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET : return holiday view template
    """
    queryset = Holidays.objects.all()
    previous_data = request.GET.urlencode()
    holiday_filter = HolidayFilter(request.GET, queryset).qs
    if request.GET.get("sortby"):
        holiday_filter = sortby(request, holiday_filter, "sortby")
    page_number = request.GET.get("page")
    page_obj = paginator_qry(holiday_filter[::-1], page_number)
    data_dict = parse_qs(previous_data)
    get_key_instances(Holidays, data_dict)
    return render(
        request,
        "holiday/holiday.html",
        {"holidays": page_obj, "pd": previous_data, "filter_dict": data_dict},
    )


@login_required
@hx_request_required
@permission_required("base.change_holidays")
def holiday_update(request, obj_id):
    """
    function used to update holiday.

    Parameters:
    request (HttpRequest): The HTTP request object.
    id : holiday id

    Returns:
    GET : return holiday update form template
    POST : return holiday view template
    """
    query_string = request.GET.urlencode()
    if query_string.startswith("pd="):
        previous_data = unquote(query_string[len("pd=") :])
    else:
        previous_data = unquote(query_string)
    holiday = Holidays.objects.get(id=obj_id)
    form = HolidayForm(instance=holiday)
    if request.method == "POST":
        form = HolidayForm(request.POST, instance=holiday)
        if form.is_valid():
            form.save()
            messages.success(request, _("Holidays updated successfully.."))
    return render(
        request,
        "holiday/holiday_update_form.html",
        {"form": form, "id": obj_id, "pd": previous_data},
    )


@post_only
@login_required
@hx_request_required
@permission_required("base.delete_holidays")
def holiday_delete(request, obj_id):
    """
    function used to delete holiday.

    Parameters:
    request (HttpRequest): The HTTP request object.
    id : holiday id

    Returns:
    GET : return holiday view template
    """
    query_string = request.GET.urlencode()
    try:
        Holidays.objects.get(id=obj_id).delete()
        messages.success(request, _("Holidays deleted successfully.."))
    except Holidays.DoesNotExist:
        messages.error(request, _("Holidays not found."))
    except ProtectedError:
        messages.error(request, _("Related entries exists"))
    if not Holidays.objects.filter():
        return HorillaRedirect(request)
    return redirect(f"/holiday-filter?{query_string}")


@login_required
@require_http_methods(["POST"])
@permission_required("base.delete_holiday")
def bulk_holiday_delete(request):
    """
    Deletes multiple holidays based on IDs passed in the POST request.
    """
    ids = request.POST.getlist("ids")
    deleted_count = Holidays.objects.filter(id__in=ids).delete()[0]
    messages.success(
        request, _("{} Holidays have been successfully deleted.".format(deleted_count))
    )
    return redirect("holiday-filter")


@login_required
def holiday_select(request):
    page_number = request.GET.get("page")
    holidays = Holidays.objects.none()

    if page_number == "all":
        holidays = Holidays.objects.all()

    holiday_ids = [str(hol.id) for hol in holidays]
    total_count = holidays.count()

    context = {"employee_ids": holiday_ids, "total_count": total_count}

    return JsonResponse(context, safe=False)


@login_required
def holiday_select_filter(request):
    page_number = request.GET.get("page")
    filtered = request.GET.get("filter")
    filters = json.loads(filtered) if filtered else {}
    context = {}

    if page_number == "all":
        employee_filter = HolidayFilter(filters, queryset=Holidays.objects.all())

        # Get the filtered queryset
        filtered_employees = employee_filter.qs

        employee_ids = [str(emp.id) for emp in filtered_employees]
        total_count = filtered_employees.count()

        context = {"employee_ids": employee_ids, "total_count": total_count}

    return JsonResponse(context)


@login_required
@hx_request_required
@permission_required("base.add_companyleaves")
def company_leave_creation(request):
    """
    function used to create company leave.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET : return company leave creation form template
    POST : return company leave view template
    """
    form = CompanyLeaveForm()
    if request.method == "POST":
        form = CompanyLeaveForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, _("New company leave created successfully.."))
            if CompanyLeaves.objects.filter().count() == 1:
                return HorillaRedirect(request)
    return render(
        request, "company_leave/company_leave_creation_form.html", {"form": form}
    )


@login_required
@permission_required("base.view_companyleaves")
def company_leaves_settings_view(request):
    """
    Company Leaves ("Weekly Off Days") settings page. Migrated from the
    Configuration menu into Settings > Organization; reuses the existing
    nav/list HTMX endpoints.
    """
    return render(request, "base/settings/company_leaves.html")


@login_required
def company_leave_view(request):
    """
    function used to view company leave.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET : return company leave view template
    """
    queryset = CompanyLeaves.objects.all()
    previous_data = request.GET.urlencode()
    page_number = request.GET.get("page")
    page_obj = paginator_qry(queryset, page_number)
    company_leave_filter = CompanyLeaveFilter()
    return render(
        request,
        "company_leave/company_leave_view.html",
        {
            "company_leaves": page_obj,
            "weeks": WEEKS,
            "week_days": WEEK_DAYS,
            "form": company_leave_filter.form,
            "pd": previous_data,
        },
    )


@login_required
@hx_request_required
def company_leave_filter(request):
    """
    function used to filter company leave.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET : return company leave view template
    """
    queryset = CompanyLeaves.objects.all()
    previous_data = request.GET.urlencode()
    page_number = request.GET.get("page")
    company_leave_filter = CompanyLeaveFilter(request.GET, queryset).qs
    page_obj = paginator_qry(company_leave_filter, page_number)
    data_dict = parse_qs(previous_data)
    get_key_instances(CompanyLeaves, data_dict)

    return render(
        request,
        "company_leave/company_leave.html",
        {
            "company_leaves": page_obj,
            "weeks": WEEKS,
            "week_days": WEEK_DAYS,
            "pd": previous_data,
            "filter_dict": data_dict,
        },
    )


@login_required
@hx_request_required
@permission_required("base.change_companyleaves")
def company_leave_update(request, id):
    """
    function used to update company leave.

    Parameters:
    request (HttpRequest): The HTTP request object.
    id : company leave id

    Returns:
    GET : return company leave update form template
    POST : return company leave view template
    """
    company_leave = CompanyLeaves.objects.get(id=id)
    form = CompanyLeaveForm(instance=company_leave)
    if request.method == "POST":
        form = CompanyLeaveForm(request.POST, instance=company_leave)
        if form.is_valid():
            form.save()
            messages.success(request, _("Company leave updated successfully.."))
    return render(
        request,
        "company_leave/company_leave_update_form.html",
        {"form": form, "id": id},
    )


@post_only
@login_required
@hx_request_required
@permission_required("base.delete_companyleaves")
def company_leave_delete(request, id):
    """
    function used to create company leave.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET : return company leave creation form template
    POST : return company leave view template
    """
    query_string = request.GET.urlencode()
    try:
        CompanyLeaves.objects.get(id=id).delete()
        messages.success(request, _("Company leave deleted successfully.."))
    except CompanyLeaves.DoesNotExist:
        messages.error(request, _("Company leave not found."))
    except ProtectedError:
        messages.error(request, _("Related entries exists"))
    if not CompanyLeaves.objects.filter():
        return HorillaRedirect(request)
    return redirect(f"/company-leave-filter?{query_string}")
