using JobTracker.Domain;
using Microsoft.EntityFrameworkCore;

namespace JobTracker.Data;

/// <summary>An application together with its history and what the history adds up to.</summary>
public sealed record ApplicationView(
    Application Application,
    IReadOnlyList<StatusEvent> Events,
    ProjectionResult Projection)
{
    /// <summary>True if a later correction has replaced this event. It stays in the history, shown struck out.</summary>
    public bool IsSuperseded(StatusEvent e) => Events.Any(x => x.SupersedesEventId == e.Id);
}

public sealed record ChangeResult(bool Succeeded, string? Error)
{
    public static ChangeResult Ok() => new(true, null);
    public static ChangeResult Fail(string error) => new(false, error);
}

/// <summary>
/// The "front desk": the one place screens talk to. It checks the rules before anything is saved,
/// then saves through EF. It only ever adds events, never edits or deletes them.
/// </summary>
public sealed class ApplicationService(JobTrackerDbContext db, TimeProvider clock)
{
    public Application AddApplication(string companyName, string jobTitle, DateOnly dateApplied, JobType? jobType = null,
        ResumeVariant? resumeVariant = null, Priority? priority = null, ReferralType? referralType = null)
    {
        if (string.IsNullOrWhiteSpace(companyName))
            throw new ArgumentException("Company name is required.", nameof(companyName));
        if (string.IsNullOrWhiteSpace(jobTitle))
            throw new ArgumentException("Job title is required.", nameof(jobTitle));

        var app = new Application
        {
            CompanyName = companyName.Trim(),
            JobTitle = jobTitle.Trim(),
            DateApplied = dateApplied,
            JobType = jobType,
            ResumeVariant = resumeVariant,
            Priority = priority,
            ReferralType = referralType,
        };

        // Application and its first event are saved together or not at all.
        using var tx = db.Database.BeginTransaction();
        db.Applications.Add(app);
        db.SaveChanges();

        var appliedAt = new DateTimeOffset(dateApplied.ToDateTime(TimeOnly.MinValue), TimeSpan.Zero);
        db.StatusEvents.Add(StatusEvent.Create(0, app.Id, null, ApplicationStatus.Applied,
            appliedAt, clock.GetUtcNow(), EventSource.Manual));
        db.SaveChanges();
        tx.Commit();

        return app;
    }

    public ChangeResult RecordStatusChange(int applicationId, ApplicationStatus newStatus,
        DateTimeOffset effectiveAt, string? note)
    {
        if (!db.Applications.Any(a => a.Id == applicationId))
            return ChangeResult.Fail($"Application {applicationId} does not exist.");

        var existing = LoadEvents(applicationId);
        var current = StatusProjection.Project(existing, clock.GetUtcNow()).Status;

        if (current is null)
            return ChangeResult.Fail("This application has no valid history to change from.");

        if (!StatusTransitions.IsAllowed(current.Value, newStatus))
        {
            var allowed = StatusTransitions.AllowedNext(current.Value);
            var options = allowed.Count == 0 ? "none (final status)" : string.Join(", ", allowed);
            return ChangeResult.Fail($"{current} to {newStatus} is not allowed. Allowed from {current}: {options}.");
        }

        // Plain-language check first; the dry run below is the safety net for anything else.
        var latest = existing.Max(e => e.EffectiveAt);
        if (effectiveAt < latest)
            return ChangeResult.Fail($"The date must be on or after {latest:yyyy-MM-dd}, the date of the latest event.");

        var cleanNote = string.IsNullOrWhiteSpace(note) ? null : note.Trim();
        StatusEvent Build(long id) => StatusEvent.Create(id, applicationId, current, newStatus, effectiveAt,
            clock.GetUtcNow(), EventSource.Manual, note: cleanNote);

        // Dry run: would the whole history still be consistent? This catches backdating that breaks the order.
        // The trial copy gets an Id above every existing one, like the database will assign, so an event with
        // the same timestamp as an older one sorts after it. The saved row keeps Id 0 (database generates it).
        var trial = StatusProjection.Project(existing.Append(Build(NextEventId(existing))), clock.GetUtcNow());
        if (trial.Problems.Count > 0)
            return ChangeResult.Fail($"That date does not fit the history: {trial.Problems[0]}");

        var candidate = Build(0);
        db.StatusEvents.Add(candidate);
        db.SaveChanges();
        return ChangeResult.Ok();
    }

