using JobTracker.Domain;

namespace JobTracker.Data.Importing;

/// <summary>A row that was (or, in a dry run, would be) imported. Numbers only, no names, so a report is safe to share.</summary>
public sealed record ImportedRow(int SourceRow, string SourceId, int EventCount, int FlaggedEvents);

public sealed record ImportWarning(int SourceRow, string SourceId, string Message);

/// <param name="Written">False for a dry run: nothing was saved.</param>
public sealed record ImportReport(
    bool Written, IReadOnlyList<ImportedRow> Imported, IReadOnlyList<SkippedRow> Skipped, IReadOnlyList<ImportWarning> Warnings);

/// <summary>
/// Writes parsed tracker rows into the database as applications plus their planned status events.
/// A real run is one transaction (all or nothing). A row that already exists, or whose planned events do not
/// project back to the tracker's status, is skipped and reported; nothing is repaired or guessed.
/// </summary>
public sealed class TrackerImporter(JobTrackerDbContext db, TimeProvider clock)
{
    public ImportReport Import(TrackerParseResult parsed, bool write)
    {
        var now = clock.GetUtcNow();
        var imported = new List<ImportedRow>();
        var skipped = new List<SkippedRow>(parsed.Skipped);
        var warnings = new List<ImportWarning>();

        var seen = new HashSet<string>(
            db.Applications.Select(a => new { a.CompanyName, a.JobTitle, a.DateApplied })
                .AsEnumerable().Select(a => Key(a.CompanyName, a.JobTitle, a.DateApplied)),
            StringComparer.OrdinalIgnoreCase);

        using var tx = write ? db.Database.BeginTransaction() : null;

        foreach (var row in parsed.Rows)
        {
            if (!seen.Add(Key(row.CompanyName, row.JobTitle, row.DateApplied)))
            {
                skipped.Add(new SkippedRow(row.SourceRow, row.SourceId, "Already in the app (same company, title and date applied)."));
                continue;
            }

            var plan = EventPlanner.Plan(row);
            var problem = CheckPlan(row, plan, now);
            if (problem is not null)
            {
                seen.Remove(Key(row.CompanyName, row.JobTitle, row.DateApplied));
                skipped.Add(new SkippedRow(row.SourceRow, row.SourceId, problem));
                continue;
            }

            foreach (var message in plan.Warnings)
                warnings.Add(new ImportWarning(row.SourceRow, row.SourceId, message));
            imported.Add(new ImportedRow(row.SourceRow, row.SourceId, plan.Events.Count, plan.Events.Count(e => !e.DateKnown)));

            if (write)
                Save(row, plan, now);
        }

        tx?.Commit();
        return new ImportReport(write, imported, skipped, warnings);
    }

    private void Save(ImportRow row, EventPlan plan, DateTimeOffset now)
    {
        var app = new Application
        {
            CompanyName = row.CompanyName,
            JobTitle = row.JobTitle,
            DateApplied = row.DateApplied,
            JobType = row.JobType,
            ResumeVariant = row.ResumeVariant,
            Priority = row.Priority,
            ReferralType = row.ReferralType,
            Location = row.Location,
        };
        db.Applications.Add(app);
        db.SaveChanges(); // gives the application its Id; still inside the import's transaction

        foreach (var e in plan.Events)
            db.StatusEvents.Add(Build(0, app.Id, e, now));
        db.SaveChanges();
    }

    /// <summary>Safety net: the planned events must project back to exactly the tracker's status, with no problems.</summary>
    private static string? CheckPlan(ImportRow row, EventPlan plan, DateTimeOffset now)
    {
        var trial = plan.Events.Select((e, i) => Build(i + 1, 0, e, now)).ToList();
        var result = StatusProjection.Project(trial, now);

        if (result.Problems.Count > 0)
            return $"The planned history is not consistent: {result.Problems[0]}";

        // Compares the stored status. "No Reply" is a separate derived flag on the projection, so it does not interfere.
        if (result.Status != row.Status)
            return $"The planned history ends in {result.Status}, not the tracker's {row.Status}.";

        return null;
    }

    private static StatusEvent Build(long id, int applicationId, PlannedEvent e, DateTimeOffset now) =>
        StatusEvent.Create(id, applicationId, e.PreviousStatus, e.NewStatus,
            new DateTimeOffset(e.Date.ToDateTime(TimeOnly.MinValue), TimeSpan.Zero), now, EventSource.Import,
            note: e.DateKnown ? null : "Imported: real date unknown, placeholder date.",
            dateKnown: e.DateKnown);

    private static string Key(string company, string title, DateOnly date) =>
        $"{company.Trim()}|{title.Trim()}|{date:yyyy-MM-dd}";
}
