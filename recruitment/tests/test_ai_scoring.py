"""
recruitment/tests/test_ai_scoring.py

Coverage for AI-based candidate scoring (recruitment/ai_scoring.py): every
outcome branch, the automatic post_save trigger, and the manual
trigger/history/concurrency-guard behavior. Only the LLM call is mocked -
real DB, real PyMuPDF against a real generated PDF fixture.
"""

import re
import threading
import time
from unittest.mock import patch

from django.core.cache import cache
from django.core.files.base import ContentFile
from django.test import Client, TestCase, TransactionTestCase

from base.models import Company, Department, JobPosition
from employee.models import Employee
from horilla_auth.models import HorillaUser
from recruitment.models import Candidate, CandidateAIScore, Recruitment
from recruitment.tests.test_intake import TINY_PDF
from recruitment import ai_scoring


class AIScoringTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.company = Company.objects.create(
            company="Test Co", address="x", country="x", state="x", city="x", zip="00000"
        )
        self.department = Department.objects.create(department="Engineering")
        self.job_position = JobPosition.objects.create(
            job_position="Backend Engineer", department_id=self.department
        )
        self.recruitment = Recruitment.objects.create(
            title="Backend Engineer",
            closed=False,
            is_active=True,
            company_id=self.company,
            job_position_id=self.job_position,
        )
        self.recruitment.open_positions.add(self.job_position)

    def make_candidate(self, with_resume=True, email="cand@example.com", name="Jane Doe"):
        # Default name matches TINY_PDF's resume text (see test_intake.py)
        # so the identity gate in ai_scoring.score_candidate sees a normal
        # match by default; tests for the mismatch path pass a different name.
        candidate = Candidate(
            name=name,
            email=email,
            recruitment_id=self.recruitment,
            job_position_id=self.job_position,
        )
        if with_resume:
            candidate.resume.save("resume.pdf", ContentFile(TINY_PDF), save=False)
        candidate.save()
        return candidate


GOOD_LLM_RESULT = {
    "score": 82,
    "key_points": ["Strong Python/Django match", "5 years relevant experience"],
    "reasoning": "Solid alignment with the role's core requirements.",
}