    /// <summary>
    /// Fixes a wrong entry by ADDING a correction row that replaces it. The wrong row is never edited or deleted.
    /// </summary>
    public ChangeResult CorrectEvent(int applicationId, long eventId, ApplicationStatus newStatus,
        DateTimeOffset effectiveAt, string? note)
    {
        if (!db.Applications.Any(a => a.Id == applicationId))
            return ChangeResult.Fail($"Application {applicationId} does not exist.");

        var existing = LoadEvents(applicationId);
        var target = existing.SingleOrDefault(e => e.Id == eventId);
        if (target is null)
            return ChangeResult.Fail($"Event {eventId} does not exist on this application.");

        if (existing.Any(e => e.SupersedesEventId == eventId))
            return ChangeResult.Fail("That event has already been corrected. Correct its latest version instead.");

        if (string.IsNullOrWhiteSpace(note))
            return ChangeResult.Fail("A correction needs a note explaining what was wrong.");

        // The correction takes the wrong event's place, so it keeps that event's "came from" status.
        StatusEvent Build(long id) => StatusEvent.Create(id, applicationId, target.PreviousStatus, newStatus, effectiveAt,
            clock.GetUtcNow(), EventSource.Correction, supersedesEventId: eventId, note: note.Trim());

        // Dry run. Refuse if the correction adds a problem. Problems that were already there do not
        // block it, so a correction can repair a history that was broken before.
        // The trial copy gets an Id above every existing one (see RecordStatusChange).
        var now = clock.GetUtcNow();
        var before = StatusProjection.Project(existing, now).Problems;
        var after = StatusProjection.Project(existing.Append(Build(NextEventId(existing))), now).Problems;
        var newProblems = after.Except(before).ToList();
        if (newProblems.Count > 0)
            return ChangeResult.Fail($"That correction would break the history: {newProblems[0]}");

        var candidate = Build(0);
        db.StatusEvents.Add(candidate);
        db.SaveChanges();
        return ChangeResult.Ok();
    }

    public ApplicationView? Get(int applicationId)
    {
        var app = db.Applications.AsNoTracking().SingleOrDefault(a => a.Id == applicationId);
        if (app is null) return null;

        var events = LoadEvents(applicationId);
        return new ApplicationView(app, events, StatusProjection.Project(events, clock.GetUtcNow()));
    }

    public IReadOnlyList<ApplicationView> List()
    {
        var apps = db.Applications.AsNoTracking().OrderBy(a => a.Id).ToList();
        var byApp = db.StatusEvents.AsNoTracking().ToList().GroupBy(e => e.ApplicationId)
            .ToDictionary(g => g.Key, g => (IReadOnlyList<StatusEvent>)Sort(g));
        var now = clock.GetUtcNow();

        return apps.Select(a =>
        {
            var events = byApp.GetValueOrDefault(a.Id) ?? [];
            return new ApplicationView(a, events, StatusProjection.Project(events, now));
        }).ToList();
    }

    /// <summary>
    /// An Id higher than any event in this application. Database identity values only grow, so a new row's real Id
    /// will also be higher than every existing one. Only used for dry runs, never saved.
    /// </summary>
    private static long NextEventId(IReadOnlyList<StatusEvent> existing) =>
        existing.Count == 0 ? 1 : existing.Max(e => e.Id) + 1;

    private List<StatusEvent> LoadEvents(int applicationId) =>
        Sort(db.StatusEvents.AsNoTracking().Where(e => e.ApplicationId == applicationId).ToList());

    private static List<StatusEvent> Sort(IEnumerable<StatusEvent> events) =>
        events.OrderBy(e => e.EffectiveAt).ThenBy(e => e.Id).ToList();
}
