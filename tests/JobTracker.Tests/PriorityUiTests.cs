using JobTracker.Data;
using JobTracker.Domain;
using JobTracker.Web;
using JobTracker.Web.Pages;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using Xunit;

namespace JobTracker.Tests;

/// <summary>Priority through the service and the Add page. Own throwaway database (JobTracker_Test_Priority).</summary>
public sealed class PriorityUiTests : IDisposable
{
    private static readonly DateTimeOffset Now = new(2026, 11, 21, 12, 0, 0, TimeSpan.Zero);
    private readonly FakeClock _clock = new(Now);

    public PriorityUiTests()
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
            .UseSqlServer(@"Server=(localdb)\MSSQLLocalDB;Database=JobTracker_Test_Priority;Trusted_Connection=True;")
            .Options);

    [Fact]
    public void The_service_saves_the_priority_it_is_given()
    {
        using (var db = NewContext())
            new ApplicationService(db, _clock).AddApplication(
                "Acme", "Analyst", new DateOnly(2026, 11, 20), priority: Priority.MediumPriority);

        using var db2 = NewContext();
        var saved = Assert.Single(new ApplicationService(db2, _clock).List());
        Assert.Equal(Priority.MediumPriority, saved.Application.Priority);
    }

    [Fact]
    public void The_service_keeps_job_type_resume_variant_and_priority_independent()
    {
        using (var db = NewContext())
            new ApplicationService(db, _clock).AddApplication(
                "Acme", "Analyst", new DateOnly(2026, 11, 20), JobType.DataEngineer, ResumeVariant.FrontendSwe, Priority.LowPriority);

        using var db2 = NewContext();
        var saved = Assert.Single(new ApplicationService(db2, _clock).List()).Application;
        Assert.Equal(JobType.DataEngineer, saved.JobType);
        Assert.Equal(ResumeVariant.FrontendSwe, saved.ResumeVariant);
        Assert.Equal(Priority.LowPriority, saved.Priority);
    }

    [Fact]
    public void Add_page_saves_the_chosen_priority()
    {
        using var db = NewContext();
        var service = new ApplicationService(db, _clock);
        var page = new AddModel(service, _clock)
        {
            CompanyName = "Acme",
            JobTitle = "Analyst",
            DateApplied = new DateOnly(2026, 11, 20),
            Priority = Priority.TopPriority,
        };

        var result = page.OnPost();

        Assert.IsType<RedirectToPageResult>(result);
        Assert.Equal(Priority.TopPriority, Assert.Single(service.List()).Application.Priority);
    }

    [Fact]
    public void Add_page_leaves_priority_empty_when_none_is_chosen()
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

        Assert.Null(Assert.Single(service.List()).Application.Priority);
    }

    [Fact]
    public void Dropdown_labels_are_the_dashboard_names_in_order_without_unspecified()
    {
        // The dashboard list ends with "Unspecified", which the app shows as the empty "(none)" choice instead.
        var csv = File.ReadAllLines(Path.Combine(RepoPaths.SyntheticDataDir, "dim_priority.csv"))
            .Skip(1)
            .Select(line => line.Split(',', 2)[1])
            .ToList();
        Assert.Equal("Unspecified", csv[^1]);

        var labels = Enum.GetValues<Priority>().Select(PriorityDisplay.Label).ToList();

        Assert.Equal(3, labels.Count);
        Assert.Equal(csv.Take(3), labels);
    }
}
