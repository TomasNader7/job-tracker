using JobTracker.Domain;
using Xunit;

namespace JobTracker.Tests;

public class ResumeVariantTests
{
    private static readonly DateOnly AppliedOn = new(2026, 11, 1);

    [Fact]
    public void An_application_can_carry_a_resume_variant()
    {
        var app = new Application
        {
            CompanyName = "Acme",
            JobTitle = "Analyst",
            DateApplied = AppliedOn,
            ResumeVariant = ResumeVariant.BackendSwe,
        };

        Assert.Equal(ResumeVariant.BackendSwe, app.ResumeVariant);
    }

    [Fact]
    public void An_application_without_a_resume_variant_is_still_valid_and_means_unspecified()
    {
        var app = new Application
        {
            CompanyName = "Acme",
            JobTitle = "Analyst",
            DateApplied = AppliedOn,
        };

        Assert.Null(app.ResumeVariant);
    }

    [Fact]
    public void The_list_has_the_seven_real_variants_and_no_unspecified_value()
    {
        // "Unspecified" is represented by null, never by an enum value.
        Assert.Equal(7, Enum.GetValues<ResumeVariant>().Length);
        Assert.DoesNotContain("Unspecified", Enum.GetNames<ResumeVariant>());
    }
}
