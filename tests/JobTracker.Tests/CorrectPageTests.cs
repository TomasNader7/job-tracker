using JobTracker.Data;
using JobTracker.Domain;
using JobTracker.Web.Pages;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;
using Xunit;

namespace JobTracker.Tests;

/// <summary>Uses its own throwaway database (JobTracker_Test_Correct).</summary>
public sealed class CorrectPageTests : IDisposable
{
    private static readonly DateTimeOffset Now = new(2026, 11, 21, 12, 0, 0, TimeSpan.Zero);
    private readonly FakeClock _clock = new(Now);

    public CorrectPageTests()
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
            .UseSqlServer(@"Server=(localdb)\MSSQLLocalDB;Database=JobTracker_Test_Correct;Trusted_Connection=True;")
            .Options);

    private (int AppId, long WrongId) Seed(ApplicationService service)
    {
        var id = service.AddApplication("Acme", "Data Analyst", new DateOnly(2026, 11, 1)).Id;
        service.RecordStatusChange(id, ApplicationStatus.Rejected, new DateTimeOffset(2026, 11, 5, 0, 0, 0, TimeSpan.Zero), null);
        return (id, service.Get(id)!.Events[1].Id);
    }

    [Fact]
    public void Unknown_application_or_event_gives_not_found()
    {
        using var db = NewContext();
        var service = new ApplicationService(db, _clock);
        var (id, _) = Seed(service);
        var page = new CorrectModel(service);

        Assert.IsType<NotFoundResult>(page.OnGet(9999, 1));
        Assert.IsType<NotFoundResult>(page.OnGet(id, 99999));
    }

    [Fact]
    public void Form_starts_with_the_values_of_the_event_being_corrected()
    {
        using var db = NewContext();
        var service = new ApplicationService(db, _clock);
        var (id, wrongId) = Seed(service);
        var page = new CorrectModel(service);

        var result = page.OnGet(id, wrongId);

        Assert.IsType<PageResult>(result);
        Assert.Equal(ApplicationStatus.Rejected, page.NewStatus);
        Assert.Equal(new DateOnly(2026, 11, 5), page.EffectiveDate);
    }

    [Fact]
    public void An_event_that_was_already_corrected_gives_not_found()
    {
        using var db = NewContext();
        var service = new ApplicationService(db, _clock);
        var (id, wrongId) = Seed(service);
        service.CorrectEvent(id, wrongId, ApplicationStatus.InterviewScheduled,
            new DateTimeOffset(2026, 11, 5, 0, 0, 0, TimeSpan.Zero), "fix");

        Assert.IsType<NotFoundResult>(new CorrectModel(service).OnGet(id, wrongId));
    }

    [Fact]
    public void Valid_correction_is_saved_and_goes_back_to_the_details_page()
    {
        using var db = NewContext();
        var service = new ApplicationService(db, _clock);
        var (id, wrongId) = Seed(service);
        var page = new CorrectModel(service)
        {
            NewStatus = ApplicationStatus.InterviewScheduled,
            EffectiveDate = new DateOnly(2026, 11, 5),
            Note = "Typed the wrong status",
        };

        var result = page.OnPost(id, wrongId);

        var redirect = Assert.IsType<RedirectToPageResult>(result);
        Assert.Equal("Details", redirect.PageName);
        Assert.Equal(id, redirect.RouteValues!["id"]);
        Assert.Equal(ApplicationStatus.InterviewScheduled, service.Get(id)!.Projection.Status);
    }

    [Fact]
    public void Correction_without_a_note_shows_the_message_and_saves_nothing()
    {
        using var db = NewContext();
        var service = new ApplicationService(db, _clock);
        var (id, wrongId) = Seed(service);
        var page = new CorrectModel(service)
        {
            NewStatus = ApplicationStatus.InterviewScheduled,
            EffectiveDate = new DateOnly(2026, 11, 5),
            Note = "",
        };

        var result = page.OnPost(id, wrongId);

        Assert.IsType<PageResult>(result);
        Assert.Contains("note", Assert.Single(page.ModelState[string.Empty]!.Errors).ErrorMessage,
            StringComparison.OrdinalIgnoreCase);
        Assert.Equal(2, service.Get(id)!.Events.Count);
    }
}
