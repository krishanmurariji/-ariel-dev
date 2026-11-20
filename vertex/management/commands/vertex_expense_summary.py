"""
Send the Vertex expense summary now.

    python manage.py vertex_expense_summary daily
    python manage.py vertex_expense_summary weekly

The same summaries go out by themselves after 18:00 (daily) and on Fridays after 18:00 (weekly);
this command is for sending one by hand or from the server's own scheduler.
"""

from django.core.management.base import BaseCommand

from vertex import expenses


class Command(BaseCommand):
    help = "Send the daily or weekly expense summary to its recipients."

    def add_arguments(self, parser):
        parser.add_argument("kind", choices=["daily", "weekly"])

    def handle(self, *args, **options):
        result = expenses.send_summary(options["kind"])
        self.stdout.write(
            self.style.SUCCESS(
                f"{options['kind']} summary: {result['recipients']} recipient(s), "
                f"{result['emailed']} e-mail(s) delivered, Teams: {'yes' if result['teams'] else 'no'}"
            )
        )