class ScoreCandidateTests(AIScoringTestCase):
    def test_successful_scoring(self):
        candidate = self.make_candidate()
        with patch("recruitment.ai_scoring._call_llm", return_value=GOOD_LLM_RESULT):
            result = ai_scoring.score_candidate(candidate)
        self.assertEqual(result.status, "scored")
        self.assertEqual(result.score, 82)
        self.assertEqual(result.key_points, GOOD_LLM_RESULT["key_points"])
        self.assertEqual(result.scored_against, self.recruitment)
        self.assertEqual(result.candidate, candidate)
        # denormalized field used by the Candidates grid/Pipeline sort
        candidate.refresh_from_db()
        self.assertEqual(candidate.latest_ai_score, 82)

    def test_no_resume_skips_llm_call(self):
        candidate = self.make_candidate(with_resume=False)
        with patch("recruitment.ai_scoring._call_llm") as mocked:
            result = ai_scoring.score_candidate(candidate)
        self.assertEqual(result.status, "no_resume")
        mocked.assert_not_called()

    def test_empty_extracted_text_parse_failed(self):
        candidate = self.make_candidate()
        with patch("recruitment.ai_scoring.extract_resume_text", return_value="   "), \
             patch("recruitment.ai_scoring._call_llm") as mocked:
            result = ai_scoring.score_candidate(candidate)
        self.assertEqual(result.status, "parse_failed")
        mocked.assert_not_called()

    def test_llm_error_recorded(self):
        candidate = self.make_candidate()
        with patch(
            "recruitment.ai_scoring._call_llm",
            side_effect=ai_scoring.ScoringError("connection refused"),
        ):
            result = ai_scoring.score_candidate(candidate)
        self.assertEqual(result.status, "error")
        self.assertIn("connection refused", result.error_message)

    def test_score_clamped_to_valid_range(self):
        candidate = self.make_candidate()
        with patch(
            "recruitment.ai_scoring._call_llm",
            return_value={**GOOD_LLM_RESULT, "score": 150},
        ):
            result = ai_scoring.score_candidate(candidate)
        self.assertEqual(result.score, 100)

    def test_non_numeric_score_stored_as_none(self):
        candidate = self.make_candidate()
        with patch(
            "recruitment.ai_scoring._call_llm",
            return_value={**GOOD_LLM_RESULT, "score": "high"},
        ):
            result = ai_scoring.score_candidate(candidate)
        self.assertEqual(result.status, "scored")
        self.assertIsNone(result.score)

    def test_rescoring_keeps_history_not_overwrite(self):
        candidate = self.make_candidate()
        with patch("recruitment.ai_scoring._call_llm", return_value=GOOD_LLM_RESULT):
            ai_scoring.score_candidate(candidate)
        with patch(
            "recruitment.ai_scoring._call_llm",
            return_value={**GOOD_LLM_RESULT, "score": 40},
        ):
            ai_scoring.score_candidate(candidate)
        self.assertEqual(CandidateAIScore.objects.filter(candidate=candidate).count(), 2)
        # order by -id, not -created_at: two rows created microseconds
        # apart in the same test can tie on auto_now_add's resolution.
        latest = CandidateAIScore.objects.filter(candidate=candidate).order_by("-id").first()
        self.assertEqual(latest.score, 40)
        # re-scoring updates the denormalized field to the newest result
        candidate.refresh_from_db()
        self.assertEqual(candidate.latest_ai_score, 40)

    def test_resume_too_short_parse_failed(self):
        candidate = self.make_candidate()
        with patch(
            "recruitment.ai_scoring.extract_resume_text",
            return_value="Short resume text.",
        ), patch("recruitment.ai_scoring._call_llm") as mocked:
            result = ai_scoring.score_candidate(candidate)
        self.assertEqual(result.status, "parse_failed")
        mocked.assert_not_called()

    def test_name_not_in_resume_flags_low_confidence_and_caps_score(self):
        # Resume text (TINY_PDF) is about "Jane Doe" - this candidate's
        # recorded name never appears in it, the exact scenario reported:
        # a resume with no trace of the candidate's name still scoring high.
        candidate = self.make_candidate(name="John Smith")
        with patch("recruitment.ai_scoring._call_llm", return_value=GOOD_LLM_RESULT):
            result = ai_scoring.score_candidate(candidate)
        self.assertEqual(result.confidence, "low")
        self.assertIn("candidate_name_not_found", result.flags)
        self.assertLessEqual(result.score, ai_scoring.NAME_MISMATCH_SCORE_CAP)
        self.assertIn("verify this resume", result.key_points[0])

    def test_matching_name_stays_high_confidence(self):
        candidate = self.make_candidate(name="Jane Doe")
        with patch("recruitment.ai_scoring._call_llm", return_value=GOOD_LLM_RESULT):
            result = ai_scoring.score_candidate(candidate)
        self.assertEqual(result.confidence, "high")
        self.assertEqual(result.flags, [])
        self.assertEqual(result.score, GOOD_LLM_RESULT["score"])
        self.assertEqual(result.key_points, GOOD_LLM_RESULT["key_points"])

    def test_hallucinated_missing_skill_is_filtered_and_flagged(self):
        # Real-world case: the model claimed "React" was missing from a
        # resume that explicitly listed it. TINY_PDF's resume text lists
        # "Python" under skills - if the model claims Python is missing,
        # that's provably false and must not reach the user unchallenged.
        candidate = self.make_candidate()
        with patch(
            "recruitment.ai_scoring._call_llm",
            return_value={**GOOD_LLM_RESULT, "missing_skills": ["Python"]},
        ):
            result = ai_scoring.score_candidate(candidate)
        self.assertEqual(result.confidence, "low")
        self.assertIn("hallucinated_missing_skill", result.flags)
        self.assertFalse(
            any("Missing skills" in point for point in result.key_points)
        )

    def test_genuinely_missing_skill_is_kept_and_not_flagged(self):
        candidate = self.make_candidate()
        with patch(
            "recruitment.ai_scoring._call_llm",
            return_value={**GOOD_LLM_RESULT, "missing_skills": ["Kubernetes"]},
        ):
            result = ai_scoring.score_candidate(candidate)
        self.assertNotIn("hallucinated_missing_skill", result.flags)
        self.assertTrue(
            any(
                "Missing skills" in point and "Kubernetes" in point
                for point in result.key_points
            )
        )

    def test_divergent_scores_flag_low_confidence_and_average(self):
        candidate = self.make_candidate()
        with patch(
            "recruitment.ai_scoring._call_llm",
            side_effect=[
                {**GOOD_LLM_RESULT, "score": 90},
                {**GOOD_LLM_RESULT, "score": 20},
            ],
        ):
            result = ai_scoring.score_candidate(candidate)
        self.assertEqual(result.confidence, "low")
        self.assertIn("score_inconsistent", result.flags)
        self.assertEqual(result.score, 55)  # round((90 + 20) / 2)

    def test_second_scoring_pass_failure_keeps_first_result(self):
        candidate = self.make_candidate()
        with patch(
            "recruitment.ai_scoring._call_llm",
            side_effect=[GOOD_LLM_RESULT, ai_scoring.ScoringError("timeout")],
        ):
            result = ai_scoring.score_candidate(candidate)
        self.assertEqual(result.status, "scored")
        self.assertEqual(result.score, GOOD_LLM_RESULT["score"])
        self.assertEqual(result.confidence, "high")


