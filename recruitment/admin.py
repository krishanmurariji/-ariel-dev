"""
admin.py

This page is used to register the model with admins site.
"""

from django.contrib import admin

from recruitment.models import (
    Candidate,
    CandidateIntakeLog,
    CandidateIntakeMailbox,
    CandidateRating,
    InterviewSchedule,
    Recruitment,
    RecruitmentSurvey,
    RecruitmentSurveyAnswer,
    RejectedCandidate,
    SkillZone,
    Stage,
)

# Register your models here.


admin.site.register(Stage)
admin.site.register(Recruitment)
admin.site.register(Candidate)
admin.site.register(RejectedCandidate)
admin.site.register(RecruitmentSurveyAnswer)
admin.site.register(RecruitmentSurvey)
admin.site.register(CandidateRating)
admin.site.register(SkillZone)
admin.site.register(InterviewSchedule)
# LinkedInAccount deliberately isn't registered here - it holds the
# LinkedIn app's client secret and OAuth access token, and the default
# ModelAdmin would show both in plaintext, unmasked, to anyone with
# Django-admin access to it.
admin.site.register(CandidateIntakeMailbox)
admin.site.register(CandidateIntakeLog)
