"""
payroll_pro/admin.py
"""

from django.contrib import admin

from payroll_pro.models import (
    EmployeeSalary,
    PayrollProAutoSetting,
    PayrollRun,
    PayrollRunRecord,
    SalaryComponent,
    SalaryStructure,
    TaxDeclaration,
    TaxRegime,
    TaxSlab,
)


class TaxSlabInline(admin.TabularInline):
    model = TaxSlab
    extra = 0


class SalaryComponentInline(admin.TabularInline):
    model = SalaryComponent
    extra = 0


@admin.register(TaxRegime)
class TaxRegimeAdmin(admin.ModelAdmin):
    list_display = ["name", "standard_deduction", "rebate_limit"]
    inlines = [TaxSlabInline]


@admin.register(SalaryStructure)
class SalaryStructureAdmin(admin.ModelAdmin):
    list_display = ["name", "tax_regime_id", "pf_enabled", "pt_enabled", "tds_enabled"]
    inlines = [SalaryComponentInline]


admin.site.register(EmployeeSalary)
admin.site.register(TaxDeclaration)
admin.site.register(PayrollRun)
admin.site.register(PayrollRunRecord)
admin.site.register(PayrollProAutoSetting)
