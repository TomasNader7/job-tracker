using JobTracker.Data;
using JobTracker.Domain;
using JobTracker.Web;
using JobTracker.Web.Pages;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using Xunit;

namespace JobTracker.Tests;

/// <summary>Job type through the service and the Add page. Own throwaway database (JobTracker_Test_JobType).</summary>
public sealed class JobTypeUiTests : IDisposable
{
    private static readonly DateTimeOffset Now = new(2026, 11, 21, 12, 0, 0, TimeSpan.Zero);
    private readonly FakeClock _clock = new(Now);

    public JobTypeUiTests()
    {
        using var db = NewContext();
        db.Database.EnsureDeleted();
        db.Database.Migrate();
    }

    public void Dispose()
    {
        using var db = NewContext();
        db.Database.EnsureDeleted();
    }

    private static JobTrackerDbContext NewContext() =>
        new(new DbContextOptionsBuilder<JobTrackerDbContext>()
            .UseSqlServer(@"Server=(localdb)\MSSQLLocalDB;Database=JobTracker_Test_JobType;Trusted_Connection=True;")
            .Options);

    [Fact]
    public void The_service_saves_the_job_type_it_is_given()
    {
        using (var db = NewContext())
            new ApplicationService(db, _clock).AddApplication("Acme", "Analyst", new DateOnly(2026, 11, 20), JobType.BiAnalyst);

        using var db2 = NewContext();
        var saved = Assert.Single(new ApplicationService(db2, _clock).List());
        Assert.Equal(JobType.BiAnalyst, saved.Application.JobType);
    }

    [Fact]
    public void The_service_still_adds_an_application_with_no_job_type()
    {
        using (var db = NewContext())
            new ApplicationService(db, _clock).AddApplication("Acme", "Analyst", new DateOnly(2026, 11, 20));

        using var db2 = NewContext();
        var saved = Assert.Single(new ApplicationService(db2, _clock).List());
        Assert.Null(saved.Application.JobType);
    }

    [Fact]
    public void Add_page_saves_the_chosen_job_type()
    {
        using var db = NewContext();
        var service = new ApplicationService(db, _clock);
        var page = new AddModel(service, _clock)
        {
            CompanyName = "Acme",
            JobTitle = "Analyst",
            DateApplied = new DateOnly(2026, 11, 20),
            JobType = JobType.DataEngineer,
        };

        var result = page.OnPost();

        Assert.IsType<RedirectToPageResult>(result);
        Assert.Equal(JobType.DataEngineer, Assert.Single(service.List()).Application.JobType);
    }

    [Fact]
    public void Add_page_leaves_job_type_empty_when_none_is_chosen()
    {
        using var db = NewContext();
        var service = new ApplicationService(db, _clock);
        var page = new AddModel(service, _clock)
        {
            CompanyName = "Acme",
            JobTitle = "Analyst",
            DateApplied = new DateOnly(2026, 11, 20),
        };

        page.OnPost();

        Assert.Null(Assert.Single(service.List()).Application.JobType);
    }

    [Fact]
    public void The_list_shows_a_dash_when_an_application_has_no_job_type()
    {
        Assert.Equal("-", JobTypeDisplay.LabelOrDash(null));
    }

    [Fact]
    public void The_list_shows_the_label_when_an_application_has_a_job_type()
    {
        Assert.Equal("BI Analyst", JobTypeDisplay.LabelOrDash(JobType.BiAnalyst));
    }

    [Fact]
    public void Dropdown_labels_are_the_nine_names_from_the_dashboard_list_in_order()
    {
        var csv = File.ReadAllLines(Path.Combine(RepoPaths.SyntheticDataDir, "dim_job_type.csv"))
            .Skip(1)
            .Select(line => line.Split(',', 2)[1])
            .ToList();

        var labels = Enum.GetValues<JobType>().Select(JobTypeDisplay.Label).ToList();

        Assert.Equal(9, labels.Count);
        Assert.Equal(csv, labels);
    }
}
