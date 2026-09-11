"""
Utility functions related to biometric attendance.

This file contains utility functions related to biometric attendance,
including a function to check if the biometric system is installed.

Functions:
    biometric_is_installed(request): Checks if the biometric system is installed.
"""

from django.core.exceptions import ValidationError

from base.models import BiometricAttendance, Company


def biometric_is_installed(request):
    """
    Check if the biometric system is installed for the selected company.

    Args:
        request: The HTTP request object.

    Returns:
        dict: A dictionary with 'is_installed' boolean for the selected company.
    """
    selected_company = (
        request.session.get("selected_company") if hasattr(request, "session") else None
    )
    if selected_company == "all":
        company = None
    else:
        company = (
            Company.objects.filter(id=selected_company).first()
            if selected_company
            else None
        )

    instance = BiometricAttendance.objects.filter(company_id=company).first()
    if not instance:
        # This runs on every page render, so two concurrent first-requests
        # for a company with no row yet can both pass the .filter().first()
        # check above before either commits - BiometricAttendance.save()
        # correctly detects that and raises, but letting that raise here
        # would take down the page render for whoever lost the race.
        # Fall back to the row the winning request just created instead.
        try:
            instance = BiometricAttendance.objects.create(
                is_installed=False, company_id=company
            )
        except ValidationError:
            instance = BiometricAttendance.objects.filter(company_id=company).first()
    return {"is_installed": instance.is_installed if instance else False}