class PipelineOrderingTests(AIScoringTestCase):
    def test_higher_score_sorts_first_unscored_sorts_last(self):
        from django.db.models import F

        low = self.make_candidate(email="low@example.com")
        low.latest_ai_score = 40
        low.save(update_fields=["latest_ai_score"])

        high = self.make_candidate(email="high@example.com")
        high.latest_ai_score = 90
        high.save(update_fields=["latest_ai_score"])

        unscored = self.make_candidate(email="unscored@example.com")

        ordered = list(
            Candidate.objects.filter(
                pk__in=[low.pk, high.pk, unscored.pk]
            ).order_by(F("latest_ai_score").desc(nulls_last=True))
        )
        self.assertEqual(ordered, [high, low, unscored])


class AutoScoreSignalTests(AIScoringTestCase):
    def test_signal_scores_new_candidate_with_resume(self):
        with patch("recruitment.ai_scoring.score_candidate") as mocked:
            with self.captureOnCommitCallbacks(execute=True):
                candidate = self.make_candidate()
        mocked.assert_called_once()
        self.assertEqual(mocked.call_args[0][0].pk, candidate.pk)

    def test_signal_skips_candidate_without_resume(self):
        with patch("recruitment.ai_scoring.score_candidate") as mocked:
            with self.captureOnCommitCallbacks(execute=True):
                self.make_candidate(with_resume=False)
        mocked.assert_not_called()

    def test_signal_skips_on_update_not_create(self):
        candidate = self.make_candidate()
        with patch("recruitment.ai_scoring.score_candidate") as mocked:
            with self.captureOnCommitCallbacks(execute=True):
                candidate.name = "Renamed"
                candidate.save()
        mocked.assert_not_called()


class ManualTriggerViewTests(AIScoringTestCase):
    def setUp(self):
        super().setUp()
        self.user = HorillaUser.objects.create_superuser(
            username="scoretestuser", email="scoretestuser@example.com", password="Pass123!"
        )
        Employee.objects.create(
            employee_user_id=self.user,
            employee_first_name="Score",
            employee_last_name="Tester",
            email="scoretestuser@example.com",
        )
        self.client = Client()
        self.client.login(username="scoretestuser", password="Pass123!")

    def tearDown(self):
        # ThreadLocalMiddleware stashes the request (and its user) in a
        # ContextVar that outlives this test's transaction rollback -
        # left alone, a later test in the same process would see a
        # "modified_by" pointing at a user row that no longer exists.
        from horilla.horilla_middlewares import _thread_locals

        _thread_locals.request = None
        super().tearDown()

    def test_trigger_starts_a_background_thread(self):
        candidate = self.make_candidate()
        with patch("recruitment.views.ai_scoring.threading.Thread") as mocked_thread_cls:
            resp = self.client.post(
                f"/recruitment/candidate-ai-score-trigger/{candidate.id}/",
                HTTP_HX_REQUEST="true",
            )
        self.assertEqual(resp.status_code, 200)
        mocked_thread_cls.assert_called_once()
        mocked_thread_cls.return_value.start.assert_called_once()

    def test_guard_blocks_concurrent_trigger(self):
        from recruitment.views.ai_scoring import _running_cache_key

        candidate = self.make_candidate()
        cache.set(_running_cache_key(candidate.id), True, timeout=None)

        with patch("recruitment.views.ai_scoring.threading.Thread") as mocked_thread_cls:
            resp = self.client.post(
                f"/recruitment/candidate-ai-score-trigger/{candidate.id}/",
                HTTP_HX_REQUEST="true",
            )
        self.assertEqual(resp.status_code, 200)
        mocked_thread_cls.assert_not_called()


