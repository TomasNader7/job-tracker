using JobTracker.Domain;
using Xunit;

namespace JobTracker.Tests;

public class PriorityTests
{
    private static readonly DateOnly AppliedOn = new(2026, 11, 1);

    [Fact]
    public void An_application_can_carry_a_priority()
    {
        var app = new Application
        {
            CompanyName = "Acme",
            JobTitle = "Analyst",
            DateApplied = AppliedOn,
            Priority = Priority.TopPriority,
        };

        Assert.Equal(Priority.TopPriority, app.Priority);
    }

    [Fact]
    public void An_application_without_a_priority_is_still_valid_and_means_unspecified()
    {
        var app = new Application
        {
            CompanyName = "Acme",
            JobTitle = "Analyst",
            DateApplied = AppliedOn,
        };

        Assert.Null(app.Priority);
    }

    [Fact]
    public void The_list_has_the_three_real_priorities_and_no_unspecified_value()
    {
        // "Unspecified" is represented by null, never by an enum value.
        Assert.Equal(3, Enum.GetValues<Priority>().Length);
        Assert.DoesNotContain("Unspecified", Enum.GetNames<Priority>());
    }
}
