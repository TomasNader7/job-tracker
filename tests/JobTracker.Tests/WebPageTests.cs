using JobTracker.Data;
using JobTracker.Domain;
using JobTracker.Web;
using JobTracker.Web.Pages;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;
using Microsoft.EntityFrameworkCore;
using Xunit;

namespace JobTracker.Tests;

/// <summary>Uses its own throwaway database (JobTracker_Test_Web).</summary>
public sealed class WebPageTests : IDisposable
{
    private static readonly DateTimeOffset Now = new(2026, 11, 21, 12, 0, 0, TimeSpan.Zero);
    private readonly FakeClock _clock = new(Now);

    public WebPageTests()
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
            .UseSqlServer(@"Server=(localdb)\MSSQLLocalDB;Database=JobTracker_Test_Web;Trusted_Connection=True;")
            .Options);

    [Fact]
    public void Add_page_saves_a_valid_form_and_goes_back_to_the_list()
    {
        using var db = NewContext();
        var service = new ApplicationService(db, _clock);
        var page = new AddModel(service, _clock)
        {
            CompanyName = "Acme",
            JobTitle = "Data Analyst",
            DateApplied = new DateOnly(2026, 11, 20),
        };

        var result = page.OnPost();

        var redirect = Assert.IsType<RedirectToPageResult>(result);
        Assert.Equal("Index", redirect.PageName);
        var saved = Assert.Single(service.List());
        Assert.Equal("Acme", saved.Application.CompanyName);
        Assert.Equal(ApplicationStatus.Applied, saved.Projection.Status);
    }

    [Fact]
    public void Add_page_with_an_invalid_form_shows_the_form_again_and_saves_nothing()
    {
        using var db = NewContext();
        var service = new ApplicationService(db, _clock);
        var page = new AddModel(service, _clock);
        page.ModelState.AddModelError("CompanyName", "required");

        var result = page.OnPost();

        Assert.IsType<PageResult>(result);
        Assert.Empty(service.List());
    }

    [Fact]
    public void Add_page_defaults_the_date_to_today()
    {
        using var db = NewContext();
        var page = new AddModel(new ApplicationService(db, _clock), _clock);

        page.OnGet();

        Assert.Equal(new DateOnly(2026, 11, 21), page.DateApplied);
    }

    [Fact]
    public void List_page_shows_newest_applications_first()
    {
        using var db = NewContext();
        var service = new ApplicationService(db, _clock);
        service.AddApplication("Older", "Analyst", new DateOnly(2026, 11, 1));
        service.AddApplication("Newer", "Analyst", new DateOnly(2026, 11, 15));
        var page = new IndexModel(service);

        page.OnGet();

        Assert.Equal(["Newer", "Older"], page.Applications.Select(v => v.Application.CompanyName));
    }

    private static ApplicationView View(ApplicationStatus? status, bool noReply) =>
        new(new Application { CompanyName = "X", JobTitle = "Y", DateApplied = new DateOnly(2026, 11, 1) },
            [], new ProjectionResult(status, noReply, []));

    [Theory]
    [InlineData(ApplicationStatus.NotStarted, "Not Started")]
    [InlineData(ApplicationStatus.Applied, "Applied")]
    [InlineData(ApplicationStatus.InterviewScheduled, "Interview Scheduled")]
    [InlineData(ApplicationStatus.Rejected, "Rejected")]
    public void Status_labels_read_as_words(ApplicationStatus status, string expected)
    {
        Assert.Equal(expected, StatusDisplay.Label(View(status, false)));
    }

    [Fact]
    public void No_reply_replaces_Applied_on_screen()
    {
        Assert.Equal("No Reply", StatusDisplay.Label(View(ApplicationStatus.Applied, true)));
    }

    [Fact]
    public void Missing_history_reads_as_no_history()
    {
        Assert.Equal("No history", StatusDisplay.Label(View(null, false)));
    }
}
