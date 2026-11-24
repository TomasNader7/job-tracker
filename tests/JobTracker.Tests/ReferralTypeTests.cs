using JobTracker.Domain;
using Xunit;

namespace JobTracker.Tests;

public class ReferralTypeTests
{
    private static readonly DateOnly AppliedOn = new(2026, 11, 1);

    [Fact]
    public void An_application_can_carry_a_referral_type()
    {
        var app = new Application
        {
            CompanyName = "Acme",
            JobTitle = "Analyst",
            DateApplied = AppliedOn,
            ReferralType = ReferralType.NetworkingContact,
        };

        Assert.Equal(ReferralType.NetworkingContact, app.ReferralType);
    }

    [Fact]
    public void An_application_without_a_referral_type_is_still_valid()
    {
        var app = new Application
        {
            CompanyName = "Acme",
            JobTitle = "Analyst",
            DateApplied = AppliedOn,
        };

        Assert.Null(app.ReferralType);
    }

    [Fact]
    public void The_list_has_exactly_the_two_dashboard_values()
    {
        Assert.Equal(new[] { "NetworkingContact", "ColdApply" }, Enum.GetNames<ReferralType>());
    }
}
