using JobTracker.Data;
using JobTracker.Domain;
using Microsoft.EntityFrameworkCore;
using Xunit;

namespace JobTracker.Tests;

/// <summary>
/// The DateKnown flag: an imported event whose real date the tracker never had carries a placeholder date
/// and DateKnown = false. Own throwaway database (JobTracker_Test_DateKnown).
/// </summary>
public sealed class DateKnownTests : IDisposable
{
    private static readonly DateTimeOffset Day = new(2026, 10, 5, 0, 0, 0, TimeSpan.Zero);
    private static readonly DateTimeOffset Now = new(2026, 11, 24, 12, 0, 0, TimeSpan.Zero);

    public DateKnownTests()
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
            .UseSqlServer(@"Server=(localdb)\MSSQLLocalDB;Database=JobTracker_Test_DateKnown;Trusted_Connection=True;")
            .Options);

    [Fact]
    public void An_event_has_a_known_date_unless_it_says_otherwise()
    {
        var e = StatusEvent.Create(0, 1, null, ApplicationStatus.Applied, Day, Now, EventSource.Manual);

        Assert.True(e.DateKnown);
    }

    [Fact]
    public void An_imported_event_can_have_an_unknown_date()
    {
        var e = StatusEvent.Create(0, 1, ApplicationStatus.Applied, ApplicationStatus.Rejected, Day, Now,
            EventSource.Import, dateKnown: false);

        Assert.False(e.DateKnown);
    }

    [Theory]
    [InlineData(EventSource.Manual)]
    [InlineData(EventSource.Correction)]
    public void Only_imported_events_may_have_an_unknown_date(EventSource source)
    {
        long? supersedes = source == EventSource.Correction ? 5 : null;
        string? note = source == EventSource.Correction ? "fix" : null;

        Assert.Throws<ArgumentException>(() => StatusEvent.Create(
            1, 1, ApplicationStatus.Applied, ApplicationStatus.Rejected, Day, Now, source,
            supersedes, note, dateKnown: false));
    }

    [Fact]
    public void The_flag_survives_a_round_trip_through_the_database()
    {
        int appId;
        using (var db = NewContext())
        {
            var app = new Application { CompanyName = "Acme", JobTitle = "Analyst", DateApplied = new DateOnly(2026, 10, 5) };
            db.Applications.Add(app);
            db.SaveChanges();
            appId = app.Id;
            db.StatusEvents.Add(StatusEvent.Create(0, appId, null, ApplicationStatus.Applied, Day, Now, EventSource.Import));
            db.StatusEvents.Add(StatusEvent.Create(0, appId, ApplicationStatus.Applied, ApplicationStatus.Rejected, Day, Now,
                EventSource.Import, dateKnown: false));
            db.SaveChanges();
        }

        using var db2 = NewContext();
        var events = db2.StatusEvents.Where(e => e.ApplicationId == appId).OrderBy(e => e.Id).ToList();
        Assert.Equal(new[] { true, false }, events.Select(e => e.DateKnown));
    }
}
