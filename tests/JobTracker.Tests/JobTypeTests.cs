using JobTracker.Domain;
using Xunit;

namespace JobTracker.Tests;

public class JobTypeTests
{
    private static readonly DateOnly AppliedOn = new(2026, 11, 1);

    [Fact]
    public void An_application_can_carry_a_job_type()
    {
        var app = new Application
        {
            CompanyName = "Acme",
            JobTitle = "Analyst",
            DateApplied = AppliedOn,
            JobType = JobType.DataAnalyst,
        };

        Assert.Equal(JobType.DataAnalyst, app.JobType);
    }

    [Fact]
    public void An_application_without_a_job_type_is_still_valid()
    {
        var app = new Application
        {
            CompanyName = "Acme",
            JobTitle = "Analyst",
            DateApplied = AppliedOn,
        };

        Assert.Null(app.JobType);
    }
}
