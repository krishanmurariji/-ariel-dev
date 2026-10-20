"""
Which Google Cloud credential "sign in with Google" uses. An account with no company (the
admin account) used to get "Google Cloud Credential not found" even though one was saved,
because the lookup only matched credentials saved against the employee's own company.
"""

from types import SimpleNamespace

from base.models import Company
from employee.models import EmployeeWorkInformation
from horilla.test_access_control import AccessTestCase
from horilla_auth.models import HorillaUser
from horilla_meet.methods import cloud_credential_for
from horilla_meet.models import GoogleCloudCredential


def company(name):
    return Company.objects.bulk_create(
        [Company(company=name, address="x", country="IN", state="x", city="x", zip="1")]
    )[0]


def credential(company=None, label="c"):
    return GoogleCloudCredential.objects.create(
        project_id=label, client_id=f"{label}.apps.googleusercontent.com", client_secret="secret",
        redirect_uris="https://example.test/meet/auth-callback/", company_id=company,
    )


class CloudCredentialLookupTests(AccessTestCase):
    def request_for(self, user, employee_company=None):
        EmployeeWorkInformation.objects.filter(employee_id__employee_user_id=user).update(company_id=employee_company)
        return SimpleNamespace(user=HorillaUser.objects.get(pk=user.pk))

    def test_the_account_with_no_company_finds_the_single_credential(self):
        saved = credential(company("Ariel"))
        self.assertEqual(cloud_credential_for(self.request_for(self.hr_user)), saved)

    def test_an_account_with_no_company_does_not_guess_between_several(self):
        credential(company("Ariel"), "a")
        credential(company("Other"), "b")
        self.assertIsNone(cloud_credential_for(self.request_for(self.hr_user)))

    def test_a_credential_not_tied_to_any_company_is_shared(self):
        credential(company("Ariel"), "a")
        shared = credential(None, "shared")
        self.assertEqual(cloud_credential_for(self.request_for(self.hr_user)), shared)

    def test_an_employee_gets_their_own_companys_credential(self):
        mine, theirs = company("Mine"), company("Theirs")
        credential(theirs, "theirs")
        own = credential(mine, "mine")
        self.assertEqual(cloud_credential_for(self.request_for(self.owner_user, mine)), own)

    def test_an_employee_never_gets_another_companys_credential(self):
        mine, theirs = company("Mine"), company("Theirs")
        credential(theirs, "theirs")
        self.assertIsNone(cloud_credential_for(self.request_for(self.owner_user, mine)))

    def test_nothing_saved_means_nothing_found(self):
        self.assertIsNone(cloud_credential_for(self.request_for(self.hr_user)))
