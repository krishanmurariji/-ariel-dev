"""
accessibility/signals.py
"""

import threading

from django.core.cache import cache
from django.db.models.signals import post_save
from django.dispatch import receiver

from accessibility.middlewares import accessibility_cache_key
from accessibility.models import DefaultAccessibility
from employee.models import Employee, EmployeeWorkInformation
from horilla.signals import post_bulk_update


def _clear_accessibility_cache_for_all_default_accessibility_employees():
    """
    A DefaultAccessibility row (which feature is gated, exclude_all, which
    employees are on the list) changed - every employee's cached answer for
    that feature could now be wrong, and there is no cheap way to know in
    advance which employees were affected (added to the list, removed from
    it, exclude_all flipped, ...), so every employee's cache is cleared.
    """
    cache.delete_many(
        [accessibility_cache_key(pk) for pk in Employee.objects.values_list("id", flat=True)]
    )


def _clear_bulk_employees_cache(queryset):
    cache.delete_many(
        [accessibility_cache_key(instance.employee_id_id) for instance in queryset]
    )


@receiver(post_save, sender=EmployeeWorkInformation)
def monitor_employee_update(sender, instance, created, **kwargs):
    """
    This method tracks updates to an employee's work information instance.
    """
    if instance.employee_id_id:
        cache.delete(accessibility_cache_key(instance.employee_id_id))


@receiver(post_save, sender=DefaultAccessibility)
def monitor_accessibility_update(sender, instance, created, **kwargs):
    """
    This method is used to track accessibility updates
    """
    thread = threading.Thread(
        target=_clear_accessibility_cache_for_all_default_accessibility_employees
    )
    thread.start()


@receiver(post_bulk_update, sender=EmployeeWorkInformation)
def monitor_employee_bulk_update(sender, queryset, *args, **kwargs):
    """
    This method is used to track accessibility updates
    """
    # Was threading.Thread(target=_clear_bulk_employees_cache(queryset)) -
    # calling the function immediately and passing its return value (None)
    # as the thread's target, so this ran synchronously right here and the
    # Thread object created afterwards did nothing at all when started.
    thread = threading.Thread(target=_clear_bulk_employees_cache, args=(queryset,))
    thread.start()
