using JobTracker.Data;
using JobTracker.Domain;
using Microsoft.EntityFrameworkCore;
using Xunit;

namespace JobTracker.Tests;

/// <summary>A clock we control, so "30 days later" is one line instead of a 30 day wait.</summary>
internal sealed class FakeClock(DateTimeOffset now) : TimeProvider
{
    private DateTimeOffset _now = now;
    public override DateTimeOffset GetUtcNow() => _now;
    public void Advance(TimeSpan by) => _now += by;
}

/// <summary>Uses its own throwaway database (JobTracker_Test_Service) so it never clashes with other test classes.</summary>
public sealed class ApplicationServiceTests : IDisposable
{
    private static readonly DateTimeOffset Start = new(2026, 11, 1, 12, 0, 0, TimeSpan.Zero);
    private static readonly DateOnly AppliedOn = new(2026, 11, 1);
    private readonly FakeClock _clock = new(Start);

    public ApplicationServiceTests()
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
            .UseSqlServer(@"Server=(localdb)\MSSQLLocalDB;Database=JobTracker_Test_Service;Trusted_Connection=True;")
            .Options);

    private ApplicationService NewService(JobTrackerDbContext db) => new(db, _clock);

    private int AddAcme()
    {
        using var db = NewContext();
        return NewService(db).AddApplication("Acme", "Data Analyst", AppliedOn).Id;
    }

    [Fact]
    public void Adding_an_application_starts_its_history_at_Applied()
    {
        var id = AddAcme();

        using var db = NewContext();
        var view = NewService(db).Get(id);

        Assert.NotNull(view);
        Assert.Equal("Acme", view.Application.CompanyName);
        Assert.Equal(ApplicationStatus.Applied, view.Projection.Status);
        Assert.Single(view.Events);
    }

    [Theory]
    [InlineData("", "Data Analyst")]
    [InlineData("   ", "Data Analyst")]
    [InlineData("Acme", "")]
    [InlineData("Acme", "  ")]
    public void Blank_company_or_title_is_rejected(string company, string title)
    {
        using var db = NewContext();

        Assert.Throws<ArgumentException>(() => NewService(db).AddApplication(company, title, AppliedOn));
    }

    [Fact]
    public void Legal_status_change_is_saved_and_shows_in_the_projection()
    {
        var id = AddAcme();
        _clock.Advance(TimeSpan.FromDays(3));

        using var db = NewContext();
        var service = NewService(db);
        var result = service.RecordStatusChange(id, ApplicationStatus.InterviewScheduled, _clock.GetUtcNow(), null);

        Assert.True(result.Succeeded);
        var view = service.Get(id)!;
        Assert.Equal(ApplicationStatus.InterviewScheduled, view.Projection.Status);
        Assert.Equal(2, view.Events.Count);
        Assert.Equal(ApplicationStatus.Applied, view.Events[1].PreviousStatus);
    }

    [Fact]
    public void Illegal_status_change_is_refused_and_nothing_is_saved()
    {
        var id = AddAcme();

        using var db = NewContext();
        var service = NewService(db);
        var result = service.RecordStatusChange(id, ApplicationStatus.Accepted, _clock.GetUtcNow(), null);

        Assert.False(result.Succeeded);
        Assert.Contains("Accepted", result.Error);
        Assert.Single(service.Get(id)!.Events);
    }

    [Fact]
    public void Withdrawn_is_terminal()
    {
        var id = AddAcme();

        using var db = NewContext();
        var service = NewService(db);
        Assert.True(service.RecordStatusChange(id, ApplicationStatus.Withdrawn, _clock.GetUtcNow(), null).Succeeded);

        var again = service.RecordStatusChange(id, ApplicationStatus.InterviewScheduled, _clock.GetUtcNow(), null);

        Assert.False(again.Succeeded);
    }

    [Fact]
    public void Reopening_after_rejection_is_allowed()
    {
        var id = AddAcme();

        using var db = NewContext();
        var service = NewService(db);
        Assert.True(service.RecordStatusChange(id, ApplicationStatus.Rejected, _clock.GetUtcNow(), null).Succeeded);
        _clock.Advance(TimeSpan.FromDays(2));

        var reopen = service.RecordStatusChange(id, ApplicationStatus.InterviewScheduled, _clock.GetUtcNow(), null);

        Assert.True(reopen.Succeeded);
        Assert.Equal(ApplicationStatus.InterviewScheduled, service.Get(id)!.Projection.Status);
    }

    [Fact]
    public void Backdating_an_event_to_before_the_application_started_is_refused()
    {
        var id = AddAcme();

        using var db = NewContext();
        var service = NewService(db);
        var result = service.RecordStatusChange(id, ApplicationStatus.InterviewScheduled,
            Start.AddDays(-10), null);

        Assert.False(result.Succeeded);
        Assert.Contains("on or after 2026-11-01", result.Error);
        Assert.Single(service.Get(id)!.Events);
    }

    [Fact]
    public void Unknown_application_is_refused()
    {
        using var db = NewContext();

        var result = NewService(db).RecordStatusChange(9999, ApplicationStatus.Rejected, _clock.GetUtcNow(), null);

        Assert.False(result.Succeeded);
    }

    [Fact]
    public void Get_returns_null_for_an_unknown_application()
    {
        using var db = NewContext();

        Assert.Null(NewService(db).Get(9999));
    }

    [Fact]
    public void No_reply_appears_after_30_days_and_clears_on_a_late_reply()
    {
        var id = AddAcme();
        _clock.Advance(TimeSpan.FromDays(31));

        using var db = NewContext();
        var service = NewService(db);
        Assert.True(service.Get(id)!.Projection.IsNoReply);

        service.RecordStatusChange(id, ApplicationStatus.Rejected, _clock.GetUtcNow(), null);

        var view = service.Get(id)!;
        Assert.Equal(ApplicationStatus.Rejected, view.Projection.Status);
        Assert.False(view.Projection.IsNoReply);
    }

    // ---- Corrections ----

    private (int AppId, long WrongEventId) AppliedThenWronglyRejected(ApplicationService service)
    {
        var id = service.AddApplication("Acme", "Data Analyst", AppliedOn).Id;
        service.RecordStatusChange(id, ApplicationStatus.Rejected, Start.AddDays(5), null);
        return (id, service.Get(id)!.Events[1].Id);
    }

    [Fact]
    public void Correction_replaces_the_wrong_event_and_keeps_the_original_row()
    {
        using var db = NewContext();
        var service = NewService(db);
        var (id, wrongId) = AppliedThenWronglyRejected(service);

        var result = service.CorrectEvent(id, wrongId, ApplicationStatus.InterviewScheduled,
            Start.AddDays(5), "Typed Rejected by mistake");

        Assert.True(result.Succeeded, result.Error);
        var view = service.Get(id)!;
        Assert.Equal(ApplicationStatus.InterviewScheduled, view.Projection.Status);
        Assert.Empty(view.Projection.Problems);
        Assert.Equal(3, view.Events.Count);
        Assert.Contains(view.Events, e => e.Id == wrongId && e.NewStatus == ApplicationStatus.Rejected);
        var correction = view.Events.Single(e => e.Source == EventSource.Correction);
        Assert.Equal(wrongId, correction.SupersedesEventId);
        Assert.Equal("Typed Rejected by mistake", correction.Note);
        Assert.True(view.IsSuperseded(view.Events.Single(e => e.Id == wrongId)));
        Assert.False(view.IsSuperseded(correction));
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("   ")]
    public void Correction_without_a_note_is_refused_and_nothing_is_saved(string? note)
    {
        using var db = NewContext();
        var service = NewService(db);
        var (id, wrongId) = AppliedThenWronglyRejected(service);

        var result = service.CorrectEvent(id, wrongId, ApplicationStatus.InterviewScheduled, Start.AddDays(5), note);

        Assert.False(result.Succeeded);
        Assert.Contains("note", result.Error, StringComparison.OrdinalIgnoreCase);
        Assert.Equal(2, service.Get(id)!.Events.Count);
    }

    [Fact]
    public void An_event_that_was_already_corrected_cannot_be_corrected_again()
    {
        using var db = NewContext();
        var service = NewService(db);
        var (id, wrongId) = AppliedThenWronglyRejected(service);
        Assert.True(service.CorrectEvent(id, wrongId, ApplicationStatus.InterviewScheduled, Start.AddDays(5), "fix").Succeeded);

        var again = service.CorrectEvent(id, wrongId, ApplicationStatus.Withdrawn, Start.AddDays(5), "fix again");

        Assert.False(again.Succeeded);
        Assert.Contains("already been corrected", again.Error);
        Assert.Equal(3, service.Get(id)!.Events.Count);
    }

    [Fact]
    public void A_correction_can_itself_be_corrected()
    {
        using var db = NewContext();
        var service = NewService(db);
        var (id, wrongId) = AppliedThenWronglyRejected(service);
        service.CorrectEvent(id, wrongId, ApplicationStatus.InterviewScheduled, Start.AddDays(5), "first fix");
        var firstFixId = service.Get(id)!.Events.Single(e => e.Source == EventSource.Correction).Id;

        var result = service.CorrectEvent(id, firstFixId, ApplicationStatus.Withdrawn, Start.AddDays(5), "actually withdrew");

        Assert.True(result.Succeeded, result.Error);
        var view = service.Get(id)!;
        Assert.Equal(ApplicationStatus.Withdrawn, view.Projection.Status);
        Assert.Equal(4, view.Events.Count);
        Assert.Empty(view.Projection.Problems);
    }

    [Fact]
    public void Correction_of_a_missing_event_or_another_applications_event_is_refused()
    {
        using var db = NewContext();
        var service = NewService(db);
        var (id, _) = AppliedThenWronglyRejected(service);
        var other = service.AddApplication("Globex", "BI Analyst", AppliedOn).Id;
        var otherEventId = service.Get(other)!.Events[0].Id;

        Assert.False(service.CorrectEvent(id, 99999, ApplicationStatus.Rejected, Start, "x").Succeeded);
        Assert.False(service.CorrectEvent(id, otherEventId, ApplicationStatus.Rejected, Start, "x").Succeeded);
        Assert.False(service.CorrectEvent(9999, 1, ApplicationStatus.Rejected, Start, "x").Succeeded);
    }

    [Fact]
    public void Correction_that_would_break_later_events_is_refused_and_nothing_is_saved()
    {
        using var db = NewContext();
        var service = NewService(db);
        var id = service.AddApplication("Acme", "Data Analyst", AppliedOn).Id;
        service.RecordStatusChange(id, ApplicationStatus.InterviewScheduled, Start.AddDays(5), null);
        service.RecordStatusChange(id, ApplicationStatus.Interviewed, Start.AddDays(8), null);
        var interviewScheduledId = service.Get(id)!.Events[1].Id;

        // Rejected followed by Interviewed is not a legal chain.
        var result = service.CorrectEvent(id, interviewScheduledId, ApplicationStatus.Rejected, Start.AddDays(5), "wrong");

        Assert.False(result.Succeeded);
        Assert.Contains("break the history", result.Error);
        var view = service.Get(id)!;
        Assert.Equal(3, view.Events.Count);
        Assert.Equal(ApplicationStatus.Interviewed, view.Projection.Status);
    }

    [Fact]
    public void Correction_can_repair_a_history_that_was_already_broken()
    {
        using var db = NewContext();
        var service = NewService(db);
        var id = service.AddApplication("Acme", "Data Analyst", AppliedOn).Id;
        // Bad data that the service itself would never save (for example from an import).
        db.StatusEvents.Add(StatusEvent.Create(0, id, ApplicationStatus.Applied, ApplicationStatus.Accepted,
            Start.AddDays(5), Start.AddDays(5), EventSource.Import));
        db.SaveChanges();
        var broken = service.Get(id)!;
        Assert.NotEmpty(broken.Projection.Problems);
        var badEventId = broken.Events[1].Id;

        var result = service.CorrectEvent(id, badEventId, ApplicationStatus.Rejected, Start.AddDays(5), "Import had wrong status");

        Assert.True(result.Succeeded, result.Error);
        var fixedView = service.Get(id)!;
        Assert.Equal(ApplicationStatus.Rejected, fixedView.Projection.Status);
        Assert.Empty(fixedView.Projection.Problems);
    }

    [Fact]
    public void List_returns_every_application_with_its_own_status()
    {
        var first = AddAcme();
        using (var db = NewContext())
        {
            var service = NewService(db);
            var second = service.AddApplication("Globex", "BI Analyst", AppliedOn).Id;
            service.RecordStatusChange(second, ApplicationStatus.Rejected, _clock.GetUtcNow(), null);
        }

        using var db2 = NewContext();
        var all = NewService(db2).List();

        Assert.Equal(2, all.Count);
        Assert.Equal(ApplicationStatus.Applied, all.Single(v => v.Application.Id == first).Projection.Status);
        Assert.Equal(ApplicationStatus.Rejected, all.Single(v => v.Application.CompanyName == "Globex").Projection.Status);
    }
}
