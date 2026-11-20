"""
vertex/learning.py

Training suggestions, ported from the rule-based recommender of ArielVertex (Learning controller):
weak KRA goal categories (average progress under 60 percent) become suggested training. Additions:

* the latest published performance report is used too: a competency scored under 60 percent suggests
  training for that competency;
* nothing is invented when no gap is found (Vertex added "Technical Growth" for everyone);
* a gap that already has open training is skipped;
* the category -> training table is a setting (VertexSetting "learning.mapping", JSON) that falls back
  to the Vertex defaults below.
"""

import json

from django.db.models import Avg
from django.utils.translation import gettext as _t

from vertex.models import (
    Goal,
    GoalStatus,
    PerformanceReport,
    TrainingRecommendation,
    TrainingSource,
    TrainingStatus,
    VertexSetting,
)
from vertex.notify import notify_employees

THRESHOLD = 60
SETTING_KEY = "learning.mapping"

GOAL_TRAINING = {
    "Project Delivery": ("Agile Delivery & Estimation Workshop", 2),
    "Technical Growth": ("Advanced Engineering / Cloud Certification", 3),
    "Quality & Ownership": ("Test Automation & Code Quality Bootcamp", 2),
    "Collaboration": ("Stakeholder Communication Program", 1),
    "Leadership": ("Leadership & Mentoring Program", 3),
    "Process & Compliance": ("Process Excellence & Compliance Training", 1),
}
COMPETENCY_TRAINING = {
    "technical": ("Advanced Engineering / Cloud Certification", 3),
    "delivery": ("Agile Delivery & Estimation Workshop", 2),
    "collaboration": ("Team Collaboration & Communication Workshop", 1),
    "client": ("Stakeholder Communication Program", 1),
    "accountability": ("Ownership & Accountability Program", 2),
    "availability": ("Time Management & Responsiveness Training", 1),
}
FALLBACK = ("Skill Development Program", 3)


def mapping():
    """Defaults, overridden by the JSON setting {"goal": {category: [training, months]}, "competency": {...}}."""
    result = {"goal": dict(GOAL_TRAINING), "competency": dict(COMPETENCY_TRAINING)}
    raw = VertexSetting.objects.filter(key=SETTING_KEY).values_list("value", flat=True).first()
    if raw:
        try:
            custom = json.loads(raw)
            for kind in ("goal", "competency"):
                for key, (training, months) in (custom.get(kind) or {}).items():
                    result[kind][key] = (str(training), int(months))
        except (ValueError, TypeError):
            pass  # a broken setting never stops suggestions: the defaults stay
    return result


def find_gaps(employee):
    """[(skill gap text, training, months)] for one employee, from goals and the latest published report."""
    table = mapping()
    gaps = []
    goals = (
        Goal.objects.filter(employee=employee).exclude(status=GoalStatus.CANCELLED).exclude(category="")
        .values("category").annotate(average=Avg("progress")).order_by("category")
    )
    for row in goals:
        if row["average"] is not None and row["average"] < THRESHOLD:
            training, months = table["goal"].get(row["category"], FALLBACK)
            gaps.append((_t("%(what)s - goal progress below %(n)s%%") % {"what": row["category"], "n": THRESHOLD}, training, months))

    report = (
        PerformanceReport.objects.filter(subject=employee, is_published=True).order_by("period").last()
    )
    if report is not None:
        from vertex.performance import CATEGORY_BY_KEY

        for score in report.category_scores.filter(not_applicable=False, score__lt=THRESHOLD).order_by("category"):
            category = CATEGORY_BY_KEY.get(score.category)
            name = category.name if category else score.category
            training, months = table["competency"].get(score.category, FALLBACK)
            gaps.append((_t("%(what)s - score below %(n)s%%") % {"what": name, "n": THRESHOLD}, training, months))
    return gaps


def suggest(employee, by):
    """Create training for the gaps found that are not already being worked on; returns the new rows."""
    open_gaps = set(
        TrainingRecommendation.objects.filter(employee=employee)
        .exclude(status=TrainingStatus.COMPLETED).values_list("skill_gap", flat=True)
    )
    created = []
    for gap, training, months in find_gaps(employee):
        if gap in open_gaps:
            continue
        created.append(
            TrainingRecommendation.objects.create(
                employee=employee, skill_gap=gap, recommended_training=training, duration_months=months,
                source=TrainingSource.SUGGESTED, created_by=by,
            )
        )
    if created:
        from django.urls import reverse

        notify_employees(
            by, [employee],
            _t("%(n)s training path(s) were suggested for you.") % {"n": len(created)},
            reverse("vertex-training"), icon="school-outline",
        )
    return created
