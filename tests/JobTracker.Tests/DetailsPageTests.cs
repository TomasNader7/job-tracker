using JobTracker.Data;
using JobTracker.Domain;
using JobTracker.Web.Pages;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;
using Xunit;

namespace JobTracker.Tests;

/// <summary>Uses its own throwaway database (JobTracker_Test_Details).</summary>
public sealed class DetailsPageTests : IDisposable
{
    private static readonly DateTimeOffset Now = new(2026, 11, 21, 12, 0, 0, TimeSpan.Zero);
    private readonly FakeClock _clock = new(Now);

    public DetailsPageTests()
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
            .UseSqlServer(@"Server=(localdb)\MSSQLLocalDB;Database=JobTracker_Test_Details;Trusted_Connection=True;")
            .Options);

    private static int SeedApplication(ApplicationService service) =>
        service.AddApplication("Acme", "Data Analyst", new DateOnly(2026, 11, 1)).Id;

    [Fact]
    public void Unknown_application_gives_not_found()
    {
        using var db = NewContext();
        var page = new DetailsModel(new ApplicationService(db, _clock), _clock);

        Assert.IsType<NotFoundResult>(page.OnGet(9999));
    }

    [Fact]
    public void Page_offers_only_the_moves_the_rules_allow()
    {
        using var db = NewContext();
        var service = new ApplicationService(db, _clock);
        var id = SeedApplication(service);
        var page = new DetailsModel(service, _clock);

        var result = page.OnGet(id);

        Assert.IsType<PageResult>(result);
        Assert.Equal(
            [ApplicationStatus.InterviewScheduled, ApplicationStatus.Rejected, ApplicationStatus.Withdrawn],
            page.AllowedNext);
        Assert.Equal(new DateOnly(2026, 11, 21), page.EffectiveDate);
    }

    [Fact]
    public void Final_status_offers_no_moves()
    {
        using var db = NewContext();
        var service = new ApplicationService(db, _clock);
        var id = SeedApplication(service);
        service.RecordStatusChange(id, ApplicationStatus.Withdrawn, Now, null);
        var page = new DetailsModel(service, _clock);

        page.OnGet(id);

        Assert.Empty(page.AllowedNext);
    }

    [Fact]
    public void Legal_change_is_saved_and_the_page_reloads()
    {
        using var db = NewContext();
        var service = new ApplicationService(db, _clock);
        var id = SeedApplication(service);
        var page = new DetailsModel(service, _clock)
        {
            NewStatus = ApplicationStatus.InterviewScheduled,
            EffectiveDate = new DateOnly(2026, 11, 10),
            Note = "Recruiter emailed",
        };

        var result = page.OnPost(id);

        var redirect = Assert.IsType<RedirectToPageResult>(result);
        Assert.Equal(id, redirect.RouteValues!["id"]);
        var view = service.Get(id)!;
        Assert.Equal(ApplicationStatus.InterviewScheduled, view.Projection.Status);
        Assert.Equal("Recruiter emailed", view.Events[1].Note);
    }

    [Fact]
    public void Illegal_change_shows_the_rule_message_and_saves_nothing()
    {
        using var db = NewContext();
        var service = new ApplicationService(db, _clock);
        var id = SeedApplication(service);
        var page = new DetailsModel(service, _clock)
        {
            NewStatus = ApplicationStatus.Accepted,
            EffectiveDate = new DateOnly(2026, 11, 10),
        };

        var result = page.OnPost(id);

        Assert.IsType<PageResult>(result);
        var message = Assert.Single(page.ModelState[string.Empty]!.Errors).ErrorMessage;
        Assert.Contains("not allowed", message);
        Assert.Single(service.Get(id)!.Events);
    }

    [Fact]
    public void Posting_to_an_unknown_application_gives_not_found()
    {
        using var db = NewContext();
        var page = new DetailsModel(new ApplicationService(db, _clock), _clock) { NewStatus = ApplicationStatus.Rejected };

        Assert.IsType<NotFoundResult>(page.OnPost(9999));
    }
}
