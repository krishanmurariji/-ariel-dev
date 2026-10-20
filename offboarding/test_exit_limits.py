"""
Text limits on the exit screens: the allowed length is shown while typing (so it is
seen before saving), and a line break counts once - the way the box counts it.
"""

from django import forms
from django.test import SimpleTestCase
from django.urls import reverse

from offboarding.form_fields import TextCharField
from offboarding.test_exit_pages import HX
from offboarding.test_exit_task_pages import TaskPageBase


class Sample(forms.Form):
    plain = forms.CharField(max_length=20, required=False)
    text = TextCharField(max_length=20, required=False, widget=forms.Textarea)


class LineBreakCountingTests(SimpleTestCase):
    def test_a_text_that_fits_the_box_is_not_refused_for_its_line_breaks(self):
        # 20 characters as typed (the box counts a line break once); sent as CR LF it is 21
        sent = "a" * 9 + "\r\n" + "b" * 9
        self.assertEqual(len(sent.replace("\r\n", "\n")), 19)
        sent += "x"
        self.assertFalse(Sample({"plain": sent}).is_valid())  # the plain field counts CR LF as two
        form = Sample({"text": sent})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["text"], "a" * 9 + "\n" + "b" * 9 + "x")

    def test_too_long_is_still_refused(self):
        form = Sample({"text": "x" * 21})
        self.assertFalse(form.is_valid())
        self.assertIn("text", form.errors)

    def test_the_box_carries_the_limit(self):
        self.assertIn('maxlength="20"', str(Sample()["text"]))


class LimitShownInTheWindowsTests(TaskPageBase):
    def window(self, url):
        self.client.force_login(self.hr_user)
        return self.client.get(url, **HX).content.decode()

    def test_every_window_with_a_limited_field_loads_the_counter(self):
        task = self.task("Exit interview")
        for url in (
            reverse("exit-task-action", args=[task.pk, "skip"]) + "?next=panel",
            reverse("exit-cancel", args=[self.case.pk]),
            reverse("exit-change-date", args=[self.case.pk]),
            reverse("exit-initiate"),
        ):
            with self.subTest(url=url):
                html = self.window(url)
                self.assertIn("maxlength", html)
                self.assertIn("window.xmLimits", html)
                self.assertIn("xm-count", html)
