using JobTracker.Domain;
using Microsoft.EntityFrameworkCore;

namespace JobTracker.Data;

/// <summary>An application together with its history and what the history adds up to.</summary>
public sealed record ApplicationView(
    Application Application,
    IReadOnlyList<StatusEvent> Events,
    ProjectionResult Projection);

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
    public Application AddApplication(string companyName, string jobTitle, DateOnly dateApplied)
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

        var candidate = StatusEvent.Create(0, applicationId, current, newStatus, effectiveAt,
            clock.GetUtcNow(), EventSource.Manual, note: string.IsNullOrWhiteSpace(note) ? null : note.Trim());

        // Dry run: would the whole history still be consistent? This catches backdating that breaks the order.
        var trial = StatusProjection.Project(existing.Append(candidate), clock.GetUtcNow());
        if (trial.Problems.Count > 0)
            return ChangeResult.Fail($"That date does not fit the history: {trial.Problems[0]}");

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

    private List<StatusEvent> LoadEvents(int applicationId) =>
        Sort(db.StatusEvents.AsNoTracking().Where(e => e.ApplicationId == applicationId).ToList());

    private static List<StatusEvent> Sort(IEnumerable<StatusEvent> events) =>
        events.OrderBy(e => e.EffectiveAt).ThenBy(e => e.Id).ToList();
}
