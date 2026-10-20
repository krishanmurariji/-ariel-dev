"""
Form fields shared by the exit screens.
"""

from django import forms


class TextCharField(forms.CharField):
    """
    A text field whose length is counted the way the person sees it.

    A browser counts a line break as one character while typing but sends it as two
    (CR LF), so a text that fit the on-screen limit could still be refused as "too long".
    Line breaks are made single before the length is checked.
    """

    def to_python(self, value):
        value = super().to_python(value)
        return value.replace("\r\n", "\n").replace("\r", "\n") if value else value
