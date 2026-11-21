using JobTracker.Data;
using JobTracker.Domain;
using Microsoft.EntityFrameworkCore;
using Xunit;

namespace JobTracker.Tests;

/// <summary>
/// Talks to a real LocalDB database, but a throwaway one (JobTracker_Test), never the real JobTracker.
/// It is built from the migrations, so it also proves the migration matches the model.
/// </summary>
public sealed class DatabaseRoundTripTests : IDisposable
{
    private static readonly DateTimeOffset Day1 = new(2026, 10, 1, 9, 0, 0, TimeSpan.Zero);

    public DatabaseRoundTripTests()
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
            .UseSqlServer(@"Server=(localdb)\MSSQLLocalDB;Database=JobTracker_Test;Trusted_Connection=True;")
            .Options);

    private static Application NewApplication() => new()
    {
        CompanyName = "Acme",
        JobTitle = "Data Analyst",
        DateApplied = new DateOnly(2026, 10, 1),
    };

    [Fact]
    public void Saved_application_and_events_come_back_and_project_to_the_right_status()
    {
        int appId;
        using (var db = NewContext())
        {
            var app = NewApplication();
            db.Applications.Add(app);
            db.SaveChanges();
            appId = app.Id;

            // Id 0 means "the database picks it". Domain rules still run in Create.
            db.StatusEvents.Add(StatusEvent.Create(0, appId, null, ApplicationStatus.Applied,
                Day1, Day1, EventSource.Manual));
            db.StatusEvents.Add(StatusEvent.Create(0, appId, ApplicationStatus.Applied,
                ApplicationStatus.InterviewScheduled, Day1.AddDays(7), Day1.AddDays(7), EventSource.Manual));
            db.SaveChanges();
        }

        using (var db = NewContext())
        {
            var app = db.Applications.Single(a => a.Id == appId);
            var events = db.StatusEvents.Where(e => e.ApplicationId == appId).ToList();

            Assert.Equal("Acme", app.CompanyName);
            Assert.Equal(new DateOnly(2026, 10, 1), app.DateApplied);
            Assert.Equal(2, events.Count);
            Assert.All(events, e => Assert.NotEqual(0, e.Id));

            var result = StatusProjection.Project(events, Day1.AddDays(10));
            Assert.Equal(ApplicationStatus.InterviewScheduled, result.Status);
            Assert.Empty(result.Problems);
        }
    }

    [Fact]
    public void Statuses_are_stored_as_words()
    {
        using var db = NewContext();
        var app = NewApplication();
        db.Applications.Add(app);
        db.SaveChanges();
        db.StatusEvents.Add(StatusEvent.Create(0, app.Id, null, ApplicationStatus.Applied, Day1, Day1, EventSource.Manual));
        db.SaveChanges();

        var stored = db.Database.SqlQueryRaw<string>("SELECT NewStatus AS Value FROM StatusEvents").Single();

        Assert.Equal("Applied", stored);
    }

    [Fact]
    public void Event_for_a_missing_application_is_refused_by_the_database()
    {
        using var db = NewContext();
        db.StatusEvents.Add(StatusEvent.Create(0, 9999, null, ApplicationStatus.Applied, Day1, Day1, EventSource.Manual));

        Assert.Throws<DbUpdateException>(() => db.SaveChanges());
    }

    [Fact]
    public void Application_with_history_cannot_be_deleted()
    {
        int appId;
        using (var setup = NewContext())
        {
            var app = NewApplication();
            setup.Applications.Add(app);
            setup.SaveChanges();
            setup.StatusEvents.Add(StatusEvent.Create(0, app.Id, null, ApplicationStatus.Applied, Day1, Day1, EventSource.Manual));
            setup.SaveChanges();
            appId = app.Id;
        }

        // Fresh session that never loaded the events, so the refusal comes from the database itself.
        using var db = NewContext();
        db.Applications.Remove(db.Applications.Single(a => a.Id == appId));

        Assert.Throws<DbUpdateException>(() => db.SaveChanges());
    }
}