class ConcurrentAutomaticScoringTests(TransactionTestCase):
    """
    Answers a real question: if multiple applicants submit at the same
    moment, does the automatic post_save -> transaction.on_commit ->
    threading.Thread trigger (recruitment/signals.py:auto_score_new_candidate)
    hold up correctly, or does one candidate's scoring clobber/leak into
    another's?

    TransactionTestCase (not TestCase) is required: TestCase wraps every
    test in an outer atomic block that's rolled back at the end, so
    transaction.on_commit callbacks never fire for real - the signal's
    background thread would simply never start. TransactionTestCase commits
    for real, exactly like production, so the real thread fires.

    Requires DATABASES["default"]["TEST"]["NAME"] to be a real file (see
    horilla/settings/base.py) rather than the sqlite3 default of ":memory:",
    since ":memory:" is private per-connection - the background thread opens
    its own connection and would otherwise see an empty database instead of
    the just-created candidate row.
    """

    def setUp(self):
        cache.clear()
        self.company = Company.objects.create(
            company="Test Co", address="x", country="x", state="x", city="x", zip="00000"
        )
        self.department = Department.objects.create(department="Engineering")
        self.job_position = JobPosition.objects.create(
            job_position="Backend Engineer", department_id=self.department
        )
        self.recruitment = Recruitment.objects.create(
            title="Backend Engineer",
            closed=False,
            is_active=True,
            company_id=self.company,
            job_position_id=self.job_position,
        )
        self.recruitment.open_positions.add(self.job_position)

    def tearDown(self):
        # Same cleanup ManualTriggerViewTests does - ThreadLocalMiddleware
        # stashes the request in a ContextVar that outlives this test.
        from horilla.horilla_middlewares import _thread_locals

        _thread_locals.request = None
        super().tearDown()

    @staticmethod
    def _resume_bytes(candidate_name, marker_score):
        # >= MIN_RESUME_WORDS words, contains the candidate's own name (so
        # the identity-match check doesn't cap the score), and a marker the
        # fake LLM below can read back out - lets each concurrent thread
        # prove it scored the resume that actually belongs to it.
        filler = " ".join(
            ["experienced", "software", "engineer", "skilled", "professional"] * 10
        )
        return f"{candidate_name} resume. SCORE_MARKER:{marker_score}. {filler}".encode()

    @staticmethod
    def _fake_extract(resume_bytes):
        return resume_bytes.decode()

    @staticmethod
    def _fake_call_llm(resume_text, job):
        # Small sleep forces genuine overlap between concurrently running
        # threads instead of them happening to run back-to-back too fast to
        # ever actually interleave.
        time.sleep(0.05)
        marker = int(re.search(r"SCORE_MARKER:(\d+)", resume_text).group(1))
        return {"score": marker, "key_points": ["ok"], "reasoning": "ok"}

    @staticmethod
    def _wait_until(condition, timeout=10, interval=0.05):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if condition():
                return True
            time.sleep(interval)
        return condition()

    def _submit(self, email, resume_bytes):
        from django.db import close_old_connections

        try:
            candidate = Candidate(
                name=email,
                email=email,
                recruitment_id=self.recruitment,
                job_position_id=self.job_position,
            )
            candidate.resume.save("resume.pdf", ContentFile(resume_bytes), save=False)
            candidate.save()  # fires the real signal -> on_commit -> background thread
        finally:
            # This runs on its own thread (simulating a concurrent HTTP
            # request) - a real request gets its connection closed
            # automatically via Django's request_finished signal, but this
            # synthetic thread doesn't, so it has to do it explicitly, same
            # as the production signal handler in recruitment/signals.py.
            close_old_connections()

    def test_simultaneous_applications_each_get_their_own_correct_score(self):
        """
        5 candidates 'apply' from 5 separate threads at effectively the same
        instant, each with a distinct expected score baked into their
        resume. If the automatic scoring path has any cross-thread/DB race,
        this is where a candidate would end up with someone else's score,
        no score at all, or more than one CandidateAIScore row.
        """
        expected = {f"cand{i}@example.com": (i + 1) * 10 for i in range(5)}  # 10..50

        with patch(
            "recruitment.ai_scoring.extract_resume_text", side_effect=self._fake_extract
        ), patch("recruitment.ai_scoring._call_llm", side_effect=self._fake_call_llm):
            threads = [
                threading.Thread(
                    target=self._submit, args=(email, self._resume_bytes(email, score))
                )
                for email, score in expected.items()
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

            # candidate.save() returning just means the *submission* is
            # done - the actual scoring runs in its own inner background
            # thread started from transaction.on_commit, so poll for it
            # rather than assume it already finished.
            scored = self._wait_until(
                lambda: CandidateAIScore.objects.filter(
                    candidate__email__in=expected.keys()
                ).count()
                >= len(expected),
                timeout=10,
            )

        self.assertTrue(scored, "not all candidates were scored within the timeout")
        for email, expected_score in expected.items():
            candidate = Candidate.objects.get(email=email)
            self.assertEqual(
                candidate.latest_ai_score,
                expected_score,
                f"{email} ended up with a different candidate's score - "
                f"cross-talk between concurrent scoring threads",
            )
            self.assertEqual(
                CandidateAIScore.objects.filter(candidate=candidate).count(),
                1,
                f"{email} was scored more than once",
            )

    def test_one_candidates_llm_failure_does_not_affect_others(self):
        """
        One candidate's LLM call blows up mid-burst - the other candidates
        submitting at the same time must still be scored correctly. A
        failure in one background thread must not leak into another's.
        """
        FAIL_EMAIL = "fails@example.com"
        expected = {f"ok{i}@example.com": (i + 1) * 10 for i in range(4)}

        def flaky_call_llm(resume_text, job):
            if "FAIL_MARKER" in resume_text:
                raise ai_scoring.ScoringError("simulated upstream failure")
            return self._fake_call_llm(resume_text, job)

        with patch(
            "recruitment.ai_scoring.extract_resume_text", side_effect=self._fake_extract
        ), patch("recruitment.ai_scoring._call_llm", side_effect=flaky_call_llm):
            fail_bytes = b"FAIL_MARKER " + self._resume_bytes(FAIL_EMAIL, 0)
            threads = [threading.Thread(target=self._submit, args=(FAIL_EMAIL, fail_bytes))]
            threads += [
                threading.Thread(
                    target=self._submit, args=(email, self._resume_bytes(email, score))
                )
                for email, score in expected.items()
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

            all_emails = list(expected.keys()) + [FAIL_EMAIL]
            scored = self._wait_until(
                lambda: CandidateAIScore.objects.filter(
                    candidate__email__in=all_emails
                ).count()
                >= len(all_emails),
                timeout=10,
            )

        self.assertTrue(scored)
        for email, expected_score in expected.items():
            candidate = Candidate.objects.get(email=email)
            self.assertEqual(candidate.latest_ai_score, expected_score)

        failed_candidate = Candidate.objects.get(email=FAIL_EMAIL)
        failed_score_row = CandidateAIScore.objects.filter(
            candidate=failed_candidate
        ).latest("id")
        self.assertEqual(failed_score_row.status, "error")

    def test_submission_is_not_blocked_by_the_llm_call(self):
        """
        The whole point of scoring in a background thread is that the
        applicant's own request doesn't wait on the LLM. Proves
        candidate.save() returns almost immediately even though the (mocked)
        LLM call is deliberately slow.
        """
        slow_seconds = 1.0

        def slow_call_llm(resume_text, job):
            time.sleep(slow_seconds)
            return GOOD_LLM_RESULT

        with patch(
            "recruitment.ai_scoring.extract_resume_text", side_effect=self._fake_extract
        ), patch("recruitment.ai_scoring._call_llm", side_effect=slow_call_llm):
            candidate = Candidate(
                name="Speed Test",
                email="speed@example.com",
                recruitment_id=self.recruitment,
                job_position_id=self.job_position,
            )
            candidate.resume.save(
                "resume.pdf",
                ContentFile(self._resume_bytes("Speed Test", 77)),
                save=False,
            )

            start = time.monotonic()
            candidate.save()
            elapsed = time.monotonic() - start

            self.assertLess(
                elapsed,
                slow_seconds,
                "candidate.save() waited on the LLM call - scoring is no "
                "longer non-blocking for the applicant's own request",
            )

            scored = self._wait_until(
                lambda: CandidateAIScore.objects.filter(candidate=candidate).exists(),
                timeout=slow_seconds + 5,
            )
        self.assertTrue(scored)
